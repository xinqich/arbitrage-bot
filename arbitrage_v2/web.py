"""Loopback-only dashboard. Tokens stay in memory; no cloud or trading calls."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sqlite3
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit

from .evidence import read_json, utc
from .paper import configure, settings
from .paper_entry import preview as preview_paper, enter as enter_paper
from .recovery import open_recovery
from . import real_routes, real_funds, __version__, steam_wallet, holding_costs, route_details
from .routes import add_event, route_status, correct_event, _state
from .worker import controls, control, latest, now, search, available_capital

ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
REPORTS = {"DEVELOPMENT_PLAN.md", "IMPLEMENTATION_STATUS.md", "APPROVED_DESIGN.md",
           "CSFLOAT_FEASIBILITY_2026-09-14.md", "STEAM_FEE_AUDIT.md", "LOCAL_DESK.md",
           "NONCASE_QUALIFICATION_2026-09-14.md", "RELEASE_0_5_CHECKS.md", "RELEASE_0_6_CHECKS.md", "RELEASE_0_7_CHECKS.md", "DISCOVERY_FILTER_AUDIT.md", "RELEASE_0_8_CHECKS.md", "REAL_GROWTH_WORKFLOW.md", "RELEASE_0_9_CHECKS.md", "BACKUP_RESTORE.md", "RELEASE_1_0_CHECKS.md", "UI_REWORK.md"}


def overview(worker):
    journal = worker.journal
    at = now()
    routes = [dict(route_status(journal, r["route_id"], at),
                   paper_settings=settings(journal, r["route_id"])) for r in journal.records("route")]
    with journal.connect() as db:
        requests = dict(db.execute("SELECT json_extract(payload,'$.provider'),count(*) FROM records "
            "WHERE category='request_attempt' GROUP BY json_extract(payload,'$.provider')"))
        captures = db.execute("SELECT json_extract(payload,'$.retrieved_at'),json_extract(payload,'$.error'),"
            "json_extract(payload,'$.provider'),json_extract(payload,'$.title') FROM records WHERE category='capture' "
            "AND json_extract(payload,'$.input_kind')='recorded' ORDER BY seq").fetchall()
    times = sorted({utc(row[0]) for row in captures if not row[1]})
    gaps = [{"from": a.isoformat(), "to": b.isoformat(), "hours": round((b-a).total_seconds()/3600, 2)}
            for a, b in zip(times, times[1:]) if (b-a).total_seconds() > 7*3600]
    health = latest(journal, "worker_health") or {}
    eligible = [r["next_eligible_at"] for r in routes if r["paper_settings"] and r["paper_settings"]["enabled"]
                and r["next_eligible_at"] and utc(r["next_eligible_at"]) > utc(at)]
    due = ([health["next_check_at"]] if health.get("next_check_at") else [])+eligible
    if health.get("status") == "blocked" and health.get("fatal", True):
        due = []
    searches={}
    with journal.connect() as db:
        for mode in ('confirmed','paper'):
            row=db.execute("SELECT id,payload FROM records WHERE category='search_report' AND "
                           "coalesce(json_extract(payload,'$.mode'),'paper')=? ORDER BY seq DESC LIMIT 1",(mode,)).fetchone()
            searches[mode]=dict(json.loads(row[1]),record_id=row[0]) if row else None
    return {"searches":searches, "holding_costs":holding_costs.summary(journal),
        "steam_wallet":steam_wallet.status(journal), "version": __version__, "at": at, "controls": controls(journal), "busy": worker.busy,
        "worker_alive": bool(worker.thread and worker.thread.is_alive()), "health": health,
        "next_action_at": min(due, key=utc) if due else None,
        "last_observation_at": times[-1].isoformat() if times else None,
        "coverage": "Observation gaps remain gaps; price charts are not verified individual sales.",
        "gaps": gaps[-10:], "recent_errors": [dict(at=a, error=e, provider=p, title=t)
                                               for a, e, p, t in captures[-60:] if e],
        "requests": requests, "request_allowance": worker.watchlist["total_request_allowance"],
        "steam_public_allowance": worker.watchlist.get("steam_public_request_allowance", 1000),
        "routes": routes, "outcomes": journal.records("outcome"),
        "events": [e for r in routes for e in _state(journal,r["route_id"])[2]],
        "paper_decisions": journal.records("paper_decision")[-20:],
        "balances": worker.mandate["starting_balances"],
        "real_funds": real_funds.status(journal, worker.mandate),
        "real_steps": journal.records("real_step"),
        "unallocated_dmarket_paper_cents": available_capital(journal, worker.mandate),
        "search": latest(journal, "search_report"),
        "reports": sorted(REPORTS), "economic_evidence": "Not established"}


def create_server(worker, root, port=8765):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("invalid local port")
    root = Path(root).resolve()
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        server_version = "ArbitrageLocal/" + __version__

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            pass  # Avoid persisting user-entered references or query strings.

        def reply(self, status, value, mime="application/json; charset=utf-8"):
            body = json.dumps(value, ensure_ascii=False).encode() if mime.startswith("application/json") else value
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.host_ok():
                return self.reply(403, {"error": "Use the local 127.0.0.1 address."})
            path = urlsplit(self.path)
            try:
                if path.path in ASSETS and not path.query:
                    name, mime = ASSETS[path.path]
                    return self.reply(200, (Path(__file__).parent/"static"/name).read_bytes(), mime)
                if path.path == "/api/route-detail":
                    query=parse_qs(path.query)
                    if len(query)!=1 or set(query)-{'prediction_id','route_id'} or any(len(v)!=1 for v in query.values()):
                        raise ValueError('Choose one prediction or route.')
                    return self.reply(200,route_details.detail(worker.journal,at=now(),**{k:v[0] for k,v in query.items()}))
                if path.path == "/api/session":
                    return self.reply(200, {"token": token})
                if path.path == "/api/status":
                    return self.reply(200, overview(worker))
                if path.path == "/api/report":
                    name = parse_qs(path.query).get("name", [""])[0]
                    if name not in REPORTS:
                        return self.reply(404, {"error": "Unknown report"})
                    return self.reply(200, (root/"docs"/name).read_bytes(), "text/plain; charset=utf-8")
                if path.path == "/api/log":
                    return self.reply(200, worker.journal.records("worker_health")[-20:])
                return self.reply(404, {"error": "Unknown page"})
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
                return self.reply(400, {"error": "Unable to read local data: "+type(exc).__name__})

        def do_POST(self):
            try:
                size=int(self.headers.get("Content-Length","0"))
                if not 0<size<=32768:
                    return self.reply(400,{"error":"invalid request size"})
                body=self.rfile.read(size)
            except (ValueError,OSError):
                return self.reply(400,{"error":"invalid request body"})
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if (not self.host_ok() or self.headers.get("Origin") != origin
                    or not secrets.compare_digest(self.headers.get("X-Local-Token", ""), token)):
                return self.reply(403, {"error": "Local session expired. Reload the page."})
            if self.path != "/api/action" or self.headers.get("Content-Type") != "application/json":
                return self.reply(400, {"error": "JSON action required"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 32768:
                    raise ValueError("invalid request size")
                data = read_json(body)
                action = data.get("action")
                if action in {"pause", "resume", "check_now"} and set(data) == {"action"}:
                    result = control(worker.journal, action)
                    worker.wake.set()
                elif action == "backup" and set(data) == {"action"}:
                    from .backups import create_backup
                    result = create_backup(worker.journal, root/"data/backups"/("manual-"+uuid4().hex), root)
                elif action == "paper" and set(data) == {"action", "route_id", "enabled"}:
                    result = configure(worker.journal, data["route_id"], data["enabled"],
                                       worker.watchlist, worker.policy, now())
                    worker.wake.set()
                elif action == "search" and set(data) in ({"action", "purpose"}, {"action", "purpose", "mode"}):
                    if worker.busy:
                        return self.reply(409, {"error": "A market check is running. Try again when it finishes."})
                    result = search(worker.journal, data["purpose"], worker.watchlist,
                                    worker.policy, worker.mandate, now(), data.get("mode", "paper"))
                    worker.journal.append("search_report", dict(result, at=now()))
                elif action == "preview_paper" and set(data) == {"action", "prediction_id"}:
                    result = preview_paper(worker.journal, data["prediction_id"], worker.mandate, worker.policy, now())
                elif action == "enter_paper" and set(data) == {"action", "entry"}:
                    if worker.busy:
                        return self.reply(409, {"error": "A market check is running. Try again when it finishes."})
                    result = enter_paper(worker.journal, data["entry"], worker.mandate, worker.policy, now())
                    worker.wake.set()
                elif action == "steam_wallet" and set(data) == {"action","record"}:
                    result={"wallet_id":steam_wallet.record(worker.journal,data["record"])}
                elif action == "correct_steam_wallet" and set(data) == {"action","correction"}:
                    result={"correction_id":steam_wallet.correct(worker.journal,data["correction"])}
                elif action == "funding" and set(data) == {"action", "funding"}:
                    result = {"funding_id": real_funds.record_funding(worker.journal, data["funding"])}
                elif action == "correct_funding" and set(data) == {"action", "correction"}:
                    result = {"correction_id": real_funds.correct_funding(worker.journal, data["correction"])}
                elif action == "preview_real" and set(data) == {"action", "prediction_id"}:
                    result = real_routes.preview(worker.journal, data["prediction_id"], worker.mandate, worker.policy, now())
                elif action == "enter_real" and set(data) == {"action", "entry"}:
                    result = real_routes.enter(worker.journal, data["entry"], worker.mandate, worker.policy, now())
                elif action == "real_step" and set(data) == {"action", "receipt"}:
                    result = {"step_id": real_routes.record_step(worker.journal, data["receipt"])}
                elif action=="recovery" and set(data)=={"action","recovery"}:
                    result=open_recovery(worker.journal,data["recovery"])
                elif action=="correction" and set(data)=={"action","correction"}:
                    cfg=settings(worker.journal,data["correction"].get("route_id"))
                    if cfg and cfg["enabled"]:
                        raise ValueError("Pause automatic paper steps before correcting an update.")
                    result={"correction_id":correct_event(worker.journal,data["correction"])}
                elif action == "event" and set(data) == {"action", "event"}:
                    if settings(worker.journal, data["event"].get("route_id")):
                        cfg = settings(worker.journal, data["event"]["route_id"])
                        if cfg["enabled"]:
                            raise ValueError("Pause this route's paper steps before recording manual events.")
                    result = {"event_id": add_event(worker.journal, data["event"])}
                else:
                    raise ValueError("unknown action or unexpected fields")
                return self.reply(200, result)
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
                return self.reply(400, {"error": str(exc)})

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise ValueError(f"Local port {port} is unavailable. Stop the other instance or set a different port in config/local.json.") from exc
    server.daemon_threads = True
    return server
