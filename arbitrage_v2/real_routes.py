"""Reserve a real plan, then record actual receipts. Never place a market order."""
from .discovery import DISCOVERY_VERSION, SUPPORTED_DISCOVERY_VERSIONS, route_inputs
from .evidence import stamp, utc
from .funds import REAL_ACCOUNTS, account_money
from .prediction import FAMILY, ITEM_FAMILY, QUALIFIED_ITEMS, calculate, cents, may_use_balance
from .real_funds import EMPTY_MANDATE, assert_cash, real_time, reference_available, text_field
from .routes import _state, add_event, open_route


def _review(journal, prediction_id, mandate, policy, at, db):
    pred = journal.get(prediction_id, "prediction", db)
    funds = account_money(journal, mandate, mode="confirmed", db=db)
    blockers = []
    if not journal.records("real_funding", db):
        blockers.append("Record your actual DMarket funds first. The paper balance cannot fund a real route.")
    if (pred.get("search_mode") != "confirmed" or pred.get("learning_mode", "confirmed") != "confirmed"
            or pred.get("input_kind") != "recorded" or pred.get("discovery_version") not in SUPPORTED_DISCOVERY_VERSIONS
            or pred.get("family") not in {FAMILY, ITEM_FAMILY} or pred.get("purpose") != "grow"):
        blockers.append("Run a real-funds search and select one of its recorded CS2 growth estimates.")
    if pred.get("policy") != policy:
        blockers.append("The cost or wait assumptions changed. Search again.")
    recorded = db.execute("SELECT recorded_at FROM records WHERE id=?", (prediction_id,)).fetchone()[0]
    if utc(recorded) > utc(at) or not 0 <= (utc(at)-utc(pred["predicted_at"])).total_seconds() <= 14400:
        blockers.append("This estimate needs a fresh search before opening a new route.")
    remaining, funding = pred["entry_cost_cents"], []
    for kind in ("tradable", "regular"):
        account = "dmarket_"+kind
        amount = min(remaining, max(0, funds["available_accounts"].get(account, 0)))
        if amount and may_use_balance(kind, pred["app_id"], "market_purchase"):
            funding.append({"account": account, "amount_cents": amount})
            remaining -= amount
    if remaining or pred["entry_cost_cents"] > funds["available_cents"]:
        blockers.append("There is not enough uncommitted real DMarket money for this plan.")
    if not blockers:
        try:
            a, b = route_inputs(journal, pred, at, db=db)
            current = calculate(a, b, pred["quantity_a"], policy, pred["sale_scenarios"]["steam"], pred["sale_scenarios"]["dmarket"])
            if current["input_kind"] != "recorded" or current["evidence_ids"] != pred["evidence_ids"] or current["quote_legs"] != pred["quote_legs"]:
                blockers.append("Prices or source records changed. Search again and review the new estimate.")
        except (ValueError, KeyError, TypeError) as exc:
            blockers.append("A needed price must be refreshed: "+str(exc))
    return {"prediction_id": prediction_id, "prediction": pred, "mode": "confirmed", "ready": not blockers,
        "blockers": blockers, "available_cents": funds["available_cents"], "funding": funding,
        "budget_before_entry": funds, "return_basket": [pred["item_b"]],
        "outward_delay_seconds": policy["minimum_known_delay_seconds"]-policy["minimum_return_delay_seconds"],
        "return_delay_seconds": policy["minimum_return_delay_seconds"],
        "note": "Opening this plan saves the estimate and sets money aside. It does not buy anything. Record your actual receipts and unlock times after manual operations."}


def preview(journal, prediction_id, mandate, policy, at):
    with journal.connect() as db:
        db.execute("BEGIN")
        return _review(journal, prediction_id, mandate, policy, at, db)


