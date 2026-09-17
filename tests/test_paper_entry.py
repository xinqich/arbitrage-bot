from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from http.client import HTTPConnection
import json
import threading
import unittest
from unittest.mock import patch

import test_local as fixtures
from test_research import T2
from arbitrage_v2.depth import ENGINE
from arbitrage_v2.entry_depth import unused_entry
from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.funds import available_capital
from arbitrage_v2.paper import settings, step
from arbitrage_v2.paper_entry import preview, enter
from arbitrage_v2.prediction import calculate, market_snapshot, screen, QUALIFIED_ITEMS, ITEM_FAMILY
from arbitrage_v2.routes import _state, train
from arbitrage_v2.web import create_server
from arbitrage_v2.worker import Worker, search


class EntryTests(unittest.TestCase):
    def setUp(self):
        fixtures.LocalTests.setUp(self)
        self.at = T2
        self.policy.update(minimum_known_delay_seconds=20, minimum_return_delay_seconds=10)
        self.titles = ["Example Case", "Return Case"]
        self.recorded_quotes()

    def recorded_quotes(self, count=10, partition="recorded"):
        # Test-only journal. No fixture records are ever copied to the live journal.
        for title in self.titles:
            for kind in ("details", "offers", "targets"):
                if kind == "details":
                    payload = {"result": {"item": {"appId": 730, "marketName": title},
                        "meta": {"flags": {"commodity": QUALIFIED_ITEMS.get(title, True)}},
                        "histogram": {"date": self.at, "buyOrders": [{"price": "1.15", "quantity": 20}],
                                      "sellOrders": [{"price": "1.00", "quantity": 20}]}},
                        "provenance": {"book_quantity_semantics": "incremental"}}
                elif kind == "offers":
                    payload = {"items": [{"offerId": title+":"+str(i), "priceCents": 80, "locked": False,
                        "attributes": {"title": title, "gameId": "a8db", "withdrawable": True, "tradable": True,
                            "cs2": {"stickers": [], "charms": [], "isProskin": False, "phase": "",
                                    "rarePattern": "RARE_PATTERN_UNSPECIFIED", "float": "0.25"}}} for i in range(count)]}
                else:
                    payload = {"orders": [{"title": title, "attributes": {}, "price": "120", "amount": "20"}]}
                self.journal.append("capture", {"provider": "steamapis" if kind == "details" else "dmarket",
                    "kind": kind, "title": title, "app_id": 730, "retrieved_at": self.at,
                    "input_kind": partition, "status": 200, "error": None, "payload": payload})

    def make_prediction(self, quantity=2, reverse=False):
        a, b = [unused_entry(self.journal, market_snapshot(self.journal, t, self.at, 14400)) for t in self.titles]
        pred = calculate(b if reverse else a, a if reverse else b, quantity, self.policy)
        pred["predicted_at"] = stamp(utc(self.at))
        return self.journal.append("prediction", pred)

    def request(self, prediction_id=None, route_id="new-paper"):
        return {"route_id": route_id, "prediction_id": prediction_id or self.make_prediction()}

    def start(self, request):
        return enter(self.journal, request, self.mandate, self.policy, self.at)

    def test_entry_debits_funds_freezes_pair_and_keeps_original(self):
        request = self.request()
        original = self.journal.get(request["prediction_id"])
        before = len(self.journal.records("route_event"))
        review = preview(self.journal, request["prediction_id"], self.mandate, self.policy, self.at)
        self.assertTrue(review["ready"])
        self.assertEqual(len(self.journal.records("route_event")), before)
        self.start(request)
        route = _state(self.journal, request["route_id"])
        self.assertEqual(route[0]["mode"], "paper")
        self.assertEqual(route[4], {"730:Example Case": 2})
        self.assertEqual(route[5], "steam_locked")
        self.assertEqual(route[6], stamp(utc(T2)+timedelta(seconds=10)))
        self.assertEqual(available_capital(self.journal, self.mandate), 840)
        self.assertEqual(settings(self.journal, request["route_id"])["titles"], self.titles)
        self.assertEqual(self.journal.get(request["prediction_id"]), original)
        self.assertEqual(self.journal.records("outcome"), [])

    def test_entry_is_idempotent_even_after_quotes_expire(self):
        request = self.request()
        self.start(request)
        count = len(self.journal.records("route_event"))
        self.at = stamp(utc(T2)+timedelta(days=1))
        self.assertEqual(self.start(request)["status"], "already_opened")
        self.assertEqual(len(self.journal.records("route_event")), count)
        with self.assertRaisesRegex(ValueError, "different"):
            self.start(dict(request, prediction_id="changed"))

    def test_atomic_failure_leaves_no_route_debit_or_consumed_offer(self):
        request = self.request()
        with patch("arbitrage_v2.paper_entry.configure", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                self.start(request)
        for category in ("route", "route_event", "paper_entry", "paper_settings"):
            self.assertEqual(self.journal.records(category), [])
        self.assertEqual(available_capital(self.journal, self.mandate), 1000)
        self.assertTrue(preview(self.journal, request["prediction_id"], self.mandate, self.policy, self.at)["ready"])

    def test_stale_or_changed_quotes_block_entry_and_require_new_search(self):
        request = self.request()
        self.at = stamp(utc(T2)+timedelta(seconds=1))
        self.recorded_quotes()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.start(request)
        fresh = self.request(route_id="fresh")
        self.at = stamp(utc(self.at)+timedelta(hours=5))
        with self.assertRaisesRegex(ValueError, "old"):
            self.start(fresh)
        self.assertEqual(self.journal.records("route"), [])

    def test_failed_latest_capture_and_changed_policy_block_entry(self):
        request = self.request()
        altered = dict(self.policy, other_cost_cents=1)
        with self.assertRaisesRegex(ValueError, "assumptions"):
            enter(self.journal, request, self.mandate, altered, self.at)
        capture = self.journal.records("capture")[0]
        self.journal.append("capture", dict(capture, status=503, error="http_503", payload=None))
        with self.assertRaisesRegex(ValueError, "provider_request_failed"):
            self.start(request)

    def test_synthetic_or_nonpositive_predictions_are_not_page_entries(self):
        for change in ({"input_kind": "synthetic"}, {"base_net_cents": -1, "predicted_net_cents": -1},
                       {"costs_known": False, "predicted_net_cents": None}, {"learning_mode": "confirmed"}):
            pred = dict(self.journal.get(self.make_prediction()), **change)
            request = self.request(self.journal.append("prediction", pred))
            with self.assertRaises(ValueError):
                self.start(request)
        self.assertEqual(self.journal.records("route"), [])

    def test_parallel_entries_cannot_spend_the_same_paper_money(self):
        requests = [self.request(self.make_prediction(8), "first"), self.request(self.make_prediction(8, True), "second")]
        def attempt(request):
            try:
                self.start(request)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(attempt, requests)), [False, True])
        self.assertEqual(len(self.journal.records("route")), 1)
        self.assertEqual(available_capital(self.journal, self.mandate), 360)

    def test_consumed_offers_stay_unavailable_across_new_search_and_restart(self):
        request = self.request(self.make_prediction(8))
        self.start(request)
        self.at = stamp(utc(T2)+timedelta(seconds=1))
        self.recorded_quotes()  # Same offer IDs returning must not replenish them.
        result = screen(self.journal, self.watch, self.policy, self.at, 1000)
        quantities = [p["quantity_a"] for p in result["predictions"] if p["item_a"] == "Example Case"]
        self.assertEqual(max(quantities), 2)
        self.assertEqual(len(unused_entry(self.journal, market_snapshot(self.journal, "Example Case", self.at, 14400))["dmarket_ask"]["levels"]), 2)
        with self.assertRaises(ValueError):
            self.start(dict(request, route_id="duplicate-offers"))

    def test_restricted_funds_are_debited_as_restricted(self):
        self.mandate = deepcopy(self.mandate)
        self.mandate["starting_balances"][0].update(account="tradable")
        self.start(self.request())
        movement = next(e for e in self.journal.records("route_event") if e["kind"] == "movement")
        self.assertEqual(movement["account"], "dmarket_tradable")
        self.assertEqual(movement["net_delta_cents"], -160)

    def test_legacy_trial_offer_pool_is_not_silently_reused(self):
        from arbitrage_v2.routes import open_route, add_event
        prediction_id = self.make_prediction()
        open_route(self.journal, "legacy", prediction_id, "paper", self.at)
        add_event(self.journal, {"event_id": "legacy-debit", "route_id": "legacy", "at": self.at,
            "reference": "old-paper-entry", "kind": "movement", "account": "dmarket_regular",
            "net_delta_cents": -160, "fee_cents": 0})
        original = self.journal.get(prediction_id)
        with self.assertRaisesRegex(ValueError, "already_used"):
            unused_entry(self.journal, market_snapshot(self.journal, "Example Case", self.at, 14400))
        self.assertEqual(self.journal.get(prediction_id), original)
        self.assertEqual(self.journal.records("paper_entry"), [])

    def test_used_dmarket_offers_do_not_block_that_item_as_steam_return(self):
        self.recorded_quotes(count=2)
        self.start(self.request(self.make_prediction(2, True), "uses-return-offers"))
        result = screen(self.journal, self.watch, self.policy, self.at, 840)
        candidate = next(p for p in result["predictions"] if p["item_a"] == "Example Case" and p["item_b"] == "Return Case")
        self.assertFalse(any(p["item_a"] == "Return Case" for p in result["predictions"]))
        self.assertTrue(preview(self.journal, candidate["prediction_id"], self.mandate, self.policy, self.at)["ready"])

    def test_noncase_trial_finishes_and_learns_only_with_its_own_family(self):
        self.titles = ["Paris 2023 Legends Sticker Capsule", "AK-47 | Slate (Field-Tested)"]
        self.watch["items"] = [{"app_id": 730, "title": t} for t in self.titles]
        self.recorded_quotes()
        request = self.request()
        original = self.journal.get(request["prediction_id"])
        self.assertEqual(original["family"], ITEM_FAMILY)
        self.start(request)
        for seconds in (10, 20):
            self.at = stamp(utc(T2)+timedelta(seconds=seconds))
            self.recorded_quotes()
            step(self.journal, request["route_id"], self.at)
            step(self.journal, request["route_id"], self.at)
        outcome = self.journal.records("outcome")[0]
        self.assertEqual(outcome["actual_net_cents"], 56)
        self.assertEqual(outcome["family"], ITEM_FAMILY)
        result = screen(self.journal, self.watch, self.policy, self.at, 1000)
        self.assertTrue(all(p["training_sample_count"] == (1 if p["engine_version"] == ENGINE else 0) for p in result["predictions"]))
        with self.assertRaisesRegex(ValueError, "no resolved"):
            train(self.journal, ITEM_FAMILY, "confirmed", "recorded", self.at, ENGINE)
        self.assertEqual(self.journal.get(request["prediction_id"]), original)

    def test_start_and_withdraw_are_deferred_without_requests(self):
        for purpose in ("start", "withdraw"):
            result = search(self.journal, purpose, self.watch, self.policy, self.mandate, self.at)
            self.assertEqual(result["status"], "deferred")
            self.assertNotIn("blockers", result)
        self.assertEqual(self.journal.records("request_attempt"), [])

    def test_local_api_requires_session_and_only_creates_paper_entries(self):
        worker = Worker(self.journal, self.watch, self.policy, self.mandate, self.config, {})
        server = create_server(worker, fixtures.ROOT, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def call(method, path, body=None, headers=None):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request(method, path, json.dumps(body) if body else None, headers or {})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        token = call("GET", "/api/session")[1]["token"]
        headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{server.server_port}", "X-Local-Token": token}
        request = self.request()
        body = {"action": "enter_paper", "entry": request}
        with patch("arbitrage_v2.web.now", return_value=self.at):
            self.assertEqual(call("POST", "/api/action", body)[0], 403)
            self.assertEqual(call("POST", "/api/action", {"action": "preview_paper", "prediction_id": request["prediction_id"]}, headers)[1]["ready"], True)
            self.assertEqual(call("POST", "/api/action", dict(body, entry=dict(request, mode="confirmed")), headers)[0], 400)
            self.assertEqual(call("POST", "/api/action", body, headers)[0], 200)
            self.assertEqual(call("POST", "/api/action", body, headers)[1]["status"], "already_opened")
        self.assertEqual(self.journal.records("route")[0]["mode"], "paper")
