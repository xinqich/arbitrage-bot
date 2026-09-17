"""Move explicitly written-off holdings into a linked, separately resolved route."""
from datetime import datetime, timezone

from .evidence import stamp, utc
from .prediction import cents
from .routes import _state, _text, add_event


def open_recovery(journal, request):
    fields = {"route_id", "parent_route_id", "at", "reference", "asset_key", "quantity", "steam_wallet_cents"}
    if set(request) != fields:
        raise ValueError("invalid recovery request")
    for key in ("route_id", "parent_route_id", "reference"):
        _text(request[key])
    cents(request["quantity"])
    cents(request["steam_wallet_cents"])
    if not request["quantity"] and not request["steam_wallet_cents"]:
        raise ValueError("choose written-off holdings to recover")
    if request["quantity"]:
        _text(request["asset_key"])
    with journal.connect(True) as db:
        record_id = "recovery:"+request["route_id"]
        if db.execute("SELECT 1 FROM records WHERE id=?", (record_id,)).fetchone():
            journal.append("recovery_link", request, record_id, db)
            return {"route_id": request["route_id"], "parent_route_id": request["parent_route_id"]}
        parent, pred, events, movements, assets, stage, eligible, last, resolved = _state(journal, request["parent_route_id"], db)
        if not resolved or resolved["residual_disposition"] != "written_off":
            raise ValueError("the parent must have explicitly written off these holdings")
        if utc(request["at"]) < last or (parent["mode"] == "confirmed" and utc(request["at"]) > datetime.now(timezone.utc)):
            raise ValueError("recovery must follow closure and cannot be future-dated for real routes")
        links = [r for r in journal.records("recovery_link", db) if r["parent_route_id"] == parent["route_id"]]
        used_items = sum(r["quantity"] for r in links if r["asset_key"] == request["asset_key"])
        used_wallet = sum(r["steam_wallet_cents"] for r in links)
        if request["quantity"] > assets.get(request["asset_key"], 0)-used_items:
            raise ValueError("these written-off items are already assigned or were not held")
        if request["steam_wallet_cents"] > movements["steam_wallet"]-used_wallet:
            raise ValueError("this written-off Wallet amount is already assigned or was not held")
        recovery = dict(pred, family="linked_recovery_v1", purpose="recovery", destination="recovery_receipts",
            predicted_at=stamp(utc(request["at"])), entry_cost_cents=0, quantity_a=request["quantity"], quantity_b=0,
            other_cost_cents=None, base_net_cents=None, predicted_net_cents=None,
            predicted_dmarket_receipts_cents=0, predicted_destination_receipts_cents=0,
            steam_proceeds_cents=0, return_purchase_cents=0, base_duration_seconds=None,
            predicted_duration_seconds=None, minimum_known_delay_seconds=0,
            model_version="unestimated-linked-recovery-v1", engine_version="manual-recovery-v1",
            training_sample_count=0, evidence_ids=[], parent_route_id=parent["route_id"],
            estimate_type="no_prediction_for_recovery", costs_known=False, entry_recommendation=False)
        prediction_id = journal.append("prediction", recovery, db=db)
        journal.append("route", {"route_id": request["route_id"], "prediction_id": prediction_id,
            "mode": parent["mode"], "entered_at": stamp(utc(request["at"]))}, "route:"+request["route_id"], db)
        journal.append("recovery_link", request, record_id, db)
        base = {"route_id": request["route_id"], "at": request["at"], "reference": request["reference"]}
        if request["quantity"]:
            add_event(journal, dict(base, event_id=record_id+":items", kind="asset",
                                   asset_key=request["asset_key"], quantity_delta=request["quantity"]), db)
        if request["steam_wallet_cents"]:
            add_event(journal, dict(base, event_id=record_id+":wallet", kind="movement", account="steam_wallet",
                                   net_delta_cents=request["steam_wallet_cents"], fee_cents=0), db)
        add_event(journal, dict(base, event_id=record_id+":stage", kind="progress", stage="awaiting_transfer",
                               next_eligible_at=None, note="Linked recovery; original cost stays in the parent outcome."), db)
    return {"route_id": request["route_id"], "parent_route_id": request["parent_route_id"]}