def enter(journal, request, mandate, policy, at):
    if set(request) != {"route_id", "prediction_id"}:
        raise ValueError("Choose a route name and real-funds prediction.")
    text_field(request["route_id"])
    identifier = "confirmed-entry:"+request["route_id"]
    with journal.connect(True) as db:
        if db.execute("SELECT 1 FROM records WHERE id=?", (identifier,)).fetchone():
            old = journal.get(identifier, "confirmed_entry", db)
            if old["request"] != request:
                raise ValueError("This route name already belongs to a different plan.")
            return {"route_id": request["route_id"], "status": "already_opened", "mode": "confirmed"}
        at = real_time(at)
        review = _review(journal, request["prediction_id"], mandate, policy, at, db)
        if not review["ready"]:
            raise ValueError(" ".join(review["blockers"]))
        open_route(journal, request["route_id"], request["prediction_id"], "confirmed", at, db)
        journal.append("confirmed_entry", {"schema_version": 1, "route_id": request["route_id"], "request": request,
            "at": at, "funding": review["funding"], "budget_before_entry": review["budget_before_entry"],
            "prediction_id": request["prediction_id"]}, identifier, db)
        assert_cash(journal, db)
    return {"route_id": request["route_id"], "status": "opened", "mode": "confirmed"}


def entry_finished(journal, route_id, db):
    return any(r["route_id"] == route_id and (r["kind"] == "finish_entry" or r.get("entry_complete") is True)
               for r in journal.records("real_step", db))


FIELDS = {
    "buy_a": {"quantity", "regular_cents", "tradable_cents", "fee_cents", "entry_complete"},
    "finish_entry": set(), "transfer_a": {"next_eligible_at"},
    "sell_a": {"quantity", "net_cents", "fee_cents"},
    "buy_b": {"item_title", "quantity", "net_cents", "fee_cents", "next_eligible_at"},
    "transfer_b": set(), "sell_b": {"item_title", "quantity", "net_cents", "fee_cents", "account"},
    "cost": {"net_cents"}, "cancel": {"reason"},
}


