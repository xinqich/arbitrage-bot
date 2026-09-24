"""Whole-item quotes and conservative, shared paper capacity. All money is cents."""
from .money import exact_integer, price_cents

ENGINE = "observed-depth-v2"


def levels(rows, side, unit="cents", price_key="price", quantity_key="quantity",
           semantics="incremental"):
    if semantics not in {"incremental","cumulative"} or side not in {"bid","ask"}:
        raise ValueError("explicit depth semantics and side required")
    prices = {}
    for row in rows:
        p = price_cents(row[price_key], currency="USD", unit=unit)
        q = exact_integer(row[quantity_key])
        if p <= 0 or q <= 0:
            raise ValueError("positive price and quantity required")
        if p in prices and prices[p] != q:
            raise ValueError("conflicting duplicate price level")
        prices[p] = q  # Duplicate aggregate rows never add demand.
    result, previous = [], 0
    for p, q in sorted(prices.items(), reverse=side == "bid"):
        quantity = q - previous if semantics == "cumulative" else q
        if quantity <= 0:
            raise ValueError("invalid cumulative depth")
        result.append({"price_cents": p, "quantity": quantity, "level_id": str(p)})
        previous = q
    return result


def book(snapshot, name):
    value = snapshot[name]
    return value.get("levels", [dict(value, level_id=str(value["price_cents"]))])


def total(rows):
    return sum(r["quantity"] for r in rows)


def quote(rows, quantity, net=None, budget=None):
    if type(quantity) is not int or quantity < 0:
        raise ValueError("whole-item quantity required")
    remaining, spent, received, fills = quantity, 0, 0, []
    for row in rows:
        q = min(remaining, row["quantity"])
        if budget is not None:
            q = min(q, (budget - spent) // row["price_cents"])
        if q <= 0:
            break
        unit = net(row["price_cents"]) if net else row["price_cents"]
        spent += q * row["price_cents"]
        received += q * unit
        fills.append(dict(row, quantity=q, net_unit_cents=unit))
        remaining -= q
        if not remaining:
            break
    exact_integer(spent)
    exact_integer(received)
    return {"quantity": quantity - remaining, "gross_cents": spent,
            "net_cents": received, "fee_cents": spent - received,
            "complete": remaining == 0, "fills": fills}


def available(journal, key, rows, db=None):
    """Replay visible volume and simulated use; unchanged books do not refill.

    Aggregate price levels have no order IDs. Only observed positive additions
    replenish consumed capacity; reductions first remove unused capacity.
    A disappearance alone is never described as a completed sale.
    """
    state = {}
    for event in journal.records("paper_book", db):
        if event["key"] == key and event["engine"] == ENGINE:
            state = event["state"]
    observed = {str(r["level_id"]): r for r in rows}
    updated = {}
    for identity in state.keys() | observed.keys():
        old = state.get(identity, {"visible": 0, "available": 0})
        new = observed.get(identity, {}).get("quantity", 0)
        change = new - old["visible"]
        updated[identity] = {"visible": new, "available": max(0, old["available"] + change)}
    result = [dict(r, quantity=updated[str(r["level_id"])]["available"]) for r in rows]
    return [r for r in result if r["quantity"] > 0], updated


def consume(journal, key, state, fills, evidence_ids, at, decision_id, db, provider=None):
    for row in fills:
        level = state[str(row["level_id"])]
        level["available"] -= row["quantity"]
        if level["available"] < 0:
            raise ValueError("paper depth already used")
    # provider sits beside state, never inside it -- available() treats every key in
    # state as a level_id and would crash or silently corrupt replay on a string value.
    journal.append("paper_book", {"schema_version": 1, "engine": ENGINE,
        "key": key, "state": state, "fills": fills, "evidence_ids": evidence_ids,
        "at": at, "decision_id": decision_id, "provider": provider}, db=db)


def consumed_provider(journal, key, db=None):
    """The provider that produced the most recent depth actually consumed at this key.

    None means either nothing has been consumed yet, or the most recent consuming
    event predates provider tracking (Task 4 D1) -- both are "unknown", not "steady",
    so callers must not block on them.
    """
    provider = None
    for event in journal.records("paper_book", db):
        if event["key"] == key and event["engine"] == ENGINE and event["fills"]:
            provider = event.get("provider")
    return provider
