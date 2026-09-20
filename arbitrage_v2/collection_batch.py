"""Ordered read-only requests with pacing, a deadline and persistent source backoff.

The local worker owns the collection lock. A batch spans its paper checks and
research scan, so requesting a book twice does not duplicate a shared request.
"""
from datetime import timedelta
import json
import time

from .collection_settings import settings
from .collection_transport import CollectionStopped, SourceDeferred, RequestSatisfied, request_context, cooldown_seconds
from hashlib import sha256

from .collector import GAME_IDS
from . import catalogue
from .evidence import stamp, utc


def request_for(watchlist, app_id, title, kind):
    provider = (watchlist.get("item_sources", {}).get(title, watchlist.get("steam_source", "steamapis"))
                if kind == "details" else "dmarket")
    if (kind == 'details' and watchlist.get('catalogue',{}).get('enabled')
            and title not in {r['title'] for r in watchlist['items']}
            and title not in watchlist.get('item_sources',{})):
        provider='steamapis'
    request = {"provider": provider, "kind": kind, "app_id": app_id, "title": title}
    request_key(request)
    return request


def request_key(request):
    provider, kind, app_id, title = (request[k] for k in ("provider", "kind", "app_id", "title"))
    if (type(app_id) is not int or app_id not in GAME_IDS or not isinstance(title, str) or not title.strip()
            or (provider, kind) not in {("steam_public", "details"), ("steamapis", "details"),
                                        ("dmarket", "offers"), ("dmarket", "targets"), ("dmarket", "catalogue")}):
        raise ValueError("unsupported read-only collection request")
    if kind == 'catalogue':
        return provider, kind, app_id, title, request.get('cursor',''), tuple(request.get('titles') or [])
    return provider, kind, app_id, title


def fetch_request(journal, request, keys):
    from .collector import capture
    from .steam_public import capture_public
    if request["kind"] == "catalogue":
        return catalogue.fetch(journal,request,keys)
    if request["provider"] == "steam_public":
        return capture_public(journal, request["app_id"], request["title"])
    return capture(journal, request["provider"], request["kind"], request["app_id"], request["title"], keys)


def server_failure(status):
    return type(status) is int and 500 <= status <= 599


def failure_scope(request, result):
    """Keep connection/quota failures broad, but isolate unsupported item data."""
    error, status = result.get('error') or '', result.get('status')
    if (error in {'missing_STEAMAPIS_KEY', 'missing_DMarket_credentials',
                  'invalid_source_configuration', 'provider_allowance_exhausted'}
            or status in {401, 429} or server_failure(status) or error.startswith('network_')):
        return 'provider'
    if request.get('kind') == 'catalogue' or status == 403:
        return 'endpoint'
    if (status in {400, 404, 422} or error in {'invalid_json_or_size',
            'invalid_or_unsupported_steam_page'}):
        return 'item'
    return 'provider'


def scope_key(request, scope):
    key = request['provider']
    if scope in {'endpoint', 'item'}:
        key += ':' + request['kind']
    if scope == 'item':
        key += ':' + str(request['app_id']) + ':' + sha256(request['title'].encode()).hexdigest()[:24]
    return key


def normalize_sources(sources):
    """Narrow known legacy format stops without discarding existing retry state."""
    result = {}
    for old_key, source in sources.items():
        source = dict(source)
        if source.get("error") == "provider_allowance_exhausted":
            continue
        if all(k in source for k in ('provider', 'kind', 'app_id', 'title')):
            scope = failure_scope(source, source)
            source['scope'] = scope
            key = scope_key(source, scope)
        else:
            key = old_key
        result[key] = source
    return result


