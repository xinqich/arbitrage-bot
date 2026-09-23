"""Anonymous Steam market-page evidence. Decode JSON data; never execute page code."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
import gzip
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from .evidence import stamp, utc
from .collection_transport import prepare_request, prepare_redirect, retry_after, read_response
from .money import MAX_INTEGER, exact_integer

LIMIT = 8_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Steam accepts only ordinary marketable skins on the grouped path. StatTrak (Quality
# "strange") and Souvenir (Quality "tournament") buckets exist in the same family but
# are out of scope and must fail explicitly rather than silently pass validation.
_ORDINARY_QUALITY = 'normal'

# New grouped-page error strings that must classify as item-scoped, not provider-scoped,
# in collection_batch.failure_scope. Any other ValueError collapses to the historical
# 'invalid_or_unsupported_steam_page' string, unchanged from before this file supported
# grouped listings.
_NAMED_PARSE_ERRORS = frozenset({
    'title_not_in_listing_group',
    'unsupported_item_quality',
    'malformed_orderbook_endpoint_response',
    'unreliable_orderbook_date_header',
    'stale_orderbook_endpoint_cache',
})


def _validate_identity(app_id, title):
    if (type(app_id) is not int or app_id != 730 or not isinstance(title, str)
            or not title.strip() or len(title) > 300
            or any(ord(char) < 32 for char in title)):
        raise ValueError('public Steam source requires an exact CS2 item title')


def listing_url(app_id, title):
    _validate_identity(app_id, title)
    return 'https://steamcommunity.com/market/listings/730/' + quote(title, safe='') + '?currency=1&l=english'


def orderbook_url(app_id, title):
    """The standalone follow-up endpoint for a grouped item's exact-variant order book.

    Built only from (app_id, title); never derived from a bucket's filter pairs, which
    Steam silently ignores as URL/query parameters on the listing page itself.
    """
    _validate_identity(app_id, title)
    qp = quote(json.dumps([app_id, title], separators=(',', ':')), safe='')
    return 'https://steamcommunity.com/market/orderbook?q=Load&qp=' + qp


def allowed_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc != 'steamcommunity.com' or parts.fragment:
        return False
    return parts.path.startswith('/market/listings/730/') or parts.path == '/market/orderbook'


class ListingRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.get_method() != 'GET' or not allowed_url(newurl):
            return None
        prepare_redirect(req)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class InlineScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.active = 'src' not in dict(attrs)

    def handle_data(self, text):
        if self.active:
            self.scripts.append(text)

    def handle_endtag(self, tag):
        if tag == 'script':
            self.active = False


def _decode_after(scripts, pattern):
    matches = [(script, match) for script in scripts for match in re.finditer(pattern, script)]
    if len(matches) != 1:
        raise ValueError('missing_or_ambiguous_steam_page_data')
    script, match = matches[0]
    return json.JSONDecoder(parse_float=str).raw_decode(script[match.end():].lstrip())[0]


def _required_query(queries, kind, app_id, title, fields):
    rows = [row for row in queries if row.get('queryKey') == ['market', kind, app_id, title]]
    if len(rows) != 1 or rows[0]['state'].get('status') != 'success' or rows[0]['state'].get('error') is not None:
        raise ValueError('missing_or_failed_steam_market_query')
    state = rows[0]['state']
    data = state['data']
    if kind == 'description':
        data = {key: data.get(key) for key in fields}
    else:
        data = {key: data[key] for key in fields}
    return {'data': data, 'data_updated_at_ms': state['dataUpdatedAt']}


def _optional_pricehistory(queries, app_id, title):
    try:
        rows = [row for row in queries if row.get('queryKey') == ['market', 'pricehistory', app_id, title]]
        if len(rows) != 1 or rows[0]['state'].get('status') != 'success' or rows[0]['state'].get('error') is not None:
            raise ValueError('history_unavailable')
        state = rows[0]['state']
        data = state['data']
        return {'data_updated_at_ms': state['dataUpdatedAt'], 'data': {
            'ecurrency': data['ecurrency'], 'prices': [
                {key: row[key] for key in ('time', 'price_median', 'purchases')} for row in data['prices']]}}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


_DESCRIPTION_FIELDS = ('appid', 'market_hash_name', 'commodity', 'marketable')
_ORDERBOOK_FIELDS = ('eCurrency', 'amtMaxBuyOrder', 'amtMinSellOrder',
                     'cBuyOrders', 'cSellOrders', 'rgCompactBuyOrders', 'rgCompactSellOrders')


def _commodity_queries(queries, app_id, title):
    selected = {
        'description': _required_query(queries, 'description', app_id, title, _DESCRIPTION_FIELDS),
        'orderbook': _required_query(queries, 'orderbook', app_id, title, _ORDERBOOK_FIELDS),
    }
    history = _optional_pricehistory(queries, app_id, title)
    if history is not None:
        selected['pricehistory'] = history
    return selected


def _group_bucket(listing_row, title):
    """Confirm the exact title is a real bucket in this group and in scope.

    The bucket's filters are used only to check identity and Quality here — never to
    build a request URL. The order-book follow-up URL comes from orderbook_url(), which
    is built from (app_id, title) alone.
    """
    buckets = listing_row.get('buckets')
    if not isinstance(buckets, list):
        raise ValueError('unsupported_steam_listing_identity')
    matches = [bucket for bucket in buckets
               if isinstance(bucket, dict) and bucket.get('bucket_id') == title]
    if len(matches) != 1:
        raise ValueError('title_not_in_listing_group')
    filters = dict(pair for pair in matches[0].get('filters', [])
                   if isinstance(pair, list) and len(pair) == 2)
    if filters.get('Quality') != _ORDINARY_QUALITY:
        raise ValueError('unsupported_item_quality')
    return matches[0]


def _group_queries(listing_row, queries, app_id, title):
    _group_bucket(listing_row, title)
    selected = {'description': _required_query(queries, 'description', app_id, title, _DESCRIPTION_FIELDS)}
    if title == listing_row.get('initialFallbackBucketID'):
        # The fallback bucket's book ships embedded in the page's own SSR data.
        selected['orderbook'] = _required_query(queries, 'orderbook', app_id, title, _ORDERBOOK_FIELDS)
    else:
        # Every other bucket's book requires the standalone follow-up request;
        # None signals capture_public to fetch it.
        selected['orderbook'] = None
    history = _optional_pricehistory(queries, app_id, title)
    if history is not None:
        selected['pricehistory'] = history
    return selected


def page_fields(body, app_id, title):
    """Extract only public market fields; exclude login/session/global page state."""
    listing_url(app_id, title)
    if len(body) > LIMIT:
        raise ValueError('steam_page_too_large')
    parser = InlineScripts()
    parser.feed(body.decode('utf-8'))
    loaders = _decode_after(parser.scripts, r'window\.SSR\.loaderData\s*=\s*')
    loaders = [json.loads(row, parse_float=str) for row in loaders]
    config = [row for row in loaders if isinstance(row, dict) and 'filterConfig' in row]
    listing = [row for row in loaders if isinstance(row, dict) and 'bCommodity' in row]
    if (len(config) != 1 or len(listing) != 1 or listing[0].get('appid') != app_id
            or listing[0].get('success') is not True):
        raise ValueError('unsupported_steam_listing_identity')
    currency = config[0]['filterConfig']['currency']['eCurrency']
    context = json.loads(_decode_after(parser.scripts, r'window\.SSR\.renderContext\s*=\s*JSON\.parse\('), parse_float=str)
    queries = json.loads(context['queryData'], parse_float=str)['queries']
    listing_row = listing[0]
    if listing_row['bCommodity'] is True:
        selected = _commodity_queries(queries, app_id, title)
    elif listing_row['bCommodity'] is False:
        selected = _group_queries(listing_row, queries, app_id, title)
    else:
        raise ValueError('unsupported_steam_listing_identity')
    return {'app_id': app_id, 'title': title, 'page_currency': currency, 'queries': selected}


def _parse_orderbook_endpoint(body):
    """Decode the standalone /market/orderbook response into the same field set the
    page's own SSR order-book query carries, so both paths validate identically."""
    try:
        data = json.loads(body, parse_float=str)['data']['data']
    except (json.JSONDecodeError, KeyError, TypeError):
        raise ValueError('malformed_orderbook_endpoint_response')
    if not isinstance(data, dict):
        raise ValueError('malformed_orderbook_endpoint_response')
    try:
        return {key: data[key] for key in _ORDERBOOK_FIELDS}
    except KeyError:
        raise ValueError('malformed_orderbook_endpoint_response')


