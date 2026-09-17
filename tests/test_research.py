from copy import deepcopy
from datetime import datetime,timezone,timedelta
from pathlib import Path
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from nacl.signing import SigningKey

from arbitrage_v2.evidence import stamp
from arbitrage_v2.journal import Journal
from arbitrage_v2.collector import sign,request_spec,collect_once,sanitize,history_points
from arbitrage_v2.prediction import calculate,steam_net,dmarket_net,may_use_balance,market_snapshot,FAMILY
from arbitrage_v2.routes import open_route,add_event,route_status,train,refine

ROOT=Path(__file__).resolve().parents[1]
POLICY=json.loads((ROOT/"config/research_assumptions.json").read_text())
T0="2026-10-01T00:00:00Z"
T1="2026-10-02T00:00:00Z"
T2="2026-10-20T00:00:00Z"
T3="2026-10-21T00:00:00Z"

def snapshot(title="Example Case"):
    return {"title":title,"dmarket_ask":{"price_cents":100,"quantity":20},
            "steam_bid":{"price_cents":150,"quantity":20},
            "steam_ask":{"price_cents":100,"quantity":20},
            "dmarket_bid":{"price_cents":110,"quantity":20},
            "evidence_ids":[],"input_kind":"synthetic"}

def prediction():
    result=calculate(snapshot(),snapshot("Return Case"),2,POLICY)
    result["predicted_at"]=T0
    result["predicted_duration_seconds"]=17*86400
    return result

class ProviderTests(unittest.TestCase):
    def test_signature_matches_decoded_path_and_transmitted_query(self):
        key=SigningKey.generate()
        path="/marketplace-api/v1/targets-by-title/a8db/An Item | Test"
        query="title=An%20Item%20%7C%20Test"
        signature=sign(key.encode().hex(),path,query,123)
        verified=key.verify_key.verify(bytes.fromhex(signature.split()[-1])+("GET"+path+"?"+query+"123").encode())
        self.assertEqual(verified,("GET"+path+"?"+query+"123").encode())
    def test_no_mutation_endpoint_or_credential_redirect(self):
        with self.assertRaises(ValueError):
            request_spec("dmarket","buy",730,"Example Case",{})
        keys={"DMARKET_PUBLIC_KEY":"public","DMARKET_SECRET_KEY":SigningKey.generate().encode().hex()}
        url,headers=request_spec("dmarket","targets",730,"An Item | Test",keys)
        self.assertIn("An%20Item%20%7C%20Test",url)
        self.assertNotIn(keys["DMARKET_SECRET_KEY"],url)
    def test_sanitization_nested(self):
        self.assertEqual(sanitize({"x":[{"apiKey":"secret","price":12}]}),
                         {"x":[{"apiKey":"[REDACTED]","price":12}]})

