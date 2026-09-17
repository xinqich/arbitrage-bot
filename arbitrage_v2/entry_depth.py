"""An identified DMarket offer can be bought by at most one paper route."""
from .depth import book


def unused_entry(journal, snapshot, db=None):
    entries = journal.records("paper_entry", db)
    used = {str(fill["level_id"]) for entry in entries
            if entry["input_kind"] == snapshot["input_kind"] and entry["app_id"] == snapshot["app_id"]
            and entry["item_a"] == snapshot["title"] for fill in entry["entry_fills"]}
    # Earlier trials did not record which individual offers they bought. Keep
    # their observed entry pool out of reuse, without inventing historical fills.
    from .routes import _state
    tracked = {entry["route_id"] for entry in entries}
    for route in journal.records("route", db):
        if route["mode"] != "paper" or route["route_id"] in tracked:
            continue
        _, pred, events, *_ = _state(journal, route["route_id"], db)
        if (pred.get("purpose", "grow") != "grow" or pred["input_kind"] != snapshot["input_kind"]
                or pred["item_a"] != snapshot["title"] or pred["app_id"] != snapshot["app_id"]):
            continue
        if not any(e["kind"] == "movement" and e["account"].startswith("dmarket_")
                   and e["net_delta_cents"] < 0 for e in events):
            continue
        pool = set()
        for identifier in pred["evidence_ids"]:
            capture = journal.get(identifier, "capture", db)
            if capture["kind"] == "offers" and capture["title"] == snapshot["title"]:
                pool.update(str(row["offerId"]) for row in (capture.get("payload") or {}).get("items", []) if "offerId" in row)
        if not pool:
            raise ValueError("legacy_paper_entry_offer_ids_unknown")
        used.update(pool)
    rows = [dict(row) for row in book(snapshot, "dmarket_ask") if str(row["level_id"]) not in used]
    if not rows:
        raise ValueError("paper_entry_offers_already_used")
    best = rows[0]["price_cents"]
    return dict(snapshot, dmarket_ask={"price_cents": best,
        "quantity": sum(r["quantity"] for r in rows if r["price_cents"] == best), "levels": rows})
