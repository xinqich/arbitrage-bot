"""Isolated fixtures only: no provider calls, real funds or live journal writes."""
from copy import deepcopy
from datetime import timedelta
from io import BytesIO
import json
import random
import time
import unittest
from unittest.mock import patch

from arbitrage_v2 import catalogue, search_rules
from arbitrage_v2.collection_batch import CollectionBatch, request_for
from arbitrage_v2.discovery import screen, snapshot, _inputs, capture_index
from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.identity import base_offer, general_order, known_item
from arbitrage_v2.journal import Journal
from arbitrage_v2.paper_entry import preview, enter
from arbitrage_v2.paper import settings, required_evidence
from arbitrage_v2.prediction import calculate, qualified_identity
from arbitrage_v2.worker import search, Worker, latest
import test_paper_entry as fixture
import test_worker as worker_fixture
import test_real_growth as real_fixture


class SearchTests(unittest.TestCase):
    recorded_quotes=fixture.EntryTests.recorded_quotes
    def setUp(self):
        fixture.EntryTests.setUp(self)

    def change(self,title,kind,fn):
        row=deepcopy(next(r for r in reversed(self.journal.records('capture')) if r['title']==title and r['kind']==kind))
        row.pop('record_id');row.pop('_recorded_at');fn(row)
        row['revision']=len(self.journal.records('capture'));self.journal.append('capture',row)

    def valid_books(self):
        for title in self.titles:
            self.change(title,'details',lambda c:c['payload']['result']['histogram'].update(
                buyOrders=[{'price':'0.95','quantity':2},{'price':'0.80','quantity':18}],
                sellOrders=[{'price':'1.00','quantity':20}]))
            self.change(title,'offers',lambda c:[r.update(priceCents=100) for r in c['payload']['items']])
            self.change(title,'targets',lambda c:c['payload'].update(orders=[
                {'title':c['title'],'attributes':{},'price':'95','amount':'2'},
                {'title':c['title'],'attributes':{},'price':'80','amount':'18'}]))

    def scan(self,**kw):return screen(self.journal,self.watch,self.policy,self.at,**kw)

    def test_minimum_boundary_applies_to_correct_purchase_venue(self):
        for price,allowed in [(9,False),(10,True)]:
            self.change('Example Case','offers',lambda c:[r.update(priceCents=price) for r in c['payload']['items']])
            rows=self.scan()['predictions']
            self.assertEqual(any(p['item_a']=='Example Case' for p in rows),allowed)
        self.change('Return Case','details',lambda c:c['payload']['result']['histogram'].update(sellOrders=[{'price':'0.09','quantity':20}]))
        self.assertFalse(any(p['item_b']=='Return Case' for p in self.scan()['predictions']))
        self.change('Return Case','details',lambda c:c['payload']['result']['histogram'].update(sellOrders=[{'price':'0.10','quantity':20}]))
        self.assertTrue(any(p['item_b']=='Return Case' for p in self.scan()['predictions']))

    def test_nine_combinations_and_weakest_leg_sorting(self):
        self.valid_books();rows=self.scan()['predictions']
        self.assertEqual(len({tuple(p['sale_scenarios'].values()) for p in rows}),9)
        self.assertEqual([search_rules.sort_key(p) for p in rows],sorted(search_rules.sort_key(p) for p in rows))
        for p in rows:self.assertEqual(p['route_priority'],max(q['priority'] for q in p['sale_quality'].values()))
        self.assertEqual({p['route_priority'] for p in rows},{1,2,3,4})

    def test_batch_spread_uses_worst_used_bid_and_strict_threshold(self):
        self.valid_books();rows=self.scan()['predictions']
        small=next(p for p in rows if p['quantity_a']==2 and p['sale_scenarios']['steam']=='current_bids')
        large=next(p for p in rows if p['quantity_a']==3 and p['sale_scenarios']['steam']=='current_bids')
        self.assertEqual(small['sale_quality']['steam']['priority'],1)
        self.assertEqual(large['sale_quality']['steam']['best_spread_pct'],5)
        self.assertEqual(large['sale_quality']['steam']['batch_spread_pct'],20)
        self.assertEqual(large['sale_quality']['steam']['priority'],2)
        self.change('Example Case','details',lambda c:c['payload']['result']['histogram'].update(buyOrders=[{'price':'0.90','quantity':20}]))
        row=next(p for p in self.scan()['predictions'] if p['item_a']=='Example Case' and p['sale_scenarios']['steam']=='current_bids')
        self.assertEqual(row['sale_quality']['steam']['priority'],2)

    def test_smaller_return_batch_is_retained_by_priority(self):
        self.valid_books()
        self.change('Example Case','details',lambda c:c['payload']['result']['histogram'].update(buyOrders=[{'price':'5.00','quantity':20}],sellOrders=[{'price':'5.01','quantity':20}]))
        rows=[p for p in self.scan()['predictions'] if p['item_a']=='Example Case' and p['item_b']=='Return Case' and p['quantity_a']==1 and set(p['sale_scenarios'].values())=={'current_bids'}]
        self.assertEqual({p['quantity_b'] for p in rows},{2,4})
        self.assertEqual({p['route_priority'] for p in rows},{1,2})
        for p in rows:
            r=preview(self.journal,p['prediction_id'],self.mandate,self.policy,self.at)
            self.assertTrue(r['ready'],r['blockers'])

    def test_absent_ask_only_lowers_bid_priority_not_excludes(self):
        self.valid_books();self.change('Example Case','details',lambda c:c['payload']['result']['histogram'].update(sellOrders=[]))
        rows=[p for p in self.scan()['predictions'] if p['item_a']=='Example Case']
        self.assertTrue(rows);self.assertTrue(all(p['sale_scenarios']['steam']=='current_bids' for p in rows))
        self.assertTrue(all(p['sale_quality']['steam']['priority']==2 for p in rows))

    def test_all_item_types_and_paper_entry(self):
        self.titles=['Sticker | New Sticker','Special Agent | Team','Music Kit | Example, Track',
                     'StatTrak™ AK-47 | Example (Field-Tested)','Souvenir AWP | Example (Field-Tested)']
        self.recorded_quotes(3)
        self.watch['items']=[{'app_id':730,'title':t} for t in self.titles]
        rows=self.scan()['predictions'];self.assertEqual({p['item_a'] for p in rows},set(self.titles))
        p=next(p for p in rows if p['item_a']==self.titles[0] and p['item_b']==self.titles[3]
               and p['engine_version']=='observed-depth-v2' and p['predicted_net_cents']>0)
        enter(self.journal,{'route_id':'noncase','prediction_id':p['prediction_id']},self.mandate,self.policy,self.at)
        cfg=settings(self.journal,'noncase');self.assertEqual(cfg['search_settings'],search_rules.DEFAULTS)
        self.assertTrue(known_item(self.journal,self.titles[3]))
        self.assertFalse(known_item(self.journal,'AK-47 | Example (Field-Tested)'))

    def test_general_targets_and_base_decorated_offers(self):
        self.assertTrue(general_order({}));self.assertTrue(general_order({'phase':'any','paintSeed':'any','floatPartValue':'any'}))
        for attrs in ({'phase':'phase-2'},{'floatPartValue':'FT-0'},{'unknown':'any'},None):self.assertFalse(general_order(attrs))
        attr={'cs2':{'float':'.25','stickers':[{'name':'expensive'}],'phase':'phase-2'}}
        self.assertTrue(base_offer(attr,'StatTrak™ AK-47 | Example (Field-Tested)'))
        self.assertFalse(base_offer(attr,'StatTrak™ AK-47 | Example (Factory New)'))
        self.assertTrue(qualified_identity('Music Kit | Example',False))
        self.assertFalse(qualified_identity('Music Kit | Example',None))

    def test_search_settings_do_not_rewrite_saved_prediction(self):
        p=self.scan()['predictions'][0];saved=self.journal.get(p['prediction_id'])
        self.journal.append('search_settings',{'settings':{'minimum_purchase_cents':100,'narrow_spread_bps':500}})
        self.assertEqual(self.journal.get(p['prediction_id']),saved)
        self.assertFalse(self.scan()['predictions'])
        for settings in ({'minimum_purchase_cents':True,'narrow_spread_bps':1000},{'minimum_purchase_cents':10,'narrow_spread_bps':10001}):
            with self.assertRaises(ValueError):search_rules.validate(settings)

    def test_ranking_comparison_is_rechecked_before_entry(self):
        self.valid_books()
        self.change('Example Case','details',lambda c:c['payload']['result']['histogram'].update(buyOrders=[{'price':'5.00','quantity':20}],sellOrders=[{'price':'5.01','quantity':20}]))
        p=next(p for p in self.scan()['predictions'] if p['item_a']=='Example Case' and p['item_b']=='Return Case' and p['predicted_net_cents']>0 and p['engine_version']=='observed-depth-v2')
        self.change('Return Case','offers',lambda c:[r.update(priceCents=150) for r in c['payload']['items']])
        r=preview(self.journal,p['prediction_id'],self.mandate,self.policy,self.at)
        self.assertFalse(r['ready']);self.assertIn('ranking_comparison_changed',' '.join(r['blockers']))

    def test_cached_calculations_match_uncached_and_exhaustive_return_sizes(self):
        self.valid_books();rng=random.Random(431)
        for trial in range(5):
            self.change('Return Case','targets',lambda c:c['payload'].update(orders=[
                {'title':c['title'],'attributes':{},'price':str(rng.randint(91,99)),'amount':'2'},
                {'title':c['title'],'attributes':{},'price':str(rng.randint(50,85)),'amount':'18'}]))
            sources=[snapshot(self.journal,t,self.at) for t in self.titles]
            a,b=[_inputs(s,tuple(s['books'])) for s in sources]
            cache={};actual=self.scan(capital_cents=300)['predictions']
            for sm in ('current_bids','midpoint','listing_price'):
                for dm in ('current_bids','midpoint','listing_price'):
                    exhaustive=[]
                    for qa in range(1,4):
                        for qb in range(1,6):
                            try:p=calculate(a,b,qa,self.policy,sm,dm,qb)
                            except ValueError:continue
                            self.assertEqual(p,calculate(a,b,qa,self.policy,sm,dm,qb,cache))
                            search_rules.annotate(p,*sources,search_rules.DEFAULTS);exhaustive.append(p)
                    selected=[p for p in actual if p['item_a']==self.titles[0] and p['item_b']==self.titles[1] and p['sale_scenarios']=={'steam':sm,'dmarket':dm}]
                    for priority in {p['route_priority'] for p in exhaustive}:
                        wanted=min((p for p in exhaustive if p['route_priority']==priority),key=search_rules.sort_key)
                        got=min((p for p in selected if p['route_priority']==priority),key=search_rules.sort_key)
                        self.assertEqual(search_rules.sort_key(got),search_rules.sort_key(wanted))

    def test_capture_projection_is_once_per_scan(self):
        original=self.journal.records;calls=[]
        def records(category,db=None):
            if db is None:calls.append(category)
            return original(category,db)
        with patch.object(self.journal,'records',side_effect=records):self.scan()
        self.assertEqual(calls.count('capture'),1)

    def test_losses_do_not_outrank_profitable_assumptions(self):
        self.valid_books();rows=self.scan()['predictions']
        base=deepcopy(rows[0]);loss=dict(base,route_priority=1,predicted_net_cents=-1)
        gain=dict(base,route_priority=4,predicted_net_cents=1)
        self.assertLess(search_rules.sort_key(gain),search_rules.sort_key(loss))

    def test_return_rule_keeps_priority_and_floor_without_old_trial_changes(self):
        candidate={'dmarket_bid':{'price_cents':95,'quantity':2,'levels':[{'price_cents':95,'quantity':2},{'price_cents':70,'quantity':8}]},
                   'comparison_ask':{'value':{'price_cents':100}}}
        choices=search_rules.return_choices(candidate,[{'price_cents':10,'quantity':100}],100,lambda p:p-1,search_rules.DEFAULTS)
        self.assertEqual({(p,b['quantity']) for p,b,s,q in choices},{(1,2),(2,10)})
        self.assertEqual(search_rules.return_choices(candidate,[{'price_cents':9,'quantity':100}],100,lambda p:p,search_rules.DEFAULTS),[])


