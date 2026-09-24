from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import gzip
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

from arbitrage_v2.collection_batch import failure_scope
from arbitrage_v2.collection_transport import request_context
from arbitrage_v2.collector import collect_once, history_points, history_summary
from arbitrage_v2.collection_lock import collection_lock
from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.journal import Journal
from arbitrage_v2.prediction import _steam_book, _steam_observation
from arbitrage_v2.research_cli import run
from arbitrage_v2.steam_public import (LIMIT, GroupPageCache, ListingRedirect, _NAMED_PARSE_ERRORS,
                                     _endpoint_orderbook_timestamp, _parse_orderbook_endpoint,
                                     allowed_url, capture_public, listing_url,
                                     normalize_fields, orderbook_url, page_fields)
import arbitrage_v2.steam_public as steam_public_module

ROOT = Path(__file__).resolve().parents[1]


def fixture(name='fracture_case'):
    return json.loads((ROOT/'tests/fixtures'/('steam_public_'+name+'.json')).read_text(encoding='utf-8'))


def page(fields, history_status='success'):
    # Minimal page envelope with recorded market fields and unrelated private-looking
    # values that must not enter the archive. No JavaScript is executed by the parser.
    loaders = [json.dumps({'steamid': '0', 'sessionid': 'DO_NOT_ARCHIVE'}),
               json.dumps({'filterConfig': {'currency': {'eCurrency': fields['page_currency']}}}),
               json.dumps({'success': True, 'appid': fields['app_id'], 'bCommodity': True})]
    queries = [{'queryKey': ['market', kind, fields['app_id'], fields['title']],
                'state': {'data': row['data'], 'dataUpdatedAt': row['data_updated_at_ms'],
                          'status': history_status if kind == 'pricehistory' else 'success',
                          'error': None}} for kind, row in fields['queries'].items()]
    context = json.dumps({'queryData': json.dumps({'queries': queries})})
    return ('<html><script>window.SSR.loaderData = '+json.dumps(loaders)+';'
            'window.SSR.renderContext=JSON.parse('+json.dumps(context)+');'
            'throw new Error("MUST_NOT_EXECUTE");</script></html>').encode()


def group_page(spec, history_status='success'):
    # Reduced grouped-listing SSR page built from a fixture spec (buckets plus each
    # bucket's description/pricehistory/orderbook queries). Carries the same
    # DO_NOT_ARCHIVE/MUST_NOT_EXECUTE hygiene sentinels as page() above.
    app_id = spec['app_id']
    loaders = [
        json.dumps({'steamid': '0', 'sessionid': 'DO_NOT_ARCHIVE'}),
        json.dumps({'filterConfig': {'currency': {'eCurrency': spec['page_currency']}}}),
        json.dumps({'success': True, 'appid': app_id, 'bCommodity': False,
                    'initialFallbackBucketID': spec['fallback_title'], 'buckets': spec['buckets']}),
    ]
    queries = []
    for title, description in spec['descriptions'].items():
        queries.append({'queryKey': ['market', 'description', app_id, title],
            'state': {'status': 'success', 'error': None, 'dataUpdatedAt': 1, 'data': description}})
    for title, history in spec.get('pricehistory', {}).items():
        queries.append({'queryKey': ['market', 'pricehistory', app_id, title],
            'state': {'status': history_status, 'error': None,
                      'dataUpdatedAt': history['data_updated_at_ms'], 'data': history['data']}})
    if 'fallback_orderbook' in spec:
        queries.append({'queryKey': ['market', 'orderbook', app_id, spec['fallback_title']],
            'state': {'status': 'success', 'error': None,
                      'dataUpdatedAt': spec['fallback_orderbook']['data_updated_at_ms'],
                      'data': spec['fallback_orderbook']['data']}})
    context = json.dumps({'queryData': json.dumps({'queries': queries})})
    return ('<html><script>window.SSR.loaderData = '+json.dumps(loaders)+';'
            'window.SSR.renderContext=JSON.parse('+json.dumps(context)+');'
            'throw new Error("MUST_NOT_EXECUTE");</script></html>').encode()


class Response(BytesIO):
    status = 200
    url = 'https://steamcommunity.com/market/listings/730/G18DA243004?currency=1&l=english'
    headers = {'Date': 'Tue, 08 Sep 2026 13:21:17 GMT', 'Set-Cookie': 'DO_NOT_ARCHIVE'}


