"""Forward-only paper steps. Never calls a marketplace trading endpoint."""
from datetime import timedelta
from contextlib import nullcontext
from uuid import uuid4

from .depth import ENGINE, available, book, consume, quote, total
from .evidence import stamp, utc
from .prediction import (FAMILY, ITEM_FAMILY, QUALIFIED_ITEMS, cents, destination_snapshot, return_snapshot,
                         steam_sale_snapshot, steam_net, dmarket_net)
from .routes import _state, add_event


def settings(journal, route_id, db=None):
    rows = [r for r in journal.records("paper_settings", db) if r["route_id"] == route_id]
    return rows[-1] if rows else None


def configure(journal, route_id, enabled, watchlist, policy, at, db=None):
    if type(enabled) is not bool:
        raise ValueError("enabled must be true or false")
    utc(at)
    with (journal.connect(True) if db is None else nullcontext(db)) as db:
        route, pred, *rest = _state(journal, route_id, db)
        if route["mode"] != "paper" or pred["family"] not in {FAMILY, ITEM_FAMILY}:
            raise ValueError("automatic steps support qualified paper CS2 growth routes only")
        if pred.get("engine_version", ENGINE) != ENGINE:
            raise ValueError("automatic paper fills for listings are not implemented")
        if rest[-1]:
            raise ValueError("route already resolved")
        old = settings(journal, route_id, db)
        if old:
            record = {k: v for k, v in old.items() if k not in {"record_id", "_recorded_at"}}
            record.update(enabled=enabled, changed_at=at)
        else:
            titles = [i["title"] for i in watchlist["items"] if i["app_id"] == 730]
            if pred["item_a"] not in titles or not titles or len(set(titles)) != len(titles):
                raise ValueError("paper basket must include outward item and unique titles")
            if any(not (title.endswith(" Case") or title in QUALIFIED_ITEMS) for title in titles):
                raise ValueError("paper basket contains an unqualified item")
            cents(policy["minimum_return_delay_seconds"])
            if policy.get("other_cost_cents") is None:
                raise ValueError("paper cost assumption must be declared")
            cents(policy["other_cost_cents"])
            record = {"schema_version": 1, "route_id": route_id, "enabled": enabled,
                "frozen_at": at, "changed_at": at, "engine_version": ENGINE,
                "titles": titles, "policy": policy, "freshness_seconds": 14400,
                "selection_rule": "highest_destination_receipts_then_least_leftover_then_title",
                "partial_sales": True, "residual_rule": "user_decision"}
        journal.append("paper_settings", record, db=db)
    return record


def _capacity(journal, snapshot, name, db):
    key = f'{ENGINE}:{snapshot["input_kind"]}:730:{snapshot["title"]}:{name}'
    rows, state = available(journal, key, book(snapshot, name), db)
    return key, rows, state


def required_evidence(journal, route_id, at):
    """Books needed now, not all books used to predict the original route.

    This only plans collection. step() still validates quotes and holdings under
    its transaction before recording any simulated movement.
    """
    with journal.connect() as db:
        cfg = settings(journal, route_id, db)
        if not cfg or not cfg["enabled"]:
            return []
        route, pred, _, _, assets, stage, eligible, _, resolved = _state(journal, route_id, db)
        if route["mode"] != "paper" or resolved or (eligible and utc(at) < utc(eligible)):
            return []
        if stage in {"steam_locked", "awaiting_steam_sale"}:
            pairs = [(pred["item_a"], "details")]
        elif stage == "steam_wallet":
            pairs = [(title, kind) for title in cfg["titles"] for kind in ("details", "targets")]
        elif stage in {"return_item_locked", "awaiting_dmarket_sale"}:
            held = [key for key, quantity in assets.items() if quantity]
            pairs = [(held[0].removeprefix("730:"), "targets")] if len(held) == 1 else []
        else:
            pairs = []
        return [{"app_id": 730, "title": title, "kind": kind} for title, kind in pairs]


