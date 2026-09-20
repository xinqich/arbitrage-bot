"""One persistent local worker; the browser is only a remote control for it."""
from datetime import datetime, timedelta, timezone
import threading
from uuid import uuid4

from .collection_batch import CollectionBatch, fetch_request, request_for, request_key, saved_state, normalize_sources, server_failure
from .collection_transport import CollectionStopped
from .collection_settings import settings as collection_settings
from .evidence import stamp, utc
from .funds import available_capital
from .paper import required_evidence, settings, step
from .prediction import screen
from . import catalogue, search_rules
from .discovery import capture_index
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


def search(journal, purpose, watchlist, policy, mandate, at, mode="paper", max_age_seconds=14400, should_stop=None):
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
        return {"purpose": purpose, "status": "no_available_funds", "capital_cents": 0, "predictions": [], "mode": mode,
            "ranking_version":search_rules.VERSION,"search_settings":search_rules.current(journal),
            "catalogue_coverage":catalogue.coverage(journal,[]),
            "note": "Record real DMarket funds first." if mode=="confirmed" and not journal.records("real_funding") else "No uncommitted money is available in this funding pool."}
    scan_watch=dict(watchlist,items=list(watchlist["items"]))
    known={(r["app_id"],r["title"]) for r in scan_watch["items"]}
    scan_watch["items"].extend(r for r in watchlist.get("exploration_items",[]) if (r["app_id"],r["title"]) not in known)
    if watchlist.get('catalogue',{}).get('enabled'):
        index=capture_index(journal,at)
        # Pending catalogue names remain visible in coverage; only captured names
        # are priced. This keeps a large catalogue out of the quadratic calculator.
        known={r['title'] for r in scan_watch['items']}
        scan_watch['items'].extend({'app_id':730,'title':title} for title in index if title not in known)
    result = screen(journal, scan_watch, policy, at, capital_cents=capital, mode=mode, max_age_seconds=max_age_seconds, should_stop=should_stop)
    result['catalogue_coverage']=catalogue.coverage(journal,result['snapshots'],[r['title'] for r in scan_watch['items']])
    return dict(result, purpose=purpose, status="conditional_estimates", capital_cents=capital, mode=mode,
                predictions=result["predictions"], prediction_count=len(result["predictions"]),
                note="These alternatives share the same funds. Existing route funds are excluded.")