def record_step(journal, request):
    kind = request.get("kind")
    if kind not in FIELDS or set(request) != {"action_id", "route_id", "kind", "at", "reference"} | FIELDS[kind]:
        raise ValueError("Invalid receipt fields.")
    for key in ("action_id", "route_id", "reference"):
        text_field(request[key])
    record = dict(request, at=real_time(request["at"]), schema_version=1)
    if "next_eligible_at" in record and record["next_eligible_at"] is not None:
        record["next_eligible_at"] = stamp(utc(record["next_eligible_at"]))
    identifier = "real-step:"+request["action_id"]
    with journal.connect(True) as db:
        if db.execute("SELECT 1 FROM records WHERE id=?", (identifier,)).fetchone():
            journal.append("real_step", record, identifier, db)
            return identifier
        route, pred, events, movements, assets, stage, eligible, last, resolved = _state(journal, request["route_id"], db)
        journal.get("confirmed-entry:"+route["route_id"], "confirmed_entry", db)
        if route["mode"] != "confirmed" or resolved:
            raise ValueError("Choose an open real route created from a saved estimate.")
        if utc(record["at"]) < last:
            raise ValueError("The receipt must follow earlier updates in this route.")
        reference_available(journal, record["reference"], db)
        if any(r["reference"] == record["reference"] for r in journal.records("real_step", db)):
            raise ValueError("This real-operation reference is already recorded.")
        for field in ("quantity", "regular_cents", "tradable_cents", "net_cents", "fee_cents"):
            if field in record:
                cents(record[field])
        if "quantity" in record and record["quantity"] <= 0:
            raise ValueError("Enter a positive whole-item quantity.")
        if "next_eligible_at" in record and record["next_eligible_at"] is not None:
            record["next_eligible_at"] = stamp(utc(record["next_eligible_at"]))
        finished = entry_finished(journal, route["route_id"], db)
        akey = "730:"+pred["item_a"]
        # Save inside this transaction before movements so a completed entry can
        # release its own unused reservation. Any invalid receipt rolls it all back.
        journal.append("real_step", record, identifier, db)
        base = {"route_id": route["route_id"], "at": record["at"], "reference": record["reference"]}
        count = 0
        def event(event_kind, **values):
            nonlocal count
            count += 1
            add_event(journal, dict(base, event_id=identifier+":"+str(count), kind=event_kind, **values), db)
        def progress(value, unlock=None):
            event("progress", stage=value, next_eligible_at=unlock, note="Actual manual operation: "+kind)
        def movement(account, amount, fee=0):
            event("movement", account=account, net_delta_cents=amount, fee_cents=fee)
        if kind == "buy_a":
            if finished or stage not in {"entered", "awaiting_transfer"} or type(record["entry_complete"]) is not bool:
                raise ValueError("Initial purchase recording is already finished or the completion flag is invalid.")
            if not record["regular_cents"] + record["tradable_cents"]:
                raise ValueError("Enter the actual DMarket purchase cost.")
            if record["fee_cents"] > record["regular_cents"] + record["tradable_cents"]:
                raise ValueError("Included purchase fees cannot exceed the total paid.")
            fee = record["fee_cents"]
            for account, amount in (("dmarket_tradable", record["tradable_cents"]), ("dmarket_regular", record["regular_cents"])):
                if amount:
                    if not may_use_balance(account.removeprefix("dmarket_"), pred["app_id"], "market_purchase"):
                        raise ValueError("This balance type cannot fund the purchase.")
                    portion = min(amount, fee)
                    movement(account, -amount, portion)
                    fee -= portion
            event("asset", asset_key=akey, quantity_delta=record["quantity"])
            progress("awaiting_transfer")
        elif kind == "finish_entry":
            if finished or not assets.get(akey) or stage != "awaiting_transfer":
                raise ValueError("Record the initial purchase before marking it complete.")
            progress("awaiting_transfer")
        elif kind == "transfer_a":
            if not finished or not assets.get(akey) or stage != "awaiting_transfer":
                raise ValueError("Finish recording the initial purchases before transfer.")
            progress("steam_locked", record["next_eligible_at"])
        elif kind == "sell_a":
            if not finished or stage not in {"steam_locked", "awaiting_steam_sale"}:
                raise ValueError("Record the Steam transfer before its sale.")
            if eligible and utc(record["at"]) < utc(eligible):
                raise ValueError("The sale precedes the recorded unlock. Correct the unlock time first if needed.")
            event("asset", asset_key=akey, quantity_delta=-record["quantity"])
            movement("steam_wallet", record["net_cents"], record["fee_cents"])
            progress("steam_wallet" if assets.get(akey, 0) == record["quantity"] else "awaiting_steam_sale")
        elif kind in {"buy_b", "sell_b"}:
            title = record["item_title"]
            text_field(title)
            if not (title.endswith(" Case") or title in QUALIFIED_ITEMS):
                raise ValueError("Choose a supported CS2 item title.")
            key = "730:"+title
            if kind == "buy_b":
                if stage not in {"steam_wallet", "return_item_locked", "awaiting_dmarket_sale"} or not finished:
                    raise ValueError("Finish selling the outward items before recording return purchases.")
                if any(q and held != key for held, q in assets.items()):
                    raise ValueError("This route supports one return item type at a time.")
                if record["net_cents"] <= 0:
                    raise ValueError("Enter the actual Steam purchase total.")
                if record["fee_cents"] > record["net_cents"]:
                    raise ValueError("Included purchase fees cannot exceed the total paid.")
                movement("steam_wallet", -record["net_cents"], record["fee_cents"])
                event("asset", asset_key=key, quantity_delta=record["quantity"])
                unlock = record["next_eligible_at"]
                if assets.get(key, 0):
                    unlock = max((eligible, unlock), key=utc) if eligible and unlock else None
                progress("return_item_locked", unlock)
            else:
                if stage != "awaiting_dmarket_sale" or record["account"] not in REAL_ACCOUNTS:
                    raise ValueError("Record the DMarket transfer and choose its actual credited account.")
                event("asset", asset_key=key, quantity_delta=-record["quantity"])
                movement(record["account"], record["net_cents"], record["fee_cents"])
                progress("awaiting_settlement" if sum(assets.values()) == record["quantity"] else "awaiting_dmarket_sale")
        elif kind == "transfer_b":
            if stage != "return_item_locked" or not any(assets.values()):
                raise ValueError("Record a return purchase before transfer to DMarket.")
            if eligible and utc(record["at"]) < utc(eligible):
                raise ValueError("The transfer precedes the recorded unlock. Correct the unlock time first if needed.")
            progress("awaiting_dmarket_sale")
        elif kind == "cost":
            if record["net_cents"] <= 0:
                raise ValueError("Enter the positive cost amount.")
            movement("external_cost", -record["net_cents"], 0)
        else:
            text_field(record["reason"])
            event("cancel", reason=record["reason"], confirmed=True)
        assert_cash(journal, db)
    return identifier
