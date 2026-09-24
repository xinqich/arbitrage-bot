"""Failure isolation and evidence replay, using only isolated fixture journals."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_paper_entry as entry_fixture
import test_worker as worker_fixture
from arbitrage_v2.collection_batch import CollectionBatch, failure_scope, request_for, scope_key
from arbitrage_v2.discovery import capture_index, snapshot
from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.journal import Journal
from arbitrage_v2.prediction import market_snapshot
from arbitrage_v2.paper import step
from arbitrage_v2.steam_public import _NAMED_PARSE_ERRORS
from arbitrage_v2.worker import latest


class FailureScopeTests(unittest.TestCase):
    def setUp(self):
        self.f = worker_fixture.WorkerTests()
        self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_named_steam_public_parse_errors_are_item_scoped_and_stay_in_sync(self):
        import arbitrage_v2.steam_public as steam_public
        self.assertIs(_NAMED_PARSE_ERRORS, steam_public._NAMED_PARSE_ERRORS)
        request = {'provider': 'steam_public', 'kind': 'details', 'app_id': 730, 'title': 'x'}
        for error in _NAMED_PARSE_ERRORS:
            with self.subTest(error=error):
                self.assertEqual(failure_scope(request, {'error': error, 'status': None}), 'item')

    def test_details_followup_failures_are_item_scoped_but_provider_failures_still_defer(self):
        followup = {'provider': 'steam_public', 'kind': 'details_followup', 'app_id': 730, 'title': 'x'}
        self.assertEqual(failure_scope(followup, {'error': 'malformed_orderbook_endpoint_response', 'status': None}), 'item')
        self.assertEqual(failure_scope(followup, {'error': 'http_503', 'status': 503}), 'provider')
        self.assertEqual(failure_scope(followup, {'error': 'http_429', 'status': 429}), 'provider')

    def batch(self, fetch, states=None):
        f=self.f
        return CollectionBatch(f.journal,f.watch,f.config,{},states or {},f.clock,lambda:False,fetch)

    def test_bad_skin_does_not_block_case_and_stop_survives_restart(self):
        f=self.f
        bad=request_for(f.watch,730,'AK-47 | Slate (Field-Tested)','details')
        good=request_for(f.watch,730,'Example Case','details')
        calls=[]
        def fetch(j,r,k):
            calls.append(r)
            return {'status':200,'error':'invalid_or_unsupported_steam_page' if r==bad else None}
        batch=self.batch(fetch);batch.ensure([bad,good])
        self.assertEqual(calls,[bad,good])
        item_key=scope_key(bad,'item')
        self.assertEqual(batch.sources[item_key]['scope'],'item')
        restarted=self.batch(fetch,batch.sources);restarted.ensure([bad,good])
        self.assertEqual(calls,[bad,good,good])
        self.assertIn(item_key,restarted.sources)

    def test_legacy_item_block_is_narrowed_but_not_forgotten(self):
        f=self.f
        bad=request_for(f.watch,730,'Bad Skin','details')
        states={'steam_public':dict(bad,status=200,error='invalid_or_unsupported_steam_page',
                                   state='blocked',next_retry_at=None,retry_count=1)}
        calls=[]
        batch=self.batch(lambda j,r,k:(calls.append(r) or {'status':200,'error':None}),states)
        good=request_for(f.watch,730,'Example Case','details')
        batch.ensure([bad,good])
        self.assertEqual(calls,[good])
        self.assertIn(scope_key(bad,'item'),batch.sources)

    def test_endpoint_denial_does_not_block_other_dmarket_endpoint(self):
        f=self.f;calls=[]
        def fetch(j,r,k):
            calls.append(r)
            return {'status':403,'error':'http_403'} if r['kind']=='offers' else {'status':200,'error':None}
        batch=self.batch(fetch)
        batch.ensure([request_for(f.watch,730,title,kind) for title,kind in
                      [('Bad Skin','offers'),('Example Case','offers'),('Example Case','targets')]])
        self.assertEqual([r['kind'] for r in calls],['offers','targets'])
        self.assertEqual(batch.sources['dmarket:offers']['scope'],'endpoint')

    def test_provider_throttling_still_blocks_other_items_and_endpoints(self):
        f=self.f;calls=[]
        def fetch(j,r,k):
            calls.append(r)
            return {'status':429,'error':'http_429'} if r['provider']=='dmarket' else {'status':200,'error':None}
        batch=self.batch(fetch)
        batch.ensure([request_for(f.watch,730,'Example Case',k) for k in ('offers','targets','details')])
        self.assertEqual([r['provider'] for r in calls],['dmarket','steam_public'])
        self.assertEqual(batch.sources['dmarket']['scope'],'provider')

    def test_unsupported_research_item_does_not_change_locked_route(self):
        f=self.f;f.route();f.captures(bid_quantity=2)
        self.assertEqual(step(f.journal,'r1',f.at)['action'],'steam_sale')
        self.assertEqual(step(f.journal,'r1',f.at)['action'],'return_purchase')
        before=f.journal.records('route_event');prediction=f.journal.get(f.pred_id)
        f.watch['exploration_items']=[{'app_id':730,'title':'Unsupported Skin'}]
        def fetch(j,r,k):
            if r['title']=='Unsupported Skin' and r['kind']=='details':
                return dict(r,status=200,error='invalid_or_unsupported_steam_page')
            return f.fetch(j,r,k)
        f.worker(fetch).tick(f.at)
        self.assertFalse(latest(f.journal,'worker_health')['fatal'])
        self.assertIsNotNone(latest(f.journal,'worker_health')['next_check_at'])
        self.assertEqual(f.journal.records('route_event'),before)
        self.assertEqual(f.journal.get(f.pred_id),prediction)


class FakeSteamResponse(BytesIO):
    status = 200

    def __init__(self, body, url):
        super().__init__(body)
        self.url = url
        # capture_public's retrieved_at is real wall-clock time (datetime.now), not the
        # batch's fixture clock, so the Date header must track it, not a fixed string.
        self.headers = {'Date': format_datetime(datetime.now(timezone.utc))}


def group_page_body(app_id, fallback_title, target_title):
    """A minimal grouped-listing SSR page: target_title is a real, in-scope bucket that
    is NOT the fallback, so its order book is absent and must come from the follow-up."""
    loaders = [
        json.dumps({'filterConfig': {'currency': {'eCurrency': 1}}}),
        json.dumps({'success': True, 'appid': app_id, 'bCommodity': False,
                    'initialFallbackBucketID': fallback_title,
                    'buckets': [
                        {'bucket_id': target_title, 'filters': [['Quality', 'normal'], ['Exterior', 'WearCategory2']]},
                        {'bucket_id': fallback_title, 'filters': [['Quality', 'normal'], ['Exterior', 'WearCategory0']]},
                    ]}),
    ]
    queries = [{'queryKey': ['market', 'description', app_id, target_title],
                'state': {'status': 'success', 'error': None, 'dataUpdatedAt': 1,
                          'data': {'appid': app_id, 'market_hash_name': target_title,
                                   'commodity': False, 'marketable': True}}}]
    context = json.dumps({'queryData': json.dumps({'queries': queries})})
    return ('<html><script>window.SSR.loaderData = '+json.dumps(loaders)+';'
            'window.SSR.renderContext=JSON.parse('+json.dumps(context)+');'
            '</script></html>').encode()


ORDERBOOK_ENDPOINT_BOOK = {'eCurrency': 1, 'amtMaxBuyOrder': 100, 'amtMinSellOrder': 110,
    'cBuyOrders': 5, 'cSellOrders': 3, 'rgCompactBuyOrders': [100, 5], 'rgCompactSellOrders': [110, 3]}


def reuse_group_page_body(app_id, fallback_title, target_title):
    """A grouped-listing SSR page carrying both bucket titles' own description
    queries and the fallback's embedded order book, so either title's capture can
    succeed independently -- needed to exercise Stage 3 reuse across two titles in
    one batch, unlike group_page_body above which only supports target_title."""
    loaders = [
        json.dumps({'filterConfig': {'currency': {'eCurrency': 1}}}),
        json.dumps({'success': True, 'appid': app_id, 'bCommodity': False,
                    'initialFallbackBucketID': fallback_title,
                    'buckets': [
                        {'bucket_id': target_title, 'filters': [['Quality', 'normal'], ['Exterior', 'WearCategory2']]},
                        {'bucket_id': fallback_title, 'filters': [['Quality', 'normal'], ['Exterior', 'WearCategory0']]},
                    ]}),
    ]
    queries = [{'queryKey': ['market', 'description', app_id, title],
                'state': {'status': 'success', 'error': None, 'dataUpdatedAt': 1,
                          'data': {'appid': app_id, 'market_hash_name': title,
                                   'commodity': False, 'marketable': True}}}
               for title in (fallback_title, target_title)]
    queries.append({'queryKey': ['market', 'orderbook', app_id, fallback_title],
        'state': {'status': 'success', 'error': None, 'dataUpdatedAt': 1,
                  'data': {'eCurrency': 1, 'amtMaxBuyOrder': 150, 'amtMinSellOrder': 160,
                           'cBuyOrders': 3, 'cSellOrders': 3,
                           'rgCompactBuyOrders': [150, 3], 'rgCompactSellOrders': [160, 3]}}})
    context = json.dumps({'queryData': json.dumps({'queries': queries})})
    return ('<html><script>window.SSR.loaderData = '+json.dumps(loaders)+';'
            'window.SSR.renderContext=JSON.parse('+json.dumps(context)+');'
            '</script></html>').encode()


class GroupedFollowupTests(unittest.TestCase):
    """2B: the standalone order-book request for a grouped, non-fallback title is a
    second, visibly registered request kind, distinct from its parent 'details' request."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Journal(Path(self.tmp.name)/'journal.sqlite3')
        self.journal.initialize()
        self.target_title = 'AK-47 | Slate (Field-Tested)'
        self.fallback_title = 'AK-47 | Slate (Factory New)'
        self.watch = {'steam_source': 'steam_public', 'items': [{'app_id': 730, 'title': self.target_title}]}

    def fake_open(self, request, timeout=None):
        url = request.full_url
        if '/market/orderbook' in url:
            body = json.dumps({'data': {'success': True, 'data': ORDERBOOK_ENDPOINT_BOOK}}).encode()
        else:
            body = group_page_body(730, self.fallback_title, self.target_title)
        return FakeSteamResponse(body, url)

    def test_unlock_hook_result_during_followup_does_not_abandon_or_misclassify_the_capture(self):
        request = request_for(self.watch, 730, self.target_title, 'details')
        hook_calls = []

        def before_request():
            hook_calls.append(1)
            # Simulates check_unlocks (worker.py:305) appending an unrelated result while
            # the follow-up request is inside begin_request; before the 2B fix this made
            # request_key(followup) raise, which capture_public then swallowed as a
            # generic invalid_or_unsupported_steam_page.
            batch.results.append({'provider': 'dmarket', 'kind': 'offers', 'app_id': 730,
                                   'title': 'Unrelated Item', 'status': 200, 'error': None})

        batch = CollectionBatch(self.journal, self.watch,
            {'request_spacing_seconds': {'steam_public': 0}}, {}, {}, lambda: '2026-09-08T13:21:17Z',
            lambda: False, wait=lambda seconds: None)
        batch.before_request = before_request
        with patch('arbitrage_v2.steam_public.build_opener', return_value=type('O', (), {'open': self.fake_open})()):
            batch.ensure([request])
        self.assertGreaterEqual(len(hook_calls), 2)  # fired for both the outer and follow-up requests
        self.assertEqual(len(batch.results), 1 + len(hook_calls))
        result = next(r for r in batch.results if r.get('provider') == 'steam_public')
        self.assertIsNone(result['error'])
        self.assertEqual(result['status'], 200)
        record = self.journal.get(result['record_id'], 'capture')
        book = record['payload']['result']['histogram']
        self.assertEqual(book['buyOrders'][0], {'price': '1.00', 'quantity': 5})
        self.assertEqual(book['sellOrders'][0], {'price': '1.10', 'quantity': 3})
        self.assertEqual(record['payload']['provenance']['book_timestamp_source'], 'http_date_header')