def _integer(value):
    if type(value) is not int:
        raise ValueError('Steam order-book integers required')
    return exact_integer(value)


def _timestamp(value, milliseconds=False):
    value = _integer(value)
    return EPOCH + (timedelta(milliseconds=value) if milliseconds else timedelta(seconds=value))


def _book_rows(book, side):
    buy = side == 'buy'
    packed = book['rgCompactBuyOrders' if buy else 'rgCompactSellOrders']
    if not isinstance(packed, list) or len(packed) % 2:
        raise ValueError('invalid_compact_order_book')
    pairs = [(_integer(packed[i]), _integer(packed[i+1])) for i in range(0, len(packed), 2)]
    prices = [price for price, _ in pairs]
    if (any(price <= 0 or quantity <= 0 for price, quantity in pairs)
            or prices != sorted(set(prices), reverse=buy)):
        raise ValueError('invalid_order_book_sort_or_quantity')
    if sum(q for _, q in pairs) != _integer(book['cBuyOrders' if buy else 'cSellOrders']):
        raise ValueError('order_book_quantity_total_mismatch')
    if (prices[0] if prices else 0) != _integer(book['amtMaxBuyOrder' if buy else 'amtMinSellOrder']):
        raise ValueError('order_book_best_price_mismatch')
    return [{'price': f'{price//100}.{price%100:02d}', 'quantity': quantity} for price, quantity in pairs]


