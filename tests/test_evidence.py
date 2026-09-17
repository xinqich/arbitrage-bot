from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from arbitrage_v2.evidence import (
    histogram_time, inspect_book, normalize, normalize_depth, quote_quantity,
)
from arbitrage_v2.money import MAX_INTEGER, price_cents
from arbitrage_v2.store import EvidenceStore

ROOT = Path(__file__).resolve().parents[1]


def sample():
    return json.loads((ROOT / "examples" / "synthetic_evidence.json").read_text(encoding="utf-8-sig"))


def raw(value):
    return json.dumps(value).encode("utf-8")


class MoneyTests(unittest.TestCase):
    def test_integer_looking_dollars_have_explicit_units(self):
        self.assertEqual(price_cents("12", currency="USD", unit="usd"), 1200)
        self.assertEqual(price_cents("12", currency="USD", unit="cents"), 12)

    def test_rejects_unsafe_and_ambiguous_money(self):
        for value in [True, 2.91, "NaN", "Infinity", "-1", "0.001",
                      "1.00000000000000000000000000000000000000000001",
                      "1e-999999999", str(MAX_INTEGER)]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                price_cents(value, currency="USD", unit="usd")
        for currency, unit in [("EUR", "cents"), ("USD", "auto"), ("USD", {})]:
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                price_cents(1, currency=currency, unit=unit)

    def test_exact_cent_boundary_and_large_integer(self):
        self.assertEqual(price_cents("0.29", currency="USD", unit="usd"), 29)
        self.assertEqual(price_cents(str(MAX_INTEGER), currency="USD", unit="cents"), MAX_INTEGER)


