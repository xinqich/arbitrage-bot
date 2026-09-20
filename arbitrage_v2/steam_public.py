"""Anonymous Steam market-page evidence. Decode JSON data; never execute page code."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
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

LIMIT = 4_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def listing_url(app_id, title):
    if (type(app_id) is not int or app_id != 730 or not isinstance(title, str)
            or not title.strip() or len(title) > 300
            or any(ord(char) < 32 for char in title)):
        raise ValueError('public Steam source requires an exact CS2 item title')
    return 'https://steamcommunity.com/market/listings/730/' + quote(title, safe='') + '?currency=1&l=english'


def allowed_url(url):
    parts = urlsplit(url)
    return (parts.scheme == 'https' and parts.netloc == 'steamcommunity.com'
            and parts.path.startswith('/market/listings/730/') and not parts.fragment)


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
            or listing[0].get('bCommodity') is not True or listing[0].get('success') is not True):
        raise ValueError('unsupported_steam_listing_identity')
    currency = config[0]['filterConfig']['currency']['eCurrency']
    context = json.loads(_decode_after(parser.scripts, r'window\.SSR\.renderContext\s*=\s*JSON\.parse\('), parse_float=str)
    queries = json.loads(context['queryData'], parse_float=str)['queries']
    selected = {}
    for kind in ('description', 'orderbook'):
        rows = [row for row in queries if row.get('queryKey') == ['market', kind, app_id, title]]
        if len(rows) != 1 or rows[0]['state'].get('status') != 'success' or rows[0]['state'].get('error') is not None:
            raise ValueError('missing_or_failed_steam_market_query')
        state = rows[0]['state']
        data = state['data']
        if kind == 'description':
            data = {key: data.get(key) for key in ('appid', 'market_hash_name', 'commodity', 'marketable')}
        elif kind == 'orderbook':
            data = {key: data[key] for key in ('eCurrency', 'amtMaxBuyOrder', 'amtMinSellOrder',
                'cBuyOrders', 'cSellOrders', 'rgCompactBuyOrders', 'rgCompactSellOrders')}
        selected[kind] = {'data': data, 'data_updated_at_ms': state['dataUpdatedAt']}
    # Chart history is optional and never determines whether a valid book exists.
    try:
        rows = [row for row in queries if row.get('queryKey') == ['market', 'pricehistory', app_id, title]]
        if len(rows) != 1 or rows[0]['state'].get('status') != 'success' or rows[0]['state'].get('error') is not None:
            raise ValueError('history_unavailable')
        state = rows[0]['state']
        data = state['data']
        selected['pricehistory'] = {'data_updated_at_ms': state['dataUpdatedAt'], 'data': {
            'ecurrency': data['ecurrency'], 'prices': [
                {key: row[key] for key in ('time', 'price_median', 'purchases')} for row in data['prices']]}}
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    return {'app_id': app_id, 'title': title, 'page_currency': currency, 'queries': selected}


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


def normalize_fields(fields, retrieved_at):
    title, app_id = fields['title'], fields['app_id']
    listing_url(app_id, title)
    queries = fields['queries']
    description = queries['description']['data']
    book = queries['orderbook']['data']
    if (description['appid'] != app_id or description['market_hash_name'] != title
            or type(description['commodity']) is not bool or description['marketable'] is not True):
        raise ValueError('Steam item identity mismatch')
    if any(type(currency) is not int or currency != 1 for currency in
           (fields['page_currency'], book['eCurrency'])):
        raise ValueError('explicit_USD_currency_required')
    observed = _timestamp(queries['orderbook']['data_updated_at_ms'], True)
    if observed > utc(retrieved_at):
        raise ValueError('future_steam_query_timestamp')
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
            'history_observed_at': history_observed, 'history_status': history_status,
            'history_semantics': 'Steam_chart_aggregate_medians_and_reported_purchase_counts',
            'history_coverage': 'unknown', 'underlying_market_cache_age': 'not_exposed'}}


def capture_public(journal, app_id, title, opener=None):
    url = listing_url(app_id, title)
    identifier = str(uuid4())
    started = stamp(datetime.now(timezone.utc))
    timeout = prepare_request(journal, dict(provider='steam_public', kind='details', app_id=app_id, title=title), identifier)
    status, error, payload = None, None, None
    retry_after_value = None
    provenance = {'requested_url': url}
    try:
        request = Request(url, headers={'User-Agent': 'arbitrage-v2-research/0.4',
                          'Accept': 'text/html', 'Accept-Language': 'en-US,en;q=0.9'})
        with (opener or build_opener(ListingRedirect())).open(request, timeout=timeout) as response:
            status = response.status
            if not allowed_url(response.url):
                raise ValueError('unexpected_steam_redirect')
            body = read_response(response,LIMIT+1)
            if len(body) > LIMIT:
                raise ValueError('steam_page_too_large')
            if body[:2] == b'\x1f\x8b':
                with gzip.GzipFile(fileobj=BytesIO(body)) as compressed:
                    body = compressed.read(LIMIT+1)
            if len(body) > LIMIT:
                raise ValueError('steam_page_too_large')
            retrieved = stamp(datetime.now(timezone.utc))
            provenance.update(final_url=response.url, decoded_body_sha256=sha256(body).hexdigest(),
                response_date=response.headers.get('Date'), response_age=response.headers.get('Age'))
        fields = page_fields(body, app_id, title)
        payload = normalize_fields(fields, retrieved)
        payload['source_fields'] = fields
    except HTTPError as exc:
        status, error = exc.code, 'http_' + str(exc.code)
        retry_after_value = retry_after(exc.headers)
    except (URLError, TimeoutError, OSError) as exc:
        error = 'network_' + type(exc).__name__
    except (ValueError, UnicodeError, KeyError, TypeError, AttributeError, OverflowError, InvalidOperation, EOFError):
        error = 'invalid_or_unsupported_steam_page'
    record = {'provider': 'steam_public', 'kind': 'details', 'app_id': app_id, 'title': title,
        'started_at': started, 'retrieved_at': stamp(datetime.now(timezone.utc)),
        'status': status, 'error': error, 'retry_after': retry_after_value, 'payload': payload, 'input_kind': 'recorded',
        'archive_format': 'steam_ssr_market_fields_v1', 'source_provenance': provenance}
    record_id = journal.append('capture', record, 'capture:' + identifier)
    return {'record_id': record_id, 'provider': 'steam_public', 'kind': 'details',
            'title': title, 'status': status, 'error': error, 'retry_after': retry_after_value}
