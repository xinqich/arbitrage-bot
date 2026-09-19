"""Failure isolation and evidence replay, using only isolated fixture journals."""
from copy import deepcopy
from datetime import timedelta
import unittest

import test_paper_entry as entry_fixture
import test_worker as worker_fixture
from arbitrage_v2.collection_batch import CollectionBatch, request_for, scope_key
from arbitrage_v2.discovery import capture_index, snapshot
from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.prediction import market_snapshot
from arbitrage_v2.paper import step
from arbitrage_v2.worker import latest


class FailureScopeTests(unittest.TestCase):
    def setUp(self):
        self.f = worker_fixture.WorkerTests()
        self.f.setUp();self.addCleanup(self.f.doCleanups)

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
