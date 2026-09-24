"""Task 4 live qualification run (bounded, anonymous, read-only Steam diagnostic).

Sends real GET requests to steamcommunity.com to qualify the grouped-page parser,
run-local page reuse, and the D4 timestamp logic against real Steam market pages.
Never writes to data/research.sqlite3; everything is recorded in a temporary
journal. Dry run by default -- pass --run to actually send requests.

See codebase-analysis-docs/TASK4_LIVE_QUALIFICATION_PROMPT.md for the full plan,
preconditions and definition of done this script implements.
"""
import argparse
import gzip
import json
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import arbitrage_v2.steam_public as steam_public
from arbitrage_v2.collection_batch import CollectionBatch, failure_scope, request_for
from arbitrage_v2.collection_lock import collection_lock
from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.journal import Journal
from arbitrage_v2.worker import controls, latest

# Phase 1: the qualified reference family and its fallback bucket (data/steam-probes/
# grouped-discovery-*.json). Requesting the fallback bucket first seeds the run-local
# cache with the whole family's bucket list, at zero follow-up cost for this title.
PHASE1_SEED = 'AK-47 | Slate (Factory New)'

# Phase 2: any ordinary, certainly-marketable grouped skin family, to prove the cache
# keys per family rather than globally. Redline is one of the most continuously traded
# CS2 skins and is not expected to require any special-casing.
PHASE2_SEED = 'AK-47 | Redline (Field-Tested)'

# Phase 3: the D2 negative (an agent, expected to fail its own page, item-scoped) and
# the commodity control (proves the pre-existing commodity path is untouched).
NEGATIVE_AGENT = 'Sir Bloody Miami Darryl'
COMMODITY_CONTROL = 'Fracture Case'

APP_ID = 730


class ProbeStop(Exception):
    """A named, clean stop: a go/no-go gate, the HTTP budget, or a lost precondition."""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def busy_precondition(web_status):
    """Gap C: an unreachable /api/status is not silent uncertainty about contention --
    it means the desk web server isn't running at all, so nothing it owns can be busy.
    That is an accepted substitute for the endpoint check, not a failure to verify, and
    the report should say so explicitly rather than leaving a bare `false`. Absence of
    contention in that case rests on background_collection_paused (checked separately
    in main(), before this function is ever reached)."""
    if web_status is not None:
        return {'web_status_reachable': True, 'web_status_busy': web_status.get('busy'),
                'busy_verified_via': 'web_status_endpoint'}
    return {'web_status_reachable': False, 'web_status_busy': None,
            'busy_verified_via': 'accepted_substitute: worker_paused_and_desk_unreachable'}