def _history_points(query, retrieved_at):
    history = query['data']
    if type(history['ecurrency']) is not int or history['ecurrency'] != 1:
        raise ValueError('explicit_USD_currency_required')
    history_observed = _timestamp(query['data_updated_at_ms'], True)
    if history_observed > utc(retrieved_at):
        raise ValueError('future_steam_query_timestamp')
    points = {}
    for row in history['prices']:
        at = _timestamp(row['time'])
        quantity = _integer(row['purchases'])
        price = str(row['price_median'])
        if len(price) > 128:
            raise ValueError('invalid_history_price')
        number = Decimal(price)
        if (not number.is_finite() or number < 0 or number > MAX_INTEGER
                or abs(number.as_tuple().exponent) > 128 or at > history_observed):
            raise ValueError('invalid_history_price_or_time')
        point = {'date': stamp(at), 'price': price, 'quantity': quantity}
        if at in points and points[at] != point:
            raise ValueError('conflicting_history_timestamp')
        points[at] = point
    return [points[at] for at in sorted(points)], stamp(history_observed)


def _endpoint_orderbook_timestamp(orderbook_query, retrieved_at):
    """D4: the follow-up book carries no dataUpdatedAt, so freshness rests on the HTTP
    Date header. An intermediary cache can rewrite Date, so a present, nonzero Age header
    or an implausible Date fails the item; an absent/unparseable Date falls back to our
    own retrieval time, recorded honestly in book_timestamp_source."""
    retrieved = utc(retrieved_at)
    if orderbook_query.get('response_age_header') not in (None, '0'):
        raise ValueError('stale_orderbook_endpoint_cache')
    header = orderbook_query.get('response_date_header')
    if header is None:
        return retrieved, 'retrieval_time_fallback'
    try:
        observed = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return retrieved, 'retrieval_time_fallback'
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    if observed > retrieved or retrieved - observed > timedelta(seconds=120):
        raise ValueError('unreliable_orderbook_date_header')
    return observed, 'http_date_header'


def normalize_fields(fields, retrieved_at):
    title, app_id = fields['title'], fields['app_id']
    listing_url(app_id, title)
    queries = fields['queries']
    description = queries['description']['data']
    orderbook_query = queries['orderbook']
    book = orderbook_query['data']
    if (description['appid'] != app_id or description['market_hash_name'] != title
            or type(description['commodity']) is not bool or description['marketable'] is not True):
        raise ValueError('Steam item identity mismatch')
    if any(type(currency) is not int or currency != 1 for currency in
           (fields['page_currency'], book['eCurrency'])):
        raise ValueError('explicit_USD_currency_required')
    if 'data_updated_at_ms' in orderbook_query:
        observed = _timestamp(orderbook_query['data_updated_at_ms'], True)
        if observed > utc(retrieved_at):
            raise ValueError('future_steam_query_timestamp')
        timestamp_source = 'ssr_query_dataUpdatedAt'
    else:
        observed, timestamp_source = _endpoint_orderbook_timestamp(orderbook_query, retrieved_at)
    points, history_observed, history_status = [], None, 'unavailable'
    if 'pricehistory' in queries:
        try:
            points, history_observed = _history_points(queries['pricehistory'], retrieved_at)
            history_status = 'available' if points else 'empty'
        except (ValueError, KeyError, TypeError, AttributeError, InvalidOperation, OverflowError):
            history_status = 'invalid'
    return {'result': {'item': {'appId': app_id, 'marketName': title},
        'meta': {'flags': {'commodity': description['commodity'], 'marketable': True}},
        'histogram': {'date': stamp(observed), 'buyOrders': _book_rows(book, 'buy'),
                      'sellOrders': _book_rows(book, 'sell')},
        'priceHistory': {'data': points}},
        'provenance': {'source': 'steam_public', 'currency': 'USD',
            'book_price_unit_original': 'cents', 'book_quantity_semantics': 'incremental',
            'book_timestamp_kind': 'steam_server_query_observed_at',
            'book_timestamp_source': timestamp_source,
            'history_observed_at': history_observed, 'history_status': history_status,
            'history_semantics': 'Steam_chart_aggregate_medians_and_reported_purchase_counts',
            'history_coverage': 'unknown', 'underlying_market_cache_age': 'not_exposed'}}


