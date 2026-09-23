"""Stage 0.5: does /market/orderbook return the same book as the page's own SSR data?

For the fallback bucket (the one Steam auto-selects on the group page), both mechanisms
describe the SAME title's book, so they're directly comparable. Fetch both back to back,
three rounds spread over ~15 minutes, and check they move together rather than one lagging
or disagreeing outright.

Uses the project's own request-attempt journal logging and Steam request spacing
(request_spacing_seconds['steam_public'] = 5s in collection_settings), and checks the
production worker's pause flag before every round — unlike the throwaway probes this
replaces, which called urlopen directly with no pacing or lock discipline.

Precondition: pause the production worker before running (checked on every round).
"""
import json
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote
from urllib.request import Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arbitrage_v2.collection_lock import collection_lock
from arbitrage_v2.collection_settings import settings
from arbitrage_v2.collection_transport import prepare_request, read_response
from arbitrage_v2.journal import Journal
from arbitrage_v2.steam_public import InlineScripts, ListingRedirect, _decode_after, allowed_url
from arbitrage_v2.worker import controls

APP_ID = 730
TITLE = 'AK-47 | Slate (Factory New)'  # the observed fallback bucket: same title, both mechanisms
GROUP_URL = 'https://steamcommunity.com/market/listings/730/G1807208B083004?currency=1&l=english'
ENDPOINT_SPACING = settings({})['request_spacing_seconds']['steam_public']  # 5s
ROUNDS = 3
ROUND_INTERVAL_SECONDS = 7 * 60  # ~15 minutes total across 3 rounds
PRODUCTION_JOURNAL = ROOT / 'data' / 'research.sqlite3'


def guard():
    if not controls(Journal(PRODUCTION_JOURNAL))['paused']:
        raise SystemExit('production worker is not paused; aborting')


def fetch(journal, identifier_suffix, url, opener):
    timeout = prepare_request(journal, dict(provider='steam_public', kind='diagnostic', app_id=APP_ID, title=TITLE),
                               'attempt:equivalence:' + identifier_suffix)
    request = Request(url, headers={'User-Agent': 'arbitrage-v2-research/0.4',
                      'Accept': 'application/json, text/html', 'Accept-Language': 'en-US,en;q=0.9'})
    started = time.monotonic()
    with opener.open(request, timeout=timeout) as response:
        body = read_response(response, 8_000_000)
        headers = dict(response.headers)
        status = response.status
        final_url = response.url
    return status, final_url, headers, body, round(time.monotonic() - started, 3)


def page_orderbook(journal, round_id, opener):
    status, final_url, headers, body, elapsed = fetch(journal, f'{round_id}:page', GROUP_URL, opener)
    if not allowed_url(final_url):
        raise ValueError('unexpected_redirect_surface')
    parser = InlineScripts()
    parser.feed(body.decode('utf-8'))
    context = json.loads(_decode_after(parser.scripts, r'window\.SSR\.renderContext\s*=\s*JSON\.parse\('),
                          parse_float=str)
    queries = json.loads(context['queryData'], parse_float=str)['queries']
    rows = [q for q in queries if q.get('queryKey') == ['market', 'orderbook', APP_ID, TITLE]]
    if len(rows) != 1:
        raise ValueError(f'expected exactly one orderbook query for fallback bucket, found {len(rows)}')
    state = rows[0]['state']
    return {'mechanism': 'page_ssr', 'status': status, 'response_date': headers.get('Date'),
            'age': headers.get('Age'), 'cache_control': headers.get('Cache-Control'),
            'elapsed_seconds': elapsed, 'data_updated_at_ms': state['dataUpdatedAt'], 'data': state['data']}


def endpoint_orderbook(journal, round_id, opener):
    qp = quote(json.dumps([APP_ID, TITLE], separators=(',', ':')), safe='')
    url = f'https://steamcommunity.com/market/orderbook?q=Load&qp={qp}'
    status, final_url, headers, body, elapsed = fetch(journal, f'{round_id}:endpoint', url, opener)
    payload = json.loads(body)
    return {'mechanism': 'orderbook_endpoint', 'status': status, 'response_date': headers.get('Date'),
            'age': headers.get('Age'), 'cache_control': headers.get('Cache-Control'),
            'elapsed_seconds': elapsed, 'data': payload['data']['data']}


COMPARE_FIELDS = ('amtMaxBuyOrder', 'amtMinSellOrder', 'cBuyOrders', 'cSellOrders',
                   'rgCompactBuyOrders', 'rgCompactSellOrders')


def compare(page_row, endpoint_row):
    diffs = {field: (page_row['data'].get(field), endpoint_row['data'].get(field))
             for field in COMPARE_FIELDS if page_row['data'].get(field) != endpoint_row['data'].get(field)}
    return {'identical': not diffs, 'diffs': diffs}


def main():
    guard()
    folder = ROOT / 'data' / 'steam-probes'
    folder.mkdir(parents=True, exist_ok=True)
    report = {'title': TITLE, 'app_id': APP_ID, 'started_at': datetime.now(timezone.utc).isoformat(), 'rounds': []}
    opener = build_opener(ListingRedirect())

    with TemporaryDirectory(prefix='steam_equivalence_') as tmpdir:
        journal = Journal(Path(tmpdir) / 'equivalence.sqlite3')
        journal.initialize()
        with collection_lock(Path(tmpdir) / 'equivalence.sqlite3'):
            next_start = time.monotonic()
            for round_number in range(1, ROUNDS + 1):
                while time.monotonic() < next_start:
                    time.sleep(min(5, next_start - time.monotonic()))
                guard()
                round_id = f'r{round_number}'
                page_row = page_orderbook(journal, round_id, opener)
                time.sleep(ENDPOINT_SPACING)
                guard()
                endpoint_row = endpoint_orderbook(journal, round_id, opener)
                comparison = compare(page_row, endpoint_row)
                round_report = {'round': round_number, 'at': datetime.now(timezone.utc).isoformat(),
                                 'page': page_row, 'endpoint': endpoint_row, 'comparison': comparison}
                report['rounds'].append(round_report)
                print(json.dumps({'round': round_number, 'identical': comparison['identical'],
                                  'diffs': list(comparison['diffs'].keys()),
                                  'page_date': page_row['response_date'], 'endpoint_date': endpoint_row['response_date'],
                                  'endpoint_age_header': endpoint_row['age'], 'endpoint_cache_control': endpoint_row['cache_control']}),
                      flush=True)
                next_start = time.monotonic() + ROUND_INTERVAL_SECONDS

    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    report['all_rounds_identical'] = all(r['comparison']['identical'] for r in report['rounds'])
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output_path = folder / f'orderbook-equivalence-{ts}.json'
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'Report saved: {output_path}')
    print(f"all_rounds_identical: {report['all_rounds_identical']}")


if __name__ == '__main__':
    main()
