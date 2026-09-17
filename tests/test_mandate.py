import copy
import json
from pathlib import Path
import tempfile
import unittest
from arbitrage_v2.mandate import load_mandate,mandate_summary
ROOT=Path(__file__).resolve().parents[1]
class MandateTests(unittest.TestCase):
    def test_approved_design(self):
        report=mandate_summary(load_mandate(ROOT/"config/mandate.json"))
        self.assertEqual(report["starting_balances"][0]["amount_cents"],1000)
        self.assertFalse(report["fixed_evaluation_window"])
        self.assertFalse(report["automatic_risk_accounting"])
        self.assertEqual(report["evaluation"],"resolved_routes_only")
        self.assertEqual(report["eligible_balance_type"],"including_restricted_tradable")
        self.assertTrue(report["steam_steps_user_operated"])
    def test_old_loss_or_month_fields_cannot_silently_return(self):
        data=load_mandate(ROOT/"config/mandate.json")
        with tempfile.TemporaryDirectory() as tmp:
            for key,value in [("max_loss_cents",200),("horizon_calendar_months",1)]:
                path=Path(tmp)/"mandate.json"
                path.write_text(json.dumps(dict(data,**{key:value})))
                with self.assertRaises(ValueError):
                    load_mandate(path)
    def test_invalid_capital_and_automatic_execution_rejected(self):
        data=load_mandate(ROOT/"config/mandate.json")
        with tempfile.TemporaryDirectory() as tmp:
            for key,value in [("starting_dmarket_cents",True),("starting_dmarket_cents",0),("execution","automatic")]:
                path=Path(tmp)/"mandate.json"
                path.write_text(json.dumps(dict(data,**{key:value})))
                with self.assertRaises(ValueError):
                    load_mandate(path)

