"""Public screening summaries. Never a capture, order book or sale-event source."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from email.utils import parsedate_to_datetime
from hashlib import sha256
from io import BytesIO
import gzip
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
from uuid import uuid4

from .collector import NoRedirect
from .collection_transport import prepare_request, read_response, retry_after
from .evidence import stamp, utc
from .identity import valid_title

URL = 'https://prices.csgotrader.app/latest/steam.json'
# Qualification is code-reviewed, not an operator switch for guessed prices.
# See docs/CSGOTRADER_SCREENING.md. USD major units and period labels are known;
# whether safe_ts includes buyer fees is not established by the public contract.
PRICE_CONTRACT = {'currency': 'USD', 'unit': 'usd', 'buyer_fees': 'unverified',
                  'calculation': 'provider_summary_unspecified', 'version': 'csgotrader-2026-09-20'}
LIMITATION = 'Steam summary fee meaning is unverified; names are used, summary prices are not scored.'


def request(config=None):
    from .collection_settings import settings
    cfg = settings(config)
    return dict(provider='csgotrader', kind='screening', app_id=730, title='CS2 Steam screening',
                max_bytes=cfg['screening_max_bytes'])


def parse(payload):
    if not isinstance(payload, dict) or not payload:
        raise ValueError('invalid_screening_file')
    items = {}
    invalid_names = 0
    for title, values in payload.items():
        if not valid_title(title):
            invalid_names += 1
            continue
        selected = dict(period=None, price_usd=None, price_cents=None)
        for period in ('last_24h', 'last_7d'):
            value = values.get(period) if isinstance(values, dict) else None
            if value is None or isinstance(value, bool):
                continue
            try:
                amount = Decimal(str(value))
                if not amount.is_finite() or not 0 < amount <= Decimal('100000000'):
                    continue
                selected = dict(period=period, price_usd=str(amount),
                                price_cents=int((amount * 100).to_integral_value(rounding=ROUND_FLOOR)))
                break
            except (InvalidOperation, ValueError, OverflowError):
                continue
        items[title] = selected
    if not items:
        raise ValueError('no_supported_screening_names')
    return dict(items=items, invalid_names=invalid_names)


def view(journal):
    """Load only the latest price file and append-only name deltas, not all files."""
    with journal.connect() as db:
        end = db.execute("SELECT coalesce(max(seq),0) FROM records WHERE category IN "
                         "('screening_check','screening_snapshot','screening_names')").fetchone()[0]
        cached = getattr(journal, '_screening_cache', None)
        if cached and cached[0] == end:
            return cached[1]
        names = set(cached[1]['names']) if cached else set()
        for (encoded,) in db.execute("SELECT payload FROM records WHERE category='screening_names' AND seq>? ORDER BY seq",
                                     (cached[0] if cached else 0,)):
            names.update(json.loads(encoded)['names'])
        row = db.execute("SELECT id,payload FROM records WHERE category='screening_snapshot' ORDER BY seq DESC LIMIT 1").fetchone()
        snapshot = dict(json.loads(row[1]), record_id=row[0]) if row else {}
        row = db.execute("SELECT payload FROM records WHERE category='screening_check' ORDER BY seq DESC LIMIT 1").fetchone()
        check = json.loads(row[0]) if row else {}
    result = dict(names=names, snapshot=snapshot, check=check)
    journal._screening_cache = (end, result)
    return result


def fresh(at, observed, seconds):
    try:
        return observed is not None and 0 <= (utc(at) - utc(observed)).total_seconds() <= seconds
    except (ValueError, TypeError, OverflowError):
        return False


def hints(journal, at, max_age=86400):
    state = view(journal)
    data = state['snapshot']
    check = state['check']
    # An unchanged body (including a new Last-Modified) keeps its original age.
    published = data.get('published_at')
    contract = data.get('price_contract', {})
    qualified = (contract.get('currency') == 'USD' and contract.get('unit') == 'usd'
                 and contract.get('buyer_fees') == 'included' and
                 PRICE_CONTRACT.get('buyer_fees') == 'included')
    usable = qualified and fresh(at, published, max_age)
    return dict(items=data.get('items', {}) if usable else {},
        status=dict(provider='csgotrader', names=len(state['names']),
            last_checked_at=check.get('checked_at'), published_at=published,
            age_seconds=(utc(at)-utc(published)).total_seconds() if published else None,
            prices_usable=usable, price_contract=contract or PRICE_CONTRACT,
            limitation=LIMITATION if not qualified else None,
            age_status='fresh' if fresh(at, published, max_age) else 'unknown' if not published else 'stale',
            error=check.get('error'), snapshot_id=data.get('record_id')))


def due(journal, at, interval=3600):
    last = view(journal)['check'].get('checked_at')
    return not fresh(at, last, interval - 0.001)


def publication(headers, at):
    try:
        value = parsedate_to_datetime(headers.get('Last-Modified', ''))
        if value.tzinfo is None or value > utc(at):
            return None
        return stamp(value)
    except (TypeError, ValueError, OverflowError):
        return None


def fetch(journal, job, keys=None, opener=None, clock=None):
    clock = clock or (lambda: stamp(datetime.now(timezone.utc)))
    state = view(journal)
    previous = state['snapshot']
    headers = {'Accept': 'application/json', 'Accept-Encoding': 'gzip',
               'User-Agent': 'ArbitrageBot/1.0 public-price-screening'}
    if previous.get('etag'):
        headers['If-None-Match'] = previous['etag']
    if previous.get('last_modified'):
        headers['If-Modified-Since'] = previous['last_modified']
    identifier = uuid4().hex
    timeout = prepare_request(journal, job, identifier)
    status, error, retry = None, None, None
    changed = False
    try:
        try:
            response = (opener or build_opener(NoRedirect())).open(Request(URL, headers=headers), timeout=timeout)
        except HTTPError as exc:
            if exc.code != 304:
                raise
            response = exc
        with response:
            status = response.code if isinstance(response, HTTPError) else response.status
            if status == 304:
                if not previous:
                    raise ValueError('304_without_saved_file')
            elif status == 200:
                limit = job.get('max_bytes', 16000000)
                data = read_response(response, limit + 1)
                if len(data) > limit:
                    raise ValueError('screening_file_too_large')
                encoding = response.headers.get('Content-Encoding', '').lower()
                if encoding == 'gzip':
                    with gzip.GzipFile(fileobj=BytesIO(data)) as decoded:
                        data = read_response(decoded, limit + 1)
                elif encoding not in ('', 'identity'):
                    raise ValueError('unsupported_screening_encoding')
                if len(data) > limit:
                    raise ValueError('screening_file_too_large')
                digest = sha256(data).hexdigest()
                if digest != previous.get('body_sha256'):
                    parsed = parse(json.loads(data, parse_float=str))
                    new_names = sorted(set(parsed['items']) - state['names'])
                    at = clock()
                    snapshot = dict(parsed, body_sha256=digest, published_at=publication(response.headers, at),
                        retrieved_at=at, price_contract=PRICE_CONTRACT, provider='csgotrader',
                        etag=response.headers.get('ETag'), last_modified=response.headers.get('Last-Modified'),
                        input_kind='recorded', purpose='screening_only')
                    with journal.connect(True) as db:
                        if new_names:
                            journal.append('screening_names', dict(names=new_names), db=db)
                        journal.append('screening_snapshot', snapshot, 'screening:' + identifier, db)
                    changed = True
            else:
                error = 'http_' + str(status)
    except HTTPError as exc:
        status, error, retry = exc.code, 'http_' + str(exc.code), retry_after(exc.headers)
    except gzip.BadGzipFile:
        error = 'invalid_screening_contract'
    except (URLError, TimeoutError, OSError) as exc:
        error = 'network_' + type(exc).__name__
    except (ValueError, TypeError, KeyError, UnicodeError, EOFError):
        error = 'invalid_screening_contract'
    record = dict(provider='csgotrader', status=status, error=error, retry_after=retry,
                  checked_at=clock(), changed=changed)
    rid = journal.append('screening_check', record, 'screening-check:' + identifier)
    return dict(record, record_id=rid)
