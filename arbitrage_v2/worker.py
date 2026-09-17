"""One persistent local worker; the browser is only a remote control for it."""
from datetime import datetime, timedelta, timezone
import threading
from uuid import uuid4

from .collection_batch import CollectionBatch, fetch_request, request_for
from .evidence import stamp, utc
from .funds import available_capital
from .paper import required_evidence, settings, step
from .prediction import screen
from .routes import route_status, _state


def now():
    return stamp(datetime.now(timezone.utc))


def latest(journal, category):
    import json
    with journal.connect() as db:
        row = db.execute("SELECT id,payload FROM records WHERE category=? ORDER BY seq DESC LIMIT 1",
                         (category,)).fetchone()
    return dict(json.loads(row[1]), record_id=row[0]) if row else None


def control(journal, action):
    if action not in {"pause", "resume", "check_now"}:
        raise ValueError("unknown worker action")
    record = {"schema_version": 1, "action": action, "at": now()}
    journal.append("worker_control", record, "control:"+str(uuid4()))
    return record


def controls(journal):
    paused, check, resume = False, None, None
    for row in journal.records("worker_control"):
        if row["action"] in {"pause", "resume"}:
            paused = row["action"] == "pause"
        if row["action"] == "resume":
            resume = row["record_id"]
        if row["action"] == "check_now":
            check = row["record_id"]
    return {"paused": paused, "check_request": check, "resume_request": resume}


def search(journal, purpose, watchlist, policy, mandate, at, mode="paper"):
    if mode not in {"paper", "confirmed"}:
        raise ValueError("Choose paper or real funds.")
    if purpose not in {"start", "grow", "withdraw"}:
        raise ValueError("choose start, grow or withdraw")
    if purpose != "grow":
        return {"purpose": purpose, "status": "deferred", "predictions": [],
            "note": ("You are handling the start route manually. CSFloat integration is postponed."
                     if purpose == "start" else "CSFloat withdrawal work is postponed until you need it."),
            "economic_evidence": "not_established"}
    capital = available_capital(journal, mandate, mode=mode)
    if not capital:
        return {"purpose": purpose, "status": "no_available_funds", "capital_cents": 0, "predictions": [], "mode": mode, "note": "Record real DMarket funds first." if mode=="confirmed" and not journal.records("real_funding") else "No uncommitted money is available in this funding pool."}
    scan_watch=dict(watchlist,items=list(watchlist["items"]))
    known={(r["app_id"],r["title"]) for r in scan_watch["items"]}
    scan_watch["items"].extend(r for r in watchlist.get("exploration_items",[]) if (r["app_id"],r["title"]) not in known)
    result = screen(journal, scan_watch, policy, at, capital_cents=capital, mode=mode)
    return dict(result, purpose=purpose, status="conditional_estimates", capital_cents=capital, mode=mode,
                predictions=result["predictions"], prediction_count=len(result["predictions"]),
                note="These alternatives share the same funds. Existing route funds are excluded.")