class SteamPublicTests(unittest.TestCase):
    def setUp(self):
        self.ref = fixture()
        self.fields = deepcopy(self.ref['fields'])
        self.at = self.ref['source']['retrieved_at']
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Journal(Path(self.tmp.name)/'journal.sqlite3')
        self.journal.initialize()

    def test_recorded_pages_preserve_integer_prices_quantities_and_extended_history(self):
        for name, bid, ask, count in [('fracture_case', ('0.69',58), ('0.71',604),2936),
                                     ('kilowatt_case', ('0.18',20198), ('0.19',446),1657)]:
            with self.subTest(item=name):
                reference = fixture(name)
                normalized = normalize_fields(reference['fields'], reference['source']['retrieved_at'])
                result = normalized['result']
                self.assertEqual(result['histogram']['buyOrders'][0], {'price':bid[0],'quantity':bid[1]})
                self.assertEqual(result['histogram']['sellOrders'][0], {'price':ask[0],'quantity':ask[1]})
                self.assertEqual(len(result['priceHistory']['data']),count)
                self.assertEqual(normalized['provenance']['book_price_unit_original'],'cents')
                self.assertEqual(normalized['provenance']['underlying_market_cache_age'],'not_exposed')

    def test_parser_reads_json_without_running_code_or_archiving_global_state(self):
        parsed = page_fields(page(self.fields),730,'Fracture Case')
        self.assertEqual(parsed,self.fields)
        self.assertNotIn('DO_NOT_ARCHIVE',json.dumps(parsed))
        self.assertNotIn('MUST_NOT_EXECUTE',json.dumps(parsed))

    def test_wrong_price_currency_is_rejected(self):
        for where in ('page','book'):
            bad = deepcopy(self.fields)
            if where == 'page':bad['page_currency'] = 3
            elif where == 'book':bad['queries']['orderbook']['data']['eCurrency'] = 3
            with self.subTest(where=where),self.assertRaisesRegex(ValueError,'USD'):
                normalize_fields(bad,self.at)

    def test_identity_and_commodity_are_checked(self):
        for key,value in [('market_hash_name','Recoil Case'),('appid',570),('commodity',None)]:
            bad = deepcopy(self.fields)
            bad['queries']['description']['data'][key] = value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'identity'):
                normalize_fields(bad,self.at)
        with self.assertRaises(ValueError):listing_url(570,'Fracture Case')

    def test_commodity_page_with_an_integer_bcommodity_is_rejected_not_regrouped(self):
        body = page(self.fields).decode().replace('\\"bCommodity\\": true', '\\"bCommodity\\": 1')
        self.assertIn('\\"bCommodity\\": 1', body)
        with self.assertRaisesRegex(ValueError,'unsupported_steam_listing_identity'):
            page_fields(body.encode(),730,'Fracture Case')

    def test_missing_failed_or_invalid_history_keeps_verified_book(self):
        expected = normalize_fields(self.fields,self.at)['result']['histogram']
        for scenario in ('missing', 'failed', 'currency', 'future', 'malformed', 'empty'):
            with self.subTest(scenario=scenario):
                fields = deepcopy(self.fields)
                if scenario == 'missing':fields['queries'].pop('pricehistory')
                if scenario == 'currency':fields['queries']['pricehistory']['data']['ecurrency'] = 3
                if scenario == 'future':fields['queries']['pricehistory']['data_updated_at_ms'] = 9999999999999
                if scenario == 'malformed':fields['queries']['pricehistory']['data']['prices'] = None
                if scenario == 'empty':fields['queries']['pricehistory']['data']['prices'] = []
                parsed = page_fields(page(fields,'error' if scenario=='failed' else 'success'),730,'Fracture Case')
                result = normalize_fields(parsed,self.at)
                self.assertEqual(result['result']['histogram'],expected)
                self.assertEqual(result['result']['priceHistory']['data'],[])
                self.assertIn(result['provenance']['history_status'], ('unavailable','invalid','empty'))
                _steam_observation({'payload':result},'Fracture Case',self.at,14400)

    def test_missing_required_query_still_rejects_page(self):
        for kind in ('description','orderbook'):
            fields = deepcopy(self.fields);fields['queries'].pop(kind)
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError,'market_query'):
                page_fields(page(fields),730,'Fracture Case')

    def test_packed_book_boundaries_and_totals_fail_closed(self):
        alterations = [lambda b:b['rgCompactBuyOrders'].pop(),
                       lambda b:b['rgCompactBuyOrders'].__setitem__(1,-1),
                       lambda b:b['rgCompactBuyOrders'].__setitem__(0,0.69),
                       lambda b:b.__setitem__('cBuyOrders',1),
                       lambda b:b.__setitem__('amtMaxBuyOrder',999),
                       lambda b:b['rgCompactBuyOrders'].__setitem__(2,69)]
        for number,alter in enumerate(alterations):
            bad = deepcopy(self.fields)
            alter(bad['queries']['orderbook']['data'])
            with self.subTest(case=number),self.assertRaises(ValueError):
                normalize_fields(bad,self.at)

    def test_server_observation_timestamp_is_preserved_and_does_not_reset_on_fetch(self):
        result = normalize_fields(self.fields,'2030-01-01T00:00:00Z')
        self.assertEqual(result['result']['histogram']['date'],'2026-09-08T13:21:17.693000+00:00')
        with self.assertRaisesRegex(ValueError,'stale_steam_book'):
            _steam_observation({'payload':result},'Fracture Case','2030-01-01T00:00:00Z',14400)
        with self.assertRaisesRegex(ValueError,'future_steam_query_timestamp'):
            normalize_fields(self.fields,'2020-01-01T00:00:00Z')

    def test_history_medians_are_not_rounded_to_executable_cent_prices(self):
        result = normalize_fields(self.fields,self.at)
        points = result['result']['priceHistory']['data']
        self.assertEqual(points[-1]['price'],'0.7053998708724976')
        self.assertEqual(points[-1]['quantity'],681)
        self.assertEqual(result['provenance']['history_coverage'],'unknown')
        bad = deepcopy(self.fields)
        history = bad['queries']['pricehistory']['data']['prices']
        history.append(dict(history[-1],purchases=12345))
        invalid = normalize_fields(bad,self.at)
        self.assertEqual(invalid['provenance']['history_status'], 'invalid')
        self.assertEqual(invalid['result']['priceHistory']['data'], [])
        self.assertEqual(invalid['result']['histogram'], result['result']['histogram'])

    def test_redirects_cannot_leave_public_listing_surface(self):
        redirect = ListingRedirect()
        request = Request(listing_url(730,'Fracture Case'))
        for target in ('https://example.com/market/listings/730/x',
                       'https://steamcommunity.com/login',
                       'http://steamcommunity.com/market/listings/730/x',
                       'https://steamcommunity.com/market/orderbookX',
                       'https://steamcommunity.com/market/orderbook/anything',
                       'https://steamcommunity.com/market/orderbook?q=Load#frag',
                       'http://steamcommunity.com/market/orderbook',
                       'https://evil.example.com/market/orderbook'):
            self.assertIsNone(redirect.redirect_request(request,None,302,'',{},target))
        self.assertIsNotNone(redirect.redirect_request(request,None,302,'',{},Response.url))
        self.assertTrue(allowed_url('https://steamcommunity.com/market/orderbook?q=Load&qp=x'))
        self.assertFalse(allowed_url('https://steamcommunity.com/market/orderbook#frag'))

    def test_anonymous_capture_archives_market_fields_and_fingerprint_only(self):
        calls = []
        def fetch(request,timeout):
            calls.append(request)
            return Response(page(self.fields))
        result = capture_public(self.journal,730,'Fracture Case',SimpleNamespace(open=fetch))
        self.assertIsNone(result['error'])
        self.assertEqual(len(self.journal.records('request_attempt')),1)
        record = self.journal.get(result['record_id'],'capture')
        self.assertNotIn('DO_NOT_ARCHIVE',json.dumps(record))
        self.assertNotIn('cookie',{k.lower() for k in calls[0].headers})
        self.assertNotIn('x-api-key',{k.lower() for k in calls[0].headers})
        self.assertEqual(record['provider'],'steam_public')
        self.assertEqual(len(record['source_provenance']['decoded_body_sha256']),64)

    def test_changed_page_format_is_recorded_as_failure(self):
        result = capture_public(self.journal,730,'Fracture Case',
                                SimpleNamespace(open=lambda *a,**kw:Response(b'<html>Login required</html>')))
        self.assertEqual(result['error'],'invalid_or_unsupported_steam_page')
        self.assertIsNone(self.journal.get(result['record_id'])['payload'])

    def test_old_public_allowance_is_ignored(self):
        self.journal.append('request_attempt',{'provider':'steam_public'})
        watch={'items':[{'app_id':730,'title':'Fracture Case'}], 'steam_source':'steam_public',
               'steam_public_request_allowance':1,'total_request_allowance':1}
        with patch('arbitrage_v2.steam_public.capture_public') as public,patch('arbitrage_v2.collector.capture') as other:
            public.return_value=other.return_value={'status':200,'error':None}
            self.assertEqual(len(collect_once(self.journal,watch,{})),3)
            public.assert_called_once()
    def test_public_collection_never_looks_up_paid_provider_quota(self):
        watch = Path(self.tmp.name)/'watch.json'
        watch.write_text(json.dumps({'items':[{'app_id':730,'title':'Fracture Case'}],
                                     'steam_source':'steam_public'}))
        args = SimpleNamespace(command='collect',journal=self.journal.path,watchlist=watch,
            interval_seconds=3600,max_cycles=1,steam_source=None,env_file=None,
            steam_budget=4,request_budget=12,screen_policy=None,watch=False)
        with (patch('arbitrage_v2.research_cli.quota_status') as quota,
              patch('arbitrage_v2.research_cli.collect_once',return_value=[])):
            report = run(args)
        quota.assert_not_called()
        self.assertEqual(report['provider_quota']['status'],'not_used')

    def test_history_sources_and_evidence_modes_remain_separate(self):
        point = {'date':'2026-09-08T09:00:00Z','price':'0.70','quantity':565}
        for provider,kind,quantity in [('steam_public','recorded',565),('steamapis','recorded',565),
                                       ('steam_public','synthetic',99),('steam_public','recorded',570)]:
            self.journal.append('capture',{'kind':'details','app_id':730,'title':'Fracture Case',
                'provider':provider,'input_kind':kind,'error':None,'retrieved_at':self.at,
                'payload':{'result':{'priceHistory':{'data':[dict(point,quantity=quantity)]}}}})
        self.assertEqual(len(history_points(self.journal,'Fracture Case')['points']),3)
        rows = history_points(self.journal,'Fracture Case','steam_public','recorded')['points']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['reported_point']['quantity'],570)
        summary = history_summary(self.journal,'Fracture Case')
        self.assertEqual(len(summary['sources']),3)
        self.assertEqual(summary['coverage'],'unknown')

    def test_collector_lock_excludes_duplicates_and_releases_after_error(self):
        with self.assertRaisesRegex(RuntimeError,'simulated exit'):
            with collection_lock(self.journal.path):
                with self.assertRaisesRegex(ValueError,'another collector'):
                    with collection_lock(self.journal.path):
                        self.fail('duplicate collector was allowed')
                raise RuntimeError('simulated exit')
        with collection_lock(self.journal.path):
            pass


