"""Explicit units; no binary floats or automatic currency/unit inference."""

from decimal import Decimal, InvalidOperation

MAX_INTEGER = 2**63 - 1


def exact_integer(value: object, *, scale: int = 1) -> int:
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("use an integer or decimal string, not bool/float")
    if len(str(value)) > 128:
        raise ValueError("numeric input is too long")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid number") from exc
    if not number.is_finite() or number < 0 or number > MAX_INTEGER:
        raise ValueError("number must be finite, nonnegative, and bounded")
    if abs(number.as_tuple().exponent) > 128:
        raise ValueError("numeric exponent is outside the supported range")
    numerator, denominator = number.as_integer_ratio()
    result, remainder = divmod(numerator * scale, denominator)
    if remainder or result > MAX_INTEGER:
        raise ValueError("fractional minor units or integer overflow")
    return result


def price_cents(value: object, *, currency: str, unit: str) -> int:
    if currency != "USD":
        raise ValueError("only explicit USD is supported; currency conversion is unavailable")
    if not isinstance(unit, str) or unit not in {"usd", "cents"}:
        raise ValueError("price_unit must explicitly be usd or cents")
    return exact_integer(value, scale=100 if unit == "usd" else 1)