class Worker:
    def __init__(self, journal, watchlist, policy, mandate, config, keys, fetch=fetch_request, clock=now):
        self.journal, self.watchlist, self.policy = journal, watchlist, policy
        self.mandate, self.config, self.keys = mandate, config, keys
        self.fetch, self.clock = fetch, clock
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.busy = False
        self.thread = None
        self.error = None
        self.mutex = threading.Lock()

    def start(self):
        self.thread = threading.Thread(target=self.run, name="market-worker", daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            self.tick(self.clock())
            self.wake.wait(2)
            self.wake.clear()

    def cancelled(self):
        return self.stop.is_set() or controls(self.journal)["paused"]

    def tick(self, at):
        with self.mutex:
            state = controls(self.journal)
            health = latest(self.journal, "worker_health") or {}
            try:
                return self._tick(at, state, health)
            except Exception as exc:
                # Save the command IDs from the start of this attempt. Otherwise a
                # previously consumed Resume would look new on every two-second tick.
                self.error = "worker_" + type(exc).__name__
                saved = {k: v for k, v in health.items() if k != "record_id"}
                saved.update(schema_version=2, at=self.clock(), last_attempt_at=at,
                             status="blocked", fatal=True, reason=self.error, next_check_at=None,
                             check_request=state["check_request"], resume_request=state["resume_request"])
                self.journal.append("worker_health", saved)
            finally:
                self.busy = False

    def _routes(self):
        active = []
        for route in self.journal.records("route"):
            cfg = settings(self.journal, route["route_id"])
            if cfg and cfg["enabled"] and not _state(self.journal, route["route_id"])[-1]:
                active.append((route["route_id"], cfg))
        return active

    def _next_due(self, research_at, sources, checks, fallback, at):
        due = [research_at]
        due.extend(s["next_retry_at"] for s in sources.values() if s.get("next_retry_at"))
        for route_id, cfg in self._routes():
            checked = checks.get(route_id, {}).get("at", fallback)
            eligible = _state(self.journal, route_id)[6]
            if eligible and (not checked or utc(eligible) > utc(checked)):
                due.append(eligible)
            # Enabling a paper route wakes it without waiting for the research scan.
            if (not checked or utc(cfg["changed_at"]) > utc(checked)):
                due.append(eligible if eligible and utc(eligible) > utc(at) else at)
        return min(due, key=utc)

    def _initial_research_at(self, health, at):
        if health.get("next_research_at"):
            return health["next_research_at"]
        if health.get("last_success_at"):
            return stamp(utc(health["last_success_at"]) + timedelta(seconds=self.config["collection_interval_seconds"]))
        if health.get("next_check_at"):
            return health["next_check_at"]
        if not health:
            with self.journal.connect() as db:
                row = db.execute("SELECT MAX(json_extract(payload,'$.retrieved_at')) FROM records "
                    "WHERE category='capture' AND json_extract(payload,'$.error') IS NULL "
                    "AND json_extract(payload,'$.input_kind')='recorded'").fetchone()
            if row[0]:
                return stamp(utc(row[0]) + timedelta(seconds=self.config["collection_interval_seconds"]))
        return at

    def _requests(self, requirements):
        return [request_for(self.watchlist, r["app_id"], r["title"], r["kind"]) for r in requirements]

    def _tick(self, at, state, health):
        if state["paused"] or self.stop.is_set():
            return
        requested = state["check_request"] != health.get("check_request")
        resumed = state["resume_request"] != health.get("resume_request")
        manual = requested or resumed
        if health.get("status") == "blocked" and health.get("fatal", True) and not manual:
            return
        research_at = self._initial_research_at(health, at)
        sources = dict(health.get("source_states", {}))
        if "source_states" not in health:
            # Preserve existing optional authentication/format stops on upgrade.
            for error in health.get("optional_source_errors", []):
                sources[error["provider"]] = dict(error, state="blocked", next_retry_at=None, retry_count=1)
        if manual:
            sources = {}
        checks = dict(health.get("route_checks", {}))
        next_at = self._next_due(research_at, sources, checks, health.get("last_attempt_at"), at)
        if utc(at) < utc(next_at) and not manual:
            if health.get("schema_version") != 2:
                saved = {k: v for k, v in health.items() if k != "record_id"}
                saved.update(schema_version=2, at=at, status="waiting", fatal=False,
                    next_check_at=next_at, next_research_at=research_at, source_states=sources,
                    check_request=state["check_request"], resume_request=state["resume_request"])
                self.journal.append("worker_health", saved)
            return
        self.busy = True
        research_due = manual or utc(at) >= utc(research_at)
        retry_requests = [{k: s[k] for k in ("provider", "kind", "app_id", "title")}
                          for s in sources.values() if s.get("next_retry_at") and utc(s["next_retry_at"]) <= utc(at)]
        batch = CollectionBatch(self.journal, self.watchlist, self.config, self.keys, sources,
                                self.clock, self.cancelled, self.fetch)
        routes = self._routes()
        # All open routes' current steps go ahead of the broader research roster.
        for route_id, _ in routes:
            batch.ensure(self._requests(required_evidence(self.journal, route_id, self.clock())))
        results = []
        for route_id, cfg in routes:
            for _ in range(4):
                if self.cancelled():
                    break
                batch.ensure(self._requests(required_evidence(self.journal, route_id, self.clock())))
                if self.cancelled():
                    break
                checked_at = self.clock()
                result = step(self.journal, route_id, checked_at)
                results.append(dict(result, route_id=route_id))
                checks[route_id] = {"at": checked_at, "settings_id": cfg["record_id"]}
                if result["status"] != "advanced":
                    break
        batch.ensure(retry_requests)
        roster = list(self.watchlist["items"])
        rotation = health.get("rotation_index", 0)
        if research_due:
            exploration = self.watchlist.get("exploration_items", [])
            if exploration:
                roster.append(exploration[rotation % len(exploration)])
            research_requests = self._requests([dict(item, kind=kind) for item in roster
                                                for kind in ("details", "offers", "targets")])
            batch.ensure(research_requests)
        finished = self.clock()
        cancelled = self.cancelled()
        complete = research_due and not cancelled and batch.completed(research_requests)
        if research_due and not cancelled:
            report = search(self.journal, "grow", self.watchlist, self.policy, self.mandate, finished,
                            mode="confirmed" if self.journal.records("real_funding") else "paper")
            self.journal.append("search_report", dict(report, at=finished))
            research_at = stamp(utc(finished) + timedelta(seconds=self.config["collection_interval_seconds"]))
            rotation += 1
        next_at = self._next_due(research_at, batch.sources, checks, health.get("last_attempt_at"), finished)
        status = "paused" if cancelled else "retry_wait" if any(s.get("next_retry_at") for s in batch.sources.values()) else "waiting"
        reason = None
        if batch.exhausted and not cancelled:
            status, reason, next_at = "blocked", "Request allowance exhausted; review settings before resuming.", None
        self.journal.append("worker_health", {"schema_version": 2, "at": finished, "status": status,
            "fatal": batch.exhausted, "reason": reason, "last_attempt_at": at,
            "last_success_at": finished if complete else health.get("last_success_at"),
            "next_check_at": next_at, "next_research_at": research_at,
            "collection_kind": "research" if research_due else "source_retry" if retry_requests else "route_check",
            "request_count": batch.count, "deferred_requests": batch.deferred,
            "source_states": batch.sources, "paper_steps": results, "route_checks": checks,
            "rotation_index": rotation, "check_request": state["check_request"], "resume_request": state["resume_request"]})
        self.error = None
