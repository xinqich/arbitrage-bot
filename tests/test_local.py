from copy import deepcopy
from datetime import timedelta
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest

from arbitrage_v2.depth import ENGINE, available, book, consume, consumed_provider, levels, quote
from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.journal import Journal
from arbitrage_v2.mandate import load_mandate, convert_legacy
from arbitrage_v2.paper import configure, step
from arbitrage_v2.prediction import calculate, market_snapshot
from arbitrage_v2.routes import open_route, add_event, route_status
from arbitrage_v2.web import create_server
from arbitrage_v2.worker import Worker, control, controls, latest, available_capital
from test_research import snapshot, POLICY, T0, T1, T2

ROOT = Path(__file__).resolve().parents[1]


class DepthTests(unittest.TestCase):
    def test_walks_levels_and_charges_per_item(self):
        a, b = snapshot(), snapshot("Return Case")
        a["dmarket_ask"]["levels"] = [{"price_cents": 43, "quantity": 6}, {"price_cents": 45, "quantity": 17}]
        a["steam_bid"]["levels"] = [{"price_cents": 69, "quantity": 6}, {"price_cents": 64, "quantity": 17}]
        b["steam_ask"] = {"price_cents": 19, "quantity": 100}
        b["dmarket_bid"]["levels"] = [{"price_cents": 41, "quantity": 1}, {"price_cents": 40, "quantity": 96}]
        result = calculate(a, b, 23, POLICY)
        self.assertEqual(result["entry_cost_cents"], 1023)
        self.assertEqual(result["steam_proceeds_cents"], 1329)
        self.assertEqual(result["quantity_b"], 69)
        self.assertEqual(result["predicted_dmarket_receipts_cents"], 36+68*36)
        self.assertEqual(result["steam_wallet_residual_cents"], 18)
        self.assertEqual(len(result["quote_legs"]["steam_sale"]["fills"]), 2)

    def test_duplicate_and_cumulative_rows(self):
        rows = [{"price": 10, "quantity": 2}, {"price": 10, "quantity": 2}, {"price": 9, "quantity": 5}]
        result = levels(rows, "bid", semantics="cumulative")
        self.assertEqual([r["quantity"] for r in result], [2, 3])
        with self.assertRaises(ValueError):
            levels(rows+[{"price": 10, "quantity": 3}], "bid")

    def test_budget_and_unknown_costs(self):
        rows = [{"price_cents": 10, "quantity": 2}, {"price_cents": 15, "quantity": 10}]
        self.assertEqual(quote(rows, 9, budget=49)["gross_cents"], 35)
        result = calculate(snapshot(), snapshot("Return Case"), 2, dict(POLICY, other_cost_cents=None))
        self.assertIsNone(result["predicted_net_cents"])
        self.assertFalse(result["costs_known"])