class SteamPublicGroupedParserTests(unittest.TestCase):
    """Stage 1's grouped-page branch: bucket resolution, variant selection, and the
    explicit failures required for out-of-scope buckets (D2)."""

    def setUp(self):
        self.spec = fixture('slate_group')
        self.endpoint = fixture('slate_orderbook_endpoint')
        self.at = self.spec['source']['retrieved_at']
        self.app_id = self.spec['app_id']
        self.fallback_title = self.spec['fallback_title']
        self.target_title = self.spec['target_title']
        self.stattrak_title = self.spec['stattrak_title']

    def followup_fields(self, spec=None, title=None):
        spec = spec or self.spec
        fields = page_fields(group_page(spec), self.app_id, title or self.target_title)
        fields['queries']['orderbook'] = {'data': _parse_orderbook_endpoint(json.dumps(self.endpoint).encode()),
            'response_date_header': format_datetime(utc(self.at) - timedelta(seconds=1)), 'response_age_header': None}
        return fields

    def test_non_fallback_bucket_uses_followup_book_not_the_embedded_fallback(self):
        result = normalize_fields(self.followup_fields(), self.at)
        histogram = result['result']['histogram']
        self.assertEqual(histogram['buyOrders'][0], {'price': '1.05', 'quantity': 2})
        self.assertEqual(histogram['sellOrders'][0], {'price': '1.11', 'quantity': 1})
        self.assertEqual(result['provenance']['book_timestamp_source'], 'http_date_header')

    def test_fallback_bucket_uses_embedded_book_and_page_fields_requests_no_followup(self):
        fields = page_fields(group_page(self.spec), self.app_id, self.fallback_title)
        self.assertIsNotNone(fields['queries']['orderbook'])
        result = normalize_fields(fields, self.at)
        histogram = result['result']['histogram']
        self.assertEqual(histogram['buyOrders'][0], {'price': '1.50', 'quantity': 3})
        self.assertEqual(histogram['sellOrders'][0], {'price': '1.60', 'quantity': 4})
        self.assertEqual(result['provenance']['book_timestamp_source'], 'ssr_query_dataUpdatedAt')

    def test_non_fallback_bucket_sentinels_orderbook_for_capture_public_to_fetch(self):
        fields = page_fields(group_page(self.spec), self.app_id, self.target_title)
        self.assertIsNone(fields['queries']['orderbook'])

    def test_followup_url_is_built_from_title_alone_never_bucket_filter_pairs(self):
        url = orderbook_url(self.app_id, self.target_title)
        for value in ('Quality', 'normal', 'Exterior', 'WearCategory2'):
            self.assertNotIn(value, url)

    def test_title_not_in_listing_group_fails_item_scoped_not_a_neighbouring_bucket(self):
        with self.assertRaisesRegex(ValueError, 'title_not_in_listing_group'):
            page_fields(group_page(self.spec), self.app_id, 'Not A Real Bucket')
        request = {'provider': 'steam_public', 'kind': 'details', 'app_id': self.app_id, 'title': 'Not A Real Bucket'}
        self.assertEqual(failure_scope(request, {'error': 'title_not_in_listing_group', 'status': None}), 'item')

    def test_stattrak_and_souvenir_quality_fail_before_the_marketable_check(self):
        for quality in ('strange', 'tournament'):
            with self.subTest(quality=quality):
                spec = deepcopy(self.spec)
                bucket = next(b for b in spec['buckets'] if b['bucket_id'] == self.stattrak_title)
                bucket['filters'] = [['Quality', quality], ['Exterior', 'WearCategory2']]
                # The description for this bucket is already marketable: False; if the
                # Quality check did not fire first, normalize_fields's marketable check
                # would raise a different ('identity mismatch') error instead.
                with self.assertRaisesRegex(ValueError, 'unsupported_item_quality'):
                    page_fields(group_page(spec), self.app_id, self.stattrak_title)
        request = {'provider': 'steam_public', 'kind': 'details', 'app_id': self.app_id, 'title': self.stattrak_title}
        self.assertEqual(failure_scope(request, {'error': 'unsupported_item_quality', 'status': None}), 'item')

    def test_quality_normal_but_not_marketable_fails_as_identity_mismatch(self):
        spec = deepcopy(self.spec)
        bucket = next(b for b in spec['buckets'] if b['bucket_id'] == self.stattrak_title)
        bucket['filters'] = [['Quality', 'normal'], ['Exterior', 'WearCategory2']]
        fields = self.followup_fields(spec, self.stattrak_title)
        with self.assertRaisesRegex(ValueError, 'identity'):
            normalize_fields(fields, self.at)
        request = {'provider': 'steam_public', 'kind': 'details', 'app_id': self.app_id, 'title': self.stattrak_title}
        self.assertEqual(failure_scope(request, {'error': 'invalid_or_unsupported_steam_page', 'status': None}), 'item')

    def test_duplicate_bucket_id_fails_rather_than_silently_picking_the_first(self):
        spec = deepcopy(self.spec)
        spec['buckets'].append(dict(spec['buckets'][1], min_price='1'))
        with self.assertRaisesRegex(ValueError, 'title_not_in_listing_group'):
            page_fields(group_page(spec), self.app_id, self.target_title)

    def test_page_level_bcommodity_must_be_a_real_boolean_not_an_integer(self):
        # 1 == True and 0 == False, so a membership test against (True, False) admits
        # integers; select_title_fields then branches on `is True`, which 1 fails, and a
        # commodity page would be routed down the grouped path instead of being rejected.
        for value in ('1', '0', '1.0', 'null', '[]'):
            body = group_page(self.spec).decode().replace('\\"bCommodity\\": false',
                                                          '\\"bCommodity\\": ' + value)
            self.assertIn('\\"bCommodity\\": ' + value, body)
            for title in (self.target_title, self.fallback_title):
                with self.subTest(value=value, title=title), \
                        self.assertRaisesRegex(ValueError, 'unsupported_steam_listing_identity'):
                    page_fields(body.encode(), self.app_id, title)


