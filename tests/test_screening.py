"""Offline provider contracts, selection and scheduling; no market requests."""
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from email.utils import format_datetime
from io import BytesIO
import gzip
import json
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import test_cs2_search as fixtures
import test_worker as workers
from arbitrage_v2 import catalogue, csgotrader, search_rules
from arbitrage_v2.collection_batch import CollectionBatch, request_for, saved_state
from arbitrage_v2.collection_settings import settings
from arbitrage_v2.collection_transport import CollectionStopped, request_context
from arbitrage_v2.discovery import capture_index, snapshot
from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.journal import Journal
from arbitrage_v2.screening import select
from arbitrage_v2.worker import Worker, latest, search, control


class Response(BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data);self.status=status;self.headers=headers or {}


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.CatalogueTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.j=self.f.journal;self.at=self.f.at;self.requests=[]

    def advance(self, seconds):
        self.at=stamp(utc(self.at)+timedelta(seconds=seconds))

    def fetch(self, payload=None, status=200, headers=None, raw=None, job=None):
        data=raw if raw is not None else json.dumps(payload or {'Example Case':{'last_24h':1.15}}).encode()
        if headers is None:headers={'Last-Modified':format_datetime(utc(self.at),usegmt=True),'ETag':'"fixture"'}
        outer=self
        class Opener:
            def open(_,req,timeout):
                outer.requests.append(req)
                if status>=300:raise HTTPError(req.full_url,status,'fixture',headers,BytesIO(b''))
                return Response(data,status,headers)
        return csgotrader.fetch(self.j,job or csgotrader.request(),opener=Opener(),clock=lambda:self.at)

    def hints(self):return csgotrader.hints(self.j,self.at)

    def qualify(self):
        # Artificially qualified fixture contract tests the dormant numeric path;
        # the shipped provider contract remains unqualified.
        return patch.dict(csgotrader.PRICE_CONTRACT,buyer_fees='included')

    def roster(self, slots=300,index=None,config=None,seeds=()):
        return catalogue.research_roster(self.j,seeds,1000,search_rules.DEFAULTS,slots,
            index or {},self.at,config=config,policy=self.f.policy,with_audit=True)

    def test_exact_variants_and_period_fallback(self):
        names=['AK-47 | Slate (Field-Tested)','StatTrak™ AK-47 | Slate (Field-Tested)',
               'Souvenir AWP | Dragon Lore (Factory New)','AK-47 | Slate (Minimal Wear)']
        result=csgotrader.parse(dict(zip(names,[{'last_24h':1.234,'last_7d':2},
            {'last_24h':0,'last_7d':'2.51'},{'last_24h':None,'last_7d':None},
            {'last_24h':True,'last_7d':'NaN'}])))['items']
        self.assertEqual(set(result),set(names));self.assertEqual(result[names[0]]['period'],'last_24h')
        self.assertEqual(result[names[0]]['price_cents'],123)
        self.assertEqual(result[names[1]]['period'],'last_7d')
        self.assertIsNone(result[names[2]]['price_cents']);self.assertIsNone(result[names[3]]['price_cents'])

    def test_unverified_prices_disabled_names_remain(self):
        before=self.j.records('capture')
        self.fetch({'New provider name':{'last_24h':1.15}});h=self.hints()
        self.assertEqual(h['items'],{});self.assertIn('unverified',h['status']['limitation'])
        self.assertIn('New provider name',csgotrader.view(self.j)['names'])
        self.assertEqual(snapshot(self.j,'New provider name',self.at)['books'],{})
        self.assertEqual(self.j.records('capture'),before)

    def test_304_and_unchanged_200_cannot_renew_age(self):
        with self.qualify():
            self.fetch();published=self.hints()['status']['published_at']
            self.advance(3600);self.fetch(status=304)
            self.assertIn('If-none-match',self.requests[-1].headers)
            self.assertEqual(self.hints()['status']['published_at'],published)
            self.advance(86400);self.fetch()  # Identical bytes, newer modification time.
            self.assertEqual(self.hints()['items'],{})
            self.assertEqual(self.hints()['status']['published_at'],published)
            self.assertEqual(len(self.j.records('screening_snapshot')),1)
            self.assertFalse(csgotrader.due(self.j,self.at));self.advance(3600)
            self.assertTrue(csgotrader.due(self.j,self.at))

    def test_unknown_future_and_stale_publication_disable_hints(self):
        with self.qualify():
            for value in ('invalid',format_datetime(utc(self.at)+timedelta(hours=1),usegmt=True),
                          format_datetime(utc(self.at)-timedelta(hours=25),usegmt=True)):
                self.fetch({'New '+value:{'last_24h':1}},headers={'Last-Modified':value})
                self.assertFalse(self.hints()['status']['prices_usable'])

    def test_24h_boundary_and_provider_failure_keeps_old_hints(self):
        with self.qualify():
            self.fetch();self.advance(86400);self.fetch(status=503)
            self.assertTrue(self.hints()['items']);self.assertEqual(self.hints()['status']['error'],'http_503')
            self.advance(.001);self.assertEqual(self.hints()['items'],{})
            self.assertEqual(len(csgotrader.view(Journal(self.j.path))['names']),1)

    def test_new_file_drops_old_prices_but_not_names(self):
        with self.qualify():
            self.fetch({'Old':{'last_24h':3}});self.advance(1)
            self.fetch({'New':{'last_24h':4}})
            self.assertEqual(csgotrader.view(Journal(self.j.path))['names'],{'Old','New'})
            self.assertNotIn('Old',self.hints()['items'])

    def test_gzip_limits_malformed_and_retry_after(self):
        raw=gzip.compress(b'{"Music Kit | X":{"last_24h":1.25}}')
        self.assertIsNone(self.fetch(raw=raw,headers={'Content-Encoding':'gzip'})['error'])
        old=csgotrader.view(self.j)['snapshot']
        result=self.fetch(raw=gzip.compress(b' '*3000),headers={'Content-Encoding':'gzip'},job=dict(csgotrader.request(),max_bytes=100))
        self.assertEqual(result['error'],'invalid_screening_contract')
        self.assertEqual(csgotrader.view(self.j)['snapshot'],old)
        self.assertEqual(self.fetch(raw=b'[]')['error'],'invalid_screening_contract')
        self.assertEqual(self.fetch(status=429,headers={'Retry-After':'120'})['retry_after'],120)

    def test_304_without_snapshot_is_failure(self):
        self.assertEqual(self.fetch(status=304)['error'],'invalid_screening_contract')

    def test_download_respects_deadline_without_saving_partial_file(self):
        with request_context(lambda r:None,lambda:0):
            with self.assertRaises(CollectionStopped):self.fetch()
        self.assertEqual(csgotrader.view(self.j)['snapshot'],{})

    def test_bulk_cannot_change_same_detailed_route_calculation(self):
        self.f.recorded_quotes()
        before=search(self.j,'grow',self.f.watch,self.f.policy,self.f.mandate,self.at)['predictions']
        with self.qualify():self.fetch({r['title']:{'last_24h':999999} for r in self.f.watch['items']})
        self.f.page([(r['title'],999999) for r in self.f.watch['items']])
        after=search(self.j,'grow',self.f.watch,self.f.policy,self.f.mandate,self.at)['predictions']
        self.assertTrue(before);self.assertEqual(before,after)

    def test_balanced_300_unique_with_both_legs_and_exploration(self):
        items=[(f'Item {i:04}',100) for i in range(600)]
        self.f.page(items)
        with self.qualify():
            self.fetch({t:{'last_24h':1+(i%100)/100} for i,(t,_) in enumerate(items)})
            roster,count,audit=self.roster()
        self.assertEqual(len({r['title'] for r in roster}),300);self.assertEqual(count,300)
        self.assertEqual(Counter(r['selected_group'] for r in audit['selections']),
                         {'outward':120,'returning':120,'exploration':60})
        self.assertEqual([r['requested_group'] for r in audit['selections'][:5]],
                         ['outward','outward','returning','returning','exploration'])
        self.assertNotEqual(audit['selections'][0]['title'],audit['selections'][2]['title'])

    def test_missing_and_steam_only_names_fill_slots_and_oldest_explores(self):
        self.fetch({'Steam only':{},'Another':{}})
        self.j.append('catalogue_progress',dict(selection_count=4,checked={'Another':self.at}))
        selected,count,audit=self.roster(3,seeds=[dict(app_id=730,title='Supported')])
        self.assertEqual(selected[0]['title'],'Steam only')
        self.assertEqual({r['title'] for r in selected},{'Another','Steam only','Supported'})
        self.assertTrue(all(r['outward_ratio'] is None for r in audit['selections']))

    def test_dmarket_age_is_per_page_and_names_stay_eligible(self):
        self.f.page([('Old',100)]);self.advance(86401);self.f.at=self.at
        self.f.page([('New',100)])
        with self.qualify():
            self.fetch({'Old':{'last_24h':2},'New':{'last_24h':2}})
            selected,_,audit=self.roster(3)
        rows={r['title']:r for r in audit['selections']}
        self.assertIsNone(rows['Old']['outward_ratio']);self.assertIsNotNone(rows['New']['outward_ratio'])
        self.assertEqual(len(selected),2)

    def test_detailed_prices_override_summaries_without_history(self):
        self.f.recorded_quotes();index=capture_index(self.j,self.at)
        self.f.page([(r['title'],999) for r in self.f.watch['items']])
        with self.qualify():
            self.fetch({r['title']:{'last_24h':999} for r in self.f.watch['items']})
            _,_,audit=catalogue.research_roster(self.j,[],1000,search_rules.DEFAULTS,5,index,self.at,
                                              refresh_seconds=0,with_audit=True)
        self.assertTrue(audit['selections'])
        for row in audit['selections']:
            for source in row['sources'].values():self.assertEqual(source['source'],'detailed')

    def test_bid_hint_and_listing_fallback_are_labelled(self):
        self.f.page([('Bid',100),('Ask',100)])
        state=deepcopy(catalogue.view(self.j));state['items']['Ask']['dmarket_bid_cents']=None
        with self.qualify():
            self.fetch({'Bid':{'last_24h':1},'Ask':{'last_7d':1}})
            _,_,audit=select(self.j,state,[],1000,search_rules.DEFAULTS,2,{},self.at)
        sources={r['title']:r['sources']['return_sale'] for r in audit['selections']}
        self.assertEqual(sources['Bid']['method'],'dmarket_bid');self.assertEqual(sources['Ask']['method'],'dmarket_ask')

    def test_local_http_lists_steam_only_names_and_explains_limit(self):
        from http.client import HTTPConnection
        from pathlib import Path
        import threading
        from arbitrage_v2.web import create_server
        self.fetch({'Only Steam':{'last_24h':1.15}})
        worker=Worker(self.j,self.f.watch,self.f.policy,self.f.mandate,self.f.config,{})
        server=create_server(worker,Path.cwd(),0)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def read(path):
            connection=HTTPConnection('127.0.0.1',server.server_port)
            try:
                connection.request('GET',path);response=connection.getresponse()
                self.assertEqual(response.status,200);return json.loads(response.read())
            finally:connection.close()
        catalogue_page=read('/api/catalogue')
        item=next(i for i in catalogue_page['items'] if i['title']=='Only Steam')
        self.assertEqual(item['detail_status'],'pending_first_check')
        self.assertFalse(item['screening_dmarket_fresh'])
        steam=read('/api/status')['catalogue_coverage']['screening']['steam']
        self.assertEqual(steam['names'],1);self.assertFalse(steam['prices_usable'])
        self.assertIn('unverified',steam['limitation'])

    def test_large_file_selection_and_projection_performance(self):
        payload={f'Sticker | Large {i:05}':{'last_24h':1.15} for i in range(35000)}
        start=time.perf_counter();self.fetch(payload)
        selected,_,_=self.roster();self.assertEqual(len(selected),300)
        for _ in range(5):self.assertEqual(len(catalogue.combined_items(self.j)),35000)
        self.assertLess(time.perf_counter()-start,5)


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.f=workers.WorkerTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.watch['catalogue']={'enabled':True}
        self.f.config.update(csgotrader_enabled=True,research_batch_size=5,
            request_spacing_seconds={'steam_public':0,'steamapis':0,'dmarket':0,'csgotrader':0})
        self.calls=[];self.end=50;self.fail={};self.loop=False

    def fetch(self,j,req,keys):
        self.calls.append(req)
        if req['provider'] in self.fail:return dict(req,**self.fail[req['provider']])
        if req['provider']=='csgotrader':return dict(req,status=304,error=None)
        if req['kind']!='catalogue':return self.f.fetch(j,req,keys)
        n=int(req.get('cursor') or '0')
        nxt=str(n if self.loop else n+1) if n+1<self.end else ''
        data=catalogue.parse({'aggregatedPrices':[{'title':f'Agent {n:03}',
            'offerBestPrice':{'Currency':'USD','Amount':'100'},'offerCount':'1','orderCount':'0'}],
            'nextCursor':nxt},self.f.at)
        data.update(requested_titles=None,request_cursor=req.get('cursor',''))
        rid=j.append('capture',dict(req,payload=data,status=200,error=None,input_kind='recorded',retrieved_at=self.f.at))
        return dict(req,record_id=rid,status=200,error=None)

    def worker(self):return Worker(self.f.journal,self.f.watch,self.f.policy,self.f.mandate,self.f.config,{},self.fetch,self.f.clock)
    def advance(self):self.f.at=latest(self.f.journal,'worker_health')['next_check_at']

    def test_ten_pages_resume_and_stop_at_end(self):
        self.worker().tick(self.f.at)
        self.assertFalse(latest(self.f.journal,'worker_health')['fatal'])
        self.assertEqual(sum(r['kind']=='catalogue' for r in self.calls),10)
        self.assertEqual(catalogue.view(Journal(self.f.journal.path))['cursor'],'10')
        self.end=13;self.calls=[];self.advance();self.worker().tick(self.f.at)
        pages=[r for r in self.calls if r['kind']=='catalogue']
        self.assertEqual([r['cursor'] for r in pages],['10','11','12'])
        self.assertEqual(catalogue.view(self.f.journal)['cursor'],'')

    def test_repeated_cursor_stops_and_restart_can_start_new_pass(self):
        self.loop=True;self.worker().tick(self.f.at)
        state=catalogue.view(Journal(self.f.journal.path))
        self.assertEqual(state['cursor'],'');self.assertEqual(state['error'],'repeated_catalogue_cursor')
        self.assertLessEqual(sum(r['kind']=='catalogue' for r in self.calls),2)

    def test_active_first_bulk_before_paired_details(self):
        self.f.route();self.end=1;self.worker().tick(self.f.at)
        bulk=next(i for i,r in enumerate(self.calls) if r['kind']=='catalogue')
        self.assertGreater(bulk,0);self.assertEqual(self.calls[0]['title'],'Example Case')
        after=self.calls[bulk+2:]
        for i in range(0,len(after),3):
            self.assertEqual(len({r['title'] for r in after[i:i+3]}),1)
        self.assertEqual(self.calls[bulk+1]['provider'],'csgotrader')

    def test_bulk_failures_are_independent_and_provider_cooldown_survives(self):
        self.fail={'csgotrader':dict(status=429,error='http_429',retry_after=7200)}
        self.end=1;self.worker().tick(self.f.at)
        self.assertTrue(any(r['kind']=='details' for r in self.calls))
        source=saved_state(self.f.journal)['source_states']['csgotrader']
        self.assertEqual(source['state'],'cooldown')
        self.calls=[];control(self.f.journal,'check_now');self.worker().tick(self.f.at)
        self.assertFalse(any(r['provider']=='csgotrader' for r in self.calls))
        self.assertTrue(any(r['provider']=='dmarket' for r in self.calls))

    def test_dmarket_failure_does_not_stop_steam_bulk_or_details(self):
        self.fail={'dmarket':dict(status=403,error='http_403')};self.worker().tick(self.f.at)
        self.assertTrue(any(r['provider']=='csgotrader' for r in self.calls))
        self.assertTrue(any(r['kind']=='details' for r in self.calls))
        self.assertFalse(latest(self.f.journal,'worker_health')['fatal'])

    def test_pause_during_bulk_does_not_fetch_remaining_pages_or_details(self):
        original=self.fetch
        def fetch(j,req,keys):
            result=original(j,req,keys);control(j,'pause');return result
        Worker(self.f.journal,self.f.watch,self.f.policy,self.f.mandate,self.f.config,{},fetch,self.f.clock).tick(self.f.at)
        self.assertEqual(len(self.calls),1);self.assertEqual(latest(self.f.journal,'worker_health')['status'],'paused')

    def test_regular_bulk_check_does_not_skip_due_to_late_previous_fetch(self):
        self.f.config.update(collection_interval_seconds=3600)
        self.end=1
        original=self.fetch
        def fetch(j,req,keys):
            result=original(j,req,keys)
            if req['provider']=='csgotrader':
                j.append('screening_check',dict(checked_at=self.f.at,status=304,error=None))
            return result
        def run():
            Worker(self.f.journal,self.f.watch,self.f.policy,self.f.mandate,self.f.config,{},fetch,self.f.clock).tick(self.f.at)
        run()
        self.f.at=stamp(utc(self.f.at)+timedelta(minutes=30))
        # Manual rechecks within the hour reuse the current file.
        control(self.f.journal,'check_now');run()
        self.assertEqual(sum(r['provider']=='csgotrader' for r in self.calls),1)
        # Previous retrieval late in a run must not make the next hour skip it.
        self.f.journal.append('screening_check',dict(checked_at=self.f.at,status=304,error=None))
        self.advance();run()
        self.assertEqual(sum(r['provider']=='csgotrader' for r in self.calls),2)

    def test_configuration_validation(self):
        for config in ({'catalogue_page_size':101},{'catalogue_pages_per_run':0},
                       {'screening_selection_pattern':['bad']},{'screening_max_age_seconds':-1}):
            with self.assertRaises(ValueError):settings(config)
        self.f.config.update(catalogue_pages_per_run=2);self.worker().tick(self.f.at)
        self.assertEqual(sum(r['kind']=='catalogue' for r in self.calls),2)


if __name__=='__main__':unittest.main()
