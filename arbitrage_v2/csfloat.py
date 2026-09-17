"""Documented read-only CSFloat listings; never treat asks as buyer demand."""
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from uuid import uuid4
import json

from .collector import NoRedirect
from .evidence import stamp, utc
from .prediction import cents, dmarket_net, calculate, return_snapshot, steam_sale_snapshot


def sale_net(gross_cents):
    """Two percent, rounded up per item to one USD cent (qualified FAQ rule)."""
    return dmarket_net(gross_cents, 200)


def payout(amount, policy):
    cents(amount)
    fields = ("fee_bps", "minimum_cents", "fixed_fee_cents", "conversion_fee_cents", "bank_fee_cents")
    missing = [key for key in fields if policy.get(key) is None]
    if missing:
        return {"status": "blocked", "missing": missing, "cash_received_cents": None}
    for key in fields:
        cents(policy[key])
    if policy["fee_bps"] >= 10000 or policy.get("rounding") != "ceil":
        raise ValueError("explicit valid payout rate and cent-rounding assumption required")
    if amount < policy["minimum_cents"]:
        return {"status": "below_minimum", "cash_received_cents": None}
    fee = (amount*policy["fee_bps"]+9999)//10000
    fee += sum(policy[key] for key in ("fixed_fee_cents", "conversion_fee_cents", "bank_fee_cents"))
    return {"status": "conditional", "cash_received_cents": max(0, amount-fee),
            "fee_cents": fee, "duration_seconds": None,
            "note": "Use actual payout receipts for final accounting; this is a declared-cost estimate."}


def normalize_listings(payload, title, at):
    rows = payload if isinstance(payload, list) else payload.get("data")
    if not isinstance(rows, list) or len(rows) > 50:
        raise ValueError("unsupported CSFloat listing response")
    listings = {}
    for row in rows:
        item = row.get("item", {})
        if row.get("state") != "listed" or row.get("type") != "buy_now" or item.get("market_hash_name") != title:
            continue
        identifier, asset_id = row.get("id"), item.get("asset_id")
        if not isinstance(identifier, str) or not identifier or not isinstance(asset_id, str) or not asset_id:
            raise ValueError("CSFloat listing and asset IDs required")
        price = row.get("price")
        cents(price)
        if not price:
            raise ValueError("zero CSFloat price")
        tradable = item.get("tradable")
        # Documented timestamp-shaped values only; missing status is not unlocked.
        eligible = type(tradable) is int and 0 <= tradable <= int(utc(at).timestamp())
        entry = {"listing_id": identifier, "asset_id": asset_id, "price_cents": price,
            "quantity": 1, "level_id": identifier, "market_hash_name": title,
            "eligibility_reported": tradable, "eligible": eligible,
            "attributes": {k: item.get(k) for k in ("float_value", "paint_seed", "paint_index", "stickers")}}
        if identifier in listings and listings[identifier] != entry:
            raise ValueError("conflicting CSFloat listing ID")
        if any(old["asset_id"] == asset_id and old["listing_id"] != identifier for old in listings.values()):
            raise ValueError("duplicate CSFloat asset in different listings")
        listings[identifier] = entry
    return {"schema_version": 1, "currency": "USD", "listings": sorted(listings.values(), key=lambda r: r["price_cents"]),
        "semantics": "active_asks_not_completed_sales_or_bids", "title": title,
        "contract_status": "documented_fields_require_live_qualification"}


def capture_listings(journal, title, keys, total_allowance, opener=None):
    if not keys.get("CSFLOAT_API_KEY"):
        raise ValueError("CSFLOAT_API_KEY is not configured locally")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("exact CS2 title required")
    attempts = journal.records("request_attempt")
    if len(attempts) >= total_allowance or sum(a["provider"] == "csfloat" for a in attempts) >= 100:
        raise ValueError("CSFloat or shared request allowance exhausted")
    identifier, at = str(uuid4()), stamp(datetime.now(timezone.utc))
    journal.append("request_attempt", {"provider": "csfloat", "kind": "listings", "app_id": 730,
        "title": title, "started_at": at}, "attempt:"+identifier)
    url = "https://csfloat.com/api/v1/listings?"+urlencode({"market_hash_name": title, "type": "buy_now",
                                                          "sort_by": "lowest_price", "limit": 50})
    status, error, payload = None, None, None
    try:
        request = Request(url, headers={"Authorization": keys["CSFLOAT_API_KEY"], "Accept": "application/json",
                                       "User-Agent": "arbitrage-v2-research/0.5"})
        with (opener or build_opener(NoRedirect())).open(request, timeout=20) as response:
            status = response.status
            body = response.read(4_000_001)
            if len(body) > 4_000_000:
                raise ValueError("response too large")
            payload = normalize_listings(json.loads(body, parse_float=str), title, at)
    except HTTPError as exc:
        status, error = exc.code, "http_"+str(exc.code)
    except (URLError, OSError, TimeoutError) as exc:
        error = "network_"+type(exc).__name__
    except (ValueError, TypeError, KeyError, AttributeError):
        error = "unsupported_csfloat_contract"
    record = {"provider": "csfloat", "kind": "listings", "app_id": 730, "title": title,
        "started_at": at, "retrieved_at": stamp(datetime.now(timezone.utc)), "status": status,
        "error": error, "payload": payload, "input_kind": "recorded", "authenticated": True}
    record_id = journal.append("capture", record, "capture:"+identifier)
    return {"record_id": record_id, "status": status, "error": error,
            "qualification": "Captured for review; entry remains blocked until the live contract is qualified."}