class SteamPublicFollowupResponseTests(unittest.TestCase):
    """Validation of the standalone /market/orderbook response body, including the
    data.success check missing before this stage (see summary for the fix)."""

    def setUp(self):
        self.good = fixture('slate_orderbook_endpoint')['data']['data']

    def test_malformed_followup_bodies_fail_closed(self):
        cases = {
            'not_json': b'not json',
            'outer_data_missing': json.dumps({'nope': True}).encode(),
            'inner_data_missing': json.dumps({'data': {'success': True}}).encode(),
            'inner_data_not_a_dict': json.dumps({'data': {'success': True, 'data': 'nope'}}).encode(),
            'success_missing': json.dumps({'data': {'data': self.good}}).encode(),
            'success_false': json.dumps({'data': {'success': False, 'data': self.good}}).encode(),
        }
        for key in self.good:
            trimmed = {k: v for k, v in self.good.items() if k != key}
            cases['missing_' + key] = json.dumps({'data': {'success': True, 'data': trimmed}}).encode()
        for name, body in cases.items():
            with self.subTest(case=name):
                with self.assertRaisesRegex(ValueError, 'malformed_orderbook_endpoint_response'):
                    _parse_orderbook_endpoint(body)

    def test_valid_followup_body_parses(self):
        body = json.dumps({'data': {'success': True, 'data': self.good}}).encode()
        self.assertEqual(_parse_orderbook_endpoint(body), self.good)


class SteamPublicGroupedBookValidationTests(unittest.TestCase):
    """A follow-up book must fail closed exactly like the commodity path's own book."""

    def setUp(self):
        self.spec = fixture('slate_group')
        self.endpoint = fixture('slate_orderbook_endpoint')
        self.at = self.spec['source']['retrieved_at']
        fields = page_fields(group_page(self.spec), self.spec['app_id'], self.spec['target_title'])
        fields['queries']['orderbook'] = {'data': _parse_orderbook_endpoint(json.dumps(self.endpoint).encode()),
            'response_date_header': format_datetime(utc(self.at) - timedelta(seconds=1)), 'response_age_header': None}
        self.fields = fields

    def test_followup_book_boundaries_and_totals_fail_closed(self):
        alterations = [lambda b: b['rgCompactBuyOrders'].pop(),
                       lambda b: b['rgCompactBuyOrders'].__setitem__(1, -1),
                       lambda b: b['rgCompactBuyOrders'].__setitem__(0, 1.05),
                       lambda b: b.__setitem__('cBuyOrders', 1),
                       lambda b: b.__setitem__('amtMaxBuyOrder', 999),
                       lambda b: b['rgCompactBuyOrders'].__setitem__(2, 999)]
        for number, alter in enumerate(alterations):
            bad = deepcopy(self.fields)
            alter(bad['queries']['orderbook']['data'])
            with self.subTest(case=number), self.assertRaises(ValueError):
                normalize_fields(bad, self.at)

    def test_followup_currency_mismatch_is_rejected(self):
        bad = deepcopy(self.fields)
        bad['queries']['orderbook']['data']['eCurrency'] = 3
        with self.assertRaisesRegex(ValueError, 'USD'):
            normalize_fields(bad, self.at)