def _read_body(response):
    body = read_response(response, LIMIT+1)
    if len(body) > LIMIT:
        raise ValueError('steam_page_too_large')
    if body[:2] == b'\x1f\x8b':
        with gzip.GzipFile(fileobj=BytesIO(body)) as compressed:
            body = compressed.read(LIMIT+1)
    if len(body) > LIMIT:
        raise ValueError('steam_page_too_large')
    return body


def _fetch_group_orderbook(journal, app_id, title, opener, provenance):
    """The visible second request a grouped, non-fallback title requires."""
    url = orderbook_url(app_id, title)
    identifier = str(uuid4())
    timeout = prepare_request(journal, dict(provider='steam_public', kind='details_followup',
        app_id=app_id, title=title), identifier)
    request = Request(url, headers={'User-Agent': 'arbitrage-v2-research/0.4',
                      'Accept': 'application/json', 'Accept-Language': 'en-US,en;q=0.9'})
    with opener.open(request, timeout=timeout) as response:
        if not allowed_url(response.url):
            raise ValueError('unexpected_steam_redirect')
        body = _read_body(response)
        provenance.update(followup_url=url, followup_final_url=response.url,
            followup_response_date=response.headers.get('Date'),
            followup_response_age=response.headers.get('Age'))
        data = _parse_orderbook_endpoint(body)
        return {'data': data, 'response_date_header': response.headers.get('Date'),
                'response_age_header': response.headers.get('Age')}


def capture_public(journal, app_id, title, opener=None):
    url = listing_url(app_id, title)
    identifier = str(uuid4())
    started = stamp(datetime.now(timezone.utc))
    timeout = prepare_request(journal, dict(provider='steam_public', kind='details', app_id=app_id, title=title), identifier)
    status, error, payload = None, None, None
    retry_after_value = None
    provenance = {'requested_url': url}
    opener = opener or build_opener(ListingRedirect())
    try:
        request = Request(url, headers={'User-Agent': 'arbitrage-v2-research/0.4',
                          'Accept': 'text/html', 'Accept-Language': 'en-US,en;q=0.9'})
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            if not allowed_url(response.url):
                raise ValueError('unexpected_steam_redirect')
            body = _read_body(response)
            provenance.update(final_url=response.url, decoded_body_sha256=sha256(body).hexdigest(),
                response_date=response.headers.get('Date'), response_age=response.headers.get('Age'))
        fields = page_fields(body, app_id, title)
        if fields['queries']['orderbook'] is None:
            fields['queries']['orderbook'] = _fetch_group_orderbook(journal, app_id, title, opener, provenance)
        retrieved = stamp(datetime.now(timezone.utc))
        payload = normalize_fields(fields, retrieved)
        payload['source_fields'] = fields
    except HTTPError as exc:
        status, error = exc.code, 'http_' + str(exc.code)
        retry_after_value = retry_after(exc.headers)
    except (URLError, TimeoutError, OSError) as exc:
        error = 'network_' + type(exc).__name__
    except (ValueError, UnicodeError, KeyError, TypeError, AttributeError, OverflowError, InvalidOperation, EOFError) as exc:
        error = str(exc) if isinstance(exc, ValueError) and str(exc) in _NAMED_PARSE_ERRORS else 'invalid_or_unsupported_steam_page'
    record = {'provider': 'steam_public', 'kind': 'details', 'app_id': app_id, 'title': title,
        'started_at': started, 'retrieved_at': stamp(datetime.now(timezone.utc)),
        'status': status, 'error': error, 'retry_after': retry_after_value, 'payload': payload, 'input_kind': 'recorded',
        'archive_format': 'steam_ssr_market_fields_v1', 'source_provenance': provenance}
    record_id = journal.append('capture', record, 'capture:' + identifier)
    return {'record_id': record_id, 'provider': 'steam_public', 'kind': 'details',
            'title': title, 'status': status, 'error': error, 'retry_after': retry_after_value}
