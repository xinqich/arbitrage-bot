"""Immutable conditional return choices funded by one open route's recorded Wallet."""
from datetime import datetime, timezone

from .evidence import stamp, utc
from .prediction import FAMILY, ITEM_FAMILY, cents, dmarket_net, return_snapshot
from .routes import _state
from .depth import book, quote, total, ENGINE


def review_returns(journal, route_id, watchlist, policy, as_of,
                   max_age_seconds=14400, remaining_cost_cents=None):
    now = utc(as_of)
    if type(max_age_seconds) is not int or max_age_seconds < 0:
        raise ValueError("invalid freshness bound")
    if remaining_cost_cents is not None:
        cents(remaining_cost_cents)
    delay = policy.get("minimum_return_delay_seconds")
    if delay is not None:
        cents(delay)
    fee = policy["dmarket_fee"]
    dmarket_net(0, fee["bps"], fee["minimum_cents"])  # Validate even with no quotes.
    items = watchlist.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 30:
        raise ValueError("watchlist must contain 1-30 exact items")
    seen = set()
    for item in items:
        if (not isinstance(item, dict) or set(item) != {"app_id", "title"}
                or type(item["app_id"]) is not int
                or not isinstance(item["title"], str) or not item["title"].strip()):
            raise ValueError("invalid watchlist item")
        identity = (item["app_id"], item["title"])
        if identity in seen:
            raise ValueError("duplicate watchlist item")
        seen.add(identity)

    # One transaction freezes the route state and observations used by this review.
    with journal.connect(True) as db:
        route, pred, events, movements, assets, stage, eligible, last, resolved = _state(journal, route_id, db)
        if resolved:
            raise ValueError("route already resolved")
        if pred["family"] not in {FAMILY,ITEM_FAMILY} or pred["app_id"] != 730:
            raise ValueError("unsupported_game_or_family")
        if now < last:
            raise ValueError("review time precedes recorded events")
        if route["mode"] == "confirmed" and now > datetime.now(timezone.utc):
            raise ValueError("confirmed review cannot be in the future")
        record_times = [event["_recorded_at"] for event in events]
        for identifier in ("route:" + route_id, route["prediction_id"]):
            record_times.append(db.execute("SELECT recorded_at FROM records WHERE id=?",
                                           (identifier,)).fetchone()[0])
        if any(utc(at) > now for at in record_times):
            raise ValueError("route state was not yet recorded at review time")

        wallet = movements["steam_wallet"]
        blockers = []
        if stage != "steam_wallet":
            blockers.append("route_not_at_steam_wallet_stage")
        if wallet <= 0:
            blockers.append("no_recorded_steam_wallet_funds")
        if any(assets.values()):
            blockers.append("items_still_held_complete_or_reconcile_current_leg")
        if eligible is not None and now < utc(eligible):
            blockers.append("recorded_eligibility_delay_not_elapsed")

        options, excluded = [], []
        if not blockers:
            for item in items:
                try:
                    if item["app_id"] != 730:
                        raise ValueError("unsupported_game_or_family")
                    snapshot = return_snapshot(journal, item["title"], as_of, max_age_seconds, db)
                    if snapshot["input_kind"] != pred["input_kind"]:
                        raise ValueError("route_and_quote_evidence_partition_mismatch")
                    purchase=quote(book(snapshot,"steam_ask"),total(book(snapshot,"dmarket_bid")),budget=wallet)
                    quantity=purchase["quantity"]
                    if quantity==0:
                        raise ValueError("no_affordable_supported_return")
                    sale=quote(book(snapshot,"dmarket_bid"),quantity,
                        lambda p:dmarket_net(p,fee["bps"],fee["minimum_cents"]))
                    receipts=sale["net_cents"]
                    if receipts<=0:
                        raise ValueError("no_proceeds_after_declared_dmarket_fee")
                    price=snapshot["steam_ask"]["price_cents"]
                    gross_unit=snapshot["dmarket_bid"]["price_cents"]
                    options.append({"item_b": item["title"], "quantity_b": quantity,
                        "steam_purchase_unit_cents": price,
                        "return_purchase_cents": purchase["gross_cents"],
                        "steam_wallet_residual_cents": wallet - purchase["gross_cents"],
                        "dmarket_sale_unit_gross_cents": gross_unit,
                        "predicted_dmarket_fee_cents": sale["fee_cents"],
                        "predicted_dmarket_receipts_cents": receipts,
                        "remaining_cost_cents": remaining_cost_cents,
                        "predicted_receipts_after_remaining_costs_cents":
                            None if remaining_cost_cents is None else receipts - remaining_cost_cents,
                        "minimum_return_delay_seconds": delay,
                        "predicted_remaining_duration_seconds": None,
                        "quote_legs":{"purchase":purchase,"sale":sale},"snapshot": snapshot})
                except (ValueError, KeyError, TypeError) as exc:
                    excluded.append({"title": item["title"], "reason": str(exc)})
        options.sort(key=lambda row: (-row["predicted_dmarket_receipts_cents"],
                                     row["steam_wallet_residual_cents"], row["item_b"]))
        report = {"route_id": route_id, "original_prediction_id": route["prediction_id"],
            "mode": route["mode"], "input_kind": pred["input_kind"], "family": pred["family"],
            "evaluated_at": stamp(now), "model_version": ENGINE,
            "route_event_ids": [event["record_id"] for event in events],
            "wallet_available_cents": wallet, "remaining_cost_cents": remaining_cost_cents,
            "status": "blocked" if blockers else "conditional_options" if options else "no_supported_options",
            "blockers": blockers, "options": options, "excluded": excluded,
            "policy": policy, "max_age_seconds": max_age_seconds,
            "qualification": "research_only_future_exit_not_reserved",
            "entry_recommendation": False, "actual_profit": None,
            "notes": ["Options share the same route Wallet; choose at most one before refreshing.",
                      "Future DMarket prices and quantities are conditional, not reserved demand.",
                      "Remaining costs are unknown unless explicitly supplied; original route costs are not charged again.",
                      "Residual Steam Wallet receives no DMarket value and still requires disposition.",
                      "Actual account/item eligibility and purchase confirmation require manual verification.",
                      "This review neither revises the entry prediction nor trains the outcome model."]}
        identifier = journal.append("return_review", report, db=db)
        return dict(report, review_id=identifier)