class ProviderTrackingTests(unittest.TestCase):
    """depth.consume/consumed_provider (Task 4 D1), tested against a bare journal --
    no route or paper.step involved. The route-level blocking behaviour these support
    is covered separately in ProviderSwitchTests below."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = Journal(Path(self.temp.name)/"journal.sqlite3")
        self.journal.initialize()
        self.rows = [{"price_cents": 10, "quantity": 5, "level_id": "10"}]

    def test_provider_is_recorded_beside_state_and_available_still_replays_correctly(self):
        _, state = available(self.journal, "k", self.rows)
        consume(self.journal, "k", state, [{"level_id": "10", "quantity": 2}], [], T1, "d1", None,
                provider="steam_public")
        event = self.journal.records("paper_book")[-1]
        self.assertEqual(event["provider"], "steam_public")
        self.assertNotIn("provider", event["state"]["10"])
        remaining, _ = available(self.journal, "k", self.rows)
        self.assertEqual(remaining[0]["quantity"], 3)

    def test_consumed_provider_ignores_events_without_fills(self):
        _, state = available(self.journal, "k", self.rows)
        consume(self.journal, "k", state, [], [], T1, "empty", None, provider="steam_public")
        self.assertIsNone(consumed_provider(self.journal, "k"))
        _, state = available(self.journal, "k", self.rows)
        consume(self.journal, "k", state, [{"level_id": "10", "quantity": 1}], [], T1, "real", None,
                provider="steamapis")
        self.assertEqual(consumed_provider(self.journal, "k"), "steamapis")

    def test_consumed_provider_is_none_with_no_history_or_with_pre_d1_history(self):
        self.assertIsNone(consumed_provider(self.journal, "k"))
        # A live pre-D1 paper trial's paper_book events have no 'provider' key at all.
        self.journal.append("paper_book", {"schema_version": 1, "engine": ENGINE, "key": "k",
            "state": {"10": {"visible": 5, "available": 3}}, "fills": [{"level_id": "10", "quantity": 2}],
            "evidence_ids": [], "at": T1, "decision_id": "legacy"})
        self.assertIsNone(consumed_provider(self.journal, "k"))


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = Journal(Path(self.temp.name)/"journal.sqlite3")
        self.journal.initialize()
        self.watch = {"items": [{"app_id": 730, "title": t} for t in ("Example Case", "Return Case")],
                      "total_request_allowance": 1000, "steam_public_request_allowance": 1000}
        self.policy = dict(POLICY, minimum_return_delay_seconds=10)
        self.mandate = load_mandate(ROOT/"config/mandate.json")
        self.config = json.loads((ROOT/"config/local.json").read_text())
        self.config.update(request_spacing_seconds={"steam_public":0,"dmarket":0,"steamapis":0}, collection_interval_seconds=21600)
        self.prediction = calculate(snapshot(), snapshot("Return Case"), 2, self.policy)
        self.prediction["predicted_at"] = T0
        self.pred_id = self.journal.append("prediction", self.prediction)
        self.events = 0

    def route(self, identifier="r1", quantity=2):
        open_route(self.journal, identifier, self.pred_id, "paper", T1)
        self.event(identifier, "movement", account="dmarket_regular", net_delta_cents=-160, fee_cents=0)
        self.event(identifier, "asset", asset_key="Example Case", quantity_delta=quantity)
        self.event(identifier, "progress", stage="steam_locked", next_eligible_at=T2, note="paper")
        configure(self.journal, identifier, True, self.watch, self.policy, T1)

    def event(self, identifier, kind, **kw):
        self.events += 1
        add_event(self.journal, dict(event_id=str(self.events), route_id=identifier,
            at=T1, reference="manual:"+str(self.events), kind=kind, **kw))

    def captures(self, at=T2, bid_quantity=1):
        for title in ("Example Case", "Return Case"):
            for kind in ("details", "targets"):
                if kind == "details":
                    payload = {"result": {"item": {"appId": 730, "marketName": title},
                        "meta": {"flags": {"commodity": True}}, "histogram": {"date": at,
                        "buyOrders": [{"price": "1.15", "quantity": bid_quantity}],
                        "sellOrders": [{"price": "1.00", "quantity": 20}]}},
                        "provenance": {"book_quantity_semantics": "incremental"}}
                else:
                    payload = {"orders": [{"title": title, "attributes": {}, "price": "120", "amount": "20"}]}
                self.journal.append("capture", {"provider": "steam_public" if kind == "details" else "dmarket",
                    "kind": kind, "app_id": 730, "title": title, "retrieved_at": at,
                    "input_kind": "synthetic", "status": 200, "error": None, "payload": payload})

    def test_full_cycle_partial_sales_restart_and_frozen_prediction(self):
        self.route()
        self.assertEqual(step(self.journal, "r1", T1)["status"], "waiting_for_unlock")
        self.captures()
        self.assertEqual(step(self.journal, "r1", T2)["action"], "steam_sale")
        before = len(self.journal.records("route_event"))
        restarted = Journal(self.journal.path)
        self.assertEqual(step(restarted, "r1", T2)["status"], "waiting_for_new_depth")
        self.assertEqual(len(self.journal.records("route_event")), before)
        later = stamp(utc(T2)+timedelta(seconds=1))
        self.captures(later, bid_quantity=2)
        self.assertEqual(step(restarted, "r1", later)["action"], "steam_sale")
        self.assertEqual(step(restarted, "r1", later)["action"], "return_purchase")
        self.assertEqual(step(restarted, "r1", later)["status"], "waiting_for_unlock")
        status = route_status(self.journal, "r1", later)
        self.assertIsNone(status["actual_profit"])
        self.assertEqual(status["next_eligible_at"], stamp(utc(later)+timedelta(seconds=10)))
        finish = stamp(utc(later)+timedelta(seconds=10))
        self.assertEqual(step(restarted, "r1", finish)["status"], "waiting_for_evidence")
        self.captures(finish, bid_quantity=2)
        self.assertEqual(step(restarted, "r1", finish)["action"], "dmarket_sale")
        self.assertEqual(step(restarted, "r1", finish)["action"], "resolved")
        outcome = self.journal.records("outcome")[0]
        self.assertEqual(outcome["actual_net_cents"], 56)
        self.assertEqual(outcome["engine_version"], ENGINE)
        self.assertEqual(step(restarted, "r1", finish)["status"], "resolved")
        self.assertEqual(self.journal.get(self.pred_id), self.prediction)

    def test_shared_depth_across_routes_and_positive_additions(self):
        self.route("r1", 1)
        self.route("r2", 1)
        self.captures()
        step(self.journal, "r1", T2)
        self.assertEqual(step(self.journal, "r2", T2)["status"], "waiting_for_new_depth")
        later = stamp(utc(T2)+timedelta(seconds=1))
        self.captures(later, bid_quantity=2)
        self.assertEqual(step(self.journal, "r2", later)["action"], "steam_sale")

    def test_stale_quotes_do_not_advance_and_pause_survives_restart(self):
        self.route()
        self.captures()
        later = stamp(utc(T2)+timedelta(days=1))
        count = len(self.journal.records("route_event"))
        self.assertEqual(step(self.journal, "r1", later)["status"], "waiting_for_evidence")
        self.assertEqual(len(self.journal.records("route_event")), count)
        configure(self.journal, "r1", False, self.watch, self.policy, later)
        self.assertEqual(step(Journal(self.journal.path), "r1", later)["status"], "paused")
        control(self.journal, "pause")
        self.assertTrue(controls(Journal(self.journal.path))["paused"])

    def test_allocation_and_separate_csfloat_paper_funds(self):
        self.route()
        self.assertEqual(available_capital(self.journal, self.mandate), 840)
        self.assertEqual(available_capital(self.journal, self.mandate, "csfloat"), 1000)
        self.assertEqual(available_capital(self.journal, self.mandate, mode="confirmed"), 0)

    def test_http_local_security_actions_and_assets(self):
        worker = Worker(self.journal, self.watch, self.policy, self.mandate, self.config, {})
        server = create_server(worker, ROOT, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_port
        def request(method, path, body=None, headers=None):
            con = HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                con.request(method, path, json.dumps(body) if body is not None else None, headers or {})
                response = con.getresponse()
                return response.status, response.read(), response.getheader("Content-Security-Policy")
            finally:
                con.close()
        status, body, csp = request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'Local desk', body)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(request("GET", "/api/report?name=../config/mandate.json")[0], 404)
        self.assertEqual(request("GET", "/api/status", headers={"Host": "evil.test"})[0], 403)
        token = json.loads(request("GET", "/api/session")[1])["token"]
        headers = {"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json", "X-Local-Token": token}
        self.assertEqual(request("POST", "/api/action", {"action": "pause"})[0], 403)
        self.assertEqual(request("POST", "/api/action", {"action": "pause"}, dict(headers, Origin="https://evil.test"))[0], 403)
        self.assertEqual(request("POST", "/api/action", {"action": "pause", "unexpected": 1}, headers)[0], 400)
        self.assertEqual(request("POST", "/api/action", {"action": "pause"}, headers)[0], 200)
        data = json.loads(request("GET", "/api/status")[1])
        self.assertTrue(data["controls"]["paused"])
        self.assertNotIn("keys", data)

    def test_legacy_config_conversion_does_not_invent_funds(self):
        old = dict(self.mandate, schema_version=3, starting_dmarket_cents=1000)
        del old["starting_balances"]
        converted = convert_legacy(old)
        self.assertEqual(len(converted["starting_balances"]), 1)
        self.assertEqual(converted["starting_balances"][0]["venue"], "dmarket")


class ProviderSwitchTests(unittest.TestCase):
    """Task 4 D1: a provider switch must not replenish already-consumed paper depth."""
    route = LocalTests.route
    event = LocalTests.event
    captures = LocalTests.captures

    def setUp(self):
        LocalTests.setUp(self)

    def switched_details(self, title, provider, at, bid_quantity=None, sell_quantity=None):
        """Append a newer 'details' capture for title, same shape as LocalTests.captures
        produces, but from a different provider and (optionally) a different book."""
        previous = next(c for c in reversed(self.journal.records("capture"))
                         if c["title"] == title and c["kind"] == "details")
        payload = deepcopy(previous["payload"])
        histogram = payload["result"]["histogram"]
        histogram["date"] = at
        if bid_quantity is not None:
            histogram["buyOrders"] = [{"price": "1.15", "quantity": bid_quantity}]
        if sell_quantity is not None:
            histogram["sellOrders"] = [{"price": "1.00", "quantity": sell_quantity}]
        return self.journal.append("capture", {"provider": provider, "kind": "details", "app_id": 730,
            "title": title, "retrieved_at": at, "input_kind": "synthetic", "status": 200,
            "error": None, "payload": payload})

    def test_provider_switch_after_consumed_bid_depth_blocks_the_step_and_records_nothing(self):
        self.route()
        self.captures()  # provider steam_public, bid_quantity=1
        self.assertEqual(step(self.journal, "r1", T2)["action"], "steam_sale")  # consumes 1 of 2
        events_before = len(self.journal.records("route_event"))
        books_before = len(self.journal.records("paper_book"))
        decisions_before = len(self.journal.records("paper_decision"))
        later = stamp(utc(T2) + timedelta(seconds=1))
        self.switched_details("Example Case", "steamapis", later, bid_quantity=5)
        result = step(self.journal, "r1", later)
        self.assertEqual(result["status"], "waiting_for_evidence")
        self.assertEqual(result["reason"], "steam_source_changed_since_consumed_depth")
        self.assertEqual(len(self.journal.records("route_event")), events_before)
        self.assertEqual(len(self.journal.records("paper_book")), books_before)
        self.assertEqual(len(self.journal.records("paper_decision")), decisions_before)

    def test_same_provider_after_consumed_bid_depth_advances_normally(self):
        self.route()
        self.captures()
        self.assertEqual(step(self.journal, "r1", T2)["action"], "steam_sale")
        later = stamp(utc(T2) + timedelta(seconds=1))
        self.switched_details("Example Case", "steam_public", later, bid_quantity=5)
        self.assertEqual(step(self.journal, "r1", later)["action"], "steam_sale")

    def test_provider_difference_with_nothing_consumed_yet_is_not_blocked(self):
        self.route()
        for title in ("Example Case", "Return Case"):
            for kind in ("details", "targets"):
                if kind == "details":
                    payload = {"result": {"item": {"appId": 730, "marketName": title},
                        "meta": {"flags": {"commodity": True}}, "histogram": {"date": T2,
                        "buyOrders": [{"price": "1.15", "quantity": 1}],
                        "sellOrders": [{"price": "1.00", "quantity": 20}]}},
                        "provenance": {"book_quantity_semantics": "incremental"}}
                else:
                    payload = {"orders": [{"title": title, "attributes": {}, "price": "120", "amount": "20"}]}
                # 'steamapis' from the very first observation: nothing was ever consumed
                # under a different provider, so there is nothing to protect.
                self.journal.append("capture", {"provider": "steamapis" if kind == "details" else "dmarket",
                    "kind": kind, "app_id": 730, "title": title, "retrieved_at": T2,
                    "input_kind": "synthetic", "status": 200, "error": None, "payload": payload})
        self.assertEqual(step(self.journal, "r1", T2)["action"], "steam_sale")

    def test_pre_d1_consumption_with_no_provider_recorded_does_not_block_the_next_step(self):
        self.route()
        self.captures()
        key = f"{ENGINE}:synthetic:730:Example Case:steam_bid"
        # A live pre-D1 paper trial's paper_book event: fills present, no 'provider' key.
        # state is left empty so it does not constrain the real book's replayed depth.
        self.journal.append("paper_book", {"schema_version": 1, "engine": ENGINE, "key": key,
            "state": {}, "fills": [{"level_id": "999", "quantity": 1}], "evidence_ids": [],
            "at": T1, "decision_id": "legacy"})
        later = stamp(utc(T2) + timedelta(seconds=1))
        self.switched_details("Example Case", "steamapis", later, bid_quantity=5)
        self.assertEqual(step(self.journal, "r1", later)["action"], "steam_sale")

    def test_return_leg_provider_switch_after_consumed_ask_depth_reports_missing_evidence(self):
        # Two routes share the same depth keys (test_shared_depth_across_routes_and_
        # positive_additions above). r1's return purchase consumes 'Example Case'
        # steam_ask depth; r2 then evaluates the same shared title with a switched
        # provider and must be held, without losing 'Return Case' as a reported option.
        self.route("r1")
        self.route("r2")
        self.captures(bid_quantity=10)  # generous shared pool: both outward sales complete in one step
        self.assertEqual(step(self.journal, "r1", T2)["action"], "steam_sale")
        self.assertEqual(step(self.journal, "r2", T2)["action"], "steam_sale")
        self.assertEqual(step(self.journal, "r1", T2)["action"], "return_purchase")  # consumes Example Case steam_ask
        even_later = stamp(utc(T2) + timedelta(seconds=1))
        self.switched_details("Example Case", "steamapis", even_later, sell_quantity=20)
        result = step(self.journal, "r2", even_later)
        self.assertEqual(result["status"], "waiting_for_evidence")
        self.assertEqual(result["missing"], [{"title": "Example Case",
            "reason": "steam_source_changed_since_consumed_depth"}])
