from copy import deepcopy
from datetime import timedelta
import json

import unittest
import test_local as fixtures
ROOT=fixtures.ROOT
from test_research import T0, T1, T2
from arbitrage_v2.csfloat import sale_net, payout, normalize_listings, capture_listings
from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.paper import step
from arbitrage_v2.prediction import market_snapshot
from arbitrage_v2.routes import add_event, correct_event, open_route, route_status, _state
from arbitrage_v2.worker import available_capital


class NewContracts(unittest.TestCase):
    setUp=fixtures.LocalTests.setUp
    route=fixtures.LocalTests.route
    event=fixtures.LocalTests.event
    captures=fixtures.LocalTests.captures

    def test_csfloat_cent_fees_and_unknown_payout(self):
        self.assertEqual(sale_net(14), 13)
        self.assertEqual(sale_net(43), 42)
        self.assertIsNone(payout(500, {})["cash_received_cents"])
        policy = {"fee_bps": 250, "minimum_cents": 500, "fixed_fee_cents": 0,
                  "conversion_fee_cents": 0, "bank_fee_cents": 0, "rounding": "ceil"}
        self.assertEqual(payout(499, policy)["status"], "below_minimum")
        self.assertEqual(payout(1001, policy)["cash_received_cents"], 975)

    def test_csfloat_listings_are_deduped_and_never_demand(self):
        listing = {"id": "1", "type": "buy_now", "state": "listed", "price": 43,
                   "item": {"asset_id": "asset1", "tradable": 0, "market_hash_name": "Fracture Case"}}
        value = normalize_listings([listing, listing], "Fracture Case", T2)
        self.assertEqual(len(value["listings"]), 1)
        self.assertIn("not_completed_sales_or_bids", value["semantics"])
        with self.assertRaises(ValueError):
            normalize_listings([listing, dict(listing, price=44)], "Fracture Case", T2)
        with self.assertRaises(ValueError):
            normalize_listings([listing, dict(listing, id="2")], "Fracture Case", T2)
        with self.assertRaises(ValueError):
            capture_listings(self.journal, "Fracture Case", {}, 100)
        self.assertEqual(self.journal.records("request_attempt"), [])

    def test_noncase_live_contract_fixtures_identity_and_general_demand(self):
        rows = json.loads((ROOT/"tests/fixtures/noncase_sources.json").read_text())
        for c in rows:
            c["input_kind"] = "synthetic"
            c["retrieved_at"] = T2
            if c["kind"] == "details":
                c["payload"]["result"]["histogram"]["date"] = T2
            self.journal.append("capture", c)
        capsule = market_snapshot(self.journal, "Paris 2023 Legends Sticker Capsule", T2, 14400)
        self.assertEqual(capsule["steam_bid"]["price_cents"], 11)
        self.assertEqual(len(capsule["dmarket_ask"]["levels"]), 20)
        skin = market_snapshot(self.journal, "AK-47 | Slate (Field-Tested)", T2, 14400)
        self.assertEqual(skin["dmarket_bid"]["quantity"], 1)  # Excludes the FT-0/FT-1 orders at the same price.
        self.assertEqual(len(skin["dmarket_ask"]["levels"]), 1)  # Excludes locked/premium-decorated offers.
        self.assertEqual(skin["steam_ask"]["quantity"], 1)

    def test_correction_keeps_raw_record_and_changes_final_cost(self):
        self.route()
        raw = self.journal.get("event:1")
        correction = {"correction_id": "c1", "route_id": "r1", "target_event_id": "1", "at": T2,
                      "reference": "corrected-receipt", "reason": "Correct receipt total", "value": -170, "fee_cents": 2}
        correct_event(self.journal, correction)
        self.assertEqual(correct_event(self.journal, correction), "correction:c1")
        self.assertEqual(self.journal.get("event:1"), raw)
        self.assertEqual(_state(self.journal, "r1")[3]["dmarket_regular"], -170)
        self.assertEqual(available_capital(self.journal, self.mandate), 830)
        self.assertIsNone(route_status(self.journal, "r1", T2)["actual_profit"])

    def test_invalid_correction_rolls_back_atomically(self):
        self.route()
        self.captures()
        step(self.journal, "r1", T2)
        events = self.journal.records("route_event")
        sale = next(e for e in events if e["kind"] == "asset" and e["quantity_delta"] < 0)
        with self.assertRaises(ValueError):
            correct_event(self.journal, {"correction_id": "bad", "route_id": "r1", "target_event_id": sale["event_id"],
                "at": T2, "reference": "bad-receipt", "reason": "Wrong amount", "value": -99, "fee_cents": 0})
        self.assertEqual(self.journal.records("event_correction"), [])

    def test_withdrawal_waits_for_credit_and_counts_internal_transfers_once(self):
        pred = dict(self.prediction, purpose="withdraw", family="cs2_withdraw_direct_v1", destination="cash",
                    route_variant="direct_transfer", predicted_destination_receipts_cents=878,
                    predicted_net_cents=-122, entry_cost_cents=1000, steam_proceeds_cents=0, return_purchase_cents=0)
        identifier = self.journal.append("prediction", pred)
        open_route(self.journal, "withdraw", identifier, "paper", T1)
        def move(account, amount, fee=0):
            self.events += 1
            add_event(self.journal, {"event_id": str(self.events), "route_id": "withdraw", "at": T2,
                "reference": "receipt-"+str(self.events), "kind": "movement", "account": account,
                "net_delta_cents": amount, "fee_cents": fee})
        close = {"event_id": "close", "route_id": "withdraw", "at": T2, "reference": "payout-confirmed",
                 "kind": "resolve", "resolution": "completed", "pending_operations": 0,
                 "residual_disposition": "none", "confirmed": True}
        move("dmarket_regular", -1000)
        move("csfloat_pending", 900, 20)
        with self.assertRaisesRegex(ValueError, "pending"):
            add_event(self.journal, close)
        move("csfloat_pending", -900)
        move("csfloat_withdrawable", 900)
        move("csfloat_withdrawable", -900)
        move("cash_received", 878, 22)
        add_event(self.journal, close)
        result = route_status(self.journal, "withdraw", T2)["actual_profit"]
        self.assertEqual(result["actual_net_cents"], -122)
        self.assertEqual(result["fee_cents_reported"], 42)

    def test_paper_leftover_never_closes_itself(self):
        self.route()
        self.captures(bid_quantity=2)
        step(self.journal, "r1", T2)
        step(self.journal, "r1", T2)
        finish = stamp(utc(T2)+timedelta(seconds=10))
        self.captures(finish, 2)
        step(self.journal, "r1", finish)
        self.events += 1
        add_event(self.journal, {"event_id": "wallet-remainder", "route_id": "r1", "at": finish,
            "reference": "extra-paper-sale", "kind": "movement", "account": "steam_wallet", "net_delta_cents": 1, "fee_cents": 0})
        self.assertEqual(step(self.journal, "r1", finish)["reason"], "leftovers_require_user_decision")
        self.assertEqual(self.journal.records("outcome"), [])

    def test_unfunded_route_entry_reserves_its_predicted_cost(self):
        open_route(self.journal, "reserved", self.pred_id, "paper", T1)
        self.assertEqual(available_capital(self.journal, self.mandate), 800)

    def test_existing_trial_asset_key_format_is_supported(self):
        self.route()
        self.event("r1","asset",asset_key="Example Case",quantity_delta=-2)
        self.event("r1","asset",asset_key="730:Example Case",quantity_delta=2)
        self.captures(bid_quantity=2)
        self.assertEqual(step(self.journal,"r1",T2)["action"],"steam_sale")
        self.assertEqual(_state(self.journal,"r1")[4]["730:Example Case"],0)

    def test_linked_recovery_preserves_loss_and_does_not_charge_original_cost_twice(self):
        from arbitrage_v2.recovery import open_recovery
        self.route()
        add_event(self.journal,{"event_id":"writeoff","route_id":"r1","at":T2,"reference":"operator-writeoff",
            "kind":"resolve","resolution":"write_off","residual_disposition":"written_off","pending_operations":0,"confirmed":True})
        original=self.journal.get("outcome:r1")
        request={"route_id":"recovered","parent_route_id":"r1","at":T2,"reference":"recover-holdings",
                 "asset_key":"Example Case","quantity":2,"steam_wallet_cents":0}
        open_recovery(self.journal,request)
        open_recovery(self.journal,request)
        with self.assertRaises(ValueError):
            open_recovery(self.journal,dict(request,route_id="duplicate"))
        base={"route_id":"recovered","at":T2,"reference":"recovery-sale"}
        add_event(self.journal,dict(base,event_id="recovery-items",kind="asset",asset_key="Example Case",quantity_delta=-2))
        add_event(self.journal,dict(base,event_id="recovery-money",kind="movement",account="dmarket_regular",net_delta_cents=100,fee_cents=0))
        add_event(self.journal,dict(base,event_id="recovery-cost",kind="movement",account="external_cost",net_delta_cents=-8,fee_cents=0))
        add_event(self.journal,dict(base,event_id="recovery-close",kind="resolve",resolution="completed",residual_disposition="none",pending_operations=0,confirmed=True))
        self.assertEqual(self.journal.get("outcome:r1"),original)
        outcome=self.journal.get("outcome:recovered")
        self.assertEqual(outcome["actual_net_cents"],92)
        self.assertEqual(outcome["actual_entry_cost_cents"],0)
        self.assertIsNone(outcome["net_error_cents"])
        self.assertEqual(outcome["parent_route_id"],"r1")

