from copy import deepcopy
from datetime import timedelta
from http.client import HTTPConnection
import json
import threading
import unittest
from unittest.mock import patch

import test_real_growth as real
import test_discovery as discovery
from arbitrage_v2.discovery import route_inputs
from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.holding_costs import route_cost, summary
from arbitrage_v2.prediction import screen, MIDPOINT_ENGINE, LISTING_ENGINE, steam_net, dmarket_net
from arbitrage_v2.real_routes import preview, enter, record_step
from arbitrage_v2.route_details import detail
from arbitrage_v2.routes import add_event, correct_event, _state
from arbitrage_v2 import steam_wallet
from arbitrage_v2.web import create_server, overview
from arbitrage_v2.worker import Worker, search
from arbitrage_v2.recovery import open_recovery


class UIDataTests(unittest.TestCase):
    def setUp(self):
        self.ex=real.RealGrowthTests(); self.ex.setUp(); self.addCleanup(self.ex.doCleanups)
        self.j=self.ex.journal

    def replace(self,title,kind,modify):
        discovery.DiscoveryTests.replace_capture(self.ex,title,kind,modify)

    def midpoint_quotes(self):
        for title in self.ex.titles:
            self.replace(title,'details',lambda c:c['payload']['result']['histogram']['sellOrders'][0].update(price='1.50'))
            self.replace(title,'offers',lambda c:[i.update(priceCents=150) for i in c['payload']['items']])
        self.replace('Example Case','offers',lambda c:[i.update(priceCents=80) for i in c['payload']['items']])

    def candidates(self):
        return screen(self.j,self.ex.watch,self.ex.policy,self.ex.at,mode='confirmed')['predictions']

    def wallet(self,amount=500,kind='balance',at=None,identifier='wallet-one'):
        request={'wallet_id':identifier,'kind':kind,'at':at or self.ex.at,'reference':identifier,'amount_cents':amount}
        steam_wallet.record(self.j,request);return request

    def test_nine_sale_combinations_and_midpoint_fees_are_not_fills(self):
        self.midpoint_quotes()
        rows=[p for p in self.candidates() if p['item_a']=='Example Case' and p['item_b']=='Return Case']
        self.assertEqual(len({tuple(p['sale_scenarios'].values()) for p in rows}),9)
        p=next(p for p in rows if set(p['sale_scenarios'].values())=={'midpoint'} and p['quantity_a']==2)
        self.assertEqual(p['engine_version'],MIDPOINT_ENGINE)
        sale=p['quote_legs']['steam_sale']
        self.assertEqual(sale['assumed_price_cents'],132)
        self.assertEqual(sale['net_cents'],2*steam_net(132,500,1000,1,1))
        self.assertEqual(p['quote_legs']['exit']['assumed_price_cents'],135)
        self.assertEqual(p['predicted_dmarket_receipts_cents'],p['quantity_b']*dmarket_net(135,1000,1))
        self.assertEqual(sale['fills'],[])
        self.assertTrue(sale['quantity_is_assumed'])
        self.assertEqual(len(p['price_sources']['exit']['sources']),2)
        self.assertIsNone(p['predicted_duration_seconds'])
        self.assertEqual(p['training_sample_count'],0)

    def test_missing_or_crossed_prices_only_disable_affected_midpoint(self):
        self.midpoint_quotes()
        self.replace('Example Case','details',lambda c:c['payload']['result']['histogram'].update(buyOrders=[]))
        rows=[p for p in self.candidates() if p['item_a']=='Example Case']
        self.assertTrue(rows)
        self.assertEqual({p['sale_scenarios']['steam'] for p in rows},{'listing_price'})
        self.replace('Return Case','offers',lambda c:[i.update(priceCents=50) for i in c['payload']['items']])
        rows=[p for p in self.candidates() if p['item_a']=='Example Case' and p['item_b']=='Return Case']
        self.assertEqual({p['sale_scenarios']['dmarket'] for p in rows},{'current_bids','listing_price'})

    def test_midpoint_real_entry_and_changed_second_source_revalidation(self):
        self.midpoint_quotes();self.ex.funding()
        p=next(p for p in self.candidates() if p['item_a']=='Example Case' and p['item_b']=='Return Case'
               and set(p['sale_scenarios'].values())=={'midpoint'} and p['quantity_a']==2)
        self.assertTrue(preview(self.j,p['prediction_id'],self.ex.mandate,self.ex.policy,self.ex.at)['ready'])
        enter(self.j,{'route_id':'mid-real','prediction_id':p['prediction_id']},self.ex.mandate,self.ex.policy,self.ex.at)
        self.assertEqual(self.j.records('route_event'),[])
        self.replace('Return Case','offers',lambda c:[i.update(priceCents=155) for i in c['payload']['items']])
        self.assertFalse(preview(self.j,p['prediction_id'],self.ex.mandate,self.ex.policy,self.ex.at)['ready'])
        self.assertEqual(self.j.get(p['prediction_id'])['engine_version'],MIDPOINT_ENGINE)

    def test_midpoint_never_enters_automatic_paper_engine(self):
        from arbitrage_v2.paper_entry import preview as paper_preview
        self.midpoint_quotes()
        rows=screen(self.j,self.ex.watch,self.ex.policy,self.ex.at,mode='paper')['predictions']
        p=next(p for p in rows if p['engine_version']==MIDPOINT_ENGINE)
        review=paper_preview(self.j,p['prediction_id'],self.ex.mandate,self.ex.policy,self.ex.at)
        self.assertFalse(review['ready'])
        self.assertTrue(any('midpoint' in b for b in review['blockers']))
        self.assertEqual(self.j.records('paper_settings'),[])

    def test_holding_cost_partials_weighted_average_and_final_rounding(self):
        self.ex.funding();self.ex.open(quantity=3)
        self.ex.step('buy_a',quantity=2,regular_cents=0,tradable_cents=101,fee_cents=1,entry_complete=False)
        self.ex.step('buy_a',quantity=1,regular_cents=0,tradable_cents=100,fee_cents=0,entry_complete=True)
        self.assertEqual(route_cost(self.j,'real-one')['cost_cents'],201)
        self.ex.step('transfer_a',next_eligible_at=None)
        self.assertEqual(route_cost(self.j,'real-one')['cost_cents'],201)
        self.ex.step('sell_a',quantity=1,net_cents=150,fee_cents=10)
        self.assertEqual(route_cost(self.j,'real-one')['cost_cents'],134)
        self.ex.step('sell_a',quantity=2,net_cents=300,fee_cents=20)
        self.assertEqual(route_cost(self.j,'real-one')['cost_cents'],0)
        self.ex.step('buy_b',item_title='Return Case',quantity=3,net_cents=200,fee_cents=0,next_eligible_at=None)
        self.ex.step('transfer_b')
        self.ex.step('sell_b',item_title='Return Case',quantity=1,net_cents=90,fee_cents=10,account='dmarket_regular')
        self.assertEqual(route_cost(self.j,'real-one')['cost_cents'],134)
        self.ex.step('sell_b',item_title='Return Case',quantity=2,net_cents=180,fee_cents=20,account='dmarket_regular')
        self.assertEqual(route_cost(self.j,'real-one')['cost_cents'],0)
        self.assertEqual(self.j.records('outcome'),[])

    def test_holding_cost_corrections_reservations_and_modes(self):
        self.ex.funding();self.ex.open()
        self.assertEqual(route_cost(self.j,'real-one')['cost_cents'],0)
        self.ex.buy()
        target=next(e for e in self.j.records('route_event') if e['kind']=='movement')
        correct_event(self.j,dict(correction_id='cost-fix',route_id='real-one',target_event_id=target['event_id'],at=self.ex.at,reference='fix',reason='Receipt correction',value=-161,fee_cents=0))
        self.ex.step('transfer_a',next_eligible_at=None);self.ex.step('sell_a',quantity=1,net_cents=100,fee_cents=10)
        result=summary(self.j)
        self.assertEqual(result['confirmed']['cost_cents'],81)
        self.assertEqual(result['paper']['cost_cents'],0)
        self.assertEqual(self.j.records('outcome'),[])

    def test_unmatched_legacy_asset_cost_is_unknown(self):
        self.ex.funding();self.ex.open()
        add_event(self.j,dict(event_id='legacy-item',route_id='real-one',at=self.ex.at,reference='no-purchase',kind='asset',asset_key='730:Example Case',quantity_delta=1))
        cost=route_cost(self.j,'real-one')
        self.assertFalse(cost['complete']);self.assertIsNone(cost['cost_cents'])

    def test_recovery_does_not_duplicate_cost_or_wallet_money(self):
        self.wallet();self.ex.advance();self.ex.funding();self.ex.open();self.ex.buy();self.ex.step('transfer_a',next_eligible_at=None)
        self.ex.step('sell_a',quantity=1,net_cents=100,fee_cents=10)
        add_event(self.j,dict(event_id='write-off',route_id='real-one',at=self.ex.at,reference='writeoff',kind='resolve',resolution='write_off',residual_disposition='written_off',pending_operations=0,confirmed=True))
        clock=self.enterContext(patch("arbitrage_v2.recovery.datetime"));clock.now.return_value=utc(self.ex.at)+timedelta(days=100)
        open_recovery(self.j,dict(route_id='recover',parent_route_id='real-one',at=self.ex.at,reference='recover',asset_key='730:Example Case',quantity=1,steam_wallet_cents=100))
        self.assertEqual(route_cost(self.j,'recover')['cost_cents'],0)
        self.assertEqual(steam_wallet.status(self.j)['balance_cents'],600)

    def test_wallet_unknown_baseline_no_double_count_and_outside_adjustment(self):
        self.assertIsNone(steam_wallet.status(self.j)['balance_cents'])
        self.ex.funding();self.ex.open();self.ex.buy();self.ex.step('transfer_a',next_eligible_at=None)
        self.ex.step('sell_a',quantity=1,net_cents=100,fee_cents=10)
        request=self.wallet(800);steam_wallet.record(self.j,request)
        self.assertEqual(steam_wallet.status(self.j)['balance_cents'],800)
        self.ex.advance();self.ex.step('sell_a',quantity=1,net_cents=100,fee_cents=10)
        self.wallet(-50,'adjustment',identifier='outside-spend')
        self.assertEqual(steam_wallet.status(self.j)['balance_cents'],850)
        self.assertEqual(len(self.j.records('steam_wallet_record')),2)
        self.ex.advance();self.wallet(900,identifier='reconcile')
        self.assertEqual(steam_wallet.status(self.j)['balance_cents'],900)

    def test_wallet_correction_replays_effective_route_amount_without_touching_profit(self):
        self.wallet();self.ex.advance();self.ex.funding();self.ex.open();self.ex.buy();self.ex.step('transfer_a',next_eligible_at=None)
        self.ex.step('sell_a',quantity=1,net_cents=100,fee_cents=10)
        target=next(e for e in self.j.records('route_event') if e['kind']=='movement' and e['account']=='steam_wallet')
        correct_event(self.j,dict(correction_id='sale-fix',route_id='real-one',target_event_id=target['event_id'],at=self.ex.at,reference='sale-fix',reason='Actual net',value=90,fee_cents=10))
        self.assertEqual(steam_wallet.status(self.j)['balance_cents'],590)
        steam_wallet.correct(self.j,dict(correction_id='wallet-fix',wallet_id='wallet-one',at=self.ex.at,reason='Opening wallet',amount_cents=400))
        self.assertEqual(steam_wallet.status(self.j)['balance_cents'],490)
        self.assertEqual(self.j.records('outcome'),[])

    def test_wallet_duplicate_references_block_both_directions(self):
        self.wallet(identifier='same')
        with self.assertRaises(ValueError):steam_wallet.record(self.j,dict(wallet_id='dup',kind='balance',at=self.ex.at,reference='same',amount_cents=100))
        self.ex.funding();self.ex.open()
        with self.assertRaises(ValueError):record_step(self.j,dict(action_id='duplicate-wallet',route_id='real-one',at=self.ex.at,kind='buy_a',quantity=1,regular_cents=1,tradable_cents=0,fee_cents=0,entry_complete=True,reference='same'))
        self.ex.buy()
        ref=self.j.records('real_step')[0]['reference']
        with self.assertRaises(ValueError):steam_wallet.record(self.j,dict(wallet_id='dup2',kind='balance',at=self.ex.at,reference=ref,amount_cents=100))

    def test_wallet_negative_reconciliation_and_paper_exclusion(self):
        self.wallet(10);self.ex.advance();self.wallet(-20,'adjustment',identifier='outside')
        self.assertTrue(steam_wallet.status(self.j)['needs_reconciliation'])
        from arbitrage_v2.paper_entry import enter as paper_enter
        p=next(p for p in screen(self.j,self.ex.watch,self.ex.policy,self.ex.at)['predictions'] if p['engine_version']=='observed-depth-v2' and p['quantity_a']==2)
        paper_enter(self.j,{'route_id':'paper','prediction_id':p['prediction_id']},self.ex.mandate,self.ex.policy,self.ex.at)
        add_event(self.j,dict(event_id='paper-money',route_id='paper',at=self.ex.at,reference='paper-income',kind='movement',account='steam_wallet',net_delta_cents=1000,fee_cents=0))
        self.assertEqual(steam_wallet.status(self.j)['balance_cents'],-10)

    def test_saved_details_do_not_substitute_new_prices_and_handle_legacy(self):
        p=self.ex.choose_prediction()
        before=detail(self.j,prediction_id=p['prediction_id'])
        self.replace('Example Case','details',lambda c:c['payload']['result']['histogram']['buyOrders'][0].update(price='9.99'))
        after=detail(self.j,prediction_id=p['prediction_id'])
        self.assertEqual(before,after)
        self.assertEqual(after['items']['a']['steam_highest_bid_cents'],115)
        old={k:v for k,v in self.j.get(p['prediction_id']).items() if k not in {'quote_legs','item_a_identity','item_b_identity','sale_scenarios'}}
        old['evidence_ids']=[]
        identifier=self.j.append('prediction',old)
        result=detail(self.j,prediction_id=identifier)
        self.assertNotIn('quote_legs',result['prediction'])
        self.assertIsNone(result['items']['a']['steam_lowest_ask_cents'])
        self.assertIn('Example%20Case',result['items']['a']['links']['steam'])

    def test_search_modes_are_saved_separately_in_status(self):
        self.ex.funding()
        for mode in ('paper','confirmed'):
            self.j.append('search_report',dict(search(self.j,'grow',self.ex.watch,self.ex.policy,self.ex.mandate,self.ex.at,mode),at=self.ex.at))
        worker=Worker(self.j,self.ex.watch,self.ex.policy,self.ex.mandate,self.ex.config,{})
        with patch('arbitrage_v2.web.now',return_value=self.ex.at):
            value=overview(worker)
        self.assertEqual(value['searches']['paper']['mode'],'paper')
        self.assertEqual(value['searches']['confirmed']['mode'],'confirmed')
        self.assertFalse(value['steam_wallet']['configured'])

    def test_http_detail_and_wallet_actions_are_local_and_idempotent(self):
        worker=Worker(self.j,self.ex.watch,self.ex.policy,self.ex.mandate,self.ex.config,{})
        server=create_server(worker,real.fixtures.fixtures.ROOT,0)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def call(path,body=None,headers=None):
            con=HTTPConnection('127.0.0.1',server.server_port,timeout=20)
            try:
                con.request('POST' if body else 'GET',path,json.dumps(body) if body else None,headers or {})
                response=con.getresponse();return response.status,json.loads(response.read())
            finally:con.close()
        headers={'Origin':f'http://127.0.0.1:{server.server_port}','Content-Type':'application/json','X-Local-Token':call('/api/session')[1]['token']}
        p=self.ex.choose_prediction();before=len(self.j.records('prediction'))
        self.assertEqual(call('/api/route-detail?prediction_id='+p['prediction_id'])[0],200)
        self.assertEqual(call('/api/route-detail?prediction_id='+p['prediction_id']+'&route_id=x')[0],400)
        body={'action':'steam_wallet','record':dict(wallet_id='http-wallet',kind='balance',at=self.ex.at,reference='wallet-balance',amount_cents=500)}
        self.assertEqual(call('/api/action',body)[0],403)
        self.assertEqual(call('/api/action',body,headers)[0],200)
        self.assertEqual(call('/api/action',body,headers)[0],200)
        self.assertEqual(len(self.j.records('steam_wallet_record')),1)
        self.assertEqual(len(self.j.records('prediction')),before)
        self.assertEqual(self.j.records('request_attempt'),[])

    def test_midpoint_learning_does_not_reuse_listing_outcomes(self):
        self.ex.complete(listing=True)
        add_event(self.j,dict(event_id='closed-listing',route_id='real-one',at=self.ex.at,reference='settled-listing',kind='resolve',resolution='completed',residual_disposition='none',pending_operations=0,confirmed=True))
        self.ex.recorded_quotes();self.midpoint_quotes()
        rows=self.candidates()
        self.assertTrue(any(p['engine_version']==MIDPOINT_ENGINE for p in rows))
        self.assertTrue(all(p['training_sample_count']==0 for p in rows if p['engine_version']==MIDPOINT_ENGINE))
        self.assertTrue(all(p['training_sample_count']==1 for p in rows if p['engine_version']==LISTING_ENGINE))

    def test_wallet_records_and_holding_cost_survive_checked_restore(self):
        from arbitrage_v2.backups import create_backup, restore_backup
        from arbitrage_v2.journal import Journal
        from pathlib import Path
        import shutil
        self.wallet();self.ex.advance();self.ex.funding();self.ex.open();self.ex.buy()
        root=self.j.path.parent/'backup-project';root.mkdir()
        shutil.copytree(Path('config'),root/'config')
        (root/'config/mandate.json').write_text(json.dumps(self.ex.mandate))
        clock=self.enterContext(patch('arbitrage_v2.backups.datetime'));clock.now.return_value=utc(self.ex.at)+timedelta(days=100)
        saved=root/'saved';create_backup(self.j,saved,root)
        restored=Journal(root/'restored.sqlite3');restored.initialize()
        restore_backup(restored,saved,root,replace_current=True)
        self.assertEqual(steam_wallet.status(restored),steam_wallet.status(self.j))
        self.assertEqual(route_cost(restored,'real-one'),route_cost(self.j,'real-one'))
