from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from http.client import HTTPConnection
import json
import threading
import unittest
from unittest.mock import patch

import test_paper_entry as fixtures
from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.funds import account_money, available_capital
from arbitrage_v2.journal import Journal
from arbitrage_v2.paper_entry import preview as paper_preview
from arbitrage_v2.prediction import ENGINE, LISTING_ENGINE, screen
from arbitrage_v2.real_funds import record_funding, correct_funding, status
from arbitrage_v2.real_routes import enter, preview, record_step
from arbitrage_v2.routes import _state, add_event, correct_event, route_status, train
from arbitrage_v2.web import create_server
from arbitrage_v2.worker import search, Worker


class RealGrowthTests(unittest.TestCase):
    recorded_quotes = fixtures.EntryTests.recorded_quotes

    def setUp(self):
        fixtures.EntryTests.setUp(self)
        for module in ('arbitrage_v2.real_funds.datetime', 'arbitrage_v2.routes.datetime'):
            mocked = self.enterContext(patch(module))
            mocked.now.return_value = utc(self.at)+timedelta(days=100)
        self.seq = 0

    def funding(self, kind='opening', regular=400, tradable=600, **kw):
        request = dict(funding_id='fund-'+str(self.seq), kind=kind, at=self.at,
                       reference='outside-'+str(self.seq), regular_cents=regular, tradable_cents=tradable)
        self.seq += 1
        request.update(kw)
        record_funding(self.journal, request)
        return request

    def choose_prediction(self, quantity=2, listing=False):
        report = screen(self.journal, self.watch, self.policy, self.at, mode='confirmed')
        return next(p for p in report['predictions'] if p['quantity_a'] == quantity and p['item_a'] == self.titles[0]
                    and p['item_b'] == self.titles[1] and p['sale_scenarios'] ==
                    {'steam':'listing_price' if listing else 'current_bids','dmarket':'current_bids'})

    def open(self, route='real-one', quantity=2, listing=False):
        p = self.choose_prediction(quantity, listing)
        request = {'route_id':route,'prediction_id':p['prediction_id']}
        enter(self.journal, request, self.mandate, self.policy, self.at)
        return request, p

    def step(self, kind, route='real-one', **fields):
        self.seq += 1
        request = dict(action_id='step-'+str(self.seq), route_id=route, at=self.at,
                       reference='receipt-'+str(self.seq), kind=kind, **fields)
        record_step(self.journal, request)
        return request

    def buy(self, **kw):
        return self.step('buy_a', quantity=2, regular_cents=0, tradable_cents=160, fee_cents=0, entry_complete=True, **kw)

    def advance(self, seconds=1):
        self.at = stamp(utc(self.at)+timedelta(seconds=seconds))

    def test_real_funds_start_empty_and_search_never_copies_paper_money(self):
        self.assertFalse(status(self.journal, self.mandate)['configured'])
        self.assertEqual(available_capital(self.journal, self.mandate, mode='confirmed'), 0)
        self.assertEqual(available_capital(self.journal, self.mandate), 1000)
        report = search(self.journal, 'grow', self.watch, self.policy, self.mandate, self.at, 'confirmed')
        self.assertEqual(report['status'], 'no_available_funds')
        self.assertEqual(self.journal.records('real_funding'), [])

    def test_funding_once_retries_corrections_and_restriction_release(self):
        request = self.funding()
        record_funding(self.journal, request)
        self.assertEqual(len(self.journal.records('real_funding')), 1)
        with self.assertRaises(ValueError):
            self.funding(funding_id='other-opening')
        self.funding('unlock', regular=200, tradable=0)
        self.funding('withdraw', regular=100, tradable=0)
        correction = dict(correction_id='cf1',funding_id=request['funding_id'],at=self.at,reason='Correct opening receipt',regular_cents=450,tradable_cents=600)
        correct_funding(self.journal, correction)
        correct_funding(self.journal, correction)
        funds = status(self.journal,self.mandate)
        self.assertEqual(funds['accounts'], {'dmarket_regular':550,'dmarket_tradable':400})
        self.assertEqual(self.journal.get('real-funding:'+request['funding_id'])['regular_cents'],400)
        self.assertEqual(self.journal.records('outcome'), [])
        with self.assertRaises(ValueError):
            self.funding('withdraw', regular=1, tradable=1)
        with self.assertRaises(ValueError):
            self.funding('unlock', regular=401, tradable=0)

    def test_opening_saves_prediction_and_budget_without_inventing_purchase(self):
        self.funding()
        request, pred = self.open(listing=True)
        state = _state(self.journal,'real-one')
        self.assertEqual(state[4], {})
        self.assertEqual(state[5], 'entered')
        self.assertEqual(self.journal.records('route_event'), [])
        self.assertEqual(self.journal.records('paper_settings'), [])
        self.assertEqual(self.journal.get(request['prediction_id']), {k:v for k,v in pred.items() if k!='prediction_id'})
        funds = status(self.journal,self.mandate)
        self.assertEqual(funds['reserved_cents'],160)
        self.assertEqual(funds['available_cents'],840)
        self.assertEqual(funds['accounts']['dmarket_tradable'],600)
        saved = self.journal.get('confirmed-entry:real-one')
        self.assertEqual(saved['budget_before_entry']['available_cents'],1000)
        self.assertEqual(saved['funding'],[{'account':'dmarket_tradable','amount_cents':160}])
        self.advance(20000)
        self.assertEqual(enter(self.journal,request,self.mandate,self.policy,self.at)['status'],'already_opened')

    def test_two_real_plans_cannot_reserve_the_same_money(self):
        self.funding()
        pred = self.choose_prediction(8)
        def attempt(route):
            try:
                enter(self.journal,{'route_id':route,'prediction_id':pred['prediction_id']},self.mandate,self.policy,self.at)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(attempt,['first','second'])),[False,True])
        self.assertEqual(status(self.journal,self.mandate)['reserved_cents'],640)
        self.assertEqual(len(self.journal.records('confirmed_entry')),1)

    def test_partial_purchase_holds_remainder_until_explicit_finish(self):
        self.funding()
        self.open(quantity=4)
        receipt=self.step('buy_a',quantity=1,regular_cents=0,tradable_cents=80,fee_cents=0,entry_complete=False)
        record_step(self.journal,receipt)
        funds=status(self.journal,self.mandate)
        self.assertEqual((funds['available_cents'],funds['reserved_cents']),(680,240))
        self.assertEqual(_state(self.journal,'real-one')[4],{'730:Example Case':1})
        self.step('finish_entry')
        self.assertEqual(status(self.journal,self.mandate)['available_cents'],920)
        self.assertEqual(status(self.journal,self.mandate)['reserved_cents'],0)
        self.assertEqual(self.journal.records('outcome'),[])

    def test_cancel_unused_plan_releases_money_without_outcome(self):
        self.funding(); self.open()
        request=self.step('cancel',reason='Not proceeding')
        record_step(self.journal,request)
        self.assertEqual(status(self.journal,self.mandate)['available_cents'],1000)
        self.assertEqual(route_status(self.journal,'real-one',self.at)['state'],'cancelled')
        self.assertEqual(self.journal.records('outcome'),[])

    def test_receipt_failure_rolls_back_money_items_and_operation_together(self):
        self.funding(); self.open()
        before=len(self.journal.records('route_event'))
        with patch('arbitrage_v2.real_routes.add_event',side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                self.buy()
        self.assertEqual(len(self.journal.records('route_event')),before)
        self.assertEqual(self.journal.records('real_step'),[])
        self.assertEqual(status(self.journal,self.mandate)['reserved_cents'],160)
        self.buy()
        with self.assertRaises(ValueError):
            self.step('cancel',reason='Already purchased')

    def test_bad_actual_amount_and_funding_correction_cannot_spend_unrecorded_money(self):
        funding=self.funding(); self.open()
        with self.assertRaises(ValueError):
            self.step('buy_a',quantity=2,regular_cents=401,tradable_cents=0,fee_cents=0,entry_complete=True)
        self.buy()
        with self.assertRaises(ValueError):
            correct_funding(self.journal,dict(correction_id='bad-funds',funding_id=funding['funding_id'],at=self.at,reason='bad',regular_cents=400,tradable_cents=100))
        self.assertEqual(self.journal.records('funding_correction'),[])
        target=next(e for e in self.journal.records('route_event') if e['kind']=='movement')
        with self.assertRaises(ValueError):
            correct_event(self.journal,dict(correction_id='overspend',route_id='real-one',target_event_id=target['event_id'],at=self.at,reference='correct',reason='wrong receipt',value=-601,fee_cents=0))
        self.assertEqual(self.journal.records('event_correction'),[])

    def test_outside_funding_cannot_be_counted_again_as_route_income(self):
        funding=self.funding(); self.open(); self.buy()
        self.advance()
        with self.assertRaises(ValueError):
            add_event(self.journal,dict(event_id='duplicate-fund',route_id='real-one',at=self.at,reference=funding['reference'],kind='movement',account='dmarket_regular',net_delta_cents=400,fee_cents=0))
        receipt=next(e for e in self.journal.records('route_event') if e['kind']=='movement')
        with self.assertRaises(ValueError):
            self.funding('add',regular=160,tradable=0,reference=receipt['reference'])

    def test_actual_receipts_do_not_require_still_fresh_original_quotes(self):
        self.funding(); self.open()
        self.advance(5*86400)
        self.buy()
        unlock=stamp(utc(self.at)+timedelta(days=7))
        receipt=self.step('transfer_a',next_eligible_at=unlock)
        record_step(self.journal,receipt)
        with self.assertRaises(ValueError):
            self.step('sell_a',quantity=1,net_cents=100,fee_cents=15)
        self.assertEqual(_state(self.journal,'real-one')[4]['730:Example Case'],2)

    def test_real_search_and_entry_do_not_mix_paper_calibration(self):
        self.funding()
        p=self.choose_prediction()
        self.assertTrue(preview(self.journal,p['prediction_id'],self.mandate,self.policy,self.at)['ready'])
        self.assertFalse(paper_preview(self.journal,p['prediction_id'],self.mandate,self.policy,self.at)['ready'])
        record=dict(p,search_mode='paper');record.pop('prediction_id')
        wrong=self.journal.append('prediction',record)
        self.assertFalse(preview(self.journal,wrong,self.mandate,self.policy,self.at)['ready'])
        self.advance(5*3600)
        self.assertFalse(preview(self.journal,p['prediction_id'],self.mandate,self.policy,self.at)['ready'])

    def complete(self, listing=True):
        self.funding(); request,pred=self.open(listing=listing)
        self.step('buy_a',quantity=2,regular_cents=65,tradable_cents=100,fee_cents=5,entry_complete=True)
        self.step('transfer_a',next_eligible_at=stamp(utc(self.at)+timedelta(days=7)))
        self.advance(7*86400)
        self.step('sell_a',quantity=1,net_cents=100,fee_cents=15)
        self.assertEqual(_state(self.journal,'real-one')[5],'awaiting_steam_sale')
        self.step('sell_a',quantity=1,net_cents=100,fee_cents=15)
        self.step('buy_b',item_title='Return Case',quantity=2,net_cents=200,fee_cents=0,next_eligible_at=stamp(utc(self.at)+timedelta(days=10)))
        self.advance(10*86400)
        self.step('transfer_b')
        self.step('sell_b',item_title='Return Case',quantity=1,net_cents=130,fee_cents=15,account='dmarket_tradable')
        self.step('sell_b',item_title='Return Case',quantity=1,net_cents=130,fee_cents=15,account='dmarket_regular')
        self.step('cost',net_cents=3)
        return request,pred

    def test_complete_cycle_evaluates_only_after_resolution_and_learns_in_its_mode(self):
        request,pred=self.complete()
        self.assertIsNone(route_status(self.journal,'real-one',self.at)['actual_profit'])
        self.funding('add',regular=200,tradable=0)
        terminal=dict(event_id='close-real',route_id='real-one',at=self.at,reference='closing-check',kind='resolve',resolution='completed',residual_disposition='none',pending_operations=0,confirmed=True)
        add_event(self.journal,terminal);add_event(self.journal,terminal)
        outcome=self.journal.records('outcome')[0]
        self.assertEqual(outcome['actual_net_cents'],92)
        self.assertEqual(outcome['actual_entry_cost_cents'],165)
        self.assertEqual(outcome['actual_duration_seconds'],17*86400)
        self.assertEqual(outcome['fee_cents_reported'],65)
        self.assertEqual(outcome['engine_version'],LISTING_ENGINE)
        self.assertEqual(status(self.journal,self.mandate)['available_cents'],1295)
        self.assertEqual(self.journal.get(request['prediction_id'])['predicted_net_cents'],pred['predicted_net_cents'])
        self.recorded_quotes()
        fresh=screen(self.journal,self.watch,self.policy,self.at,mode='confirmed')['predictions']
        self.assertTrue(all(p['training_sample_count']==(1 if p['engine_version']==LISTING_ENGINE else 0) for p in fresh))
        with self.assertRaisesRegex(ValueError,'no resolved'):
            train(self.journal,pred['family'],'paper','recorded',self.at,LISTING_ENGINE)
        reopened=Journal(self.journal.path)
        self.assertEqual(status(reopened,self.mandate),status(self.journal,self.mandate))

    def test_http_full_flow_uses_same_operations_and_local_session(self):
        worker=Worker(self.journal,self.watch,self.policy,self.mandate,self.config,{})
        server=create_server(worker,fixtures.fixtures.ROOT,0)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def call(body=None,headers=None):
            c=HTTPConnection('127.0.0.1',server.server_port,timeout=20)
            try:
                c.request('POST' if body else 'GET','/api/action' if body else '/api/session',json.dumps(body) if body else None,headers or {})
                r=c.getresponse();return r.status,json.loads(r.read())
            finally:c.close()
        token=call()[1]['token'];headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{server.server_port}','X-Local-Token':token}
        body={'action':'funding','funding':dict(funding_id='http',kind='opening',at=self.at,reference='http-opening',regular_cents=1000,tradable_cents=0)}
        self.assertEqual(call(body)[0],403)
        self.assertEqual(call(body,headers)[0],200)
        with patch('arbitrage_v2.web.now',side_effect=lambda:self.at):
            code,report=call({'action':'search','purpose':'grow','mode':'confirmed'},headers)
            self.assertEqual(code,200)
            p=next(p for p in report['predictions'] if p['quantity_a']==2 and p['item_a']=='Example Case' and p['item_b']=='Return Case' and p['engine_version']==LISTING_ENGINE)
            self.assertTrue(call({'action':'preview_real','prediction_id':p['prediction_id']},headers)[1]['ready'])
            self.assertEqual(call({'action':'enter_real','entry':{'route_id':'http-real','prediction_id':p['prediction_id']}},headers)[0],200)
            receipt=dict(action_id='http-buy',route_id='http-real',kind='buy_a',at=self.at,reference='http-buy-receipt',quantity=2,regular_cents=160,tradable_cents=0,fee_cents=0,entry_complete=True)
            self.assertEqual(call({'action':'real_step','receipt':receipt},headers)[0],200)
            self.assertEqual(call({'action':'real_step','receipt':receipt},headers)[0],200)
            self.assertEqual(_state(self.journal,'http-real')[4],{'730:Example Case':2})
            operations=[('transfer_a',{'next_eligible_at':None}),('sell_a',{'quantity':1,'net_cents':100,'fee_cents':15}),
                ('sell_a',{'quantity':1,'net_cents':100,'fee_cents':15}),
                ('buy_b',{'item_title':'Return Case','quantity':2,'net_cents':200,'fee_cents':0,'next_eligible_at':None}),
                ('transfer_b',{}),('sell_b',{'item_title':'Return Case','quantity':2,'net_cents':260,'fee_cents':30,'account':'dmarket_tradable'})]
            for index,(kind,fields) in enumerate(operations):
                self.advance()
                receipt=dict(action_id='http-op-'+str(index),route_id='http-real',kind=kind,at=self.at,reference='http-receipt-'+str(index),**fields)
                code,response=call({'action':'real_step','receipt':receipt},headers)
                self.assertEqual(code,200,response)
                self.assertEqual(self.journal.records('outcome'),[])
            close=dict(event_id='http-close',route_id='http-real',at=self.at,reference='http-final',kind='resolve',resolution='completed',residual_disposition='none',pending_operations=0,confirmed=True)
            self.assertEqual(call({'action':'event','event':close},headers)[0],200)
            self.assertEqual(call({'action':'event','event':close},headers)[0],200)
        self.assertEqual(self.journal.records('outcome')[0]['actual_net_cents'],100)
        self.assertEqual(self.journal.records('request_attempt'),[])

    def test_receipt_correction_and_backup_restore_preserve_balances_and_prediction(self):
        import sqlite3
        from contextlib import closing
        request,pred=self.complete(listing=False)
        target=next(e for e in self.journal.records('route_event') if e['kind']=='movement' and e['account']=='dmarket_regular' and e['net_delta_cents']<0)
        correction=dict(correction_id='actual-cost',route_id='real-one',target_event_id=target['event_id'],at=self.at,reference='corrected-purchase',reason='Actual receipt was 60 cents',value=-60,fee_cents=0)
        correct_event(self.journal,correction);correct_event(self.journal,correction)
        with self.assertRaises(ValueError):
            correct_event(self.journal,dict(correction,correction_id='reverse-purchase',value=60))
        add_event(self.journal,dict(event_id='closed',route_id='real-one',at=self.at,reference='settled',kind='resolve',resolution='completed',residual_disposition='none',pending_operations=0,confirmed=True))
        outcome=self.journal.records('outcome')[0]
        self.assertEqual(outcome['actual_net_cents'],97)
        with self.assertRaises(ValueError):
            correct_event(self.journal,dict(correction,correction_id='late-correction',value=-50))
        restored_path=self.journal.path.parent/'restored.sqlite3'
        with self.journal.connect() as source, closing(sqlite3.connect(restored_path)) as destination:
            source.backup(destination)
        restored=Journal(restored_path)
        self.assertEqual(status(restored,self.mandate),status(self.journal,self.mandate))
        self.assertEqual(route_status(restored,'real-one',self.at),route_status(self.journal,'real-one',self.at))
        self.assertEqual(restored.get(request['prediction_id'])['predicted_net_cents'],pred['predicted_net_cents'])

    def test_leftover_and_pending_closure_need_explicit_user_decision(self):
        self.funding();self.open();self.buy()
        terminal=dict(event_id='end',route_id='real-one',at=self.at,reference='final',kind='resolve',resolution='write_off',residual_disposition='none',pending_operations=0,confirmed=True)
        with self.assertRaises(ValueError):
            add_event(self.journal,terminal)
        with self.assertRaises(ValueError):
            add_event(self.journal,dict(terminal,residual_disposition='written_off',pending_operations=1))
        self.assertEqual(self.journal.records('outcome'),[])
        add_event(self.journal,dict(terminal,residual_disposition='written_off'))
        self.assertEqual(self.journal.records('outcome')[0]['actual_net_cents'],-160)
        self.assertEqual(status(self.journal,self.mandate)['available_cents'],840)
