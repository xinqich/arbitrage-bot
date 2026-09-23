"""No network or real waits: scheduling and rate controls use a simulated clock."""
from datetime import timedelta
from email.utils import format_datetime
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

import test_worker as fixtures
import test_real_growth as real_fixtures
from arbitrage_v2 import catalogue, search_rules
from arbitrage_v2.collection_batch import CollectionBatch, request_for, saved_state
from arbitrage_v2.collection_settings import DEFAULTS, settings
from arbitrage_v2.collection_transport import request_context, prepare_request, retry_after, CollectionStopped
from arbitrage_v2.discovery import capture_index
from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.journal import Journal
from arbitrage_v2.local_cli import load_config
from arbitrage_v2.steam_public import ListingRedirect
from arbitrage_v2.worker import Worker, control, latest, search


class HourlyTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkerTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.config.update(settings())
        self.start = self.f.at
        self.calls = []

    def advance(self, seconds):
        self.f.at = stamp(utc(self.f.at) + timedelta(seconds=seconds))

    def fetch(self, journal, request, keys):
        self.calls.append(dict(request, at=self.f.at))
        return self.f.fetch(journal, request, keys)

    def worker(self, fetch=None, wait=None):
        f = self.f
        return Worker(f.journal, f.watch, f.policy, f.mandate, f.config, {},
                      fetch or self.fetch, f.clock, wait=wait or self.advance)

    def batch(self, fetch=None, wait=None):
        f = self.f
        return CollectionBatch(f.journal, f.watch, f.config, {},
            saved_state(f.journal).get('source_states', {}), f.clock,
            lambda: False, fetch or self.fetch, wait=wait or self.advance)

    def req(self, title='Example Case', kind='details'):
        return request_for(self.f.watch, 730, title, kind)

    def health(self):
        return latest(self.f.journal, 'worker_health')

    def test_shipped_defaults_and_legacy_caps_are_ignored(self):
        cfg = load_config(Path(__file__).parents[1]/'config/local.json')
        for k, v in DEFAULTS.items():
            self.assertEqual(cfg[k], v)
        self.assertNotIn('request_budget', cfg)
        self.assertNotIn('steam_budget', cfg)
        self.f.config.update(request_budget=1, steam_budget=0)
        self.f.watch.update(total_request_allowance=0, steam_public_request_allowance=0)
        self.worker().tick(self.f.at)
        self.assertEqual(len(self.calls), 6)
        self.assertFalse(self.health()['fatal'])

    def test_spacing_across_kinds_and_restart(self):
        self.batch().ensure([self.req(), self.req(kind='offers'), self.req(kind='targets')])
        self.batch().ensure([self.req('Return Case'), self.req('Return Case', 'offers')])
        for provider, spacing in [('steam_public', 5), ('dmarket', 2)]:
            times = [utc(r['at']) for r in self.calls if r['provider'] == provider]
            self.assertGreaterEqual(len(times), 2)
            self.assertTrue(all((b-a).total_seconds() >= spacing for a,b in zip(times,times[1:])))

    def test_steam_redirect_is_spaced_and_counted(self):
        batch = self.batch()
        req = Request('https://steamcommunity.com/market/listings/730/Example%20Case')
        req.timeout = 20
        with request_context(batch.begin_request, batch.remaining):
            prepare_request(self.f.journal, self.req())
            redirected = ListingRedirect().redirect_request(req, None, 302, 'Found', {},
                'https://steamcommunity.com/market/listings/730/Example%20Case?currency=1')
        self.assertIsNotNone(redirected)
        self.assertEqual(batch.count, 2)
        self.assertEqual((utc(self.f.at)-utc(self.start)).total_seconds(), 5)
        self.assertEqual(len(self.f.journal.records('request_attempt')), 2)

    def test_pause_interrupts_spacing_and_deadline_stops_before_next_request(self):
        def pause_wait(seconds):
            self.advance(seconds)
            control(self.f.journal, 'pause')
        self.worker(wait=pause_wait).tick(self.f.at)
        self.assertEqual(len(self.calls), 2)  # Steam + first DMarket call; second DMarket waits.
        self.assertEqual(self.health()['status'], 'paused')
        self.assertLess((utc(self.f.at)-utc(self.start)).total_seconds(), 1)
        self.f.config['max_run_seconds'] = 1
        self.calls.clear()
        control(self.f.journal, 'resume')
        self.worker().tick(self.f.at)
        self.assertEqual(self.calls, [])  # Persisted Steam spacing lasts longer than this run.
        self.assertTrue(self.health()['time_limit_reached'])
        self.assertFalse(self.health()['fatal'])

    def test_hourly_anchor_manual_and_overdue_startup(self):
        self.worker().tick(self.f.at)
        due = stamp(utc(self.start)+timedelta(hours=1))
        self.assertEqual(self.health()['next_research_at'], due)
        self.advance(120)
        control(self.f.journal, 'check_now')
        self.worker().tick(self.f.at)
        self.assertEqual(self.health()['next_research_at'], due)
        self.f.at = stamp(utc(self.start)+timedelta(hours=8, minutes=15))
        before = len(self.calls)
        self.worker().tick(self.f.at)
        self.assertEqual(len(self.calls)-before, 6)
        self.assertEqual(self.health()['next_research_at'], stamp(utc(self.start)+timedelta(hours=9)))
        self.worker().tick(self.f.at)
        self.assertEqual(len(self.calls)-before, 6)

    def test_overlapping_tick_is_discarded_not_queued(self):
        entered, release = threading.Event(), threading.Event()
        def fetch(j,r,k):
            if not entered.is_set():
                entered.set()
                self.assertTrue(release.wait(3))
            return self.fetch(j,r,k)
        worker = self.worker(fetch)
        thread = threading.Thread(target=lambda: worker.tick(self.f.at))
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            worker.tick(self.f.at)
            self.assertTrue(worker.busy)
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(self.calls), 6)

    def test_long_manual_run_skips_crossed_schedule_without_catchup(self):
        self.worker().tick(self.f.at)
        self.f.at = stamp(utc(self.start)+timedelta(minutes=59))
        control(self.f.journal, 'check_now')
        self.f.config.update(max_run_seconds=3600)
        def slow(j,r,k):
            result = self.fetch(j,r,k)
            self.advance(600)
            return result
        self.worker(slow).tick(self.f.at)
        self.assertEqual(self.health()['next_research_at'], stamp(utc(self.start)+timedelta(hours=2)))
        before = len(self.calls)
        self.worker().tick(self.f.at)
        self.assertEqual(len(self.calls), before)

    def test_429_delta_persists_before_run_finishes_and_manual_cannot_bypass(self):
        def limited(j,r,k):
            result = self.fetch(j,r,k)
            if r['provider'] == 'dmarket':
                result.update(status=429,error='http_429',retry_after=1200)
            return result
        batch = self.batch(limited)
        batch.ensure([self.req(kind='offers'), self.req(kind='targets'), self.req()])
        self.assertEqual([r['provider'] for r in self.calls], ['dmarket','steam_public'])
        self.assertEqual(saved_state(Journal(self.f.journal.path))['source_states']['dmarket']['state'], 'cooldown')
        control(self.f.journal, 'check_now')
        self.worker().tick(self.f.at)
        self.assertEqual(sum(r['provider']=='dmarket' for r in self.calls), 1)
        self.f.at = stamp(utc(self.start)+timedelta(seconds=1200))
        self.worker().tick(self.f.at)
        self.assertGreater(sum(r['provider']=='dmarket' for r in self.calls), 1)
        self.assertNotIn('dmarket', saved_state(self.f.journal)['source_states'])

    def test_429_zero_still_stops_provider_for_remainder_of_run(self):
        def limited(j,r,k):
            self.calls.append(dict(r))
            return dict(status=429,error='http_429',retry_after=0)
        batch=self.batch(limited)
        batch.ensure([self.req(kind='offers'), self.req(kind='targets')])
        self.assertEqual(len(self.calls),1)

    def test_429_date_and_missing_header(self):
        for header, expected in [(format_datetime(utc(self.start)+timedelta(seconds=1800)),1800),
                                 (None,900),('nonsense',900)]:
            with self.subTest(header=header):
                batch=CollectionBatch(self.f.journal,self.f.watch,self.f.config,{}, {},self.f.clock,lambda:False,
                    lambda j,r,k:dict(status=429,error='http_429',retry_after=retry_after({'Retry-After':header})),wait=self.advance)
                batch.ensure([self.req()])
                at=batch.sources['steam_public']['next_retry_at']
                # Request spacing may have advanced the clock for later subtests.
                want=stamp(utc(self.start)+timedelta(seconds=1800)) if expected==1800 else stamp(utc(self.f.at)+timedelta(seconds=expected))
                self.assertEqual(at,want)

    def test_connection_retries_are_bounded_and_keep_regular_anchor(self):
        self.f.failures['dmarket']=(503,'http_503')
        self.worker().tick(self.f.at)
        anchor=self.health()['next_research_at']
        for _ in range(3):
            self.f.at=self.health()['source_states']['dmarket']['next_retry_at']
            self.worker().tick(self.f.at)
            self.assertEqual(self.health()['next_research_at'],anchor)
        self.assertEqual(sum(r['provider']=='dmarket' for r in self.calls),4)
        self.assertIsNone(self.health()['source_states']['dmarket']['next_retry_at'])
        self.assertFalse(self.health()['fatal'])

    def test_retry_after_run_deadline_is_not_scheduled_and_recovers_next_hour(self):
        self.f.config['max_run_seconds']=30
        self.f.failures['dmarket']=(503,'http_503')
        self.worker().tick(self.f.at)
        self.assertIsNone(self.health()['source_states']['dmarket']['next_retry_at'])
        self.f.at=self.health()['next_research_at']
        self.f.failures.clear()
        self.worker().tick(self.f.at)
        self.assertNotIn('dmarket',self.health()['source_states'])

    def test_old_six_hour_schedule_migrates_without_replaying_missed_checks(self):
        self.f.schedule(schema_version=2,collection_interval_seconds=21600)
        self.f.at=stamp(utc(self.start)+timedelta(hours=2))
        self.worker().tick(self.f.at)
        self.assertEqual(len(self.calls),6)
        self.assertEqual(self.health()['next_research_at'],stamp(utc(self.start)+timedelta(hours=3)))
        self.worker().tick(self.f.at)
        self.assertEqual(len(self.calls),6)

    def test_catalogue_checks_repeat_next_hour_even_for_late_previous_captures(self):
        self.f.watch['catalogue']={'enabled':True}
        def fetch(j,r,k):
            if r['kind']=='catalogue':
                # An access failure must not hide the two still-working seeds.
                return dict(status=403,error='http_403')
            return self.fetch(j,r,k)
        self.worker(fetch).tick(self.f.at)
        self.assertEqual(sum(r['provider']!='csgotrader' for r in self.calls),6)
        self.f.at=self.health()['next_research_at']
        self.worker(fetch).tick(self.f.at)
        self.assertEqual(sum(r['provider']!='csgotrader' for r in self.calls),12)

    def test_unlock_during_research_is_checked_first_without_shifting_hour(self):
        self.f.route()
        self.f.at=stamp(utc(self.start)-timedelta(seconds=1))
        self.worker().tick(self.f.at)
        decisions=self.f.journal.records('paper_decision')
        self.assertTrue(decisions)
        self.assertGreaterEqual(utc(decisions[0]['at']),utc(self.start))
        self.assertTrue(any(r['title']=='Example Case' and r['kind']=='details' and utc(r['at'])>=utc(self.start) for r in self.calls))
        self.assertEqual(self.health()['next_research_at'],stamp(utc(self.start)+timedelta(seconds=3599)))
        self.assertEqual(self.f.journal.get(self.f.pred_id),self.f.prediction)

    def test_300_variants_plus_active_checks_and_more_than_1000_requests(self):
        self.f.config['request_spacing_seconds']={'steam_public':0,'dmarket':0,'steamapis':0}
        self.f.config.update(request_budget=15,steam_budget=5)
        self.f.watch.update(total_request_allowance=1000,steam_public_request_allowance=1000)
        with self.f.journal.connect(True) as db:
            for n in range(1001):
                self.f.journal.append('request_attempt',{'provider':'steam_public','n':n},db=db)
        self.f.route()
        self.f.bid_quantity=1
        self.f.watch['items'] += [{'app_id':730,'title':f'Sticker | Test {n:03}'} for n in range(305)]
        self.worker().tick(self.f.at)
        research={r['title'] for r in self.calls if r['title']!='Example Case'}
        self.assertEqual(len(research),300)
        self.assertEqual(len(self.calls),901)
        self.assertEqual(self.calls[0]['title'],'Example Case')
        self.assertEqual(self.health()['research_items_selected'],300)
        self.assertFalse(self.health()['fatal'])
        self.assertGreater(len(self.f.journal.records('request_attempt')),1900)

    def test_catalogue_refresh_uses_one_hour_not_four_hour_validity(self):
        self.f.captures(bid_quantity=2)
        for title in [r["title"] for r in self.f.watch["items"]]:
            self.f.fetch(self.f.journal,self.req(title,"offers"),{})
        index=capture_index(self.f.journal,self.f.at)
        # These fixture books were captured exactly at the current clock.
        for row in index.values():
            for c in row.values():
                c['retrieved_at']=self.f.at
                c['input_kind']='recorded'
                if c['kind']=='details':c['payload']['result']['histogram']['date']=self.f.at
        seeds=self.f.watch['items']
        rows,_=catalogue.research_roster(self.f.journal,seeds,1000,search_rules.DEFAULTS,300,index,self.f.at)
        self.assertEqual(rows,[])
        self.advance(3600)
        rows,_=catalogue.research_roster(self.f.journal,seeds,1000,search_rules.DEFAULTS,300,index,self.f.at)
        self.assertEqual(len(rows),2)
        self.assertEqual(self.f.config['freshness_seconds'],14400)

    def test_search_obeys_deadline_and_keeps_original_predictions(self):
        before=self.f.journal.records('prediction')
        with self.assertRaises(CollectionStopped):
            search(self.f.journal,'grow',self.f.watch,self.f.policy,self.f.mandate,self.f.at,
                   should_stop=lambda:True)
        self.assertEqual(self.f.journal.records('prediction'),before)

    def test_real_route_checks_do_not_create_transactions(self):
        f=real_fixtures.RealGrowthTests();f.setUp();self.addCleanup(f.doCleanups)
        f.funding();f.open();f.buy()
        before=f.journal.records('route_event');funds=f.journal.records('real_funding');calls=[]
        config=settings({'request_spacing_seconds':{'steam_public':0,'dmarket':0,'steamapis':0}})
        worker=Worker(f.journal,f.watch,f.policy,f.mandate,config,{},
            lambda j,r,k:(calls.append(r) or dict(status=200,error=None)),lambda:f.at)
        worker.tick(f.at)
        self.assertEqual(calls[0]['kind'],'offers')
        self.assertEqual(calls[0]['title'],f.titles[0])
        self.assertEqual(f.journal.records('route_event'),before)
        self.assertEqual(f.journal.records('real_funding'),funds)
        self.assertFalse(latest(f.journal,'worker_health')['fatal'])


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.WorkerTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_all_transports_preserve_retry_after(self):
        from arbitrage_v2.collector import capture
        from arbitrage_v2.steam_public import capture_public
        keys={'DMARKET_PUBLIC_KEY':'a'*64,'DMARKET_SECRET_KEY':'1'*64,'STEAMAPIS_KEY':'fixture'}
        class Opener:
            def open(self, *a, **kw):
                raise HTTPError('https://example.invalid',429,'Too Many Requests',{'Retry-After':'125'},None)
        for fetch in [lambda:capture(self.f.journal,'steamapis','details',730,'Example Case',keys,Opener()),
                      lambda:capture_public(self.f.journal,730,'Example Case',Opener()),
                      lambda:catalogue.fetch(self.f.journal,catalogue.request(),keys,Opener())]:
            result=fetch()
            self.assertEqual(result['retry_after'],125)
            self.assertEqual(self.f.journal.get(result['record_id'])['retry_after'],125)

    def test_optional_keyed_service_requires_disabled_overage(self):
        from arbitrage_v2.collection_batch import fetch_request
        f=self.f;f.watch['steam_source']='steamapis'
        with patch('arbitrage_v2.collector.free_access',return_value={'status':200,'no_overage':False,'error':'free_usage_not_verified'}), \
             patch('arbitrage_v2.collector.capture') as capture:
            batch=CollectionBatch(f.journal,f.watch,f.config,{}, {},f.clock,lambda:False,fetch_request)
            batch.ensure([request_for(f.watch,730,'Example Case','details')])
            capture.assert_not_called()
            self.assertEqual(batch.sources['steamapis']['error'],'free_usage_not_verified')
