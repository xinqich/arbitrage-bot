"""Operator-recorded DMarket funding; never route income or paper capital."""
from datetime import datetime, timezone

from .evidence import stamp, utc
from .funds import REAL_ACCOUNTS, account_money, funding_changes, funding_records
from .prediction import cents
from .routes import _state

EMPTY_MANDATE = {"starting_balances": []}


def text_field(value):
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > 200:
        raise ValueError("Use a nonempty name or receipt reference, up to 200 characters.")


def real_time(value):
    at = utc(value)
    if at > datetime.now(timezone.utc):
        raise ValueError("An actual record cannot be dated in the future.")
    return stamp(at)


def amounts(kind, regular, tradable):
    cents(regular); cents(tradable)
    if kind not in {"opening", "add", "withdraw", "unlock"}:
        raise ValueError("Unknown funding operation.")
    if kind in {"withdraw", "unlock"} and (tradable or regular <= 0):
        raise ValueError("Enter the positive regular amount only for a withdrawal or restriction release.")
    if kind == "add" and not regular + tradable:
        raise ValueError("Enter the amount actually added.")


def reference_available(journal, reference, db, ignore_funding=None):
    if any(r["reference"]==reference for r in journal.records("steam_wallet_record",db)):
        raise ValueError("This reference already belongs to a Steam wallet record.")
    for row in journal.records("real_funding", db):
        if row["reference"] == reference and row["funding_id"] != ignore_funding:
            raise ValueError("This receipt reference already belongs to outside funding.")
    for row in journal.records("route_event", db):
        if row["reference"] == reference and row["kind"] in {"movement", "asset"}:
            route = journal.get("route:"+row["route_id"], "route", db)
            if route["mode"] == "confirmed":
                raise ValueError("This receipt reference is already recorded in a real route.")


def assert_cash(journal, db):
    """Cash conservation and commitments only. No valuation, loss limit or P&L."""
    order = {r[0]: r[1] for r in db.execute("SELECT id,seq FROM records")}
    timeline = [(utc(r["at"]), order[r["record_id"]], funding_changes(r)) for r in funding_records(journal, db)]
    for route in journal.records("route", db):
        if route["mode"] != "confirmed":
            continue
        for event in _state(journal, route["route_id"], db)[2]:
            if event["kind"] == "movement" and event["account"] in REAL_ACCOUNTS:
                timeline.append((utc(event["at"]), order["event:"+event["event_id"]], {event["account"]: event["net_delta_cents"]}))
    cash = dict.fromkeys(REAL_ACCOUNTS, 0)
    for _, _, changes in sorted(timeline):
        for account, amount in changes.items():
            cash[account] += amount
        if any(amount < 0 for amount in cash.values()):
            raise ValueError("These records would spend DMarket money before it was recorded as available. Check funding and receipts.")
    funds = account_money(journal, EMPTY_MANDATE, mode="confirmed", db=db)
    if any(n < 0 for n in funds["available_accounts"].values()) or sum(funds["accounts"].values()) < funds["reserved_cents"]:
        raise ValueError("This money is already set aside for another route. Check the amount or cancel the unused route plan.")


def record_funding(journal, request):
    if set(request) != {"funding_id", "kind", "at", "reference", "regular_cents", "tradable_cents"}:
        raise ValueError("Invalid funding fields.")
    for key in ("funding_id", "reference"):
        text_field(request[key])
    amounts(request["kind"], request["regular_cents"], request["tradable_cents"])
    record = dict(request, at=real_time(request["at"]), schema_version=1, mode="confirmed", venue="dmarket")
    identifier = "real-funding:"+request["funding_id"]
    with journal.connect(True) as db:
        if db.execute("SELECT 1 FROM records WHERE id=?", (identifier,)).fetchone():
            journal.append("real_funding", record, identifier, db)
            return identifier
        rows = funding_records(journal, db)
        if request["kind"] == "opening":
            if rows or any(r["mode"] == "confirmed" for r in journal.records("route", db)):
                raise ValueError("Opening funds can be recorded once, before real routes. Correct the opening record if it was mistaken.")
        elif not rows:
            raise ValueError("Record the opening DMarket funds first.")
        if rows and utc(record["at"]) < max(utc(r["at"]) for r in rows):
            raise ValueError("Funding records must follow earlier funding records.")
        reference_available(journal, record["reference"], db)
        journal.append("real_funding", record, identifier, db)
        assert_cash(journal, db)
    return identifier


def correct_funding(journal, request):
    if set(request) != {"correction_id", "funding_id", "at", "reason", "regular_cents", "tradable_cents"}:
        raise ValueError("Invalid funding correction fields.")
    for key in ("correction_id", "funding_id", "reason"):
        text_field(request[key])
    record = dict(request, at=real_time(request["at"]), schema_version=1)
    identifier = "funding-correction:"+request["correction_id"]
    with journal.connect(True) as db:
        if db.execute("SELECT 1 FROM records WHERE id=?", (identifier,)).fetchone():
            journal.append("funding_correction", record, identifier, db)
            return identifier
        target = journal.get("real-funding:"+request["funding_id"], "real_funding", db)
        amounts(target["kind"], request["regular_cents"], request["tradable_cents"])
        latest = max([utc(target["at"])] + [utc(c["at"]) for c in journal.records("funding_correction", db) if c["funding_id"] == request["funding_id"]])
        if utc(record["at"]) < latest:
            raise ValueError("Correction must follow the funding record and its earlier corrections.")
        journal.append("funding_correction", record, identifier, db)
        assert_cash(journal, db)
    return identifier


def status(journal, mandate, db=None):
    rows = funding_records(journal, db)
    funds = account_money(journal, mandate, mode="confirmed", db=db)
    return dict(funds, configured=bool(rows), records=rows,
                note="Recorded cash and commitments only. Outside funding is not route profit; paper money is separate.")
