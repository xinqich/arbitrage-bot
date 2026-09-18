from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

from arbitrage_v2.collector import collect_once, history_points, history_summary
from arbitrage_v2.collection_lock import collection_lock
from arbitrage_v2.journal import Journal
from arbitrage_v2.prediction import _steam_observation
from arbitrage_v2.research_cli import run
from arbitrage_v2.steam_public import (ListingRedirect, capture_public, listing_url,
                                     normalize_fields, page_fields)

ROOT = Path(__file__).resolve().parents[1]


def fixture(name='fracture_case'):
    return json.loads((ROOT/'tests/fixtures'/('steam_public_'+name+'.json')).read_text(encoding='utf-8'))


def page(fields):
    # Minimal page envelope with recorded market fields and unrelated private-looking
    # values that must not enter the archive. No JavaScript is executed by the parser.
    loaders = [json.dumps({'steamid': '0', 'sessionid': 'DO_NOT_ARCHIVE'}),
               json.dumps({'filterConfig': {'currency': {'eCurrency': fields['page_currency']}}}),
               json.dumps({'success': True, 'appid': fields['app_id'], 'bCommodity': True})]
    queries = [{'queryKey': ['market', kind, fields['app_id'], fields['title']],
                'state': {'data': row['data'], 'dataUpdatedAt': row['data_updated_at_ms'],
                          'status': 'success', 'error': None}} for kind, row in fields['queries'].items()]
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

    def test_wrong_currency_at_any_level_is_rejected(self):
        for where in ('page','book','history'):
            bad = deepcopy(self.fields)
            if where == 'page':bad['page_currency'] = 3
            elif where == 'book':bad['queries']['orderbook']['data']['eCurrency'] = 3
            else:bad['queries']['pricehistory']['data']['ecurrency'] = 3
            with self.subTest(where=where),self.assertRaisesRegex(ValueError,'USD'):
                normalize_fields(bad,self.at)

    def test_identity_and_commodity_are_checked(self):
        for key,value in [('market_hash_name','Recoil Case'),('appid',570),('commodity',None)]:
            bad = deepcopy(self.fields)
            bad['queries']['description']['data'][key] = value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'identity'):
                normalize_fields(bad,self.at)
        with self.assertRaises(ValueError):listing_url(570,'Fracture Case')

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
        with self.assertRaisesRegex(ValueError,'conflicting_history'):
            normalize_fields(bad,self.at)

    def test_redirects_cannot_leave_public_listing_surface(self):
        redirect = ListingRedirect()
        request = Request(listing_url(730,'Fracture Case'))
        for target in ('https://example.com/market/listings/730/x',
                       'https://steamcommunity.com/login',
                       'http://steamcommunity.com/market/listings/730/x'):
            self.assertIsNone(redirect.redirect_request(request,None,302,'',{},target))
        self.assertIsNotNone(redirect.redirect_request(request,None,302,'',{},Response.url))

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

    def test_public_allowance_is_persistent_and_independent_of_paid_source(self):
        self.journal.append('request_attempt',{'provider':'steam_public'})
        watch = {'items':[{'app_id':730,'title':'Fracture Case'}], 'steam_source':'steam_public',
                 'steam_public_request_allowance':1,'total_request_allowance':10}
        with patch('arbitrage_v2.steam_public.capture_public') as public,patch('arbitrage_v2.collector.capture') as other:
            other.return_value = {'status':200,'error':None}
            self.assertEqual(len(collect_once(self.journal,watch,{})),2)
            public.assert_not_called()
        watch['steam_public_request_allowance']=2
        self.journal.append('request_attempt',{'provider':'steamapis'})
        with patch('arbitrage_v2.steam_public.capture_public') as public,patch('arbitrage_v2.collector.capture') as other:
            public.return_value = other.return_value = {'status':200,'error':None}
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
