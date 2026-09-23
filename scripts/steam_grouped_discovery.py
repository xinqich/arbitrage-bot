"""Stage 0: capture one grouped Steam page and dump its SSR structure.

Fetches AK-47 | Slate (Field-Tested) — a known grouped (skin) listing —
and writes the decoded loaderData entries, queryKey shapes, filterConfig,
and size metrics to a JSON report. Uses a temporary journal with its own
lock; never touches the production journal.

Precondition: pause the production worker before running.
"""
import gzip
import json
import sys
import tempfile
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arbitrage_v2.steam_public import (
    InlineScripts, ListingRedirect, _decode_after, allowed_url, listing_url,
)
from arbitrage_v2.journal import Journal
from arbitrage_v2.collection_lock import collection_lock


TITLE = 'AK-47 | Slate (Field-Tested)'
APP_ID = 730
ENCODED_LIMIT = 8_000_000
DECODED_LIMIT = 8_000_000


def summarize_value(value, depth=0, max_depth=3):
    """Produce a shape summary: type, length, and shallow keys/items."""
    if depth >= max_depth:
        return f'<{type(value).__name__}>'
    if isinstance(value, dict):
        return {k: summarize_value(v, depth + 1, max_depth) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) <= 3:
            return [summarize_value(v, depth + 1, max_depth) for v in value]
        return {
            '_type': 'list',
            '_length': len(value),
            '_first': summarize_value(value[0], depth + 1, max_depth),
            '_last': summarize_value(value[-1], depth + 1, max_depth),
        }
    if isinstance(value, str) and len(value) > 200:
        return f'<string, {len(value)} chars>'
    return value