class Worker:
    def __init__(self, journal, watchlist, policy, mandate, config, keys, fetch=fetch_request, clock=now, wait=None):
        self.journal, self.watchlist, self.policy = journal, watchlist, policy
        self.mandate, self.config, self.keys = mandate, collection_settings(config), keys
        self.wait = wait
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
        # An overlapping tick is discarded, never queued for later catch-up.
        if not self.mutex.acquire(blocking=False):
            return
        state = controls(self.journal)
        health = latest(self.journal, 'worker_health') or {}
        try:
            return self._tick(at, state, health)
        except Exception as exc:
            self.error = 'worker_' + type(exc).__name__
            saved = {k: v for k, v in (latest(self.journal, 'worker_health') or health).items() if k != 'record_id'}
            saved.update(schema_version=3, at=self.clock(), last_attempt_at=at,
                         status='blocked', fatal=True, reason=self.error, next_check_at=None,
                         check_request=state['check_request'], resume_request=state['resume_request'])
            self.journal.append('worker_health', saved)
        finally:
            self.busy = False
            self.mutex.release()

    def _routes(self):
        active = []
        for route in self.journal.records('route'):
            state = _state(self.journal, route['route_id'])
            if state[-1]:
                continue
            if route['mode'] == 'confirmed':
                active.append((route['route_id'], dict(manual=True, record_id=route['record_id'],
                    changed_at=stamp(state[-2]))))
            else:
                cfg = settings(self.journal, route['route_id'])
                if cfg and cfg['enabled']:
                    active.append((route['route_id'], cfg))
        return active

    def _route_requests(self, route_id, cfg):
        at = self.clock()
        if not cfg.get('manual'):
            return self._requests(required_evidence(self.journal, route_id, at))
        _, pred, _, _, assets, stage, eligible, _, _ = _state(self.journal, route_id)
        if eligible and utc(at) < utc(eligible):
            return []
        if stage in {'entered', 'awaiting_transfer'}:
            pairs = [(pred['item_a'], 'offers')]
        elif stage in {'steam_locked', 'awaiting_steam_sale'}:
            pairs = [(pred['item_a'], 'details')]
        elif stage == 'steam_wallet':
            pairs = [(pred['item_b'], k) for k in ('details', 'targets', 'offers')]
        elif stage in {'return_item_locked', 'awaiting_dmarket_sale'}:
            pairs = [(title.removeprefix('730:'), k) for title, qty in assets.items()
                     if qty for k in ('targets', 'offers')]
        else:
            pairs = []
        return self._requests([dict(app_id=730, title=t, kind=k) for t, k in pairs])

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
        interval = self.config['collection_interval_seconds']
        if health.get('next_research_at'):
            due = health['next_research_at']
            previous_interval = health.get('collection_interval_seconds', 21600)
            if previous_interval != interval:
                # Keep the last regular anchor when settings change (also v1.0.3).
                due = stamp(utc(due) - timedelta(seconds=previous_interval) + timedelta(seconds=interval))
            return due
        if health.get('last_success_at'):
            return stamp(utc(health['last_success_at']) + timedelta(seconds=interval))
        if health.get('next_check_at'):
            return health['next_check_at']
        if not health:
            with self.journal.connect() as db:
                row = db.execute("SELECT MAX(json_extract(payload,'$.retrieved_at')) FROM records "
                    "WHERE category='capture' AND json_extract(payload,'$.error') IS NULL "
                    "AND json_extract(payload,'$.input_kind')='recorded'").fetchone()
            if row[0]:
                return stamp(utc(row[0]) + timedelta(seconds=interval))
        return at

    def _advance_schedule(self, due, at):
        interval = self.config['collection_interval_seconds']
        elapsed = (utc(at) - utc(due)).total_seconds()
        return stamp(utc(due) + timedelta(seconds=(int(elapsed // interval) + 1) * interval)) if elapsed >= 0 else due

    def _requests(self, requirements):
        return [request_for(self.watchlist, r["app_id"], r["title"], r["kind"]) for r in requirements]

    def _tick(self, at, state, health):
        if state["paused"] or self.stop.is_set():
            return
        requested = state["check_request"] != health.get("check_request")
        resumed = state["resume_request"] != health.get("resume_request")
        manual = requested or resumed
        if health.get("status") == "blocked" and health.get("fatal", True) and not manual and "allowance" not in str(health.get("reason", "")).lower():
            return
        research_at = self._initial_research_at(health, at)
        sources = normalize_sources(saved_state(self.journal).get("source_states", health.get("source_states", {})))
        if "source_states" not in health:
            # Preserve existing optional authentication/format stops on upgrade.
            for error in health.get("optional_source_errors", []):
                sources[error["provider"]] = dict(error, state="blocked", next_retry_at=None, retry_count=1)
        for source in sources.values():
            if source.get('retry_deadline') and utc(at) >= utc(source['retry_deadline']):
                source.update(state='blocked', next_retry_at=None)
            if source.get('status') == 429 and not source.get('next_retry_at'):
                source.update(state='cooldown', next_retry_at=stamp(utc(at) + timedelta(
                    seconds=self.config['rate_limit_cooldown_seconds'])))
        if manual:
            # An operator may retry an access/format block; never a pending
            # cooldown or server retry. Persisted request spacing also survives.
            sources = {k: v for k, v in sources.items() if v.get('next_retry_at')}
        checks = dict(health.get("route_checks", {}))
        next_at = self._next_due(research_at, sources, checks, health.get("last_attempt_at"), at)
        if utc(at) < utc(next_at) and not manual:
            if health.get("schema_version") != 3 or health.get("next_check_at") != next_at:
                saved = {k: v for k, v in health.items() if k != "record_id"}
                saved.update(schema_version=3, collection_interval_seconds=self.config["collection_interval_seconds"], at=at, status="waiting", fatal=False,
                    next_check_at=next_at, next_research_at=research_at, source_states=sources,
                    check_request=state["check_request"], resume_request=state["resume_request"])
                self.journal.append("worker_health", saved)
            return
        self.busy = True
        regular_due = utc(at) >= utc(research_at)
        research_due = manual or regular_due
        # A failed connection is not a permanent ban. Once the bounded retry
        # sequence ends, the next regular research check may try again.
        if regular_due:
            sources = {k: v for k, v in sources.items() if not (
                not v.get('next_retry_at') and (server_failure(v.get('status'))
                    or (v.get('error') or '').startswith('network_')))}
        retry_requests = [{k: s[k] for k in ("provider", "kind", "app_id", "title", "cursor", "titles") if k in s}
                          for s in sources.values() if s.get("next_retry_at") and utc(s["next_retry_at"]) <= utc(at)]
        batch = CollectionBatch(self.journal, self.watchlist, self.config, self.keys, sources,
                                self.clock, self.cancelled, self.fetch, wait=self.wait or self.stop.wait)
        research_at = self._advance_schedule(research_at, at)
        checkpoint = {k: v for k, v in health.items() if k != 'record_id'}
        checkpoint.update(schema_version=3, at=at, status='collecting', fatal=False,
            next_research_at=research_at, next_check_at=research_at,
            collection_interval_seconds=self.config['collection_interval_seconds'],
            check_request=state['check_request'], resume_request=state['resume_request'])
        self.journal.append('worker_health', checkpoint)
        routes = self._routes()
        results = []
        active_titles = set()

        def check_routes(due_only=False):
            for route_id, cfg in routes:
                eligible = _state(self.journal, route_id)[6]
                checked = checks.get(route_id, {}).get('at')
                if due_only and (not eligible or utc(eligible) > utc(self.clock())
                                 or (checked and utc(eligible) <= utc(checked))):
                    continue
                if due_only:
                    # A pre-unlock fetch cannot satisfy an unlock-time check.
                    for req in self._route_requests(route_id, cfg):
                        batch.seen.discard(request_key(req))
                for _ in range(4):
                    if batch.stopped():
                        break
                    requests = self._route_requests(route_id, cfg)
                    active_titles.update(r['title'] for r in requests)
                    batch.ensure(requests)
                    if batch.stopped():
                        break
                    checked_at = self.clock()
                    result = {'status': 'manual_actions_only'} if cfg.get('manual') else step(self.journal, route_id, checked_at)
                    if not cfg.get('manual'):
                        results.append(dict(result, route_id=route_id))
                    checks[route_id] = {'at': checked_at, 'settings_id': cfg['record_id']}
                    if result['status'] != 'advanced':
                        break

        check_routes()
        # The cheap time comparison avoids journal scans on every pacing wait.
        unlocks = []
        def refresh_unlocks():
            unlocks.clear()
            for route_id, _ in routes:
                eligible = _state(self.journal, route_id)[6]
                checked = checks.get(route_id, {}).get('at')
                if eligible and (not checked or utc(eligible) > utc(checked)):
                    unlocks.append(utc(eligible))
        refresh_unlocks()
        def check_unlocks():
            if unlocks and min(unlocks) <= utc(self.clock()):
                check_routes(due_only=True)
                refresh_unlocks()
        batch.before_request = check_unlocks
        batch.ensure(retry_requests)
        roster = list(self.watchlist["items"])
        rotation = health.get("rotation_index", 0)
        if research_due and self.watchlist.get('catalogue',{}).get('enabled'):
            # One catalogue page per cycle; pagination survives restarts.
            catalogue_state=catalogue.view(self.journal)
            catalogue_requests=[catalogue.request(catalogue_state['cursor'])]
            batch.ensure(catalogue_requests)
            catalogue_state=catalogue.view(self.journal)
            index=capture_index(self.journal,self.clock())
            mode='confirmed' if self.journal.records('real_funding') else 'paper'
            capital=available_capital(self.journal,self.mandate,mode=mode)
            slots=self.config['research_batch_size']
            seeds=self.watchlist['items']+self.watchlist.get('exploration_items',[])
            # Select books that would miss their refresh target before the
            # next regular run. Otherwise late-in-run captures skip every
            # second hourly scan merely because they are a few seconds young.
            refresh_age=max(0,self.config['research_refresh_seconds'] -
                (self.config['collection_interval_seconds'] if regular_due else 0))
            roster,count=catalogue.research_roster(self.journal,seeds,capital,search_rules.current(self.journal),slots,index,self.clock(),refresh_age,exclude=active_titles)
            research_requests=self._requests([dict(item,kind=kind) for item in roster for kind in ('details','offers','targets')])
            batch.ensure(research_requests)
            checked=dict(catalogue_state['progress'].get('checked',{}))
            attempted={r['title'] for r in batch.results}
            selected=[r for r in roster if r['title'] in attempted]
            for item in selected:
                checked[item['title']]=self.clock()
            self.journal.append('catalogue_progress',dict(at=self.clock(),
                selection_count=catalogue_state['progress']['selection_count']+len(selected),checked=checked))
            research_requests=catalogue_requests+research_requests
        elif research_due:
            exploration = self.watchlist.get("exploration_items", [])
            if exploration:
                roster.append(exploration[rotation % len(exploration)])
            roster = list({(r['app_id'], r['title']): r for r in roster if r['title'] not in active_titles}.values())[:self.config['research_batch_size']]
            research_requests = self._requests([dict(item, kind=kind) for item in roster
                                                for kind in ("details", "offers", "targets")])
            batch.ensure(research_requests)
        finished = self.clock()
        cancelled = self.cancelled()
        complete = research_due and not cancelled and not batch.exhausted and batch.completed(research_requests)
        if research_due and not cancelled and not batch.exhausted:
            try:
                report = search(self.journal, "grow", self.watchlist, self.policy, self.mandate, finished,
                            mode="confirmed" if self.journal.records("real_funding") else "paper",
                            max_age_seconds=self.config["freshness_seconds"], should_stop=batch.calculation_stopped)
            except CollectionStopped:
                complete = False
            else:
                self.journal.append("search_report", dict(report, at=finished))
            rotation += 1
        finished = self.clock()
        cancelled = self.cancelled()
        complete = complete and not cancelled and not batch.exhausted
        research_at = self._advance_schedule(research_at, finished)
        next_at = self._next_due(research_at, batch.sources, checks, health.get("last_attempt_at"), finished)
        status = "paused" if cancelled else "retry_wait" if any(s.get("next_retry_at") for s in batch.sources.values()) else "waiting"
        reason = None
        if batch.exhausted and not cancelled:
            reason = "Run time limit reached; unfinished research remains eligible."
        self.journal.append("worker_health", {"schema_version": 3, "collection_interval_seconds": self.config["collection_interval_seconds"], "at": finished, "status": status,
            "fatal": False, "time_limit_reached": batch.exhausted, "reason": reason, "last_attempt_at": at,
            "last_success_at": finished if complete else health.get("last_success_at"),
            "next_check_at": next_at, "next_research_at": research_at,
            "collection_kind": "research" if research_due else "source_retry" if retry_requests else "route_check",
            "request_count": batch.count, "research_items_selected": len(roster) if research_due else 0, "deferred_requests": batch.deferred,
            "source_states": batch.sources, "paper_steps": results, "route_checks": checks,
            "rotation_index": rotation, "check_request": state["check_request"], "resume_request": state["resume_request"]})
        self.error = None
