"""Transactional, append-only input archive and deduplicated reported sales."""

from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3

from .evidence import canonical, normalize, read_json, stamp

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE observations (
    id TEXT PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    normalizer_version TEXT NOT NULL,
    input_kind TEXT NOT NULL CHECK(input_kind IN ('synthetic', 'recorded')),
    raw BLOB NOT NULL,
    normalized TEXT NOT NULL
);
CREATE TABLE sales (
    source TEXT NOT NULL,
    input_kind TEXT NOT NULL,
    item_key TEXT NOT NULL,
    venue TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    observation_id TEXT NOT NULL REFERENCES observations(id),
    PRIMARY KEY(source, input_kind, item_key, venue, event_id)
);
CREATE TRIGGER observations_no_update BEFORE UPDATE ON observations
BEGIN SELECT RAISE(ABORT, 'observations are immutable'); END;
CREATE TRIGGER observations_no_delete BEFORE DELETE ON observations
BEGIN SELECT RAISE(ABORT, 'observations are immutable'); END;
CREATE TRIGGER sales_no_update BEFORE UPDATE ON sales
BEGIN SELECT RAISE(ABORT, 'sales are immutable'); END;
CREATE TRIGGER sales_no_delete BEFORE DELETE ON sales
BEGIN SELECT RAISE(ABORT, 'sales are immutable'); END;
PRAGMA user_version = 1;
"""


class EvidenceStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        mode = "ro" if readonly else "rw"
        connection = sqlite3.connect(self.path.as_uri() + "?mode=" + mode, uri=True)
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            connection.close()
            raise ValueError("unsupported database schema; no changes made")
        return connection

    def initialize(self) -> None:
        if self.path.exists():
            with closing(self._connect(readonly=True)):
                return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Reserve exclusively. Never initialize an existing unrelated database.
        with self.path.open("xb"):
            pass
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")

    def ingest(self, raw: bytes) -> dict:
        observation = normalize(raw)
        identifier = sha256(raw).hexdigest()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO observations VALUES (?, ?, ?, ?, ?, ?)",
                (identifier, stamp(datetime.now(timezone.utc)), observation["normalizer_version"],
                 observation["input_kind"], raw, canonical(observation)),
            )
            for sale in observation["sales"]:
                key = (observation["source"], observation["input_kind"],
                       observation["item_key"], sale["venue"], sale["event_id"])
                existing = connection.execute(
                    "SELECT event_json FROM sales WHERE source=? AND input_kind=? "
                    "AND item_key=? AND venue=? AND event_id=?", key
                ).fetchone()
                event_json = canonical(sale)
                if existing and existing[0] != event_json:
                    raise ValueError("conflicting reported sale identity; ingestion rolled back")
                if not existing:
                    connection.execute("INSERT INTO sales VALUES (?, ?, ?, ?, ?, ?, ?)",
                                       (*key, event_json, identifier))
        return {"observation_id": identifier, "input_kind": observation["input_kind"]}

    def get(self, identifier: str) -> dict:
        with closing(self._connect(readonly=True)) as connection:
            row = connection.execute(
                "SELECT raw, normalized FROM observations WHERE id=?", (identifier,)
            ).fetchone()
        if row is None:
            raise ValueError("unknown observation")
        if sha256(row[0]).hexdigest() != identifier:
            raise ValueError("observation integrity check failed")
        observation = normalize(row[0])
        if canonical(observation) != row[1]:
            raise ValueError("normalized evidence integrity check failed")
        return observation

    def status(self) -> dict:
        with closing(self._connect(readonly=True)) as connection:
            counts = dict(connection.execute(
                "SELECT input_kind, COUNT(*) FROM observations GROUP BY input_kind"
            ))
            event_counts = dict(connection.execute(
                "SELECT input_kind, COUNT(*) FROM sales GROUP BY input_kind"
            ))
        return {
            "schema_version": SCHEMA_VERSION, "mode": "offline_evidence",
            "observation_counts": counts, "unique_reported_sale_events": event_counts,
            "sale_history_coverage": "unknown",
            "provider_qualification": "not_established",
            "economic_milestone": "not_evaluated",
            "live_execution_available": False,
        }