def saved_state(journal):
    with journal.connect() as db:
        row = db.execute("SELECT payload FROM records WHERE category='collection_state' ORDER BY seq DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else {}


class CollectionBatch:
    def __init__(self, journal, watchlist, config, keys, source_states, clock,
                 should_stop, fetch=fetch_request, wait=None, monotonic=time.monotonic):
        self.journal, self.watchlist, self.keys = journal, watchlist, keys
        self.config = settings(config)
        self.clock, self.should_stop, self.fetch = clock, should_stop, fetch
        self.wait, self.monotonic = wait or time.sleep, monotonic
        self.deadline = utc(clock()) + timedelta(seconds=self.config['max_run_seconds'])
        self.monotonic_deadline = monotonic() + self.config['max_run_seconds']
        persisted = saved_state(journal)
        self.sources = normalize_sources(source_states)
        self.last_requests = dict(persisted.get('last_requests', {}))
        if not persisted:
            with journal.connect() as db:
                rows = db.execute("SELECT json_extract(payload,'$.provider'),MAX(json_extract(payload,'$.started_at')) "
                    "FROM records WHERE category='request_attempt' GROUP BY json_extract(payload,'$.provider')").fetchall()
            self.last_requests.update({p: at for p, at in rows if p and at})
        self.results, self.deferred, self.seen = [], [], set()
        self.failed = set()
        self.count, self.steam_count = 0, 0
        self.before_request = None
        self._inside_hook = False
        self.steamapis_verified = False
        self._cancel_check_at = 0
        self._calculation_cancelled = False

    def calculation_stopped(self):
        if self.monotonic() >= self._cancel_check_at:
            self._calculation_cancelled = self.should_stop()
            self._cancel_check_at = self.monotonic() + 0.1
        return self._calculation_cancelled or self.exhausted

    def remaining(self):
        return max(0, min((self.deadline - utc(self.clock())).total_seconds(),
                          self.monotonic_deadline - self.monotonic()))

    @property
    def exhausted(self):
        return self.remaining() <= 0

    def stopped(self):
        return self.should_stop() or self.exhausted

    def _save(self):
        self.journal.append('collection_state', dict(schema_version=1, at=self.clock(),
            source_states=self.sources, last_requests=self.last_requests))

    def finish_request(self, request):
        # Waiting from completion is conservative and includes socket/parse time.
        # It avoids shaving milliseconds off spacing through preflight overhead.
        self.last_requests[request['provider']] = self.clock()
        self._save()

    def begin_request(self, request):
        provider = request['provider']
        while True:
            if self.stopped():
                raise CollectionStopped()
            if self.before_request and not self._inside_hook:
                self._inside_hook = True
                before = len(self.results)
                try:
                    self.before_request()
                finally:
                    self._inside_hook = False
                if self.stopped():
                    raise CollectionStopped()
                if request['kind'] != 'account' and any(request_key(r) == request_key(request)
                        and r.get('status') == 200 and not r.get('error') for r in self.results[before:]):
                    raise RequestSatisfied()
            # The unlock hook can encounter a 429 while this request waits.
            if any(scope_key(request, scope) in self.failed for scope in ('provider', 'endpoint', 'item')):
                raise SourceDeferred()
            previous = self.last_requests.get(provider)
            delay = max(0, self.config['request_spacing_seconds'].get(provider, 0) -
                        (utc(self.clock()) - utc(previous)).total_seconds()) if previous else 0
            if delay <= 0:
                break
            # Pause/stop is checked at least four times per second during waits.
            self.wait(min(delay, self.remaining(), 0.25))
        self.last_requests[provider] = self.clock()
        self.count += 1
        self.steam_count += provider in {'steam_public', 'steamapis'}
        # Save before the HTTP request: restart cannot skip the spacing period.
        self._save()

    def _failure(self, request, result):
        scope = failure_scope(request, result)
        source_key = scope_key(request, scope)
        self.failed.add(source_key)
        retry = self.sources.get(source_key, {}).get('retry_count', 0) + 1
        error, status = result.get('error'), result.get('status')
        transient = server_failure(status) or (error or '').startswith('network_')
        delays = self.config['retry_seconds']
        delay = delays[retry-1] if transient and retry <= len(delays) else None
        if status == 429:
            delay = cooldown_seconds(result.get('retry_after'), self.clock(),
                                     self.config['rate_limit_cooldown_seconds'])
        retry_at = stamp(utc(self.clock()) + timedelta(seconds=delay)) if delay is not None else None
        retry_deadline = self.sources.get(source_key, {}).get('retry_deadline') or stamp(self.deadline)
        if transient and retry_at and utc(retry_at) >= utc(retry_deadline):
            retry_at = None
        self.sources[source_key] = dict(request, scope=scope, status=status,
            error=error or 'unexpected_http_status',
            state='cooldown' if status == 429 else 'retry_wait' if retry_at else 'blocked',
            retry_count=retry, next_retry_at=retry_at, retry_deadline=retry_deadline if transient else None)
        self._save()

    def ensure(self, requests):
        for request in requests:
            request_key(request)
        for request in requests:
            key = request_key(request)
            if key in self.seen:
                continue
            if self.stopped():
                return
            self.seen.add(key)
            applicable = [scope_key(request, scope) for scope in ('provider', 'endpoint', 'item')]
            blocked = next((k for k in applicable if k in self.failed or
                (k in self.sources and (not self.sources[k].get('next_retry_at')
                 or utc(self.clock()) < utc(self.sources[k]['next_retry_at'])))), None)
            if blocked is not None:
                self.deferred.append(dict(request, reason='source_waiting'))
                continue
            try:
                with request_context(self.begin_request, self.remaining, self.finish_request):
                    # Real transports call the gate for each HTTP request, including
                    # redirects. Injected fixture transports have no HTTP hook.
                    if self.fetch is not fetch_request:
                        self.begin_request(request)
                    elif request['provider'] == 'steamapis' and not self.steamapis_verified:
                        from .collector import free_access
                        access = free_access(self.journal, self.keys)
                        self.finish_request(request)
                        if not access.get('no_overage'):
                            self._failure(request, access)
                            continue
                        self.steamapis_verified = True
                    result = self.fetch(self.journal, request, self.keys)
                    self.finish_request(request)
            except CollectionStopped:
                self.seen.discard(key)
                return
            except SourceDeferred:
                self.deferred.append(dict(request, reason='source_waiting'))
                continue
            except RequestSatisfied:
                continue
            except ValueError as exc:
                known = {'missing STEAMAPIS_KEY': 'missing_STEAMAPIS_KEY',
                         'missing DMarket credentials': 'missing_DMarket_credentials'}
                self._failure(request, {'error': known.get(str(exc), 'invalid_source_configuration')})
                continue
            result = dict(result, **request)
            self.results.append(result)
            if result.get('error') or result.get('status') != 200:
                self._failure(request, result)
            else:
                for source_key in applicable:
                    self.sources.pop(source_key, None)
                self._save()

    def completed(self, requests):
        successful = {request_key(r) for r in self.results if r.get('status') == 200 and not r.get('error')}
        return all(request_key(r) in successful for r in requests)