class SteamPublicOrderbookTimestampTests(unittest.TestCase):
    """D4: every outcome of the follow-up book's HTTP-Date-based freshness check."""

    def setUp(self):
        self.retrieved_dt = datetime(2026, 9, 23, 12, 0, 5, tzinfo=timezone.utc)
        self.retrieved = stamp(self.retrieved_dt)

    def result(self, header, age):
        return _endpoint_orderbook_timestamp(
            {'response_date_header': header, 'response_age_header': age}, self.retrieved)

    def test_plausible_date_with_no_age_uses_http_date_header(self):
        observed, source = self.result(format_datetime(self.retrieved_dt - timedelta(seconds=5)), None)
        self.assertEqual(observed, self.retrieved_dt - timedelta(seconds=5))
        self.assertEqual(source, 'http_date_header')

    def test_missing_date_falls_back_to_retrieval_time(self):
        observed, source = self.result(None, None)
        self.assertEqual(observed, self.retrieved_dt)
        self.assertEqual(source, 'retrieval_time_fallback')

    def test_unparseable_date_falls_back_to_retrieval_time(self):
        observed, source = self.result('not a date', None)
        self.assertEqual(observed, self.retrieved_dt)
        self.assertEqual(source, 'retrieval_time_fallback')

    def test_nonzero_age_header_is_rejected_as_stale(self):
        with self.assertRaisesRegex(ValueError, 'stale_orderbook_endpoint_cache'):
            self.result(format_datetime(self.retrieved_dt), '5')

    def test_zero_age_header_is_accepted(self):
        observed, source = self.result(format_datetime(self.retrieved_dt), '0')
        self.assertEqual(source, 'http_date_header')

    def test_date_after_retrieval_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unreliable_orderbook_date_header'):
            self.result(format_datetime(self.retrieved_dt + timedelta(seconds=1)), None)

    def test_date_more_than_120_seconds_before_retrieval_is_rejected_at_the_boundary(self):
        observed, source = self.result(format_datetime(self.retrieved_dt - timedelta(seconds=120)), None)
        self.assertEqual(source, 'http_date_header')
        with self.assertRaisesRegex(ValueError, 'unreliable_orderbook_date_header'):
            self.result(format_datetime(self.retrieved_dt - timedelta(seconds=121)), None)

    def test_naive_date_header_is_treated_as_utc(self):
        observed, source = self.result('Wed, 23 Sep 2026 12:00:00', None)
        self.assertEqual(observed, datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(source, 'http_date_header')


class SteamPublicGroupedParityTests(unittest.TestCase):
    """D4's parity claim and provenance parity: both paths must look equally strong,
    and a grouped capture must behave identically to a commodity one downstream."""

    def setUp(self):
        self.spec = fixture('slate_group')
        self.endpoint = fixture('slate_orderbook_endpoint')
        self.commodity = fixture('fracture_case')

    def _fields(self, title):
        fields = page_fields(group_page(self.spec), self.spec['app_id'], title)
        if fields['queries']['orderbook'] is None:
            at = utc(self.spec['source']['retrieved_at'])
            fields['queries']['orderbook'] = {'data': _parse_orderbook_endpoint(json.dumps(self.endpoint).encode()),
                'response_date_header': format_datetime(at - timedelta(seconds=1)), 'response_age_header': None}
        return fields

    def test_book_timestamp_kind_and_cache_age_labels_match_across_all_paths(self):
        commodity_result = normalize_fields(self.commodity['fields'], self.commodity['source']['retrieved_at'])
        fallback_result = normalize_fields(self._fields(self.spec['fallback_title']), self.spec['source']['retrieved_at'])
        target_result = normalize_fields(self._fields(self.spec['target_title']), self.spec['source']['retrieved_at'])
        for result, expected_source in ((commodity_result, 'ssr_query_dataUpdatedAt'),
                                        (fallback_result, 'ssr_query_dataUpdatedAt'),
                                        (target_result, 'http_date_header')):
            self.assertEqual(result['provenance']['book_timestamp_kind'], 'steam_server_query_observed_at')
            self.assertEqual(result['provenance']['underlying_market_cache_age'], 'not_exposed')
            self.assertEqual(result['provenance']['book_timestamp_source'], expected_source)

    def test_commodity_path_behaviour_is_unchanged_by_the_grouped_path(self):
        result = normalize_fields(self.commodity['fields'], self.commodity['source']['retrieved_at'])
        self.assertEqual(result['provenance']['book_timestamp_source'], 'ssr_query_dataUpdatedAt')

    def test_grouped_capture_preserves_multilevel_depth_for_prediction(self):
        result = normalize_fields(self._fields(self.spec['target_title']), self.spec['source']['retrieved_at'])
        self.assertEqual(result['provenance']['book_quantity_semantics'], 'incremental')
        capture = {'payload': result}
        steam, _ = _steam_observation(capture, self.spec['target_title'], self.spec['source']['retrieved_at'], 14400)
        bid = _steam_book(capture, steam, 'bid')
        self.assertGreater(len(bid['levels']), 1)

    def test_grouped_capture_survives_independent_prediction_revalidation_and_can_go_stale(self):
        result = normalize_fields(self._fields(self.spec['target_title']), self.spec['source']['retrieved_at'])
        capture = {'payload': result}
        _steam_observation(capture, self.spec['target_title'], self.spec['source']['retrieved_at'], 14400)
        far_future = stamp(utc(self.spec['source']['retrieved_at']) + timedelta(hours=10))
        with self.assertRaisesRegex(ValueError, 'stale_steam_book'):
            _steam_observation(capture, self.spec['target_title'], far_future, 14400)


class SteamPublicBoundsTests(unittest.TestCase):
    """LIMIT and the SSR-block-uniqueness guard, deliberately asserted rather than assumed."""

    def test_limit_is_a_deliberate_8_mebibyte_bound(self):
        self.assertEqual(LIMIT, 8 * 1024 * 1024)

    def test_oversized_decoded_page_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'steam_page_too_large'):
            page_fields(b'x' * (LIMIT + 1), 730, 'Fracture Case')

    def test_oversized_body_is_rejected_when_not_compressed(self):
        from arbitrage_v2.steam_public import _read_body

        class Resp:
            def __init__(self, body):
                self._remaining = body

            def read1(self, n):
                chunk, self._remaining = self._remaining[:n], self._remaining[n:]
                return chunk

        with self.assertRaisesRegex(ValueError, 'steam_page_too_large'):
            _read_body(Resp(b'x' * (LIMIT + 1)))

    def test_oversized_body_is_rejected_after_gzip_decompression(self):
        from arbitrage_v2.steam_public import _read_body

        class Resp:
            def __init__(self, body):
                self._remaining = body

            def read1(self, n):
                chunk, self._remaining = self._remaining[:n], self._remaining[n:]
                return chunk

        compressed = gzip.compress(b'y' * (LIMIT + 1))
        self.assertLess(len(compressed), LIMIT)  # the encoded body alone is well under LIMIT
        with self.assertRaisesRegex(ValueError, 'steam_page_too_large'):
            _read_body(Resp(compressed))

    def test_ambiguous_ssr_loader_data_is_rejected(self):
        spec = fixture('slate_group')
        body = group_page(spec)
        doubled = body.replace(b'window.SSR.renderContext',
                                b'window.SSR.loaderData = [];window.SSR.renderContext')
        with self.assertRaisesRegex(ValueError, 'missing_or_ambiguous_steam_page_data'):
            page_fields(doubled, spec['app_id'], spec['target_title'])


