from copy import deepcopy
from datetime import timedelta
import unittest

import test_paper_entry as fixtures
from arbitrage_v2.depth import ENGINE
from arbitrage_v2.discovery import snapshot
from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.paper_entry import preview, enter
from arbitrage_v2.prediction import LISTING_ENGINE, calculate, screen, steam_net, dmarket_net
from arbitrage_v2.worker import search


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        fixtures.EntryTests.setUp(self)

    recorded_quotes = fixtures.EntryTests.recorded_quotes

    def replace_capture(self, title, kind, modify):
        row = next(r for r in reversed(self.journal.records('capture')) if r['title'] == title and r['kind'] == kind)
        row = {k: deepcopy(v) for k, v in row.items() if k not in {'record_id', '_recorded_at'}}
        row['revision'] = len(self.journal.records('capture'))
        modify(row)
        self.journal.append('capture', row)

    def scan(self, **kw):
        return screen(self.journal, self.watch, self.policy, self.at, **kw)

    def candidates(self, result, steam='current_bids', dmarket='current_bids'):
        return [p for p in result['predictions'] if p['item_a'] == 'Example Case' and p['item_b'] == 'Return Case'
                and p['sale_scenarios'] == {'steam': steam, 'dmarket': dmarket}]

    def test_no_history_or_outcomes_does_not_gate_positive_opportunity(self):
        self.assertEqual(self.journal.records('outcome'), [])
        predictions = self.candidates(self.scan())
        self.assertTrue(predictions)
        self.assertTrue(all(p['predicted_net_cents'] > 0 for p in predictions))
        for p in predictions:
            self.assertEqual(p['training_sample_count'], 0)
            self.assertEqual(p['predicted_net_cents'], p['base_net_cents'])
            self.assertIsNone(p['predicted_duration_seconds'])
            self.assertEqual(p['uncertainty']['future_demand'], 'unknown')

    def test_cli_cannot_attach_bid_fill_engine_to_listing_estimate(self):
        from arbitrage_v2.paper import configure
        from arbitrage_v2.routes import open_route
        pred = self.candidates(self.scan(), steam='listing_price')[0]
        open_route(self.journal, 'manual-open', pred['prediction_id'], 'paper', self.at)
        with self.assertRaisesRegex(ValueError, 'listings are not implemented'):
            configure(self.journal, 'manual-open', True, self.watch, self.policy, self.at)
        self.assertEqual(self.journal.records('paper_settings'), [])

    def test_irrelevant_missing_books_do_not_block_bid_route_or_entry(self):
        self.replace_capture('Example Case', 'targets', lambda c: c.update(status=503, error='http_503', payload=None))
        self.replace_capture('Example Case', 'details', lambda c: c['payload']['result']['histogram'].pop('sellOrders'))
        self.replace_capture('Return Case', 'offers', lambda c: c.update(status=503, error='http_503', payload=None))
        self.replace_capture('Return Case', 'details', lambda c: c['payload']['result']['histogram'].pop('buyOrders'))
        p = self.candidates(self.scan())[0]
        self.assertEqual(len(p['evidence_ids']), 4)
        self.assertTrue(preview(self.journal, p['prediction_id'], self.mandate, self.policy, self.at)['ready'])
        enter(self.journal, {'route_id': 'independent-inputs', 'prediction_id': p['prediction_id']}, self.mandate, self.policy, self.at)
        self.assertEqual(len(self.journal.records('route')), 1)

    def test_thin_steam_bids_limit_only_bid_scenario(self):
        self.replace_capture('Example Case', 'details', lambda c: c['payload']['result']['histogram']['buyOrders'][0].update(quantity=1))
        result = self.scan()
        bids = self.candidates(result)
        listings = self.candidates(result, steam='listing_price')
        self.assertEqual(max(p['quantity_a'] for p in bids), 1)
        self.assertEqual(max(p['quantity_a'] for p in listings), 10)
        self.assertTrue(any(p['predicted_net_cents'] > 0 for p in listings))

    def test_empty_steam_bids_preserve_listing_and_are_not_missing_data(self):
        self.replace_capture('Example Case', 'details', lambda c: c['payload']['result']['histogram'].update(buyOrders=[]))
        result = self.scan()
        self.assertFalse(self.candidates(result))
        self.assertTrue(self.candidates(result, steam='listing_price'))
        issue = next(i for i in result['excluded'] if i['title'] == 'Example Case' and i['book'] == 'steam_bid')
        self.assertEqual(issue['status'], 'no_observed_bids')

    def test_thin_destination_bids_do_not_limit_listing_quantity_to_competing_sellers(self):
        self.replace_capture('Return Case', 'targets', lambda c: c['payload']['orders'][0].update(amount='1'))
        # A single competitor supplies an asking-price reference, not one buyer.
        self.replace_capture('Return Case', 'offers', lambda c: c['payload'].update(items=[dict(c['payload']['items'][0], priceCents=150)]))
        result = self.scan()
        self.assertEqual(max(p['quantity_b'] for p in self.candidates(result)), 1)
        listings = self.candidates(result, dmarket='listing_price')
        best = max(listings, key=lambda p: p['quantity_b'])
        self.assertEqual(best['quantity_b'], 10)
        sale = best['quote_legs']['exit']
        self.assertEqual(sale['observed_listing_quantity_at_price'], 1)
        self.assertTrue(sale['quantity_is_assumed'])
        self.assertEqual(sale['fills'], [])
        self.assertEqual(best['predicted_dmarket_receipts_cents'], 10*dmarket_net(150, 1000, 1))

    def test_failed_targets_preserve_fresh_bids_and_report_failure_separately(self):
        self.replace_capture('Return Case', 'targets', lambda c: c.update(status=503, error='http_503', payload=None))
        result = self.scan()
        self.assertTrue(self.candidates(result))
        self.assertTrue(self.candidates(result, dmarket='listing_price'))
        item = next(i for i in result['snapshots'] if i['title'] == 'Return Case')
        self.assertEqual(item['collection_warnings'][0]['error'], 'http_503')
        self.assertEqual(item['collection_warnings'][0]['kind'], 'targets')

    def test_missing_or_stale_essential_price_is_visible_without_invented_profit(self):
        self.replace_capture('Return Case', 'details', lambda c: c['payload']['result']['histogram'].update(date=stamp(utc(self.at)-timedelta(hours=5))))
        result = self.scan()
        self.assertFalse(any(p['item_b'] == 'Return Case' for p in result['predictions']))
        self.assertTrue(any(i['title'] == 'Return Case' and i['status'] == 'needs_refresh' for i in result['excluded']))

    def test_no_listing_reference_does_not_invent_one_from_a_bid(self):
        self.replace_capture('Example Case', 'details', lambda c: c['payload']['result']['histogram'].pop('sellOrders'))
        result = self.scan()
        self.assertTrue(self.candidates(result))
        self.assertFalse(self.candidates(result, steam='listing_price'))
        self.assertTrue(any(i.get('book') == 'steam_ask' and i['status'] == 'needs_data' for i in result['excluded']))

    def test_listing_fees_and_wallet_rounding_remain_per_item(self):
        self.replace_capture('Example Case', 'details', lambda c: c['payload']['result']['histogram']['sellOrders'][0].update(price='0.66'))
        self.replace_capture('Return Case', 'details', lambda c: c['payload']['result']['histogram']['sellOrders'][0].update(price='0.20'))
        p = next(p for p in self.candidates(self.scan(), 'listing_price', 'listing_price') if p['quantity_a'] == 10)
        self.assertEqual(p['steam_proceeds_cents'], 10*steam_net(66, 500, 1000, 1, 1))
        self.assertEqual(p['quantity_b'], 20)  # Actual observed Steam supply still limits the purchase.
        self.assertEqual(p['steam_wallet_residual_cents'], 190)
        self.assertEqual(p['quote_legs']['steam_sale']['fills'], [])
        self.assertEqual(p['engine_version'], LISTING_ENGINE)

    def test_listing_review_is_visible_but_cannot_create_paper_sales_or_entry(self):
        p = self.candidates(self.scan(), steam='listing_price')[0]
        before = len(self.journal.records('route_event'))
        review = preview(self.journal, p['prediction_id'], self.mandate, self.policy, self.at)
        self.assertEqual(review['prediction']['predicted_net_cents'], p['predicted_net_cents'])
        self.assertFalse(review['ready'])
        self.assertTrue(any('listings are not implemented' in b for b in review['blockers']))
        with self.assertRaisesRegex(ValueError, 'listings are not implemented'):
            enter(self.journal, {'route_id': 'no-fake-sale', 'prediction_id': p['prediction_id']}, self.mandate, self.policy, self.at)
        self.assertEqual(len(self.journal.records('route_event')), before)
        self.assertEqual(self.journal.records('paper_book'), [])
        self.assertEqual(self.journal.records('outcome'), [])

    def test_budget_costs_and_restrictions_still_apply(self):
        self.assertFalse(self.scan(capital_cents=79)['predictions'])
        result = self.scan(capital_cents=161)
        self.assertTrue(all(p['entry_cost_cents'] <= 161 and p['quantity_a'] <= 2 for p in result['predictions']))
        self.policy['other_cost_cents'] = None
        self.assertTrue(all(p['predicted_net_cents'] is None for p in self.scan()['predictions']))
        self.policy['other_cost_cents'] = 100000
        self.assertEqual(self.scan()['positive_conditional_scenarios'], 0)
        self.replace_capture('Example Case', 'offers', lambda c: [o.update(locked=True) for o in c['payload']['items']])
        result = self.scan()
        self.assertFalse(any(p['item_a'] == 'Example Case' for p in result['predictions']))
        self.assertTrue(any(i['title'] == 'Example Case' and i['status'] == 'no_eligible_offers' for i in result['excluded']))

    def test_identity_mismatch_and_mixed_sources_do_not_create_a_price(self):
        self.replace_capture('Example Case', 'details', lambda c: c['payload']['result']['item'].update(marketName='Different Case'))
        result = self.scan()
        self.assertFalse(any(p['item_a'] == 'Example Case' or p['item_b'] == 'Example Case' for p in result['predictions']))
        self.replace_capture('Return Case', 'offers', lambda c: c.update(input_kind='synthetic'))
        self.assertFalse(self.scan()['predictions'])

    def test_all_ranked_alternatives_are_returned_by_page_search(self):
        result = search(self.journal, 'grow', self.watch, self.policy, self.mandate, self.at)
        self.assertGreater(result['prediction_count'], 20)
        self.assertEqual(result['prediction_count'], len(result['predictions']))
        self.assertEqual({p['engine_version'] for p in result['predictions']}, {ENGINE, LISTING_ENGINE})
        values = [p['predicted_net_cents'] for p in result['predictions']]
        from arbitrage_v2.search_rules import sort_key
        keys=[sort_key(p) for p in result['predictions']]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(self.journal.records('request_attempt'), [])

    def test_bad_skin_attribute_is_an_input_problem_not_a_search_crash(self):
        title = 'AK-47 | Slate (Field-Tested)'
        self.titles = ['Example Case', title]
        self.watch['items'] = [{'app_id': 730, 'title': t} for t in self.titles]
        self.recorded_quotes()
        self.replace_capture(title, 'offers', lambda c: c['payload']['items'][0]['attributes']['cs2'].update(float='invalid'))
        result = self.scan()
        self.assertTrue(any(p['item_b'] == title and p['sale_scenarios']['dmarket'] == 'current_bids' for p in result['predictions']))
        self.assertTrue(any(i['title'] == title and i['reason'] == 'invalid_skin_float' and i['status'] == 'needs_data' for i in result['excluded']))