def step(journal, route_id, at):
    """One atomic transition at the actual check time; safe to retry after restart."""
    with journal.connect(True) as db:
        cfg = settings(journal, route_id, db)
        if not cfg or not cfg["enabled"]:
            return {"route_id": route_id, "status": "paused"}
        route, pred, events, movements, assets, stage, eligible, last, resolved = _state(journal, route_id, db)
        if route["mode"] != "paper":
            raise ValueError("paper engine cannot change confirmed routes")
        if utc(at) < last or utc(at) < utc(cfg["frozen_at"]):
            raise ValueError("paper checks cannot run before recorded state")
        if resolved:
            return {"route_id": route_id, "status": "resolved"}
        if eligible and utc(at) < utc(eligible):
            return {"route_id": route_id, "status": "waiting_for_unlock", "next_eligible_at": eligible}
        policy, partition = cfg["policy"], pred["input_kind"]
        sf, df = policy["steam_fee"], policy["dmarket_fee"]
        net_steam = lambda p: steam_net(p, sf["steam_bps"], sf["game_bps"],
                                       sf["minimum_steam_cents"], sf["minimum_game_cents"])
        net_dm = lambda p: dmarket_net(p, df["bps"], df["minimum_cents"])
        decision_id = str(uuid4())
        action, fills, snapshot, capacity, payloads = None, None, None, None, []

        def snapshot_for(fn, title):
            result = fn(journal, title, at, cfg["freshness_seconds"], db)
            if result["input_kind"] != partition:
                raise ValueError("route_and_quote_evidence_partition_mismatch")
            # A fresh retrieval from before eligibility cannot act as a later price.
            if eligible and utc(result["source_time"]) < utc(eligible):
                raise ValueError("need_observation_after_unlock")
            return result

        def movement(account, delta, fee=0):
            payloads.append({"kind": "movement", "account": account,
                             "net_delta_cents": delta, "fee_cents": fee})

        def asset(title, delta):
            payloads.append({"kind": "asset", "asset_key": title, "quantity_delta": delta})

        def progress(next_stage, next_at=None):
            payloads.append({"kind": "progress", "stage": next_stage,
                "next_eligible_at": next_at, "note": "Simulated from observed quotes; no real trade."})

        try:
            if stage in {"steam_locked", "awaiting_steam_sale"}:
                title = pred["item_a"]
                keys=[key for key,q in assets.items() if q and key in {title,"730:"+title}]
                asset_key=keys[0] if len(keys)==1 else None
                quantity=assets.get(asset_key,0)
                if quantity <= 0 or any(q for t, q in assets.items() if t != asset_key):
                    return {"status": "manual_decision", "reason": "outward_inventory_needs_reconciliation"}
                snapshot = snapshot_for(steam_sale_snapshot, title)
                capacity = _capacity(journal, snapshot, "steam_bid", db)
                fill = quote(capacity[1], quantity, net_steam)
                action = "steam_sale"
                if fill["quantity"]:
                    asset(asset_key, -fill["quantity"])
                    movement("steam_wallet", fill["net_cents"], fill["fee_cents"])
                    progress("steam_wallet" if fill["complete"] else "awaiting_steam_sale", eligible)
            elif stage == "steam_wallet":
                if any(assets.values()):
                    return {"status": "manual_decision", "reason": "items_still_held"}
                choices, missing = [], []
                for title in cfg["titles"]:
                    try:
                        candidate = snapshot_for(return_snapshot, title)
                        cap = _capacity(journal, candidate, "steam_ask", db)
                        buy = quote(cap[1], total(book(candidate, "dmarket_bid")),
                                    budget=movements["steam_wallet"])
                        sale = quote(book(candidate, "dmarket_bid"), buy["quantity"], net_dm)
                        if buy["quantity"] and sale["net_cents"] > 0:
                            choices.append((sale["net_cents"], movements["steam_wallet"]-buy["gross_cents"],
                                            title, candidate, cap, buy))
                    except (ValueError, KeyError, TypeError) as exc:
                        missing.append({"title": title, "reason": str(exc)})
                # Do not silently change a predeclared comparison basket when data is missing.
                if missing:
                    return {"status": "waiting_for_evidence", "missing": missing}
                if not choices:
                    return {"status": "manual_decision", "reason": "no_affordable_supported_return"}
                choices.sort(key=lambda c: (-c[0], c[1], c[2]))
                _, _, title, snapshot, capacity, fill = choices[0]
                action = "return_purchase"
                movement("steam_wallet", -fill["gross_cents"])
                asset("730:"+title, fill["quantity"])
                progress("return_item_locked", stamp(utc(at)+timedelta(seconds=policy["minimum_return_delay_seconds"])))
            elif stage in {"return_item_locked", "awaiting_dmarket_sale"}:
                held = [(t, q) for t, q in assets.items() if q]
                if len(held) != 1:
                    return {"status": "manual_decision", "reason": "return_inventory_needs_reconciliation"}
                asset_key, quantity = held[0]
                title=asset_key.removeprefix("730:")
                snapshot = snapshot_for(destination_snapshot, title)
                capacity = _capacity(journal, snapshot, "dmarket_bid", db)
                fill = quote(capacity[1], quantity, net_dm)
                action = "dmarket_sale"
                if fill["quantity"]:
                    asset(asset_key, -fill["quantity"])
                    movement("dmarket_tradable", fill["net_cents"], fill["fee_cents"])
                    progress("awaiting_settlement" if fill["complete"] else "awaiting_dmarket_sale", eligible)
            elif stage == "awaiting_settlement":
                if any(assets.values()) or movements["steam_wallet"]:
                    return {"status": "manual_decision", "reason": "leftovers_require_user_decision"}
                # Declared paper costs are recorded once; real fees always need receipts.
                extra = policy["other_cost_cents"]
                recorded_cost = -movements["external_cost"]
                if extra > recorded_cost:
                    movement("external_cost", -(extra-recorded_cost))
                payloads.append({"kind": "resolve", "resolution": "completed",
                    "residual_disposition": "none", "pending_operations": 0, "confirmed": True})
                action = "resolved"
            else:
                return {"status": "manual_decision", "reason": "stage_requires_operator"}
        except (ValueError, KeyError, TypeError) as exc:
            return {"route_id": route_id, "status": "waiting_for_evidence", "reason": str(exc)}

        if capacity:
            consume(journal, capacity[0], capacity[2], fill["fills"], snapshot["evidence_ids"], at, decision_id, db)
            fills = fill["fills"]
        if not payloads:
            return {"route_id": route_id, "status": "waiting_for_new_depth"}
        for i, payload in enumerate(payloads):
            add_event(journal, dict(payload, event_id=decision_id+":"+str(i), route_id=route_id,
                      at=at, reference="paper:"+decision_id), db)
        decision = {"schema_version": 1, "route_id": route_id, "at": at,
            "engine_version": ENGINE, "action": action, "fills": fills,
            "evidence_ids": snapshot["evidence_ids"] if snapshot else [],
            "settings_id": cfg["record_id"], "original_prediction_id": route["prediction_id"],
            "mode": "paper", "input_kind": partition}
        journal.append("paper_decision", decision, "paper:"+decision_id, db)
        return dict(decision, status="advanced")
