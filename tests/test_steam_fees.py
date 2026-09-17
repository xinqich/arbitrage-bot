"""Regression expectations obtained from Steam's client, not the Python solver."""
import json
from pathlib import Path
import unittest

from arbitrage_v2.prediction import calculate, steam_net


ROOT = Path(__file__).resolve().parents[1]


class SteamFeeTests(unittest.TestCase):
    def test_usd_cs2_reference_outputs_including_minima_and_rounding_gaps(self):
        reference = json.loads((ROOT / 'tests/fixtures/steam_usd_fee_reference.json').read_text())
        for case in reference['cases']:
            with self.subTest(gross_cents=case['gross_cents']):
                self.assertEqual(steam_net(case['gross_cents'], 500, 1000, 1, 1),
                                 case['net_cents'])

    def test_twelve_fracture_sales_apply_fees_before_quantity(self):
        # A recorded quote replay, never inserted as a completed outcome.
        report = json.loads((ROOT / 'docs/initial_screen.json').read_text(encoding='utf-8'))
        snapshots = {row['title']: row for row in report['snapshots']}
        original = next(row for row in report['predictions']
                        if row['item_a'] == 'Fracture Case'
                        and row['item_b'] == 'Kilowatt Case'
                        and row['quantity_a'] == 12)
        result = calculate(snapshots['Fracture Case'], snapshots['Kilowatt Case'],
                           12, original['policy'])
        # Official unit result: seller 59c + Steam 2c + publisher 5c = buyer 66c.
        self.assertEqual(12 * snapshots['Fracture Case']['steam_bid']['price_cents'], 792)
        expected = {'entry_cost_cents': 660, 'steam_proceeds_cents': 708,
                    'quantity_b': 36, 'return_purchase_cents': 684,
                    'steam_wallet_residual_cents': 24,
                    'predicted_dmarket_receipts_cents': 1440,
                    'predicted_net_cents': 780, 'predicted_fee_cents': 264}
        for name, amount in expected.items():
            with self.subTest(field=name):
                self.assertEqual(result[name], amount)
                self.assertEqual(original[name], amount)
        self.assertEqual(792 - result['steam_proceeds_cents'], 84)
        self.assertNotEqual(result['steam_proceeds_cents'], steam_net(792, 500, 1000, 1, 1))
        self.assertFalse(result['entry_recommendation'])
