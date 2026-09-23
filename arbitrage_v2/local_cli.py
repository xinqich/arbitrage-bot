"""Local desk, worker controls and explicit settings conversion."""
import json
from pathlib import Path

from .collector import credentials
from .collection_lock import collection_lock
from .collection_settings import settings as collection_settings
from .evidence import read_json
from .journal import Journal
from .mandate import load_mandate, convert_legacy
from .paper import configure, step
from .web import create_server, overview
from .worker import Worker, control, latest, now, search

COMMANDS = {"web", "worker-status", "worker-control", "paper-control", "paper-step",
            "search", "mandate-convert", "csfloat-collect", "route-correct", "route-recover", "backup", "backup-check", "restore"}


def register(commands):
    for name in ("backup", "backup-check", "restore"):
        command = commands.add_parser(name, help="create, check or restore an operational backup")
        command.add_argument("directory", type=Path)
        if name == "restore":
            command.add_argument("--replace-current", action="store_true",
                                 help="replace the journal; newer records remain only in the safety backup")
    web = commands.add_parser("web", help="serve the local page and background worker")
    web.add_argument("--env-file", type=Path)
    web.add_argument("--config", type=Path, default=Path("config/local.json"))
    cf=commands.add_parser("csfloat-collect",help="read authenticated CSFloat listings for contract qualification")
    cf.add_argument("title")
    cf.add_argument("--env-file",type=Path)
    recover=commands.add_parser("route-recover")
    recover.add_argument("file",type=Path)
    correction=commands.add_parser("route-correct")
    correction.add_argument("file",type=Path)
    commands.add_parser("worker-status")
    ctl = commands.add_parser("worker-control")
    ctl.add_argument("action", choices=["pause", "resume", "check_now"])
    paper = commands.add_parser("paper-control")
    paper.add_argument("route_id")
    paper.add_argument("action", choices=["enable", "pause"])
    paper = commands.add_parser("paper-step")
    paper.add_argument("route_id")
    find = commands.add_parser("search")
    find.add_argument("purpose", choices=["start", "grow", "withdraw"])
    find.add_argument("--mode", choices=["paper", "confirmed"], default="paper")
    convert = commands.add_parser("mandate-convert")
    convert.add_argument("file", type=Path)
    convert.add_argument("output", type=Path)


def load_config(path):
    value = collection_settings(read_json(path.read_bytes()))
    if value.get("schema_version") != 1 or value.get("host") != "127.0.0.1":
        raise ValueError("local configuration must use schema 1 and 127.0.0.1")
    bounds = {"port": (1, 65535), "collection_interval_seconds": (60, 86400),
              "freshness_seconds": (1, 14400), "ui_refresh_seconds": (5, 5)}
    for field, (low, high) in bounds.items():
        if type(value.get(field)) is not int or not low <= value[field] <= high:
            raise ValueError("invalid local setting: "+field)
    retries = value.get("retry_seconds")
    if (not isinstance(retries, list) or not 1 <= len(retries) <= 3
            or any(type(r) is not int or not 60 <= r <= 3600 for r in retries)):
        raise ValueError("one to three bounded retry delays required")
    return value


def run(args):
    if args.command == "mandate-convert":
        data = convert_legacy(load_mandate(args.file))
        with args.output.open("x", encoding="utf-8") as out:
            json.dump(data, out, indent=2)
        return {"output": str(args.output)}
    if args.command in {"backup", "backup-check", "restore"}:
        from .backups import create_backup, check_backup, restore_backup
        if args.command == "backup-check":
            return check_backup(args.directory)
        if args.command == "backup":
            return create_backup(Journal(args.journal), args.directory, Path.cwd())
        return restore_backup(Journal(args.journal), args.directory, Path.cwd(),
                              replace_current=args.replace_current)
    journal = Journal(args.journal)
    journal.initialize()
    if args.command=="route-recover":
        from .recovery import open_recovery
        return open_recovery(journal,read_json(args.file.read_bytes()))
    if args.command=="route-correct":
        from .routes import correct_event
        return {"correction_id":correct_event(journal,read_json(args.file.read_bytes()))}
    if args.command == "worker-control":
        return control(journal, args.action)
    if args.command == "worker-status":
        from .worker import controls
        return {"controls": controls(journal), "last_saved_health": latest(journal, "worker_health"),
                "note": "Saved health is not proof the process is currently alive; use the local webpage or scripts/local.ps1 -Action Status."}
    watchlist = read_json(Path("config/watchlist.json").read_bytes())
    policy = read_json(Path("config/research_assumptions.json").read_bytes())
    mandate = load_mandate(Path("config/mandate.json"))
    if mandate["schema_version"] != 4:
        raise ValueError("Convert the old mandate explicitly with mandate-convert before using the local desk.")
    if args.command=="csfloat-collect":
        from .csfloat import capture_listings
        with collection_lock(journal.path):
            return capture_listings(journal,args.title,credentials(args.env_file),watchlist.get("csfloat_request_allowance", 1000))
    if args.command == "paper-control":
        return configure(journal, args.route_id, args.action == "enable", watchlist, policy, now())
    if args.command == "paper-step":
        with collection_lock(journal.path):
            return step(journal, args.route_id, now())
    if args.command == "search":
        result = search(journal, args.purpose, watchlist, policy, mandate, now(), mode=args.mode)
        journal.append("search_report", dict(result, at=now()))
        return result
    config = load_config(args.config)
    with collection_lock(journal.path):
        try:
            keys=credentials(args.env_file)
        except OSError:
            keys={}  # Keep the page available so the missing access is visible there.
        worker = Worker(journal, watchlist, policy, mandate, config, keys)
        server = create_server(worker, Path.cwd(), config["port"])
        worker.start()
        print(json.dumps({"url": f'http://127.0.0.1:{config["port"]}', "status": "started"}), flush=True)
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            worker.stop.set()
            worker.wake.set()
            worker.thread.join(timeout=25)
            server.server_close()
    return {"status": "stopped"}