class GroupCaptureResponse(BytesIO):
    status = 200

    def __init__(self, body, url, extra_headers=None):
        super().__init__(body)
        self.url = url
        # capture_public's retrieved_at is real wall-clock time, not a fixture clock,
        # so the Date header must track it rather than a fixed string.
        self.headers = dict({'Date': format_datetime(datetime.now(timezone.utc)),
                             'Set-Cookie': 'DO_NOT_ARCHIVE'}, **(extra_headers or {}))


class SteamPublicGroupedCaptureTests(unittest.TestCase):
    """End-to-end capture_public over a grouped page: request counts, the follow-up's
    own redirect guard, and archive hygiene, exercised the same way the commodity
    path already is in SteamPublicTests."""

    def setUp(self):
        self.spec = fixture('slate_group')
        self.endpoint = fixture('slate_orderbook_endpoint')
        self.app_id = self.spec['app_id']
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Journal(Path(self.tmp.name)/'journal.sqlite3')
        self.journal.initialize()

    def opener(self, followup_url_override=None, followup_body=None):
        calls = []
        def fetch(request, timeout=None):
            calls.append(request.full_url)
            if '/market/orderbook' in request.full_url:
                body = followup_body if followup_body is not None else json.dumps(self.endpoint).encode()
                return GroupCaptureResponse(body, followup_url_override or request.full_url)
            return GroupCaptureResponse(group_page(self.spec), listing_url(self.app_id, self.spec['target_title']))
        return SimpleNamespace(open=fetch), calls

    def test_non_fallback_capture_issues_exactly_one_followup_and_uses_its_book(self):
        opener, calls = self.opener()
        result = capture_public(self.journal, self.app_id, self.spec['target_title'], opener)
        self.assertIsNone(result['error'])
        attempts = self.journal.records('request_attempt')
        self.assertEqual([a['kind'] for a in attempts], ['details', 'details_followup'])
        record = self.journal.get(result['record_id'], 'capture')
        book = record['payload']['result']['histogram']
        self.assertEqual(book['buyOrders'][0], {'price': '1.05', 'quantity': 2})
        self.assertEqual(book['sellOrders'][0], {'price': '1.11', 'quantity': 1})
        followup_calls = [u for u in calls if '/market/orderbook' in u]
        self.assertEqual(len(followup_calls), 1)
        self.assertEqual(followup_calls[0], orderbook_url(self.app_id, self.spec['target_title']))

    def test_fallback_capture_uses_embedded_book_and_issues_no_followup(self):
        opener, calls = self.opener()
        result = capture_public(self.journal, self.app_id, self.spec['fallback_title'], opener)
        self.assertIsNone(result['error'])
        attempts = self.journal.records('request_attempt')
        self.assertEqual([a['kind'] for a in attempts], ['details'])
        record = self.journal.get(result['record_id'], 'capture')
        book = record['payload']['result']['histogram']
        self.assertEqual(book['buyOrders'][0], {'price': '1.50', 'quantity': 3})
        self.assertEqual(record['payload']['provenance']['book_timestamp_source'], 'ssr_query_dataUpdatedAt')
        self.assertEqual([u for u in calls if '/market/orderbook' in u], [])

    def test_followup_response_redirected_off_surface_is_rejected(self):
        # unexpected_steam_redirect is not one of the five named parser errors, so it
        # collapses to the generic string exactly as the listing page's own redirect
        # guard already does; that string is still item-scoped (see failure_scope).
        opener, calls = self.opener(followup_url_override='https://evil.example.com/market/orderbook')
        result = capture_public(self.journal, self.app_id, self.spec['target_title'], opener)
        self.assertEqual(result['error'], 'invalid_or_unsupported_steam_page')
        request = {'provider': 'steam_public', 'kind': 'details', 'app_id': self.app_id, 'title': self.spec['target_title']}
        self.assertEqual(failure_scope(request, {'error': result['error'], 'status': None}), 'item')

    def test_grouped_capture_archives_only_market_fields_plus_fingerprint(self):
        opener, calls = self.opener()
        result = capture_public(self.journal, self.app_id, self.spec['target_title'], opener)
        record = self.journal.get(result['record_id'], 'capture')
        dumped = json.dumps(record)
        self.assertNotIn('DO_NOT_ARCHIVE', dumped)
        self.assertEqual(len(record['source_provenance']['decoded_body_sha256']), 64)
        self.assertNotIn('cookie', {k.lower() for k in record['source_provenance']})


class _ClockFollowingResponse(BytesIO):
    """Like GroupCaptureResponse, but its Date header tracks whatever datetime.now()
    steam_public itself currently resolves to -- including a patched, fake clock --
    so a test can move time forward between two capture_public calls without the
    follow-up's own D4 freshness check seeing a mismatched, unrelated 'now'."""
    status = 200

    def __init__(self, body, url):
        super().__init__(body)
        self.url = url
        self.headers = {'Date': format_datetime(steam_public_module.datetime.now(timezone.utc)),
                         'Set-Cookie': 'DO_NOT_ARCHIVE'}


