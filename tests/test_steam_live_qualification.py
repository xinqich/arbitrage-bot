import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from arbitrage_v2.journal import Journal
import arbitrage_v2.steam_public as steam_public

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('steam_live_qualification', ROOT / 'scripts/steam_live_qualification.py')
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)


def grouped_page_body(app_id, buckets, descriptions, pricehistory, fallback_title):
    # Minimal real SSR envelope (same shape as tests/test_steam_public.py's group_page
    # helper) so decode_group_page/GroupPageCache.put run the actual production trim,
    # not a hand-rolled substitute.
    loaders = [
        json.dumps({'steamid': '0', 'sessionid': 'DO_NOT_ARCHIVE'}),
        json.dumps({'filterConfig': {'currency': {'eCurrency': 1}}}),
        json.dumps({'success': True, 'appid': app_id, 'bCommodity': False,
                    'initialFallbackBucketID': fallback_title, 'buckets': buckets}),
    ]
    queries = []
    for title, description in descriptions.items():
        queries.append({'queryKey': ['market', 'description', app_id, title],
            'state': {'status': 'success', 'error': None, 'dataUpdatedAt': 1, 'data': description}})
    for title, history in pricehistory.items():
        queries.append({'queryKey': ['market', 'pricehistory', app_id, title],
            'state': {'status': 'success', 'error': None,
                      'dataUpdatedAt': history['data_updated_at_ms'], 'data': history['data']}})
    context = json.dumps({'queryData': json.dumps({'queries': queries})})
    return ('<html><script>window.SSR.loaderData = ' + json.dumps(loaders) + ';'
            'window.SSR.renderContext=JSON.parse(' + json.dumps(context) + ');'
            'throw new Error("MUST_NOT_EXECUTE");</script></html>').encode()


class DeepSizeofTests(unittest.TestCase):
    def test_deep_sizeof_walks_nested_structures_sys_getsizeof_does_not(self):
        obj = {'a': list(range(5000))}
        self.assertGreater(q.deep_sizeof(obj), sys.getsizeof(obj) * 100)

    def test_summarize_cache_reports_the_real_retained_weight_not_the_object_shell(self):
        # Gap A regression guard: the report once recorded 48 bytes per entry for every
        # retained page regardless of size -- sys.getsizeof's bare object-shell reading
        # -- because the call site bypassed the recursive walk. Build a realistically
        # populated entry through the real GroupPageCache.put path and confirm
        # summarize_cache's figure tracks deep_sizeof(entry), not the shell.
        app_id = 730
        title = 'AK-47 | Slate (Factory New)'
        buckets = [{'bucket_id': title, 'filters': [['Quality', 'normal'], ['Exterior', 'WearCategory4']],
                    'min_price': '999', 'classid': '1'}]
        descriptions = {title: {'appid': app_id, 'market_hash_name': title, 'commodity': False, 'marketable': True}}
        prices = [{'time': 1600000000 + i * 86400, 'price_median': '10.00', 'purchases': 5} for i in range(3000)]
        pricehistory = {title: {'data_updated_at_ms': 1, 'data': {'ecurrency': 1, 'prices': prices}}}
        body = grouped_page_body(app_id, buckets, descriptions, pricehistory, title)
        page = steam_public.decode_group_page(body, app_id)
        cache = steam_public.GroupPageCache()
        cache.put(app_id, page, '2026-09-24T00:00:00+00:00', 'https://example', 'a' * 64)
        entry = cache.get(app_id, title)
        summary = q.summarize_cache(cache)
        self.assertEqual(summary['entries'][0]['deep_sizeof_bytes'], q.deep_sizeof(entry))
        self.assertGreater(summary['entries'][0]['deep_sizeof_bytes'], 100_000)


class BuildTitleReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Journal(Path(self.tmp.name) / 'journal.sqlite3')
        self.journal.initialize()

    def test_failure_scope_is_recorded_for_an_item_scoped_parse_error(self):
        title = 'StatTrak™ AK-47 | Slate (Battle-Scarred)'
        self.journal.append('capture', {'provider': 'steam_public', 'kind': 'details', 'app_id': 730,
            'title': title, 'status': 200, 'error': 'unsupported_item_quality',
            'payload': None, 'source_provenance': {}})
        rows = q.build_title_report(self.journal, {title: 'phase1_strange'})
        self.assertEqual(rows[0]['failure_scope'], 'item')
        self.assertIsNone(rows[0]['buy_levels'])
        self.assertIsNone(rows[0]['multi_level_depth'])

    def test_failure_scope_is_none_when_there_is_no_error(self):
        self.journal.append('capture', {'provider': 'steam_public', 'kind': 'details', 'app_id': 730,
            'title': 'Fracture Case', 'status': 200, 'error': None,
            'payload': None, 'source_provenance': {}})
        rows = q.build_title_report(self.journal, {'Fracture Case': 'phase3_commodity_control'})
        self.assertIsNone(rows[0]['failure_scope'])


class BusyPreconditionTests(unittest.TestCase):
    def test_unreachable_endpoint_is_recorded_as_an_accepted_substitute_not_a_silent_false(self):
        result = q.busy_precondition(None)
        self.assertFalse(result['web_status_reachable'])
        self.assertIsNone(result['web_status_busy'])
        self.assertEqual(result['busy_verified_via'], 'accepted_substitute: worker_paused_and_desk_unreachable')

    def test_reachable_endpoint_is_recorded_as_verified_via_the_endpoint(self):
        result = q.busy_precondition({'busy': False})
        self.assertTrue(result['web_status_reachable'])
        self.assertFalse(result['web_status_busy'])
        self.assertEqual(result['busy_verified_via'], 'web_status_endpoint')


if __name__ == '__main__':
    unittest.main()