class EconomicTests(unittest.TestCase):
    def test_integer_fees_and_whole_item_return(self):
        self.assertEqual(steam_net(66,500,1000,1,1),59)
        self.assertEqual(dmarket_net(45,1000,1),40)
        result=calculate(snapshot(),snapshot("Return Case"),2,POLICY)
        self.assertEqual(result["steam_proceeds_cents"],262)  # Each 150c buyer payment nets 131c.
        self.assertEqual(result["quantity_b"],2)
        self.assertEqual(result["steam_wallet_residual_cents"],62)
        self.assertEqual(result["predicted_net_cents"],-2)
    def test_buyer_markup_does_not_establish_roundtrip_profit(self):
        a=snapshot()
        b=snapshot("Return Case")
        a["steam_bid"]["price_cents"]=200
        b["dmarket_bid"]["price_cents"]=30
        result=calculate(a,b,1,POLICY)
        self.assertLess(result["predicted_net_cents"],0)
        self.assertFalse(result["entry_recommendation"])
    def test_insufficient_return_demand_and_no_fractional_items(self):
        b=snapshot("Return Case")
        b["dmarket_bid"]["quantity"]=0
        with self.assertRaises(ValueError):
            calculate(snapshot(),b,1,POLICY)
        with self.assertRaises(ValueError):
            calculate(snapshot(),b,1.5,POLICY)
    def test_restricted_balance_keeps_its_action_restrictions(self):
        self.assertTrue(may_use_balance("tradable",730,"market_purchase"))
        self.assertFalse(may_use_balance("tradable",730,"target_purchase"))
        self.assertFalse(may_use_balance("tradable",440,"market_purchase"))
        self.assertFalse(may_use_balance("tradable",730,"withdraw"))

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal=Journal(Path(self.tmp.name)/"journal.sqlite3")
        self.journal.initialize()
        self.pred=prediction()
        self.pred_id=self.journal.append("prediction",self.pred)
        open_route(self.journal,"r1",self.pred_id,"paper",T1)
    def event(self,kind,at=T2,**kwargs):
        number=len(self.journal.records("route_event"))
        return dict(event_id="e"+str(number),route_id="r1",at=at,kind=kind,reference="ref"+str(number),**kwargs)
    def movement(self,account,delta,**kwargs):
        value=self.event("movement",account=account,net_delta_cents=delta,fee_cents=0,**kwargs)
        add_event(self.journal,value)
        return value
    def resolve(self,resolution="completed",residual="none"):
        if resolution=="completed":
            self.movement("steam_wallet",264)
            self.movement("steam_wallet",-264)
        value=self.event("resolve",resolution=resolution,residual_disposition=residual,
                         pending_operations=0,confirmed=True)
        add_event(self.journal,value)
        return value
    def test_open_route_is_not_loss_and_overdue_only_flags(self):
        self.movement("dmarket_regular",-200)
        add_event(self.journal,self.event("asset",asset_key="Example Case",quantity_delta=2))
        status=route_status(self.journal,"r1",T3)
        self.assertIsNone(status["actual_profit"])
        self.assertTrue(status["overdue"])
        self.assertEqual(status["open_assets"]["Example Case"],2)
        self.assertEqual(self.journal.records("outcome"),[])
    def test_resolution_requires_inventory_and_pending_operations_to_be_resolved(self):
        self.movement("dmarket_regular",-200)
        add_event(self.journal,self.event("asset",asset_key="Example Case",quantity_delta=2))
        with self.assertRaises(ValueError):
            self.resolve()
        self.assertEqual(self.journal.records("outcome"),[])
    def test_confirmed_writeoff_is_terminal_and_included_in_learning(self):
        self.movement("dmarket_regular",-200)
        add_event(self.journal,self.event("asset",asset_key="Example Case",quantity_delta=2))
        self.resolve("write_off","written_off")
        self.assertEqual(route_status(self.journal,"r1",T3)["actual_profit"]["actual_net_cents"],-200)
        model=train(self.journal,FAMILY,"paper","synthetic",T3)
        self.assertEqual(self.journal.get(model,"model")["sample_count"],1)
    def test_net_cashflows_count_restricted_proceeds_and_costs_once(self):
        self.movement("dmarket_regular",-200)
        fee=self.event("movement",account="dmarket_tradable",net_delta_cents=250,fee_cents=10)
        add_event(self.journal,fee)
        self.movement("external_cost",-5)
        self.resolve()
        outcome=route_status(self.journal,"r1",T3)["actual_profit"]
        self.assertEqual(outcome["actual_net_cents"],45)
        self.assertEqual(outcome["fee_cents_reported"],10)
    def test_retries_are_idempotent_but_new_id_cannot_duplicate_a_transaction(self):
        event=self.movement("dmarket_regular",-200)
        add_event(self.journal,event)
        with self.assertRaises(ValueError):
            add_event(self.journal,dict(event,event_id="different"))
        self.assertEqual(len(self.journal.records("route_event")),1)
    def test_future_model_or_other_evidence_mode_cannot_leak_into_prediction(self):
        self.movement("dmarket_regular",-200)
        self.movement("dmarket_regular",250)
        self.resolve()
        model=train(self.journal,FAMILY,"paper","synthetic",T3)
        with self.assertRaises(ValueError):
            refine(self.journal,self.pred_id,model,"paper",T2)
        with self.assertRaises(ValueError):
            refine(self.journal,self.pred_id,model,"confirmed",T3)
        with self.assertRaises(ValueError):
            train(self.journal,FAMILY,"confirmed","recorded",T3)
    def test_model_refinement_preserves_original_prediction(self):
        original=self.journal.get(self.pred_id,"prediction")
        self.movement("dmarket_regular",-200)
        self.movement("dmarket_regular",250)
        self.resolve()
        model=train(self.journal,FAMILY,"paper","synthetic",T3)
        refined=refine(self.journal,self.pred_id,model,"paper",T3)
        self.assertEqual(self.journal.get(self.pred_id,"prediction"),original)
        self.assertEqual(self.journal.get(refined,"prediction")["predicted_net_cents"],50)
        self.assertGreaterEqual(self.journal.get(refined,"prediction")["predicted_duration_seconds"],17*86400)
    def test_no_learning_from_unfinished_routes(self):
        self.movement("dmarket_regular",-200)
        with self.assertRaises(ValueError):
            train(self.journal,FAMILY,"paper","synthetic",T3)
    def test_completed_route_requires_both_conversions(self):
        self.movement("dmarket_regular",-200)
        self.movement("dmarket_regular",250)
        with self.assertRaises(ValueError):
            add_event(self.journal,self.event("resolve",resolution="completed",
                residual_disposition="none",pending_operations=0,confirmed=True))
    def test_unfunded_wallet_and_unowned_asset_cannot_be_spent(self):
        with self.assertRaises(ValueError):
            self.movement("steam_wallet",-100)
        with self.assertRaises(ValueError):
            add_event(self.journal,self.event("asset",asset_key="Example Case",quantity_delta=-1))
    def test_closed_route_cannot_be_edited(self):
        self.movement("dmarket_regular",-200)
        self.movement("dmarket_regular",250)
        terminal=self.resolve()
        add_event(self.journal,terminal)
        with self.assertRaises(ValueError):
            self.movement("external_cost",-100)
        with self.journal.connect(True) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM records")
    def test_collector_allowance_persists_across_restarts(self):
        self.journal.append("request_attempt",{"provider":"steamapis"})
        watch={"items":[{"app_id":730,"title":"Example Case"}],
               "steam_request_allowance":1,"total_request_allowance":1}
        with patch("arbitrage_v2.collector.capture") as capture:
            self.assertEqual(collect_once(self.journal,watch,{},3,1),[])
            capture.assert_not_called()
    def test_live_payload_shapes_preserve_identity_depth_and_synthetic_partition(self):
        title="Example Case"
        steam={"result":{"item":{"appId":730,"marketName":title},
            "meta":{"flags":{"commodity":True}},"histogram":{"date":T0,
            "buyOrders":[{"price":"0.66","quantity":250},{"price":"0.65","quantity":1066}],
            "sellOrders":[{"price":"0.70","quantity":18}]},"priceHistory":{"data":[]}}}
        offer={"offerId":"unique","priceCents":"55","locked":False,
            "attributes":{"title":title,"gameId":"a8db","withdrawable":True,"tradable":True}}
        targets={"orders":[{"title":title,"attributes":{},"price":"51","amount":"69"},
            {"title":title,"attributes":{"phase":"special"},"price":"500","amount":"10"}]}
        for kind,payload in [("details",steam),("offers",{"items":[offer,offer]}),("targets",targets)]:
            self.journal.append("capture",{"app_id":730,"title":title,"kind":kind,
                "payload":payload,"retrieved_at":T0,"error":None,"status":200,"input_kind":"synthetic"})
        result=market_snapshot(self.journal,title,T0,14400)
        self.assertEqual(result["steam_bid"],{"price_cents":66,"quantity":250})
        self.assertEqual(result["dmarket_ask"]["quantity"],1)
        self.assertEqual(result["dmarket_bid"]["price_cents"],51)
        self.assertEqual(result["input_kind"],"synthetic")
        with self.assertRaises(ValueError):
            market_snapshot(self.journal,title,T2,14400)
    def test_history_point_repeats_do_not_inflate_sales(self):
        for seq in range(2):
            self.journal.append("capture",{"kind":"details","title":"Example Case","app_id":730,
                "retrieved_at":T1,"error":None,
                "payload":{"result":{"priceHistory":{"data":[{"date":T0,"price":"1.00","quantity":12}]}}},
                "sequence":seq})
        report=history_points(self.journal,"Example Case")
        self.assertEqual(len(report["points"]),1)
        self.assertEqual(report["coverage"],"unknown")
    def test_cli_journal_status_smoke(self):
        result=subprocess.run([sys.executable,"-B","-m","arbitrage_v2","--journal",str(self.journal.path),
                               "routes","--as-of",T3],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIsNone(json.loads(result.stdout)[0]["actual_profit"])