class CatalogueTests(unittest.TestCase):
    def setUp(self):fixture.EntryTests.setUp(self)
    recorded_quotes=fixture.EntryTests.recorded_quotes

    def page(self,items,cursor='',requested=None):
        rows=[{'title':t,'offerBestPrice':{'Currency':'USD','Amount':str(p)},'offerCount':'50',
               'orderBestPrice':{'Currency':'USD','Amount':str(max(1,p-1))},'orderCount':'500'} for t,p in items]
        payload=catalogue.parse({'aggregatedPrices':rows,'nextCursor':cursor},self.at)
        payload.update(requested_titles=requested)
        return self.journal.append('capture',dict(provider='dmarket',kind='catalogue',app_id=730,title='CS2 catalogue',
            retrieved_at=self.at,status=200,error=None,payload=payload,input_kind='recorded'))

    def test_pagination_rebuild_and_refresh_does_not_reset_cursor(self):
        self.page([('Sticker | A',10)],'next');self.page([('Sticker | A',12)],requested=['Sticker | A'])
        fresh=Journal(self.journal.path);view=catalogue.view(fresh)
        self.assertEqual(view['cursor'],'next');self.assertEqual(view['items']['Sticker | A']['dmarket_ask_cents'],12)
        self.page([('Music Kit | B',100)])
        self.assertEqual(len(catalogue.view(fresh)['items']),2);self.assertEqual(catalogue.view(fresh)['last_complete_pass'],self.at)

    def test_bulk_units_are_cents_counts_are_not_best_level_depth(self):
        self.page([('Agent',123)])
        item=catalogue.view(self.journal)['items']['Agent'];self.assertEqual(item['dmarket_ask_cents'],123)
        self.assertNotIn('quantity',item)
        row=snapshot(self.journal,'Agent',self.at);self.assertEqual(row['books'],{})
        with self.assertRaises(ValueError):catalogue.parse({'aggregatedPrices':[{'title':'Agent','offerBestPrice':{'Currency':'EUR','Amount':'123'}}]},self.at)

    def test_every_fifth_selection_explores_and_persists(self):
        self.page([('A promising',50),('Z expensive unknown',5000)])
        self.journal.append('catalogue_progress',{'selection_count':4,'checked':{'A promising':self.at}})
        rows,count=catalogue.research_roster(Journal(self.journal.path),[],1000,search_rules.DEFAULTS,1,{},self.at)
        self.assertEqual(rows[0]['title'],'Z expensive unknown');self.assertEqual(count,5)

    def test_detail_source_for_new_titles_is_existing_steamapis_feed(self):
        watch=dict(self.watch,catalogue={'enabled':True},steam_source='steam_public')
        self.assertEqual(request_for(watch,730,'Sticker | New','details')['provider'],'steamapis')
        self.assertEqual(request_for(watch,730,'Example Case','details')['provider'],'steam_public')

    def test_failed_steam_capture_remains_pending_without_crashing_roster(self):
        self.page([('Agent',100)])
        rows,count=catalogue.research_roster(self.journal,[],1000,search_rules.DEFAULTS,2,
            {'Agent':{'details':{'payload':None,'status':503,'error':'http_503'}}},self.at)
        self.assertEqual(rows,[{'app_id':730,'title':'Agent'}])

    def test_catalogue_failure_does_not_stop_regular_market_requests(self):
        calls=[]
        def fetch(j,request,keys):
            calls.append(request)
            return {'status':403 if request['kind']=='catalogue' else 200,'error':'http_403' if request['kind']=='catalogue' else None}
        batch=CollectionBatch(self.journal,self.watch,self.config,{}, {},lambda:self.at,lambda:False,fetch)
        batch.ensure([catalogue.request(),request_for(self.watch,730,'Example Case','offers')])
        self.assertEqual(len(calls),2);self.assertIn('dmarket:catalogue',batch.sources)

    def test_catalogue_request_uses_signed_read_only_post_and_counts_attempt(self):
        observed=[]
        class Opener:
            def open(_,req,timeout):
                observed.append(req);b=BytesIO(b'{"aggregatedPrices":[],"nextCursor":""}');b.status=200;return b
        result=catalogue.fetch(self.journal,catalogue.request(),{'DMARKET_PUBLIC_KEY':'a'*64,'DMARKET_SECRET_KEY':'1'*64},Opener())
        self.assertEqual(result['status'],200);self.assertEqual(observed[0].get_method(),'POST')
        self.assertEqual(observed[0].full_url,'https://api.dmarket.com/marketplace-api/v1/aggregated-prices')
        self.assertEqual(len(self.journal.records('request_attempt')),1)
        self.assertNotIn('111111111',json.dumps(self.journal.records('capture')))

    def test_large_catalogue_only_prices_detailed_items(self):
        self.page([(f'Sticker | Fixture {i:05}',20+i%500) for i in range(10000)])
        watch=dict(self.watch,catalogue={'enabled':True})
        started=time.perf_counter();report=search(self.journal,'grow',watch,self.policy,self.mandate,self.at)
        self.assertEqual(report['catalogue_coverage']['catalogue_size'],10000)
        self.assertEqual(len(report['snapshots']),2)
        self.assertTrue(report['predictions']);self.assertLess(time.perf_counter()-started,5)