class EvidenceTests(unittest.TestCase):
    def test_negative_age_reconstructs_source_time(self):
        self.assertEqual(
            histogram_time(generated_at="2026-09-07T14:00:29.518Z", age_seconds=-207,
                           retrieved_at="2026-09-07T14:30:00Z"),
            "2026-09-07T14:03:56.518000+00:00",
        )
        with self.assertRaises(ValueError):
            histogram_time(generated_at="2026-09-07T14:00:00Z", age_seconds=-207,
                           retrieved_at="2026-09-07T14:01:00Z")

    def test_freshness_does_not_reset_on_retrieval(self):
        observation = normalize(raw(sample()))
        report = inspect_book(observation, as_of="2026-09-07T14:30:00Z",
                              max_age_seconds=600, quantity=5)
        self.assertEqual(report["data_status"], "stale_book")
        self.assertNotIn("depth", report)

    def test_no_lookahead_from_late_retrieval(self):
        observation = normalize(raw(sample()))
        report = inspect_book(observation, as_of="2026-09-07T14:04:00Z",
                              max_age_seconds=600, quantity=5)
        self.assertEqual(report["data_status"], "not_available_at_decision")

    def test_supported_quantity_uses_actual_lower_price(self):
        observation = normalize(raw(sample()))
        report = inspect_book(observation, as_of="2026-09-07T14:05:00Z",
                              max_age_seconds=120, quantity=5)
        self.assertEqual(report["depth"]["supported_gross_cents"], 292 + 4 * 291)
        self.assertEqual(report["depth"]["worst_price_cents"], 291)
        self.assertFalse(report["is_fill_confirmation"])
        self.assertEqual(report["route_readiness"], "not_evaluated")

    def test_cumulative_and_incremental_represent_same_capacity(self):
        rows = [{"price": "2.92", "quantity": 1}, {"price": "2.91", "quantity": 6}]
        cumulative = normalize_depth(rows, side="bid", semantics="cumulative",
                                     currency="USD", unit="usd")
        self.assertEqual(cumulative, normalize(raw(sample()))["book"]["levels"])
        self.assertEqual(quote_quantity(cumulative, 7)["supported_quantity"], 6)
        self.assertFalse(quote_quantity(cumulative, 7)["complete_quantity"])

    def test_asks_sort_lowest_first_and_do_not_double_count(self):
        rows = [{"price": 102, "quantity": 5}, {"price": 100, "quantity": 2}]
        levels = normalize_depth(rows, side="ask", semantics="cumulative",
                                 currency="USD", unit="cents")
        self.assertEqual(quote_quantity(levels, 4)["supported_gross_cents"], 404)

    def test_unknown_or_ambiguous_depth_fails(self):
        cases = [
            ("unknown", [{"price": 100, "quantity": 1}]),
            ("cumulative", [{"price": 100, "quantity": 4}, {"price": 99, "quantity": 2}]),
            ("incremental", [{"price": 100, "quantity": 1}, {"price": 100, "quantity": 2}]),
            ({}, []),
        ]
        for semantics, rows in cases:
            with self.subTest(semantics=semantics), self.assertRaises(ValueError):
                normalize_depth(rows, side="bid", semantics=semantics,
                                currency="USD", unit="cents")

    def test_invalid_contract_inputs_fail(self):
        invalid = []
        data = sample()
        data["book"]["observed_at"] = "2026-09-07T14:03:56"
        invalid.append(data)
        data = sample()
        data["sales"][0]["evidence_kind"] = "order_disappeared"
        invalid.append(data)
        data = sample()
        data["authorization"] = "must-not-be-retained"
        invalid.append(data)
        data = sample()
        data["input_kind"] = {}
        invalid.append(data)
        data = sample()
        data["sales"][0]["occurred_at"] = "2026-09-08T00:00:00Z"
        invalid.append(data)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                normalize(raw(data))
        with self.assertRaises(ValueError):
            normalize(b'{"schema_version": 1, "schema_version": 1}')

    def test_exact_attributes_distinguish_items(self):
        first = sample()
        second = copy.deepcopy(first)
        second["item"]["attributes"]["quality"] = "different"
        self.assertNotEqual(normalize(raw(first))["item_key"], normalize(raw(second))["item_key"])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "evidence.sqlite3"
        self.store = EvidenceStore(self.path)
        self.store.initialize()

    def test_identical_ingestion_and_repeated_sale_are_idempotent(self):
        first = self.store.ingest(raw(sample()))
        self.assertEqual(first, self.store.ingest(raw(sample())))
        second = sample()
        second["retrieved_at"] = "2026-09-07T14:06:00Z"
        self.store.ingest(raw(second))
        status = self.store.status()
        self.assertEqual(status["observation_counts"], {"synthetic": 2})
        self.assertEqual(status["unique_reported_sale_events"], {"synthetic": 1})
        self.assertEqual(status["sale_history_coverage"], "unknown")

    def test_conflicting_event_rolls_back_entire_observation(self):
        self.store.ingest(raw(sample()))
        conflict = sample()
        conflict["sales"][0]["quantity"] = 99
        with self.assertRaises(ValueError):
            self.store.ingest(raw(conflict))
        self.assertEqual(self.store.status()["observation_counts"], {"synthetic": 1})

    def test_sales_from_different_items_remain_distinct(self):
        self.store.ingest(raw(sample()))
        second = sample()
        second["item"]["market_hash_name"] = "Another synthetic item"
        self.store.ingest(raw(second))
        self.assertEqual(self.store.status()["unique_reported_sale_events"], {"synthetic": 2})

    def test_synthetic_cannot_contaminate_recorded_events(self):
        self.store.ingest(raw(sample()))
        second = sample()
        second["input_kind"] = "recorded"
        self.store.ingest(raw(second))
        self.assertEqual(self.store.status()["unique_reported_sale_events"],
                         {"synthetic": 1, "recorded": 1})
        self.assertEqual(self.store.status()["provider_qualification"], "not_established")

    def test_missing_book_and_sales_does_not_invent_inactivity_or_fills(self):
        empty = sample()
        empty["book"] = None
        empty["sales"] = []
        identifier = self.store.ingest(raw(empty))["observation_id"]
        result = inspect_book(self.store.get(identifier), as_of=empty["retrieved_at"],
                              max_age_seconds=120, quantity=1)
        self.assertEqual(result["data_status"], "missing_book")
        self.assertEqual(self.store.status()["unique_reported_sale_events"], {})
        self.assertEqual(self.store.status()["sale_history_coverage"], "unknown")

    def test_database_rejects_history_rewrites(self):
        identifier = self.store.ingest(raw(sample()))["observation_id"]
        self.assertEqual(self.store.get(identifier), normalize(raw(sample())))
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for statement in ["DELETE FROM observations", "UPDATE observations SET raw=X'00'",
                              "DELETE FROM sales", "UPDATE sales SET event_id='new'"]:
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(statement)

    def test_existing_unrelated_database_is_untouched(self):
        other = Path(self.temporary.name) / "other.sqlite3"
        with closing(sqlite3.connect(other)) as connection, connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        original = other.read_bytes()
        with self.assertRaises(ValueError):
            EvidenceStore(other).initialize()
        self.assertEqual(other.read_bytes(), original)

    def test_unknown_future_schema_is_rejected(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("PRAGMA user_version=99")
        with self.assertRaises(ValueError):
            self.store.status()

    def test_cli_smoke_and_missing_database_exit(self):
        def run(*args):
            return subprocess.run([sys.executable, "-B", "-m", "arbitrage_v2",
                                   "--db", str(self.path), *args],
                                  cwd=ROOT, capture_output=True, text=True, check=False)
        ingest = run("ingest", str(ROOT / "examples" / "synthetic_evidence.json"))
        self.assertEqual(ingest.returncode, 0, ingest.stderr)
        identifier = json.loads(ingest.stdout)["observation_id"]
        inspected = run("inspect", identifier, "--as-of", "2026-09-07T14:05:00Z",
                        "--max-age-seconds", "120", "--quantity", "5")
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertEqual(json.loads(inspected.stdout)["data_status"], "fresh_book")
        self.assertEqual(run("inspect", "missing", "--max-age-seconds", "120",
                             "--quantity", "1").returncode, 2)
