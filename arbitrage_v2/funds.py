"""Unallocated account money, never profit/loss on an unfinished route."""
from .routes import _state

REAL_ACCOUNTS = ("dmarket_regular", "dmarket_tradable")


def funding_records(journal, db=None):
    rows = journal.records("real_funding", db)
    for correction in journal.records("funding_correction", db):
        for row in rows:
            if row["funding_id"] == correction["funding_id"]:
                row.update(regular_cents=correction["regular_cents"], tradable_cents=correction["tradable_cents"])
    return rows


def funding_changes(row):
    regular, tradable = row["regular_cents"], row["tradable_cents"]
    if row["kind"] == "withdraw":
        regular = -regular
    elif row["kind"] == "unlock":
        tradable = -regular
    return {"dmarket_regular": regular, "dmarket_tradable": tradable}


def account_money(journal, mandate, venue="dmarket", mode="paper", db=None):
    balances = {}
    for row in mandate["starting_balances"]:
        if row["venue"] == venue and row["mode"] == mode:
            account = venue + "_" + row["account"]
            balances[account] = balances.get(account, 0) + row["amount_cents"]
    reserved = 0
    reserved_accounts = {}
    entries = {r["route_id"]: r for r in journal.records("confirmed_entry", db)} if mode == "confirmed" else {}
    steps = journal.records("real_step", db) if mode == "confirmed" else []
    if mode == "confirmed" and venue == "dmarket":
        for record in funding_records(journal, db):
            for account, amount in funding_changes(record).items():
                balances[account] = balances.get(account, 0) + amount
    for route in journal.records("route", db):
        if route["mode"] != mode:
            continue
        _, pred, events, movements, _, stage, _, _, resolved = _state(journal, route["route_id"], db)
        for account, amount in movements.items():
            if account.startswith(venue + "_"):
                balances[account] = balances.get(account, 0) + amount
        source = "csfloat" if pred.get("purpose", "grow") == "start" else "dmarket"
        if source == venue and not resolved and route["route_id"] in entries:
            entry = entries[route["route_id"]]
            finished = any(r["route_id"] == route["route_id"] and
                           (r["kind"] == "finish_entry" or r.get("entry_complete") is True) for r in steps)
            if not finished:
                for allocation in entry["funding"]:
                    account = allocation["account"]
                    debits = -sum(e["net_delta_cents"] for e in events if e["kind"] == "movement"
                                  and e["account"] == account and e["net_delta_cents"] < 0)
                    amount = max(0, allocation["amount_cents"] - debits)
                    reserved_accounts[account] = reserved_accounts.get(account, 0) + amount
                    reserved += amount
        elif source == venue and not resolved and stage == "entered":
            debits = -sum(e["net_delta_cents"] for e in events if e["kind"] == "movement"
                          and e["account"].startswith(venue + "_") and e["net_delta_cents"] < 0)
            reserved += max(0, pred["entry_cost_cents"] - debits)
    return {"accounts": balances, "reserved_cents": reserved, "reserved_accounts": reserved_accounts,
            "available_accounts": {a: n-reserved_accounts.get(a, 0) for a, n in balances.items()},
            "available_cents": max(0, sum(balances.values()) - reserved)}


def available_capital(journal, mandate, venue="dmarket", mode="paper", db=None):
    return account_money(journal, mandate, venue, mode, db)["available_cents"]
