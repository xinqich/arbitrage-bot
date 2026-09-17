"""Append-only operational records, separate from the original evidence database."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
from .evidence import canonical, stamp

class Journal:
    def __init__(self, path):
        self.path = Path(path).resolve()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version == 1 and ("records",) in tables:
                return
            if version != 0 or tables:
                raise ValueError("not a supported journal; unchanged")
            db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE records (seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
                category TEXT NOT NULL, recorded_at TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX records_category ON records(category,seq);
            CREATE TRIGGER records_no_update BEFORE UPDATE ON records
            BEGIN SELECT RAISE(ABORT,'immutable journal'); END;
            CREATE TRIGGER records_no_delete BEFORE DELETE ON records
            BEGIN SELECT RAISE(ABORT,'immutable journal'); END;
            PRAGMA user_version=1;
            COMMIT;
            """)

    @contextmanager
    def connect(self, write=False):
        mode = "rw" if write else "ro"
        with closing(sqlite3.connect(self.path.as_uri()+"?mode="+mode, uri=True)) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("unsupported journal schema")
            if write:
                db.execute("BEGIN IMMEDIATE")
            with db:
                yield db

    def append(self, category, payload, identifier=None, db=None):
        encoded = canonical(payload)
        identifier = identifier or sha256((category+"\n"+encoded).encode()).hexdigest()
        if db is None:
            with self.connect(True) as connection:
                return self.append(category,payload,identifier,connection)
        old = db.execute("SELECT category,payload FROM records WHERE id=?", (identifier,)).fetchone()
        if old:
            if old != (category,encoded):
                raise ValueError("idempotency key conflicts with existing record")
            return identifier
        db.execute("INSERT INTO records(id,category,recorded_at,payload) VALUES(?,?,?,?)",
                   (identifier,category,stamp(datetime.now(timezone.utc)),encoded))
        return identifier

    def records(self, category, db=None):
        if db is None:
            with self.connect() as connection:
                return self.records(category,connection)
        return [dict(json.loads(row[1]), record_id=row[0], _recorded_at=row[2]) for row in db.execute(
            "SELECT id,payload,recorded_at FROM records WHERE category=? ORDER BY seq",(category,))]

    def get(self, identifier, category=None, db=None):
        if db is None:
            with self.connect() as connection:
                return self.get(identifier,category,connection)
        row=db.execute("SELECT category,payload FROM records WHERE id=?",(identifier,)).fetchone()
        if not row or (category is not None and row[0] != category):
            raise ValueError("unknown record or unexpected category")
        return json.loads(row[1])
