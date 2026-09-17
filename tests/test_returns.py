from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.journal import Journal
from arbitrage_v2.prediction import calculate, return_snapshot
from arbitrage_v2.returns import review_returns
from arbitrage_v2.routes import add_event, open_route, route_status, train


ROOT = Path(__file__).resolve().parents[1]
AT = '2030-01-01T00:00:00Z'  # Explicit future paper fixture; no real transaction.
POLICY = json.loads((ROOT / 'config/research_assumptions.json').read_text())


class ReturnReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Journal(Path(self.tmp.name) / 'journal.sqlite3')
        self.journal.initialize()
        quote = {'title': 'Fracture Case', 'input_kind': 'synthetic', 'evidence_ids': [],
                 'dmarket_ask': {'price_cents': 55, 'quantity': 18},
                 'steam_bid': {'price_cents': 66, 'quantity': 250},
                 'steam_ask': {'price_cents': 19, 'quantity': 36},
                 'dmarket_bid': {'price_cents': 45, 'quantity': 96}}
        pred = calculate(quote, dict(quote, title='Kilowatt Case'), 12, POLICY)
        pred['predicted_at'] = AT
        self.pred_id = self.journal.append('prediction', pred)
        open_route(self.journal, 'r1', self.pred_id, 'paper', AT)
        self.watch = {'items': [{'app_id': 730, 'title': 'Kilowatt Case'}]}

    def event(self, kind, **fields):
        number = len(self.journal.records('route_event'))
        event = dict(event_id=f'e{number}', route_id='r1', at=AT, kind=kind,
                     reference=f'fixture-{number}', **fields)
        add_event(self.journal, event)

    def ready(self, wallet=650):
        self.event('movement', account='dmarket_regular', net_delta_cents=-660, fee_cents=0)
        self.event('movement', account='steam_wallet', net_delta_cents=wallet, fee_cents=84)
        self.event('progress', stage='steam_wallet', next_eligible_at=None, note='Paper funds available')

    def captures(self, title='Kilowatt Case', ask='0.19', steam_quantity=36,
                 target='45', target_quantity='96', input_kind='synthetic',
                 at=AT, source_at=AT, status=200):
        steam = {'result': {'item': {'appId': 730, 'marketName': title},
                 'meta': {'flags': {'commodity': True}},
                 'histogram': {'date': source_at,
                               'sellOrders': [{'price': ask, 'quantity': steam_quantity}]}}}
        target_row = {'title': title, 'attributes': {}, 'price': target, 'amount': target_quantity}
        targets = {'orders': [target_row, target_row,
                   {'title': title, 'attributes': {'phase': 'special'}, 'price': '999', 'amount': '1000'}]}
        for kind, payload in [('details', steam), ('targets', targets)]:
            self.journal.append('capture', {'app_id': 730, 'title': title, 'kind': kind,
                'retrieved_at': at, 'payload': payload, 'status': status,
                'error': None if status == 200 else 'http_' + str(status), 'input_kind': input_kind})

    def review(self, **kwargs):
        return review_returns(self.journal, 'r1', self.watch, POLICY, AT, **kwargs)

    def test_recorded_wallet_not_original_projection_funds_whole_items(self):
        self.ready(650)
        self.captures()
        before = self.journal.get(self.pred_id, 'prediction')
        report = self.review(remaining_cost_cents=5)
        self.assertEqual(report['status'], 'conditional_options')
        self.assertEqual(report['wallet_available_cents'], 650)
        option = report['options'][0]
        self.assertEqual(option['quantity_b'], 34)
        self.assertEqual(option['return_purchase_cents'], 646)
        self.assertEqual(option['steam_wallet_residual_cents'], 4)
        self.assertEqual(option['predicted_dmarket_fee_cents'], 170)
        self.assertEqual(option['predicted_dmarket_receipts_cents'], 1360)
        self.assertEqual(option['predicted_receipts_after_remaining_costs_cents'], 1355)
        self.assertEqual(option['minimum_return_delay_seconds'], 10 * 86400)
        self.assertIsNone(option['predicted_remaining_duration_seconds'])
        self.assertEqual(before['steam_proceeds_cents'], 708)
        self.assertEqual(self.journal.get(self.pred_id, 'prediction'), before)
        self.assertEqual(len(self.journal.records('prediction')), 1)
        self.assertIsNone(report['actual_profit'])
        self.assertEqual(self.journal.records('outcome'), [])
        self.assertEqual(self.journal.records('model'), [])
        with self.assertRaisesRegex(ValueError, 'no resolved outcomes'):
            train(self.journal, before['family'], 'paper', 'synthetic', AT)

    def test_unneeded_entry_books_do_not_block_return_and_depth_is_not_doubled(self):
        self.ready()
        self.captures(steam_quantity=12, target_quantity='3')
        option = self.review()['options'][0]
        self.assertEqual(option['quantity_b'], 3)
        self.assertEqual(option['predicted_dmarket_receipts_cents'], 120)
        self.assertEqual(len(option['snapshot']['evidence_ids']), 2)
        # No DMarket offers or Steam bids were supplied; they aren't return inputs.
        self.assertNotIn('buyOrders', self.journal.records('capture')[0]['payload']['result']['histogram'])

    def test_unknown_remaining_costs_are_not_silently_zero_or_full_route_costs(self):
        self.ready()
        self.captures()
        policy = deepcopy(POLICY)
        policy['other_cost_cents'] = 500
        policy.pop('minimum_return_delay_seconds')
        report = review_returns(self.journal, 'r1', self.watch, policy, AT)
        option = report['options'][0]
        self.assertIsNone(option['remaining_cost_cents'])
        self.assertIsNone(option['predicted_receipts_after_remaining_costs_cents'])
        self.assertIsNone(option['minimum_return_delay_seconds'])
        self.assertEqual(option['predicted_dmarket_receipts_cents'], 1360)
        self.assertFalse(report['entry_recommendation'])

    def test_no_funds_wrong_stage_owned_items_and_known_delay_are_explicit(self):
        report = self.review()
        self.assertIn('no_recorded_steam_wallet_funds', report['blockers'])
        self.assertIn('route_not_at_steam_wallet_stage', report['blockers'])
        self.assertEqual(report['options'], [])
        self.ready()
        self.event('asset', asset_key='730:Fracture Case', quantity_delta=1)
        self.event('progress', stage='steam_wallet', next_eligible_at='2030-01-02T00:00:00Z', note='Pending funds')
        report = self.review()
        self.assertIn('items_still_held_complete_or_reconcile_current_leg', report['blockers'])
        self.assertIn('recorded_eligibility_delay_not_elapsed', report['blockers'])
        self.assertEqual(report['status'], 'blocked')

    def test_wallet_from_other_route_is_not_available(self):
        self.ready(18)
        self.captures()
        open_route(self.journal, 'r2', self.pred_id, 'paper', AT)
        add_event(self.journal, dict(event_id='r2-wallet', route_id='r2', at=AT, kind='movement',
                  reference='another-route-funds', account='steam_wallet', net_delta_cents=10000, fee_cents=0))
        report = self.review()
        self.assertEqual(report['wallet_available_cents'], 18)
        self.assertEqual(report['status'], 'no_supported_options')
        self.assertEqual(report['excluded'][0]['reason'], 'no_affordable_supported_return')

    def test_new_review_uses_unspent_wallet_and_invalidates_old_review(self):
        self.ready()
        self.captures()
        first = self.review()
        self.assertEqual(first['review_id'], self.review()['review_id'])
        self.assertFalse(route_status(self.journal, 'r1', AT)['latest_return_review']['refresh_required'])
        self.event('movement', account='steam_wallet', net_delta_cents=-190, fee_cents=0)
        self.assertTrue(route_status(self.journal, 'r1', AT)['latest_return_review']['route_state_changed'])
        second = self.review()
        self.assertNotEqual(second['review_id'], first['review_id'])
        self.assertEqual(second['options'][0]['quantity_b'], 24)
        self.assertEqual(self.journal.get(first['review_id'], 'return_review')['wallet_available_cents'], 650)

    def test_alternative_item_sorted_by_receipts_does_not_rewrite_original(self):
        self.ready()
        self.captures()
        self.captures('Recoil Case', ask='0.10', target='30', target_quantity='100', steam_quantity=100)
        self.watch['items'].append({'app_id': 730, 'title': 'Recoil Case'})
        report = self.review()
        self.assertEqual([r['item_b'] for r in report['options']], ['Recoil Case', 'Kilowatt Case'])
        self.assertEqual(self.journal.get(self.pred_id)['item_b'], 'Kilowatt Case')
        self.assertEqual(len(report['options']), 2)

    def test_stale_underlying_book_is_not_refreshed_by_new_capture(self):
        self.ready()
        self.captures(source_at='2029-12-30T00:00:00Z')
        report = self.review()
        self.assertEqual(report['status'], 'no_supported_options')
        self.assertEqual(report['excluded'][0]['reason'], 'stale_steam_book')

    def test_new_failed_request_does_not_fall_back_to_previous_good_quote(self):
        self.ready()
        self.captures()
        self.captures(status=429)
        self.assertEqual(self.review()['excluded'][0]['reason'], 'provider_request_failed')

    def test_quote_and_route_evidence_partitions_cannot_mix(self):
        self.ready()
        self.captures(input_kind='recorded')
        self.assertEqual(self.review()['excluded'][0]['reason'], 'route_and_quote_evidence_partition_mismatch')

    def test_new_captures_and_expiry_flag_previous_review(self):
        self.ready()
        self.captures()
        self.review()
        self.captures(ask='0.20')
        summary = route_status(self.journal, 'r1', AT)['latest_return_review']
        self.assertTrue(summary['newer_market_evidence'])
        self.assertTrue(summary['refresh_required'])
        later = stamp(utc(AT) + timedelta(seconds=14401))
        self.assertTrue(route_status(self.journal, 'r1', later)['latest_return_review']['quotes_expired'])

    def test_late_imported_observation_cannot_be_used_in_earlier_replay(self):
        historical = '2020-01-01T00:00:00Z'
        self.captures(at=historical, source_at=historical)
        with self.assertRaisesRegex(ValueError, 'missing_capture'):
            return_snapshot(self.journal, 'Kilowatt Case', historical, 14400)

    def test_closed_route_rejects_new_review_and_open_route_has_no_profit(self):
        self.ready()
        self.captures()
        self.review()
        self.assertIsNone(route_status(self.journal, 'r1', AT)['actual_profit'])
        self.event('resolve', resolution='write_off', residual_disposition='written_off',
                   pending_operations=0, confirmed=True)
        with self.assertRaisesRegex(ValueError, 'already resolved'):
            self.review()
        self.assertTrue(route_status(self.journal, 'r1', AT)['latest_return_review']['refresh_required'])

    def test_invalid_costs_fees_and_duplicate_watchlist_fail_without_review(self):
        for value in (-1, True, 1.5):
            with self.subTest(cost=value), self.assertRaises(ValueError):
                self.review(remaining_cost_cents=value)
        policy = deepcopy(POLICY)
        policy['dmarket_fee']['bps'] = 10000
        with self.assertRaisesRegex(ValueError, 'invalid fee rate'):
            review_returns(self.journal, 'r1', self.watch, policy, AT)
        self.watch['items'] *= 2
        with self.assertRaisesRegex(ValueError, 'duplicate watchlist'):
            self.review()
        self.assertEqual(self.journal.records('return_review'), [])

    def test_cli_persists_review_and_surfaces_original_prediction_id(self):
        self.ready()
        self.captures()
        watch_path = Path(self.tmp.name) / 'watch.json'
        watch_path.write_text(json.dumps(self.watch))
        proc = subprocess.run([sys.executable, '-B', '-m', 'arbitrage_v2',
            '--journal', str(self.journal.path), 'route-returns', 'r1', str(watch_path),
            '--policy', str(ROOT / 'config/research_assumptions.json'), '--as-of', AT],
            cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report['original_prediction_id'], self.pred_id)
        self.assertEqual(self.journal.get(report['review_id'], 'return_review')['route_id'], 'r1')
        saved = subprocess.run([sys.executable, '-B', '-m', 'arbitrage_v2',
            '--journal', str(self.journal.path), 'return-review', report['review_id']],
            cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(json.loads(saved.stdout), report)
