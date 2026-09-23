"""Review and enter a paper growth route atomically. No network or real orders."""
from datetime import timedelta

from .depth import ENGINE
from .entry_depth import unused_entry
from .evidence import stamp, utc
from .funds import account_money
from .paper import configure, settings
from .prediction import FAMILY, ITEM_FAMILY, calculate, market_snapshot, may_use_balance
from .routes import add_event, open_route

FRESHNESS_SECONDS = 14400


def _review(journal, prediction_id, mandate, policy, at, db):
    pred = journal.get(prediction_id, "prediction", db)
    blockers = []
    funds = account_money(journal, mandate, db=db)
    titles = list(dict.fromkeys([pred["item_a"], pred["item_b"]]))
    outward_delay = policy["minimum_known_delay_seconds"] - policy["minimum_return_delay_seconds"]
    if (pred["family"] not in {FAMILY, ITEM_FAMILY} or pred.get("purpose") != "grow"
            or pred.get("app_id") != 730):
        blockers.append("This route is outside the supported CS2 paper growth families.")
    if pred.get("engine_version") != ENGINE:
        blockers.append("This assumed-price opportunity is visible for review, but automatic paper fills for listings are not implemented (including midpoint prices). Asking prices alone cannot confirm a sale.")
    if pred["input_kind"] != "recorded":
        blockers.append("New page trials require recorded market evidence, not synthetic fixtures.")
    if pred.get("learning_mode", "paper") != "paper" or pred.get("search_mode", "paper") != "paper":
        blockers.append("This estimate uses confirmed-trade learning. Run a paper search.")
    if pred.get("policy") != policy or outward_delay < 0:
        blockers.append("The assumptions have changed. Search again before entering.")
    if (not pred.get("costs_known") or pred.get("predicted_net_cents") is None
            or pred["predicted_net_cents"] <= 0 or (pred.get("base_net_cents") or 0) <= 0):
        blockers.append("The estimate does not support a positive net return after declared costs.")
    recorded = db.execute("SELECT recorded_at FROM records WHERE id=?", (prediction_id,)).fetchone()[0]
    if not 0 <= (utc(at)-utc(pred["predicted_at"])).total_seconds() <= FRESHNESS_SECONDS or utc(recorded) > utc(at):
        blockers.append("This estimate is old or future-dated. Search recorded prices again.")
    if pred["entry_cost_cents"] > funds["available_cents"]:
        blockers.append("There are not enough unallocated paper DMarket funds.")
    if any(amount < 0 for amount in funds["accounts"].values()):
        blockers.append("A paper account balance needs reconciliation before another entry.")
    funding, remaining = [], pred["entry_cost_cents"]
    # Spend restricted funds only on their supported CS2 market-purchase action.
    for kind in ("tradable", "regular"):
        amount = min(remaining, max(0, funds["accounts"].get("dmarket_"+kind, 0)))
        if amount and may_use_balance(kind, pred["app_id"], "market_purchase"):
            funding.append({"account": "dmarket_"+kind, "amount_cents": amount})
            remaining -= amount
    if remaining:
        blockers.append("The available account types cannot fund this purchase.")
    if not blockers:
        try:
            if pred.get("discovery_version"):
                from .discovery import route_inputs
                a, b = route_inputs(journal, pred, at, FRESHNESS_SECONDS, db)
            else:
                snapshots = {title: market_snapshot(journal, title, at, FRESHNESS_SECONDS, db) for title in titles}
                a, b = snapshots[pred["item_a"]], snapshots[pred["item_b"]]
            current = calculate(unused_entry(journal, a, db), b, pred["quantity_a"], policy, return_quantity_limit=pred.get("return_quantity_limit"))
            if current["input_kind"] != "recorded":
                blockers.append("Current evidence is not recorded market data.")
            elif current["evidence_ids"] != pred["evidence_ids"] or current["quote_legs"] != pred["quote_legs"]:
                blockers.append("Prices, evidence or unused offers changed. Search again and review the new estimate.")
        except (ValueError, KeyError, TypeError) as exc:
            blockers.append("Current evidence cannot support entry: " + str(exc))
    return {"prediction_id": prediction_id, "prediction": pred, "ready": not blockers,
        "blockers": blockers, "available_cents": funds["available_cents"], "funding": funding,
        "return_basket": titles, "outward_delay_seconds": outward_delay,
        "return_delay_seconds": policy["minimum_return_delay_seconds"],
        "note": "Paper only. Future prices and sale times are unknown. The selected pair is frozen as the return comparison basket; leftovers need your decision."}


def preview(journal, prediction_id, mandate, policy, at):
    with journal.connect() as db:
        db.execute("BEGIN")
        return _review(journal, prediction_id, mandate, policy, at, db)


def enter(journal, request, mandate, policy, at):
    if set(request) != {"route_id", "prediction_id"}:
        raise ValueError("Choose a route name and prediction for a paper entry.")
    route_id = request["route_id"]
    if (not isinstance(route_id, str) or not route_id.strip() or route_id != route_id.strip()
            or len(route_id) > 100):
        raise ValueError("Use a route name of 1-100 characters without outside spaces.")
    record_id = "paper-entry:" + route_id
    with journal.connect(True) as db:
        existing = db.execute("SELECT 1 FROM records WHERE id=?", (record_id,)).fetchone()
        if existing:
            entry = journal.get(record_id, "paper_entry", db)
            if entry["request"] != request:
                raise ValueError("This route name already belongs to a different paper entry.")
            return {"route_id": route_id, "status": "already_opened", "mode": "paper"}
        review = _review(journal, request["prediction_id"], mandate, policy, at, db)
        if not review["ready"]:
            raise ValueError(" ".join(review["blockers"]))
        pred = review["prediction"]
        open_route(journal, route_id, request["prediction_id"], "paper", at, db)
        base = {"route_id": route_id, "at": stamp(utc(at)), "reference": record_id}
        for index, debit in enumerate(review["funding"]):
            add_event(journal, dict(base, event_id=record_id+":money:"+str(index), kind="movement",
                account=debit["account"], net_delta_cents=-debit["amount_cents"], fee_cents=0), db)
        add_event(journal, dict(base, event_id=record_id+":items", kind="asset",
            asset_key="730:"+pred["item_a"], quantity_delta=pred["quantity_a"]), db)
        eligible = stamp(utc(at)+timedelta(seconds=review["outward_delay_seconds"]))
        add_event(journal, dict(base, event_id=record_id+":stage", kind="progress", stage="steam_locked",
            next_eligible_at=eligible, note="Simulated DMarket purchase. The outward wait is an assumption; no real trade."), db)
        configure(journal, route_id, True,
                  {"items": [{"app_id": 730, "title": t} for t in review["return_basket"]]}, policy, at, db)
        cfg = settings(journal, route_id, db)
        journal.append("paper_entry", {"schema_version": 1, "request": request, "route_id": route_id,
            "prediction_id": request["prediction_id"], "at": base["at"], "mode": "paper", "input_kind": "recorded",
            "engine_version": ENGINE, "app_id": 730, "item_a": pred["item_a"],
            "entry_fills": pred["quote_legs"]["entry"]["fills"], "funding": review["funding"],
            "evidence_ids": pred["evidence_ids"], "settings_id": cfg["record_id"],
            "return_basket": review["return_basket"], "outward_delay_seconds": review["outward_delay_seconds"]}, record_id, db)
    return {"route_id": route_id, "status": "opened", "mode": "paper", "next_eligible_at": eligible}
