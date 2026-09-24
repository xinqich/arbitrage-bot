"""Clock-controlled collection tests; no real market requests or transactions."""
from datetime import timedelta
import unittest
from uuid import uuid4

import test_local as fixtures
from test_research import T1, T2
from arbitrage_v2.collection_batch import CollectionBatch, request_for
from arbitrage_v2.evidence import stamp, utc
from arbitrage_v2.paper import required_evidence, step
from arbitrage_v2.routes import _state
from arbitrage_v2.worker import Worker, control, latest


class WorkerTests(unittest.TestCase):
    route = fixtures.LocalTests.route
    event = fixtures.LocalTests.event
    captures = fixtures.LocalTests.captures

    def setUp(self):
        fixtures.LocalTests.setUp(self)
        self.watch["steam_source"] = "steam_public"
        self.at, self.calls, self.failures = T2, [], {}
        self.bid_quantity, self.source_time = 2, None

    def clock(self):
        return self.at

    def fetch(self, journal, request, keys):
        self.calls.append(dict(request))
        identifier = str(uuid4())
        journal.append("request_attempt", dict(request, started_at=self.at), "attempt:" + identifier)
        status, error = self.failures.get(request["provider"], (200, None))
        title = request["title"]
        if request["kind"] == "details":
            payload = {"result": {"item": {"appId": 730, "marketName": title},
                "meta": {"flags": {"commodity": True}}, "histogram": {"date": self.source_time or self.at,
                "buyOrders": [{"price": "1.15", "quantity": self.bid_quantity}],
                "sellOrders": [{"price": "1.00", "quantity": 20}]}},
                "provenance": {"book_quantity_semantics": "incremental"}}
        elif request["kind"] == "targets":
            payload = {"orders": [{"title": title, "attributes": {}, "price": "120", "amount": "20"}]}
        else:
            payload = {"objects": []}  # Empty eligible supply stays unsupported in research.
        record = dict(request, retrieved_at=self.at, status=status, error=error,
                      payload=None if error else payload, input_kind="synthetic")
        record_id = journal.append("capture", record, "capture:" + identifier)
        return dict(request, status=status, error=error, record_id=record_id)

    def worker(self, fetch=None):
        return Worker(self.journal, self.watch, self.policy, self.mandate, self.config, {},
                      fetch or self.fetch, self.clock)

    def schedule(self, **extra):
        self.journal.append("worker_health", dict({"schema_version": 3, "collection_interval_seconds": 21600, "status": "waiting", "fatal": False,
            "last_attempt_at": T1, "next_research_at": stamp(utc(T2)+timedelta(hours=6)),
            "next_check_at": T2, "source_states": {}, "check_request": None, "resume_request": None}, **extra))

    def test_unrelated_dmarket_failure_does_not_stop_steam_sale(self):
        self.route()
        self.bid_quantity = 1
        self.failures["dmarket"] = (503, "http_503")
        self.worker().tick(self.at)
        self.assertEqual(self.calls[0]["kind"], "details")
        self.assertEqual(_state(self.journal, "r1")[5], "awaiting_steam_sale")
        self.assertEqual(len(self.journal.records("paper_decision")), 1)
        self.assertEqual(latest(self.journal, "worker_health")["source_states"]["dmarket"]["state"], "retry_wait")
        self.assertEqual(self.journal.get(self.pred_id), self.prediction)
        self.assertEqual(self.journal.records("outcome"), [])

    def test_new_steam_failure_preserves_fresh_book_and_original_prediction(self):
        self.route()
        self.captures(bid_quantity=2)
        self.failures["steam_public"] = (403, "http_403")
        before = len(self.journal.records("route_event"))
        self.worker().tick(self.at)
        self.assertGreater(len(self.journal.records("route_event")), before)
        self.assertEqual(latest(self.journal, "worker_health")["paper_steps"][0]["action"], "steam_sale")
        self.assertEqual(self.journal.get(self.pred_id), self.prediction)
        self.assertIn('steam_public:details', latest(self.journal, 'worker_health')['source_states'])

    def test_provider_switch_reason_surfaces_in_worker_health_paper_steps(self):
        self.route()
        self.captures(bid_quantity=1)  # provider steam_public; partial fill leaves 1 unit held
        self.assertEqual(step(self.journal, "r1", self.at)["action"], "steam_sale")
        self.at = stamp(utc(self.at) + timedelta(seconds=1))
        self.watch["steam_source"] = "steamapis"  # the route's next observation switches provider
        self.worker().tick(self.at)
        steps = latest(self.journal, "worker_health")["paper_steps"]
        self.assertEqual(steps[-1]["status"], "waiting_for_evidence")
        self.assertEqual(steps[-1]["reason"], "steam_source_changed_since_consumed_depth")
        self.assertEqual(self.journal.get(self.pred_id), self.prediction)

    def test_return_purchase_still_needs_every_frozen_basket_title(self):
        self.route()
        self.watch["item_sources"] = {"Return Case": "steamapis"}
        self.failures["steamapis"] = (403, "http_403")
        self.worker().tick(self.at)
        self.assertEqual(_state(self.journal, "r1")[5], "steam_wallet")
        steps = latest(self.journal, "worker_health")["paper_steps"]
        self.assertEqual(steps[0]["action"], "steam_sale")
        self.assertEqual(steps[-1]["status"], "waiting_for_evidence")
        self.assertEqual(steps[-1]["missing"][0]["title"], "Return Case")
        self.assertEqual(self.journal.get(self.pred_id), self.prediction)

    def test_fresh_retrieval_of_pre_unlock_book_cannot_sell(self):
        self.route()
        self.source_time = stamp(utc(T2)-timedelta(seconds=1))
        before = len(self.journal.records("route_event"))
        self.worker().tick(self.at)
        self.assertEqual(len(self.journal.records("route_event")), before)
        self.assertEqual(latest(self.journal, "worker_health")["paper_steps"][0]["reason"], "need_observation_after_unlock")

    def test_route_gets_last_request_and_targeted_check_keeps_research_schedule(self):
        self.route()
        self.bid_quantity = 1
        self.config.update(request_budget=1, steam_budget=1)
        self.schedule()
        research_at = latest(self.journal, "worker_health")["next_research_at"]
        self.worker().tick(self.at)
        self.assertEqual([(r["title"], r["kind"]) for r in self.calls], [("Example Case", "details")])
        health = latest(self.journal, "worker_health")
        self.assertEqual(health["collection_kind"], "route_check")
        self.assertEqual(health["next_research_at"], research_at)
        self.assertEqual(health["next_check_at"], research_at)
        self.at = stamp(utc(T2)+timedelta(seconds=5))
        self.worker().tick(self.at)  # Restart does not repeat the due check.
        self.assertEqual(len(self.calls), 1)

    def test_active_requests_are_not_repeated_for_research(self):
        self.route()
        self.config.update(request_budget=4, steam_budget=2)
        self.worker().tick(self.at)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(len({tuple(sorted(r.items())) for r in self.calls}), 4)
        self.assertFalse(any(r["kind"] == "offers" for r in self.calls))
        self.assertEqual(_state(self.journal, "r1")[5], "return_item_locked")
        self.assertFalse(latest(self.journal, "worker_health")["deferred_requests"])

    def test_dmarket_exit_needs_one_target_request_even_when_steam_is_blocked(self):
        self.route()
        self.captures(bid_quantity=2)
        step(self.journal, "r1", T2)
        step(self.journal, "r1", T2)
        self.at = stamp(utc(T2)+timedelta(seconds=10))
        self.schedule(last_attempt_at=T2, source_states={"steam_public": {
            "provider": "steam_public", "state": "blocked", "error": "http_403", "next_retry_at": None}})
        requirements = required_evidence(self.journal, "r1", self.at)
        self.assertEqual([r["kind"] for r in requirements], ["targets"])
        self.worker().tick(self.at)
        self.assertEqual([r["kind"] for r in self.calls], ["targets"])
        self.assertEqual(self.journal.records("outcome")[0]["actual_net_cents"], 56)
        self.assertEqual(self.journal.get(self.pred_id), self.prediction)

    def test_transient_retries_are_bounded_per_source_and_survive_restart(self):
        self.failures["dmarket"] = (503, "http_503")
        control(self.journal, "pause")
        self.worker().tick(self.at)
        self.assertEqual(self.calls, [])
        control(self.journal, "resume")
        for seconds in (0, 30, 60, 359, 360, 1260, 1262, 3600):
            self.at = stamp(utc(T2)+timedelta(seconds=seconds))
            self.worker().tick(self.at)
        self.assertEqual(len([r for r in self.calls if r["provider"] == "dmarket"]), 4)
        self.assertEqual(len([r for r in self.calls if r["provider"] == "steam_public"]), 2)
        source = latest(self.journal, "worker_health")["source_states"]["dmarket"]
        self.assertEqual(source["state"], "blocked")
        self.assertIsNone(source["next_retry_at"])

    def test_optional_auth_failure_does_not_stop_core_or_retry_each_cycle(self):
        self.watch.update(exploration_items=[{"app_id": 730, "title": "Exploration Case"}],
                          item_sources={"Exploration Case": "steamapis"})
        self.failures["steamapis"] = (403, "http_403")
        for hours in (0, 7):
            self.at = stamp(utc(T2)+timedelta(hours=hours))
            self.worker().tick(self.at)
        self.assertEqual(len([r for r in self.calls if r["provider"] == "steamapis"]), 1)
        self.assertEqual(len([r for r in self.calls if r["provider"] == "steam_public"]), 4)
        self.assertEqual(latest(self.journal, "worker_health")["source_states"]["steamapis:details"]["status"], 403)

    def test_unexpected_exception_consumes_command_and_never_exposes_its_text(self):
        calls = []
        def broken(*args):
            calls.append(1)
            raise RuntimeError("secret-bearing-url")
        control(self.journal, "resume")
        for _ in range(3):
            self.worker(broken).tick(self.at)
        self.assertEqual(len(calls), 1)
        health = latest(self.journal, "worker_health")
        self.assertEqual(health["reason"], "worker_RuntimeError")
        self.assertIsNotNone(health["resume_request"])
        control(self.journal, "resume")
        self.worker(broken).tick(self.at)
        self.assertEqual(len(calls), 2)

    def test_pause_during_request_does_not_move_route_or_fetch_again(self):
        self.route()
        before = len(self.journal.records("route_event"))
        def pausing(journal, request, keys):
            result = self.fetch(journal, request, keys)
            control(journal, "pause")
            return result
        self.worker(pausing).tick(self.at)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.journal.records("route_event")), before)
        self.assertEqual(latest(self.journal, "worker_health")["status"], "paused")

    def test_batch_keeps_healthy_source_and_persistent_cooldown(self):
        self.watch.update(total_request_allowance=3, steam_public_request_allowance=1)
        self.failures["dmarket"] = (429, "http_429")
        plan = [request_for(self.watch, 730, "Example Case", k) for k in ("offers", "details", "targets")]
        batch = CollectionBatch(self.journal, self.watch, self.config, {}, {}, self.clock, lambda: False, self.fetch)
        batch.ensure(plan + plan)
        self.assertEqual([r["provider"] for r in self.calls], ["dmarket", "steam_public"])
        self.failures.clear()
        restarted = CollectionBatch(self.journal, self.watch, self.config, {}, batch.sources, self.clock, lambda: False, self.fetch)
        restarted.ensure(list(reversed(plan)))
        self.assertEqual(len(self.calls), 3)
        self.assertFalse(restarted.exhausted)
        self.assertEqual(len(self.journal.records("request_attempt")), 3)

    def test_missing_credentials_stop_only_that_source_without_network_retries(self):
        self.route()
        self.bid_quantity = 1
        def missing(journal, request, keys):
            if request["provider"] == "dmarket":
                raise ValueError("missing DMarket credentials")
            return self.fetch(journal, request, keys)
        self.worker(missing).tick(self.at)
        self.assertEqual(_state(self.journal, "r1")[5], "awaiting_steam_sale")
        self.assertFalse(any(r["provider"] == "dmarket" for r in self.journal.records("request_attempt")))
        self.assertEqual(latest(self.journal, "worker_health")["source_states"]["dmarket"]["state"], "blocked")

    def test_successful_retry_clears_source_stop_without_shifting_research(self):
        self.failures["dmarket"] = (503, "http_503")
        self.worker().tick(self.at)
        research_at = latest(self.journal, "worker_health")["next_research_at"]
        self.failures.clear()
        self.calls.clear()
        self.at = stamp(utc(T2)+timedelta(seconds=60))
        self.worker().tick(self.at)
        self.assertEqual([r["provider"] for r in self.calls], ["dmarket"])
        health = latest(self.journal, "worker_health")
        self.assertEqual(health["source_states"], {})
        self.assertEqual(health["next_check_at"], research_at)
        self.assertEqual(health["next_research_at"], research_at)
        self.assertEqual(health["collection_kind"], "source_retry")

    def test_old_provider_allowance_is_ignored(self):
        self.watch["steam_public_request_allowance"] = 0
        plan = [request_for(self.watch, 730, "Example Case", k) for k in ("details", "targets")]
        batch = CollectionBatch(self.journal, self.watch, self.config, {}, {}, self.clock, lambda: False, self.fetch)
        batch.ensure(plan)
        self.assertEqual([r["provider"] for r in self.calls], ["steam_public", "dmarket"])
        self.assertEqual(batch.sources, {})

    def test_new_resume_arriving_during_failed_request_is_not_swallowed(self):
        calls = []
        def broken(*args):
            calls.append(1)
            if len(calls) == 1:
                control(self.journal, "resume")
            raise RuntimeError("failed")
        self.worker(broken).tick(self.at)
        self.worker(broken).tick(self.at)
        self.worker(broken).tick(self.at)
        self.assertEqual(len(calls), 2)

    def test_upgrade_keeps_existing_schedule_without_spending_requests(self):
        self.schedule(schema_version=1, last_success_at=T2)
        self.worker().tick(self.at)
        health = latest(self.journal, "worker_health")
        self.assertEqual(health["schema_version"], 3)
        self.assertEqual(health["last_success_at"], T2)
        self.assertEqual(health["next_check_at"], stamp(utc(T2)+timedelta(hours=6)))
        self.assertEqual(self.calls, [])