class GroupPageReuseTests(unittest.TestCase):
    """Stage 3: run-local reuse of one grouped page across every bucket title in its
    family, exercised through CollectionBatch.ensure the same way GroupedFollowupTests
    exercises the single-title follow-up path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Journal(Path(self.tmp.name)/'journal.sqlite3')
        self.journal.initialize()
        self.target_title = 'AK-47 | Slate (Field-Tested)'
        self.fallback_title = 'AK-47 | Slate (Factory New)'
        self.watch = {'steam_source': 'steam_public', 'items': [
            {'app_id': 730, 'title': self.target_title}, {'app_id': 730, 'title': self.fallback_title}]}

    def fake_open(self, request, timeout=None):
        url = request.full_url
        if '/market/orderbook' in url:
            body = json.dumps({'data': {'success': True, 'data': ORDERBOOK_ENDPOINT_BOOK}}).encode()
        else:
            body = reuse_group_page_body(730, self.fallback_title, self.target_title)
        return FakeSteamResponse(body, url)

    def batch(self):
        return CollectionBatch(self.journal, self.watch,
            {'request_spacing_seconds': {'steam_public': 0}}, {}, {}, lambda: '2026-09-08T13:21:17Z',
            lambda: False, wait=lambda seconds: None)

    def test_two_titles_in_one_family_cost_one_page_request_and_two_followups(self):
        requests = [request_for(self.watch, 730, self.target_title, 'details'),
                    request_for(self.watch, 730, self.fallback_title, 'details')]
        batch = self.batch()
        with patch('arbitrage_v2.steam_public.build_opener', return_value=type('O', (), {'open': self.fake_open})()):
            batch.ensure(requests)
        attempts = self.journal.records('request_attempt')
        self.assertEqual([a['kind'] for a in attempts], ['details', 'details_followup', 'details_followup'])
        self.assertTrue(all(r.get('error') is None for r in batch.results))
        captures = [self.journal.get(r['record_id'], 'capture') for r in batch.results]
        self.assertEqual({c['payload']['result']['item']['marketName'] for c in captures},
                         {self.target_title, self.fallback_title})
        derived = next(c for c in captures if c['title'] == self.fallback_title)
        self.assertTrue(derived['source_provenance']['derived_from_cached_page'])

    def test_a_fresh_batch_never_inherits_a_previous_batchs_cached_pages(self):
        with patch('arbitrage_v2.steam_public.build_opener', return_value=type('O', (), {'open': self.fake_open})()):
            self.batch().ensure([request_for(self.watch, 730, self.target_title, 'details')])
            second = self.batch()
            second.ensure([request_for(self.watch, 730, self.fallback_title, 'details')])
        attempts = self.journal.records('request_attempt')
        # If the second batch had reused the first batch's cache, the fallback title
        # would have cost only a 'details_followup'; a fresh 'details' proves it did not.
        self.assertEqual([a['kind'] for a in attempts].count('details'), 2)


class CaptureFallbackTests(unittest.TestCase):
    def setUp(self):
        self.f=entry_fixture.EntryTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.title=self.f.titles[0]

    def append(self,kind,**changes):
        row=next(c for c in reversed(self.f.journal.records('capture')) if c['title']==self.title and c['kind']==kind)
        row={k:deepcopy(v) for k,v in row.items() if k not in ('record_id','_recorded_at')}
        row.update(changes)
        return self.f.journal.append('capture',row)

    def fail(self,kind,**changes):
        return self.append(kind,**dict({'status':503,'error':'http_503','payload':None},**changes))

    def test_indexed_and_direct_selection_keep_same_quote_ids_and_warning(self):
        f=self.f;before=snapshot(f.journal,self.title,f.at)
        failures=[self.fail(k) for k in ('details','offers','targets')]
        for index in (None,capture_index(f.journal,f.at)):
            result=snapshot(f.journal,self.title,f.at,index=index)
            self.assertEqual(result['books'],before['books'])
            self.assertEqual({r['record_id'] for r in result['collection_warnings']},set(failures))
        actual=market_snapshot(f.journal,self.title,f.at,14400)
        self.assertEqual(set(actual['evidence_ids']),{r['evidence_id'] for r in before['books'].values()})

    def test_failed_fetch_does_not_extend_quote_lifetime(self):
        f=self.f
        later=stamp(utc(f.at)+timedelta(hours=5))
        self.fail('details',retrieved_at=later)
        for index in (None,capture_index(f.journal,later)):
            result=snapshot(f.journal,self.title,later,index=index)
            self.assertNotIn('steam_bid',result['books'])
            self.assertIn('stale_capture',{p['reason'] for p in result['issues']})

    def test_new_empty_book_or_bad_identity_must_not_restore_old_prices(self):
        f=self.f
        row=next(c for c in f.journal.records('capture') if c['title']==self.title and c['kind']=='details')
        for change in ('empty','wrong_identity'):
            data=deepcopy(row['payload'])
            if change=='empty':data['result']['histogram']['buyOrders']=[]
            else:data['result']['item']['marketName']='Wrong Item'
            self.append('details',payload=data,revision=change)
            self.assertNotIn('steam_bid',snapshot(f.journal,self.title,f.at)['books'])

    def test_partition_boundary_is_not_crossed_on_failed_fetch(self):
        f=self.f;self.fail('details',input_kind='synthetic')
        for index in (None,capture_index(f.journal,f.at)):
            result=snapshot(f.journal,self.title,f.at,index=index)
            self.assertNotIn('steam_bid',result['books'])

    def test_future_failure_and_future_success_are_excluded_from_replay(self):
        f=self.f;before=snapshot(f.journal,self.title,f.at)
        self.fail('details',retrieved_at=stamp(utc(f.at)+timedelta(hours=1)))
        self.assertEqual(snapshot(f.journal,self.title,f.at),before)

    def test_fallback_does_not_replenish_consumed_paper_depth(self):
        f=self.f;f.start(f.request())
        f.at=stamp(utc(f.at)+timedelta(seconds=20));f.recorded_quotes()
        row=next(c for c in reversed(f.journal.records('capture')) if c['title']==self.title and c['kind']=='details')
        data=deepcopy(row['payload']);data['result']['histogram']['buyOrders'][0]['quantity']=1
        self.append('details',payload=data)
        self.assertEqual(step(f.journal,'new-paper',f.at)['action'],'steam_sale')
        before=f.journal.records('route_event')
        for kind in ('details','offers','targets'):self.fail(kind)
        self.assertEqual(step(f.journal,'new-paper',f.at)['status'],'waiting_for_new_depth')
        self.assertEqual(f.journal.records('route_event'),before)