def deep_sizeof(obj, seen=None):
    """Recursive sys.getsizeof walk (not pickle.dumps, which understates resident
    memory for this structure -- see GroupPageCache's docstring)."""
    seen = seen if seen is not None else set()
    obj_id = id(obj)
    if obj_id in seen:
        return 0
    seen.add(obj_id)
    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        for key, value in obj.items():
            size += deep_sizeof(key, seen) + deep_sizeof(value, seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            size += deep_sizeof(item, seen)
    elif hasattr(obj, '__dict__'):
        # A plain instance's own sys.getsizeof only covers its shell; the actual
        # attribute values live in its __dict__, which is a separate object.
        size += deep_sizeof(vars(obj), seen)
    return size


def buckets_by_quality(entry, quality):
    titles = []
    for bucket in entry.page['listing_row'].get('buckets', []):
        if not isinstance(bucket, dict):
            continue
        filters = dict(pair for pair in bucket.get('filters', []) if isinstance(pair, list) and len(pair) == 2)
        if filters.get('Quality') == quality and isinstance(bucket.get('bucket_id'), str):
            titles.append(bucket['bucket_id'])
    return sorted(titles)


def plan_description(http_budget):
    return {
        'phase1': {'seed': PHASE1_SEED, 'selects': 'seed (fallback) + >=3 other normal wears + 1 strange + 1 tournament if present'},
        'phase2': {'seed': PHASE2_SEED, 'selects': 'seed + >=2 other normal wears'},
        'phase3': {'negative_agent': NEGATIVE_AGENT, 'commodity_control': COMMODITY_CONTROL},
        'http_budget': http_budget,
        'request_spacing_seconds_steam_public': 5,
        'note': 'Exact bucket titles for phases 1 and 2 are derived from the live page at run time, not hardcoded.',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Actually send the bounded live run; otherwise print the plan and send nothing.')
    parser.add_argument('--http-budget', type=int, default=30, help='Hard cap on real HTTP requests, including redirects (default 30, max 60).')
    args = parser.parse_args(argv)
    if not 1 <= args.http_budget <= 60:
        parser.error('--http-budget must be between 1 and 60.')

    plan = plan_description(args.http_budget)
    if not args.run:
        print(json.dumps({'plan': plan, 'network_requests': 0}, indent=2))
        return

    config = json.loads((ROOT / 'config/local.json').read_text(encoding='utf-8-sig'))
    if config['host'] != '127.0.0.1':
        raise ValueError('Only the local desk is supported.')

    prod_journal = Journal(ROOT / 'data/research.sqlite3')
    if not controls(prod_journal)['paused']:
        raise ValueError('background_collection_not_paused')
    health = latest(prod_journal, 'worker_health') or {}
    for key, value in health.get('source_states', {}).items():
        if value.get('provider') == 'steam_public' and value.get('scope', 'provider') != 'item':
            raise ValueError('existing_steam_source_block: ' + key)
    web_status = None
    try:
        with urlopen('http://127.0.0.1:{}/api/status'.format(config['port']), timeout=5) as response:
            web_status = json.load(response)
    except (URLError, OSError):
        pass  # The desk web server is not running; nothing it owns can be busy.
    if web_status is not None and web_status.get('busy'):
        raise ValueError('background_request_still_running')

    baseline = verify_production_journal_untouched(prod_journal)
    run_qualification(args.http_budget, plan, prod_journal, web_status, baseline)


def run_qualification(http_budget, plan, prod_journal, web_status, baseline):
    started_at = now_iso()
    tmp_dir = Path(tempfile.mkdtemp(prefix='steam-live-qual-'))
    report = {
        'schema_version': 1, 'run_id': str(uuid4()), 'started_at': started_at,
        'plan': plan, 'http_budget': http_budget, 'http_request_count': 0,
        'preconditions': dict({'background_collection_paused': True}, **busy_precondition(web_status)),
        'pages': [], 'titles': [], 'go_no_go': [], 'status': 'running',
    }

    def guard():
        # begin_request's own wait loop polls this hook up to 4x/second while it
        # waits out request spacing (see CollectionBatch.begin_request), not once
        # per real HTTP request -- so the budget must be measured from something
        # that only changes when a request is actually dispatched: the temp
        # journal's request_attempt rows, written by prepare_request right after
        # begin_request returns. A self-incrementing counter here would instead
        # count polling iterations and stop the run almost immediately.
        with temp_journal.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM records WHERE category='request_attempt'").fetchone()[0]
        report['http_request_count'] = count
        if count >= http_budget:
            raise ProbeStop('http_budget')
        if not controls(prod_journal)['paused']:
            raise ProbeStop('background_collection_not_paused')

    page_metrics = []
    byte_log = []
    original_trim = steam_public._trimmed_group_queries
    original_read_body = steam_public._read_body

    def instrumented_trim(app_id, page):
        result = original_trim(app_id, page)
        listing_row = page['listing_row']
        buckets = [b for b in listing_row.get('buckets', []) if isinstance(b, dict)]
        marketable_by_bucket = {}
        for row in page['queries']:
            key = row.get('queryKey')
            if isinstance(key, list) and len(key) == 4 and key[0] == 'market' and key[1] == 'description' and key[2] == app_id:
                marketable_by_bucket[key[3]] = row.get('state', {}).get('data', {}).get('marketable')
        page_metrics.append({
            'app_id': app_id, 'bucket_count': len(buckets),
            'initial_fallback_bucket_id': listing_row.get('initialFallbackBucketID'),
            'marketable_by_bucket': marketable_by_bucket,
            'total_queries_on_page': len(page['queries']), 'queries_surviving_trim': len(result),
        })
        return result

    def instrumented_read_body(response):
        body = steam_public.read_response(response, steam_public.LIMIT + 1)
        encoded_bytes = len(body)
        if encoded_bytes > steam_public.LIMIT:
            raise ValueError('steam_page_too_large')
        decoded = body
        if body[:2] == b'\x1f\x8b':
            with gzip.GzipFile(fileobj=BytesIO(body)) as compressed:
                decoded = compressed.read(steam_public.LIMIT + 1)
        if len(decoded) > steam_public.LIMIT:
            raise ValueError('steam_page_too_large')
        byte_log.append({'encoded_bytes': encoded_bytes, 'decoded_bytes': len(decoded),
                         'content_encoding': response.headers.get('Content-Encoding'),
                         'limit_bytes': steam_public.LIMIT,
                         'margin_bytes': steam_public.LIMIT - max(encoded_bytes, len(decoded))})
        return decoded

    temp_journal = Journal(tmp_dir / 'probe-journal.sqlite3')
    temp_journal.initialize()
    watchlist = {'steam_source': 'steam_public', 'item_sources': {}}
    title_phase = {}

    try:
        steam_public._trimmed_group_queries = instrumented_trim
        steam_public._read_body = instrumented_read_body
        with collection_lock(tmp_dir / 'probe.sqlite3'):
            batch = CollectionBatch(temp_journal, watchlist, {}, {}, {}, now_iso, lambda: False)
            batch.before_request = guard
            # Wait one full spacing interval before the first request rather than
            # opening with a burst; a fresh temp journal has no prior request_attempt
            # rows, so the first request would otherwise fire immediately.
            time.sleep(batch.config['request_spacing_seconds']['steam_public'])

            def run_title(title, phase):
                title_phase[title] = phase
                batch.ensure([request_for(watchlist, APP_ID, title, 'details')])
                latest_result = next((r for r in reversed(batch.results) if r['title'] == title), None)
                if latest_result is not None and latest_result.get('status') in (401, 403, 429):
                    raise ProbeStop('restriction_' + str(latest_result['status']))

            run_title(PHASE1_SEED, 'phase1_seed_fallback')
            entry1 = batch.group_page_cache.get(APP_ID, PHASE1_SEED)
            if entry1 is None:
                raise ProbeStop('phase1_seed_did_not_populate_cache')
            phase1_normal = [t for t in buckets_by_quality(entry1, 'normal') if t != PHASE1_SEED][:3]
            phase1_strange = buckets_by_quality(entry1, 'strange')[:1]
            phase1_tournament = buckets_by_quality(entry1, 'tournament')[:1]
            for title in phase1_normal:
                run_title(title, 'phase1_normal')
            for title in phase1_strange:
                run_title(title, 'phase1_strange')
            for title in phase1_tournament:
                run_title(title, 'phase1_tournament')

            run_title(PHASE2_SEED, 'phase2_seed')
            entry2 = batch.group_page_cache.get(APP_ID, PHASE2_SEED)
            if entry2 is None:
                raise ProbeStop('phase2_seed_did_not_populate_cache')
            phase2_normal = [t for t in buckets_by_quality(entry2, 'normal') if t != PHASE2_SEED][:2]
            for title in phase2_normal:
                run_title(title, 'phase2_normal')

            run_title(NEGATIVE_AGENT, 'phase3_negative_agent')
            run_title(COMMODITY_CONTROL, 'phase3_commodity_control')

            report['cache'] = summarize_cache(batch.group_page_cache)
        report['status'] = 'completed'
    except ProbeStop as exc:
        report['status'] = 'stopped: ' + str(exc)
    finally:
        steam_public._trimmed_group_queries = original_trim
        steam_public._read_body = original_read_body
        report['pages'] = page_metrics
        report['byte_log'] = byte_log
        report['titles'] = build_title_report(temp_journal, title_phase)
        report['reuse_economics'] = summarize_reuse(report['titles'], byte_log, page_metrics)
        report['go_no_go'] = evaluate_go_no_go(report)
        report['finished_at'] = now_iso()
        folder = ROOT / 'data/steam-probes'
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-live-qual-' + uuid4().hex[:8] + '.json')
        after = verify_production_journal_untouched(prod_journal)
        report['production_journal_verification'] = {'before': baseline, 'after': after, 'untouched': after == baseline}
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(json.dumps({'report': str(output), 'status': report['status'],
                          'http_request_count': report['http_request_count'],
                          'production_journal_untouched': report['production_journal_verification']['untouched'],
                          'background_collection': 'left paused'}, indent=2))


def summarize_cache(cache):
    entries = []
    for key, entry in cache._entries.items():
        entries.append({'app_id': key[0], 'decoded_body_sha256': key[1][:16] + '...',
                        'bucket_count': len(entry.bucket_titles), 'deep_sizeof_bytes': deep_sizeof(entry)})
    return {'capacity': cache.capacity, 'retained_entries': len(cache._entries), 'entries': entries}


def build_title_report(temp_journal, title_phase):
    rows = []
    for record in temp_journal.records('capture'):
        title = record.get('title')
        if title not in title_phase:
            continue
        provenance = record.get('source_provenance', {})
        payload = record.get('payload')
        error = record.get('error')
        # Gap B: record's own kind/status/error double as the request half of
        # failure_scope's signature (collection_batch.normalize_sources does the same
        # self-pairing) -- an error is always a fact about this one capture, so there
        # is no separate request object to look up.
        row = {'title': title, 'phase': title_phase[title], 'status': record.get('status'),
              'error': error, 'failure_scope': failure_scope(record, record) if error else None,
              'derived_from_cached_page': provenance.get('derived_from_cached_page', False),
              'response_date': provenance.get('response_date'), 'response_age': provenance.get('response_age'),
              'followup_url': provenance.get('followup_url'), 'followup_response_date': provenance.get('followup_response_date'),
              'followup_response_age': provenance.get('followup_response_age'),
              'buy_levels': None, 'sell_levels': None, 'multi_level_depth': None}
        if payload:
            histogram = payload['result']['histogram']
            buy, sell = histogram['buyOrders'], histogram['sellOrders']
            row.update(book_timestamp_source=payload['provenance']['book_timestamp_source'],
                       book_observed_at=histogram['date'], buy_levels=len(buy), sell_levels=len(sell),
                       best_bid=buy[0]['price'] if buy else None, best_ask=sell[0]['price'] if sell else None,
                       multi_level_depth=len(buy) > 1 or len(sell) > 1,
                       book_quantity_semantics=payload['provenance']['book_quantity_semantics'])
            date_header = row['followup_response_date'] or row['response_date']
            if date_header:
                try:
                    header_at = parsedate_to_datetime(date_header)
                    if header_at.tzinfo is None:
                        header_at = header_at.replace(tzinfo=timezone.utc)
                    row['date_header_to_book_timestamp_delta_seconds'] = (utc(row['book_observed_at']) - header_at).total_seconds()
                except (TypeError, ValueError):
                    pass
        rows.append(row)
    return rows


def summarize_reuse(titles, byte_log, page_metrics):
    page_requests = len(page_metrics)
    followup_requests = sum(1 for t in titles if t.get('followup_url'))
    cache_hits = sum(1 for t in titles if t.get('derived_from_cached_page'))
    cache_misses = sum(1 for t in titles if not t.get('derived_from_cached_page') and t.get('status') == 200)
    total_bytes_transferred = sum(entry['encoded_bytes'] for entry in byte_log)
    grouped_titles_requested = sum(1 for t in titles if t['phase'].startswith(('phase1', 'phase2')))
    average_page_bytes = (sum(m for m in (e['encoded_bytes'] for e in byte_log if e['encoded_bytes'] > 100_000)) /
                          max(1, sum(1 for e in byte_log if e['encoded_bytes'] > 100_000))) if byte_log else 0
    counterfactual_bytes_one_page_per_variant = grouped_titles_requested * average_page_bytes
    return {'page_requests': page_requests, 'followup_requests': followup_requests,
            'cache_hits': cache_hits, 'cache_misses': cache_misses,
            'total_bytes_transferred': total_bytes_transferred,
            'average_page_bytes_observed': average_page_bytes,
            'counterfactual_bytes_one_page_per_variant': counterfactual_bytes_one_page_per_variant,
            'bytes_saved_by_reuse': counterfactual_bytes_one_page_per_variant - total_bytes_transferred}


def evaluate_go_no_go(report):
    flags = []
    for page in report['pages']:
        if page['bucket_count'] == 0:
            flags.append('empty_bucket_list: ' + str(page))
    for entry in report['byte_log']:
        if entry['encoded_bytes'] > 8 * 1024 * 1024 or entry['decoded_bytes'] > 8 * 1024 * 1024:
            flags.append('page_exceeded_8MiB: ' + str(entry))
    for title in report['titles']:
        error = title.get('error')
        phase = title['phase']
        if error == 'missing_or_ambiguous_steam_page_data':
            flags.append('missing_or_ambiguous_steam_page_data on ' + title['title'])
        if error == 'unsupported_steam_listing_identity' and phase.startswith(('phase1_normal', 'phase2', 'phase1_seed')):
            flags.append('unsupported_steam_listing_identity on an ordinary marketable skin: ' + title['title'])
        if error in ('invalid_order_book_sort_or_quantity', 'order_book_quantity_total_mismatch', 'order_book_best_price_mismatch'):
            flags.append(error + ' on ' + title['title'])
        if error in ('unreliable_orderbook_date_header', 'stale_orderbook_endpoint_cache'):
            flags.append(error + ' on ' + title['title'])
        if title.get('status') == 429:
            flags.append('429 observed on ' + title['title'])
        if phase in ('phase1_seed_fallback', 'phase1_normal', 'phase2_seed', 'phase2_normal') and not error:
            if not title.get('multi_level_depth') and (title.get('buy_levels', 0) + title.get('sell_levels', 0) <= 2):
                flags.append('top_of_book_only_depth_on_marketable_skin: ' + title['title'])
    return flags


def verify_production_journal_untouched(prod_journal):
    with prod_journal.connect() as db:
        count, last_id = db.execute('SELECT COUNT(*), MAX(id) FROM records').fetchone()
    return {'record_count': count, 'last_record_id': last_id}


if __name__ == '__main__':
    main()