class SteamPublicGroupPageCacheTests(unittest.TestCase):
    """Stage 3: run-local reuse of a decoded grouped page across every bucket title in
    its family, through the GroupPageCache a CollectionBatch would own and thread in
    via collection_transport.request_context."""

    def setUp(self):
        self.spec = fixture('slate_group')
        self.endpoint = fixture('slate_orderbook_endpoint')
        self.app_id = self.spec['app_id']
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Journal(Path(self.tmp.name)/'journal.sqlite3')
        self.journal.initialize()
        self.cache = GroupPageCache()

    def _family_spec(self, n):
        fallback, target = f'Family {n} Skin (Factory New)', f'Family {n} Skin (Field-Tested)'
        description = lambda title: {'appid': self.app_id, 'market_hash_name': title,
                                      'commodity': False, 'marketable': True}
        return {'app_id': self.app_id, 'page_currency': 1, 'fallback_title': fallback, 'target_title': target,
            'buckets': [{'bucket_id': fallback, 'filters': [['Quality', 'normal'], ['Exterior', 'WearCategory0']]},
                        {'bucket_id': target, 'filters': [['Quality', 'normal'], ['Exterior', 'WearCategory2']]}],
            'descriptions': {fallback: description(fallback), target: description(target)},
            'fallback_orderbook': {'data_updated_at_ms': 1758628800000,
                'data': {'eCurrency': 1, 'amtMaxBuyOrder': 150, 'amtMinSellOrder': 160,
                         'cBuyOrders': 3, 'cSellOrders': 3, 'rgCompactBuyOrders': [150, 3], 'rgCompactSellOrders': [160, 3]}}}

    def opener(self, spec=None, followup_body=None):
        spec = spec or self.spec
        calls = []
        def fetch(request, timeout=None):
            calls.append(request.full_url)
            if '/market/orderbook' in request.full_url:
                body = followup_body if followup_body is not None else json.dumps(self.endpoint).encode()
                return _ClockFollowingResponse(body, request.full_url)
            return _ClockFollowingResponse(group_page(spec), listing_url(self.app_id, spec['target_title']))
        return SimpleNamespace(open=fetch), calls

    def capture(self, title, spec=None, followup_body=None, use_cache=True):
        opener, calls = self.opener(spec, followup_body)
        if use_cache:
            with request_context(lambda r: None, lambda: 999, cache=self.cache):
                result = capture_public(self.journal, self.app_id, title, opener)
        else:
            result = capture_public(self.journal, self.app_id, title, opener)
        return result, calls

    def test_second_title_in_family_is_served_without_a_second_page_request(self):
        first, first_calls = self.capture(self.spec['target_title'])
        self.assertIsNone(first['error'])
        self.assertTrue(any('/market/listings/' in u for u in first_calls))
        second, second_calls = self.capture(self.spec['fallback_title'])
        self.assertIsNone(second['error'])
        self.assertFalse(any('/market/listings/' in u for u in second_calls))
        attempts = self.journal.records('request_attempt')
        self.assertEqual([a['kind'] for a in attempts], ['details', 'details_followup', 'details_followup'])

    def test_second_titles_book_and_identity_are_its_own_not_the_firsts(self):
        first, _ = self.capture(self.spec['target_title'])
        second, _ = self.capture(self.spec['fallback_title'])
        first_record = self.journal.get(first['record_id'], 'capture')
        second_record = self.journal.get(second['record_id'], 'capture')
        self.assertEqual(first_record['payload']['result']['item']['marketName'], self.spec['target_title'])
        self.assertEqual(second_record['payload']['result']['item']['marketName'], self.spec['fallback_title'])
        # A derived fallback-bucket capture must never fall back to the *embedded*
        # fallback book (1.50/1.60 in this fixture) -- both here come from the fresh
        # follow-up endpoint (1.05/1.11), proving reuse never mixes up whose book it is.
        for record in (first_record, second_record):
            self.assertEqual(record['payload']['result']['histogram']['buyOrders'][0], {'price': '1.05', 'quantity': 2})
            self.assertEqual(record['payload']['provenance']['book_timestamp_source'], 'http_date_header')

    def test_title_in_a_different_family_is_a_cache_miss_and_fetches_its_own_page(self):
        other = self._family_spec(1)
        first, first_calls = self.capture(self.spec['target_title'])
        second, second_calls = self.capture(other['target_title'], other)
        self.assertIsNone(first['error'])
        self.assertIsNone(second['error'])
        self.assertTrue(any('/market/listings/' in u for u in second_calls))
        attempts = self.journal.records('request_attempt')
        self.assertEqual([a['kind'] for a in attempts], ['details', 'details_followup', 'details', 'details_followup'])

    def test_stattrak_bucket_fails_from_the_cache_path_with_the_same_reason_as_the_fresh_path(self):
        first, _ = self.capture(self.spec['target_title'])
        second, second_calls = self.capture(self.spec['stattrak_title'])
        self.assertEqual(second['error'], 'unsupported_item_quality')
        self.assertFalse(any('/market/listings/' in u for u in second_calls))
        request = {'provider': 'steam_public', 'kind': 'details', 'app_id': self.app_id, 'title': self.spec['stattrak_title']}
        self.assertEqual(failure_scope(request, {'error': second['error'], 'status': None}), 'item')

    def test_title_present_in_cached_buckets_but_missing_its_own_description_fails_item_scoped_without_a_refetch(self):
        spec = deepcopy(self.spec)
        ghost_title = 'AK-47 | Slate (Well-Worn)'
        spec['buckets'].append({'bucket_id': ghost_title, 'filters': [['Quality', 'normal'], ['Exterior', 'WearCategory4']]})
        first, _ = self.capture(spec['target_title'], spec)
        self.assertIsNone(first['error'])
        second, second_calls = self.capture(ghost_title, spec)
        # missing_or_failed_steam_market_query is not one of the five named parser
        # errors, so -- like the redirect guard -- it collapses to the generic string,
        # which is still item-scoped; see the equivalent assertion in
        # SteamPublicGroupedCaptureTests.test_followup_response_redirected_off_surface_is_rejected.
        self.assertEqual(second['error'], 'invalid_or_unsupported_steam_page')
        self.assertFalse(any('/market/listings/' in u for u in second_calls))
        request = {'provider': 'steam_public', 'kind': 'details', 'app_id': self.app_id, 'title': ghost_title}
        self.assertEqual(failure_scope(request, {'error': second['error'], 'status': None}), 'item')

    def test_derived_capture_keeps_the_original_pages_retrieval_time_even_after_the_wall_clock_moves_on(self):
        class FakeDateTime(datetime):
            fixed_now = datetime(2026, 9, 23, 12, 0, 5, tzinfo=timezone.utc)
            @classmethod
            def now(cls, tz=None):
                return cls.fixed_now

        with patch('arbitrage_v2.steam_public.datetime', FakeDateTime):
            FakeDateTime.fixed_now = datetime(2026, 9, 23, 12, 0, 5, tzinfo=timezone.utc)
            first, _ = self.capture(self.spec['target_title'])
            FakeDateTime.fixed_now = datetime(2026, 9, 23, 13, 5, 5, tzinfo=timezone.utc)  # +1h5m
            second, _ = self.capture(self.spec['fallback_title'])
        first_record = self.journal.get(first['record_id'], 'capture')
        second_record = self.journal.get(second['record_id'], 'capture')
        self.assertTrue(second_record['source_provenance']['derived_from_cached_page'])
        self.assertEqual(second_record['source_provenance']['page_retrieved_at'], first_record['retrieved_at'])
        self.assertEqual(second_record['source_provenance']['decoded_body_sha256'],
                          first_record['source_provenance']['decoded_body_sha256'])
        self.assertEqual(second_record['source_provenance']['final_url'], first_record['source_provenance']['final_url'])
        # The capture record's own retrieved_at reflects the moment of reuse, not a
        # copy of the page's original retrieval time -- the two must differ here.
        self.assertEqual(second_record['retrieved_at'], stamp(FakeDateTime.fixed_now))
        self.assertNotEqual(second_record['retrieved_at'], second_record['source_provenance']['page_retrieved_at'])

    def test_derived_capture_survives_independent_revalidation_and_orders_correctly_against_its_sibling(self):
        first, _ = self.capture(self.spec['target_title'])
        second, _ = self.capture(self.spec['fallback_title'])
        second_record = self.journal.get(second['record_id'], 'capture')
        steam, observed = _steam_observation(second_record, self.spec['fallback_title'], second_record['retrieved_at'], 14400)
        bid = _steam_book(second_record, steam, 'bid')
        self.assertGreaterEqual(bid['price_cents'], 0)
        from arbitrage_v2.capture_selection import select_captures
        rows = [dict(self.journal.get(r['record_id'], 'capture'), record_id=r['record_id'], _recorded_at=stamp(utc(second_record['retrieved_at'])))
                for r in (first, second)]
        selected = select_captures(rows, second_record['retrieved_at'])
        self.assertEqual(selected[(self.app_id, self.spec['fallback_title'], 'details')]['record_id'], second['record_id'])

    def test_cache_capacity_evicts_the_least_recently_used_page(self):
        self.cache = GroupPageCache(capacity=2)
        families = [self._family_spec(n) for n in range(3)]
        fetched = lambda calls: any('/market/listings/' in u for u in calls)

        _, calls = self.capture(families[0]['target_title'], families[0])
        self.assertTrue(fetched(calls))
        _, calls = self.capture(families[1]['target_title'], families[1])
        self.assertTrue(fetched(calls))  # cache now holds families 0 and 1, at capacity
        _, calls = self.capture(families[0]['fallback_title'], families[0])
        self.assertFalse(fetched(calls))  # hit; promotes family 0 to most-recently-used
        _, calls = self.capture(families[2]['target_title'], families[2])
        self.assertTrue(fetched(calls))  # miss; evicts the least-recently-used, family 1
        # Check the still-cached page first: a hit never mutates the cache (only a
        # miss's insert can evict), so this assertion is safe to make before the next
        # one, which is itself a miss that would disturb the order further.
        _, calls = self.capture(families[0]['fallback_title'], families[0])
        self.assertFalse(fetched(calls))  # family 0 is still retained: still a hit
        _, calls = self.capture(families[1]['fallback_title'], families[1])
        self.assertTrue(fetched(calls))  # family 1 was evicted: this refetches its page

    def test_two_entries_sharing_a_bucket_title_resolve_to_the_newest_page(self):
        # Reachable when one family is fetched twice with differing bucket lists: an
        # oldest-first scan would serve the stale page's description/history rows.
        shared = 'Shared Skin (Field-Tested)'
        def page_for(extra):
            buckets = [{'bucket_id': shared, 'filters': [['Quality', 'normal']]},
                       {'bucket_id': extra, 'filters': [['Quality', 'normal']]}]
            return {'page_currency': 1, 'bCommodity': False, 'queries': [],
                    'listing_row': {'appid': self.app_id, 'success': True, 'bCommodity': False,
                                    'initialFallbackBucketID': shared, 'buckets': buckets}}
        self.cache.put(self.app_id, page_for('Old Sibling'), '2026-09-24T09:00:00Z', 'https://old', 'sha-old')
        self.cache.put(self.app_id, page_for('New Sibling'), '2026-09-24T10:00:00Z', 'https://new', 'sha-new')
        entry = self.cache.get(self.app_id, shared)
        self.assertEqual(entry.decoded_body_sha256, 'sha-new')
        self.assertEqual(entry.retrieved_at, '2026-09-24T10:00:00Z')
        self.assertEqual(entry.final_url, 'https://new')
        # A title only the older page carries still resolves from that older page.
        self.assertEqual(self.cache.get(self.app_id, 'Old Sibling').decoded_body_sha256, 'sha-old')

    def test_capture_without_a_request_context_never_caches(self):
        first, first_calls = self.capture(self.spec['target_title'], use_cache=False)
        second, second_calls = self.capture(self.spec['fallback_title'], use_cache=False)
        self.assertIsNone(first['error'])
        self.assertIsNone(second['error'])
        self.assertTrue(any('/market/listings/' in u for u in first_calls))
        self.assertTrue(any('/market/listings/' in u for u in second_calls))

    def test_commodity_captures_neither_populate_nor_consult_the_cache(self):
        commodity = fixture('fracture_case')
        opener = SimpleNamespace(open=lambda request, timeout=None:
            _ClockFollowingResponse(page(commodity['fields']), listing_url(730, 'Fracture Case')))
        with request_context(lambda r: None, lambda: 999, cache=self.cache):
            result = capture_public(self.journal, 730, 'Fracture Case', opener)
        self.assertIsNone(result['error'])
        self.assertEqual(len(self.cache._entries), 0)

    def test_no_cache_object_ever_reaches_the_journal(self):
        first, _ = self.capture(self.spec['target_title'])
        second, _ = self.capture(self.spec['fallback_title'])
        for result in (first, second):
            record = self.journal.get(result['record_id'], 'capture')
            dumped = json.dumps(record)  # raises if a raw cache object ever leaked in
            self.assertNotIn('GroupPageCache', dumped)
            self.assertNotIn('GroupPageEntry', dumped)
