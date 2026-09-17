"""Canonical offline input contract. This is not a provider API adapter."""

from datetime import datetime, timedelta, timezone
import json

from .money import exact_integer, price_cents

NORMALIZER_VERSION = "canonical-v1"


def utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamps must be ISO strings with an explicit timezone")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("naive timestamps are not evidence")
    return result.astimezone(timezone.utc)


def stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("naive timestamps are not evidence")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def histogram_time(*, generated_at: str, age_seconds: int, retrieved_at: str) -> str:
    """Use only when the provider's age basis has been independently qualified."""
    if type(age_seconds) is not int:
        raise ValueError("histogram age must be integer seconds")
    generated, retrieved = utc(generated_at), utc(retrieved_at)
    observed = generated - timedelta(seconds=age_seconds)
    if generated > retrieved or observed > retrieved:
        raise ValueError("source timestamp is later than retrieval")
    return stamp(observed)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field: " + key)
        result[key] = value
    return result


def read_json(raw: bytes) -> dict:
    def invalid_constant(value):
        raise ValueError("invalid JSON constant: " + value)
    result = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                        parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError("expected a JSON object")
    return result


def _fields(value: object, required: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("expected fields: " + ", ".join(sorted(required)))
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected a nonempty string")
    return value


def normalize_depth(levels: list, *, side: str, semantics: str,
                    currency: str, unit: str) -> list[dict]:
    if (not isinstance(side, str) or side not in {"bid", "ask"}
            or not isinstance(semantics, str) or semantics not in {"incremental", "cumulative"}):
        raise ValueError("side and known quantity semantics are required")
    # Validate units even for an empty order book.
    price_cents(0, currency=currency, unit=unit)
    if not isinstance(levels, list):
        raise ValueError("levels must be an array")
    rows = []
    for row in levels:
        _fields(row, {"price", "quantity"})
        price = price_cents(row["price"], currency=currency, unit=unit)
        quantity = exact_integer(row["quantity"])
        if price == 0 or quantity == 0:
            raise ValueError("depth price and quantity must be positive")
        rows.append((price, quantity))
    rows.sort(reverse=side == "bid")
    if len({price for price, _ in rows}) != len(rows):
        raise ValueError("duplicate price levels require provider-specific disambiguation")
    normalized, previous = [], 0
    for price, quantity in rows:
        increment = quantity if semantics == "incremental" else quantity - previous
        if increment <= 0:
            raise ValueError("cumulative quantity must increase in execution order")
        normalized.append({"price_cents": price, "quantity": increment})
        previous = quantity
    return normalized


def quote_quantity(levels: list[dict], quantity: int) -> dict:
    if type(quantity) is not int or quantity <= 0:
        raise ValueError("requested quantity must be a positive integer")
    remaining, gross, worst = quantity, 0, None
    for row in levels:
        take = min(row["quantity"], remaining)
        gross += take * row["price_cents"]
        remaining -= take
        worst = row["price_cents"]
        if remaining == 0:
            break
    return {
        "requested_quantity": quantity,
        "supported_quantity": quantity - remaining,
        "supported_gross_cents": gross,
        "worst_price_cents": worst,
        "complete_quantity": remaining == 0,
        "includes_fees": False,
        "is_fill_confirmation": False,
    }


def normalize(raw: bytes) -> dict:
    data = read_json(raw)
    _fields(data, {"schema_version", "source", "input_kind", "retrieved_at",
                   "item", "book", "sales"})
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported evidence schema")
    source = _text(data["source"])
    if not isinstance(data["input_kind"], str) or data["input_kind"] not in {"synthetic", "recorded"}:
        raise ValueError("input_kind must be synthetic or recorded")
    retrieved = utc(data["retrieved_at"])
    item = _fields(data["item"], {"app_id", "market_hash_name", "attributes"})
    app_id = exact_integer(item["app_id"])
    if app_id == 0 or not isinstance(item["attributes"], dict):
        raise ValueError("item requires a positive app_id and exact attributes")
    for key, value in item["attributes"].items():
        _text(key)
        _text(value)
    item_key = canonical({"app_id": app_id, "market_hash_name": _text(item["market_hash_name"]),
                          "attributes": item["attributes"]})
    book = data["book"]
    if book is not None:
        _fields(book, {"venue", "observed_at", "side", "quantity_semantics",
                       "currency", "price_unit", "levels"})
        observed = utc(book["observed_at"])
        if observed > retrieved:
            raise ValueError("book timestamp is later than retrieval")
        book = {
            "venue": _text(book["venue"]), "observed_at": stamp(observed),
            "side": book["side"], "currency": book["currency"],
            "input_quantity_semantics": book["quantity_semantics"],
            "levels": normalize_depth(book["levels"], side=book["side"],
                                      semantics=book["quantity_semantics"],
                                      currency=book["currency"], unit=book["price_unit"]),
        }
    if not isinstance(data["sales"], list):
        raise ValueError("sales must be an array")
    sales = []
    for sale in data["sales"]:
        _fields(sale, {"event_id", "venue", "occurred_at", "quantity", "evidence_kind"})
        if sale["evidence_kind"] != "provider_reported_completed_sale":
            raise ValueError("only explicit reported sale events can enter sales history")
        occurred = utc(sale["occurred_at"])
        quantity = exact_integer(sale["quantity"])
        if occurred > retrieved or quantity == 0:
            raise ValueError("invalid sale time or quantity")
        sales.append({
            "event_id": _text(sale["event_id"]), "venue": _text(sale["venue"]),
            "occurred_at": stamp(occurred), "quantity": quantity,
            "evidence_kind": sale["evidence_kind"],
        })
    return {
        "normalizer_version": NORMALIZER_VERSION, "source": source,
        "input_kind": data["input_kind"], "retrieved_at": stamp(retrieved),
        "item_key": item_key, "book": book, "sales": sales,
    }


def inspect_book(observation: dict, *, as_of: str, max_age_seconds: int,
                 quantity: int) -> dict:
    if type(max_age_seconds) is not int or max_age_seconds < 0:
        raise ValueError("an explicit nonnegative freshness bound is required")
    now, retrieved = utc(as_of), utc(observation["retrieved_at"])
    report = {"input_kind": observation["input_kind"],
              "provider_qualification": "not_established",
              "route_readiness": "not_evaluated",
              "is_fill_confirmation": False}
    if retrieved > now:
        return report | {"data_status": "not_available_at_decision"}
    book = observation["book"]
    if book is None:
        return report | {"data_status": "missing_book"}
    age = (now - utc(book["observed_at"])).total_seconds()
    if age < 0 or age > max_age_seconds:
        return report | {"data_status": "stale_book", "age_seconds": age}
    return report | {"data_status": "fresh_book", "age_seconds": age,
                     "side": book["side"], "venue": book["venue"],
                     "depth": quote_quantity(book["levels"], quantity)}