class BroadWorkerTests(worker_fixture.WorkerTests):
    # Only new scenarios run here; inherited tests are loaded once in their module.
    def test_broad_worker_is_bounded_and_resume_keeps_pagination(self):
        self.watch['catalogue']={'enabled':True};self.config['request_budget']=7;self.config['steam_budget']=2
        calls=[]
        original=self.fetch
        def fetch(j,req,keys):
            calls.append(req)
            if req['kind']!='catalogue':return original(j,req,keys)
            data=catalogue.parse({'aggregatedPrices':[{'title':'Agent | Test','offerBestPrice':{'Currency':'USD','Amount':'100'},'offerCount':'1','orderCount':'0'}],'nextCursor':'cursor-two'},self.at)
            data['requested_titles']=None
            j.append('request_attempt',dict(req,started_at=self.at))
            rid=j.append('capture',dict(req,payload=data,status=200,error=None,input_kind='recorded',retrieved_at=self.at))
            return dict(req,record_id=rid,status=200,error=None)
        self.worker(fetch).tick(self.at)
        self.assertLessEqual(len(calls),7);self.assertEqual(calls[0]['kind'],'catalogue')
        self.assertEqual(catalogue.view(Journal(self.journal.path))['cursor'],'cursor-two')
        self.assertTrue(any(r['title']=='Agent | Test' and r['kind']=='details' for r in calls))
        from arbitrage_v2.worker import latest
        from arbitrage_v2.evidence import utc, stamp
        from datetime import timedelta
        for _ in range(2):
            health=latest(self.journal,'worker_health')
            self.assertFalse(health['fatal'])
            self.assertIsNone(health['reason'])
            self.assertEqual(health['status'],'waiting')
            self.assertGreater(utc(health['next_check_at']),utc(self.at))
            self.assertEqual(health['last_success_at'],self.at)
            self.at=stamp(utc(health['next_check_at'])+timedelta(seconds=1))
            self.worker(fetch).tick(self.at)  # New worker instance, same saved journal.
        self.assertFalse(latest(self.journal,'worker_health')['fatal'])
        self.assertEqual(self.journal.records('route'),[])

    def test_active_route_precedes_catalogue_and_pause_keeps_state(self):
        self.watch['catalogue']={'enabled':True};self.route();calls=[]
        def fetch(j,r,k):
            calls.append(r)
            if r['kind']=='catalogue':return dict(r,status=403,error='http_403')
            return self.fetch(j,r,k)
        self.worker(fetch).tick(self.at)
        self.assertEqual(calls[0]['kind'],'details');self.assertEqual(calls[0]['title'],'Example Case')
        from arbitrage_v2.worker import control
        control(self.journal,'pause');before=len(calls);self.worker(fetch).tick(self.at)
        self.assertEqual(len(calls),before)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.ex=real_fixture.RealGrowthTests();self.ex.setUp();self.addCleanup(self.ex.doCleanups)

    def test_new_skin_and_music_kit_manual_cycle(self):
        e=self.ex;e.titles=['StatTrak™ AK-47 | Example (Field-Tested)','Music Kit | Example, Track']
        e.watch['items']=[{'app_id':730,'title':t} for t in e.titles];e.recorded_quotes();e.funding();e.open();e.buy()
        e.advance();e.step('transfer_a',next_eligible_at=None);e.advance()
        e.step('sell_a',quantity=2,net_cents=200,fee_cents=30);e.advance()
        e.step('buy_b',item_title=e.titles[1],quantity=2,net_cents=200,fee_cents=0,next_eligible_at=None)
        e.advance();e.step('transfer_b');e.advance();e.step('sell_b',item_title=e.titles[1],quantity=2,net_cents=216,fee_cents=24,account='dmarket_tradable')
        from arbitrage_v2.routes import add_event,_state
        self.assertEqual(e.journal.records('outcome'),[])
        add_event(e.journal,dict(event_id='done',route_id='real-one',at=e.at,reference='finished',kind='resolve',resolution='completed',residual_disposition='none',pending_operations=0,confirmed=True))
        self.assertEqual(e.journal.get('outcome:real-one')['actual_net_cents'],56)

    def test_http_settings_are_protected_and_catalogue_read_only(self):
        from http.client import HTTPConnection
        import threading
        from pathlib import Path
        from arbitrage_v2.web import create_server
        e=self.ex;worker=Worker(e.journal,e.watch,e.policy,e.mandate,e.config,{})
        server=create_server(worker,Path.cwd(),0);threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def call(path,body=None,headers=None):
            c=HTTPConnection('127.0.0.1',server.server_port)
            try:
                c.request('POST' if body else 'GET',path,json.dumps(body) if body else None,headers or {})
                r=c.getresponse();return r.status,json.loads(r.read())
            finally:c.close()
        self.assertEqual(call('/api/catalogue')[0],200)
        self.assertEqual(call('/api/catalogue?offset=-1')[0],400)
        body={'action':'search_settings','settings':{'minimum_purchase_cents':15,'narrow_spread_bps':500}}
        self.assertEqual(call('/api/action',body)[0],403)
        headers={'Origin':f'http://127.0.0.1:{server.server_port}','Content-Type':'application/json','X-Local-Token':call('/api/session')[1]['token']}
        self.assertEqual(call('/api/action',body,headers)[0],200)
        self.assertEqual(search_rules.current(e.journal),body['settings'])
        self.assertEqual(call('/api/status')[1]['search_settings'],body['settings'])
        self.assertEqual(e.journal.records('request_attempt'),[])

    def test_zero_available_funds_still_reports_coverage_and_settings(self):
        e=self.ex;e.funding(regular=0,tradable=0)
        result=search(e.journal,'grow',e.watch,e.policy,e.mandate,e.at,'confirmed')
        self.assertEqual(result['status'],'no_available_funds')
        self.assertEqual(result['predictions'],[])
        self.assertEqual(result['search_settings'],search_rules.DEFAULTS)
        self.assertEqual(result['catalogue_coverage']['coverage'],'partial')


def load_tests(loader,tests,pattern):
    suite=unittest.TestSuite()
    for cls in (SearchTests,CatalogueTests,IntegrationTests):suite.addTests(loader.loadTestsFromTestCase(cls))
    for name in ('test_broad_worker_is_bounded_and_resume_keeps_pagination','test_active_route_precedes_catalogue_and_pause_keeps_state'):
        suite.addTest(BroadWorkerTests(name))
    return suite
