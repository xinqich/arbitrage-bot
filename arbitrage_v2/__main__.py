"""Run from the independent project root: python -m arbitrage_v2 --help."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

from .evidence import inspect_book, stamp
from .store import EvidenceStore
from .mandate import load_mandate, mandate_summary
from . import research_cli, local_cli


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Arbitrage V2 offline evidence tools")
    parser.add_argument("--db", default="data/evidence.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    research_cli.register(parser, commands)
    local_cli.register(commands)
    commands.add_parser("init", help="create a new evidence database")
    mandate = commands.add_parser("mandate", help="inspect constraints without starting a run")
    mandate.add_argument("file", nargs="?", type=Path, default=Path("config/mandate.json"))
    ingest = commands.add_parser("ingest", help="archive a canonical offline evidence file")
    ingest.add_argument("file", type=Path)
    commands.add_parser("status", help="show evidence counts and missing qualifications")
    inspect = commands.add_parser("inspect", help="inspect observed depth, never confirm a fill")
    inspect.add_argument("observation_id")
    inspect.add_argument("--as-of", default=None)
    inspect.add_argument("--max-age-seconds", type=int, required=True)
    inspect.add_argument("--quantity", type=int, required=True)
    args = parser.parse_args(argv)
    store = EvidenceStore(args.db)
    try:
        if args.command in local_cli.COMMANDS:
            result = local_cli.run(args)
        elif args.command in research_cli.COMMANDS:
            result = research_cli.run(args)
        elif args.command == "mandate":
            result = mandate_summary(load_mandate(args.file))
        elif args.command == "init":
            store.initialize()
            result = {"database": str(store.path), "schema_version": 1}
        elif args.command == "ingest":
            result = store.ingest(args.file.read_bytes())
        elif args.command == "status":
            result = store.status()
        else:
            result = inspect_book(
                store.get(args.observation_id),
                as_of=args.as_of or stamp(datetime.now(timezone.utc)),
                max_age_seconds=args.max_age_seconds, quantity=args.quantity,
            )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, sqlite3.Error, OverflowError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