def main():
    url = listing_url(APP_ID, TITLE)
    print(f'Target: {TITLE}')
    print(f'URL: {url}')
    print()

    with tempfile.TemporaryDirectory(prefix='steam_discovery_') as tmpdir:
        tmp_path = Path(tmpdir)
        journal = Journal(tmp_path / 'discovery.sqlite3')

        with collection_lock(tmp_path / 'discovery.sqlite3'):
            print('Fetching page...')
            request = Request(url, headers={
                'User-Agent': 'arbitrage-v2-research/0.4',
                'Accept': 'text/html',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip',
            })
            opener = build_opener(ListingRedirect())
            with opener.open(request, timeout=20) as response:
                status = response.status
                final_url = response.url
                raw = response.read(ENCODED_LIMIT + 1)
                response_date = response.headers.get('Date')

            print(f'Status: {status}')
            print(f'Final URL: {final_url}')
            print(f'Encoded size: {len(raw):,} bytes')

            if not allowed_url(final_url):
                print('ERROR: redirect landed outside allowed surface')
                sys.exit(1)

            if len(raw) > ENCODED_LIMIT:
                print(f'ERROR: encoded body exceeds {ENCODED_LIMIT:,} bytes')
                sys.exit(1)

            body = raw
            if body[:2] == b'\x1f\x8b':
                with gzip.GzipFile(fileobj=BytesIO(body)) as compressed:
                    body = compressed.read(DECODED_LIMIT + 1)
                print(f'Decoded (gzip) size: {len(body):,} bytes')
            else:
                print('Response was not gzip-compressed')

            if len(body) > DECODED_LIMIT:
                print(f'ERROR: decoded body exceeds {DECODED_LIMIT:,} bytes')
                sys.exit(1)

            body_hash = sha256(body).hexdigest()
            print(f'Decoded SHA-256: {body_hash[:16]}...')
            print()

            # Parse inline scripts
            parser = InlineScripts()
            parser.feed(body.decode('utf-8'))
            print(f'Found {len(parser.scripts)} inline script blocks')
            print()

            # Decode SSR blocks
            report = {
                'title': TITLE,
                'app_id': APP_ID,
                'fetched_at': datetime.now(timezone.utc).isoformat(),
                'final_url': final_url,
                'encoded_bytes': len(raw),
                'decoded_bytes': len(body),
                'decoded_sha256': body_hash,
                'response_date': response_date,
                'inline_script_count': len(parser.scripts),
            }

            # loaderData
            try:
                loaders = _decode_after(parser.scripts, r'window\.SSR\.loaderData\s*=\s*')
                loaders = [json.loads(row, parse_float=str) for row in loaders]
                report['loaderData_row_count'] = len(loaders)

                for i, row in enumerate(loaders):
                    key = f'loaderData_{i}'
                    if isinstance(row, dict):
                        report[key + '_keys'] = list(row.keys())
                        report[key + '_shape'] = summarize_value(row, max_depth=2)
                        if 'bCommodity' in row:
                            report[key + '_bCommodity'] = row.get('bCommodity')
                            report[key + '_appid'] = row.get('appid')
                            report[key + '_success'] = row.get('success')
                            report[key + '_marketable'] = row.get('marketable')
                            report[key + '_market_hash_name'] = row.get('market_hash_name')
                            report[key + '_initialSelectedBucketID'] = row.get('initialSelectedBucketID')
                            report[key + '_initialFallbackBucketID'] = row.get('initialFallbackBucketID')
                            # Dump bucket structure — this is the critical piece
                            buckets = row.get('buckets', [])
                            report[key + '_bucket_count'] = len(buckets)
                            # Every key verbatim (bounded by summarize_value's depth/length
                            # limits) — the allowlisted-field version missed a URL/query hint,
                            # if one exists on the bucket object.
                            bucket_summaries = [summarize_value(bucket, max_depth=4) for bucket in buckets]
                            report[key + '_buckets'] = bucket_summaries
                            # Dump listingQuery
                            lq = row.get('listingQuery')
                            if isinstance(lq, dict):
                                report[key + '_listingQuery'] = lq
                            # relevantAssetProperties
                            rap = row.get('relevantAssetProperties')
                            if isinstance(rap, dict):
                                report[key + '_relevantAssetProperties'] = rap
                        if 'filterConfig' in row:
                            report[key + '_filterConfig'] = summarize_value(
                                row['filterConfig'], max_depth=3)
                    else:
                        report[key + '_type'] = type(row).__name__
                        report[key + '_shape'] = summarize_value(row, max_depth=2)
            except ValueError as exc:
                report['loaderData_error'] = str(exc)
                print(f'loaderData decode error: {exc}')

            # renderContext / queryData
            try:
                context = json.loads(
                    _decode_after(parser.scripts, r'window\.SSR\.renderContext\s*=\s*JSON\.parse\('),
                    parse_float=str,
                )
                report['renderContext_keys'] = list(context.keys()) if isinstance(context, dict) else type(context).__name__

                if 'queryData' in context:
                    queries = json.loads(context['queryData'], parse_float=str)
                    if 'queries' in queries:
                        query_list = queries['queries']
                        report['query_count'] = len(query_list)

                        # Extract all queryKey shapes
                        query_keys = []
                        for q in query_list:
                            qk = q.get('queryKey')
                            state = q.get('state', {})
                            entry = {
                                'queryKey': qk,
                                'status': state.get('status'),
                                'has_error': state.get('error') is not None,
                                'has_data': 'data' in state,
                                'dataUpdatedAt': state.get('dataUpdatedAt'),
                            }
                            if isinstance(qk, list) and len(qk) >= 1:
                                if qk[0] == 'market_item_search':
                                    data = state.get('data', {})
                                    if isinstance(data, dict):
                                        entry['data_keys'] = list(data.keys())[:20]
                                        entry['data_shape'] = summarize_value(data, max_depth=3)
                                elif len(qk) >= 2 and qk[0] == 'market' and qk[1] == 'description':
                                    data = state.get('data', {})
                                    if isinstance(data, dict):
                                        entry['data_keys'] = list(data.keys())[:20]
                                        entry['data_commodity'] = data.get('commodity')
                                        entry['data_marketable'] = data.get('marketable')
                                        entry['data_market_hash_name'] = data.get('market_hash_name')
                                        entry['data_appid'] = data.get('appid')
                                elif qk[0] == 'market' and qk[1] == 'orderbook':
                                    data = state.get('data', {})
                                    if isinstance(data, dict):
                                        entry['data_keys'] = list(data.keys())[:20]
                                        entry['data_eCurrency'] = data.get('eCurrency')
                                        buy = data.get('rgCompactBuyOrders')
                                        sell = data.get('rgCompactSellOrders')
                                        entry['data_buy_pairs'] = len(buy) // 2 if isinstance(buy, list) else None
                                        entry['data_sell_pairs'] = len(sell) // 2 if isinstance(sell, list) else None
                                        entry['data_cBuyOrders'] = data.get('cBuyOrders')
                                        entry['data_cSellOrders'] = data.get('cSellOrders')
                                elif qk[0] == 'market' and qk[1] == 'pricehistory':
                                    data = state.get('data', {})
                                    if isinstance(data, dict):
                                        entry['data_keys'] = list(data.keys())[:10]
                                        prices = data.get('prices')
                                        entry['data_price_count'] = len(prices) if isinstance(prices, list) else None
                            query_keys.append(entry)

                        report['queries'] = query_keys

                        # Highlight the query key patterns
                        patterns = {}
                        for q in query_list:
                            qk = q.get('queryKey')
                            if isinstance(qk, list):
                                pattern = tuple(type(x).__name__ for x in qk)
                                key_preview = [x if not isinstance(x, str) or len(x) < 60 else x[:57] + '...' for x in qk]
                                if pattern not in patterns:
                                    patterns[pattern] = []
                                patterns[pattern].append(key_preview)
                        report['queryKey_patterns'] = {
                            str(k): v for k, v in patterns.items()
                        }
            except ValueError as exc:
                report['renderContext_error'] = str(exc)
                print(f'renderContext decode error: {exc}')

            # Save report
            output_dir = ROOT / 'data' / 'steam-probes'
            output_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            output_path = output_dir / f'grouped-discovery-{ts}.json'
            output_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding='utf-8',
            )
            print(f'Report saved: {output_path}')
            print()

            # Print key findings to stdout
            print('=== Key findings ===')
            if 'loaderData_row_count' in report:
                print(f'loaderData rows: {report["loaderData_row_count"]}')
            if 'query_count' in report:
                print(f'React-Query entries: {report["query_count"]}')
            if 'queryKey_patterns' in report:
                print('Query key patterns:')
                for pattern, examples in report['queryKey_patterns'].items():
                    print(f'  {pattern}:')
                    for ex in examples[:3]:
                        print(f'    {ex}')
            print()
            print('Full report written to JSON file for inspection.')


if __name__ == '__main__':
    main()
