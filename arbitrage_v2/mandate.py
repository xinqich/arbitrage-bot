"""Approved continuous-learning settings; no automatic risk or calendar cutoff."""
from pathlib import Path
from .evidence import read_json
from .money import MAX_INTEGER

def load_mandate(path: Path) -> dict:
    data = read_json(path.read_bytes())
    if data.get("schema_version")==4:
        if set(data)!={"schema_version","starting_balances","objective","evaluation","collection",
                       "risk_management","eligible_balance_type","steam_steps_user_operated","execution"}:
            raise ValueError("expected mandate schema 4 fields")
        balances=data.get("starting_balances")
        if not isinstance(balances,list) or not balances:
            raise ValueError("starting balances required")
        seen=set()
        for row in balances:
            if set(row)!={"venue","account","currency","mode","amount_cents","purpose"}:
                raise ValueError("invalid starting balance contract")
            if row["venue"] not in {"dmarket","csfloat"} or row["currency"]!="USD" or row["mode"]!="paper":
                raise ValueError("starting balances are separate USD paper experiments")
            if (row["venue"],row["account"],row["purpose"]) not in {
                ("dmarket","regular","grow"),("dmarket","tradable","grow"),("csfloat","deposited","start")}:
                raise ValueError("incompatible balance purpose or restrictions")
            key=(row["venue"],row["account"],row["mode"])
            if key in seen:
                raise ValueError("duplicate balance")
            seen.add(key)
            if type(row["amount_cents"]) is not int or not 0<row["amount_cents"]<=MAX_INTEGER:
                raise ValueError("starting capital must be positive integer cents")
        legacy=dict(data,schema_version=3,starting_dmarket_cents=1)
        del legacy["starting_balances"]
        _validate_legacy(legacy)
        return data
    _validate_legacy(data)
    return data


def _validate_legacy(data):
    required = {"schema_version", "starting_dmarket_cents", "objective", "evaluation",
                "collection", "risk_management", "eligible_balance_type",
                "steam_steps_user_operated", "execution"}
    if set(data) != required or type(data["schema_version"]) is not int or data["schema_version"] != 3:
        raise ValueError("expected mandate schema 3; old time/risk settings must be removed")
    value = data["starting_dmarket_cents"]
    if type(value) is not int or not 0 < value <= MAX_INTEGER:
        raise ValueError("starting capital must be positive integer cents")
    expected = {"objective": "reliability_first_net_growth", "evaluation": "resolved_routes_only",
                "collection": "continuous", "risk_management": "user_managed",
                "eligible_balance_type": "including_restricted_tradable",
                "steam_steps_user_operated": True, "execution": "manual"}
    if any(type(data[k]) is not type(v) or data[k] != v for k, v in expected.items()):
        raise ValueError("settings do not match the approved manual, completion-based design")
    return data

def mandate_summary(data: dict) -> dict:
    return dict(data, fixed_profit_target=False, fixed_evaluation_window=False,
                automatic_risk_accounting=False, automatic_trading_available=False)



def convert_legacy(data):
    """Explicit conversion; no invented CSFloat or confirmed funds."""
    _validate_legacy(data)
    result=dict(data,schema_version=4)
    amount=result.pop("starting_dmarket_cents")
    result["starting_balances"]=[{"venue":"dmarket","account":"regular","currency":"USD",
        "mode":"paper","amount_cents":amount,"purpose":"grow"}]
    return result
