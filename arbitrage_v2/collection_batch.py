"""Ordered read-only requests with shared limits and persistent source backoff.

The local worker owns the collection lock. A batch spans its paper checks and
research scan, so requesting a book twice cannot spend the allowance twice.
"""
from datetime import timedelta
from hashlib import sha256

from .collector import GAME_IDS, capture
from . import catalogue
from .evidence import stamp, utc
from .steam_public import capture_public


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
    if request["kind"] == "catalogue":
        return catalogue.fetch(journal,request,keys)
    if request["provider"] == "steam_public":
        return capture_public(journal, request["app_id"], request["title"])
    return capture(journal, request["provider"], request["kind"], request["app_id"], request["title"], keys)


def failure_scope(request, result):
    """Keep connection/quota failures broad, but isolate unsupported item data."""
    error, status = result.get('error') or '', result.get('status')
    if (error in {'missing_STEAMAPIS_KEY', 'missing_DMarket_credentials',
                  'invalid_source_configuration', 'provider_allowance_exhausted'}
            or status in {401, 429, 500, 502, 503, 504} or error.startswith('network_')):
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
        if all(k in source for k in ('provider', 'kind', 'app_id', 'title')):
            scope = failure_scope(source, source)
            source['scope'] = scope
            key = scope_key(source, scope)
        else:
            key = old_key
        result[key] = source
    return result


class CollectionBatch:
    def __init__(self, journal, watchlist, config, keys, source_states, clock,
                 should_stop, fetch=fetch_request):
        self.journal, self.watchlist, self.config, self.keys = journal, watchlist, config, keys
        self.clock, self.should_stop, self.fetch = clock, should_stop, fetch
        self.sources = normalize_sources(source_states)
        self.results, self.deferred, self.seen = [], [], set()
        self.failed = set()
        self.count, self.steam_count = 0, 0
        self.allowances = {"total": watchlist.get("total_request_allowance", 1000),
                           "steam_public": watchlist.get("steam_public_request_allowance", 1000),
                           "steamapis": watchlist.get("steam_request_allowance", 100)}
        if any(type(v) is not int or v < 0 for v in self.allowances.values()):
            raise ValueError("request allowances must be nonnegative integers")
        if (type(config["request_budget"]) is not int or not 1 <= config["request_budget"] <= 100
                or type(config["steam_budget"]) is not int or not 0 <= config["steam_budget"] <= config["request_budget"]):
            raise ValueError("invalid collection budget")
        attempts = journal.records("request_attempt")
        self.prior_total = len(attempts)
        self.provider_counts = {p: sum(r["provider"] == p for r in attempts)
                                for p in ("steam_public", "steamapis")}

    @property
    def exhausted(self):
        return self.prior_total + self.count >= self.allowances["total"]

    def _failure(self, request, result):
        scope = failure_scope(request, result)
        source_key = scope_key(request, scope)
        self.failed.add(source_key)
        retry = self.sources.get(source_key, {}).get("retry_count", 0) + 1
        error, status = result.get("error"), result.get("status")
        transient = status not in {401, 403, 429} and (
            status in {500, 502, 503, 504} or (error or "").startswith("network_"))
        delays = self.config["retry_seconds"]
        retry_at = (stamp(utc(self.clock()) + timedelta(seconds=delays[retry-1]))
                    if transient and retry <= len(delays) else None)
        self.sources[source_key] = dict(request, scope=scope, status=status, error=error or "unexpected_http_status",
            state="retry_wait" if retry_at else "blocked", retry_count=retry, next_retry_at=retry_at)

    def ensure(self, requests):
        # Validate the complete plan before its first network request.
        for request in requests:
            request_key(request)
        for request in requests:
            key = request_key(request)
            if key in self.seen:
                continue
            if self.should_stop():
                return
            self.seen.add(key)
            provider = request["provider"]
            applicable = [scope_key(request, scope) for scope in ('provider', 'endpoint', 'item')]
            blocked = next((k for k in applicable if k in self.failed or
                (k in self.sources and (not self.sources[k].get('next_retry_at')
                 or utc(self.clock()) < utc(self.sources[k]['next_retry_at'])))), None)
            if blocked is not None:
                self.deferred.append(dict(request, reason="source_waiting"))
                continue
            if self.exhausted or self.count >= self.config["request_budget"]:
                self.deferred.append(dict(request, reason="total_allowance" if self.exhausted else "cycle_budget"))
                continue
            if provider in self.provider_counts:
                if self.provider_counts[provider] >= self.allowances[provider]:
                    self._failure(request, {"error": "provider_allowance_exhausted"})
                    self.deferred.append(dict(request, reason="provider_allowance"))
                    continue
                if self.steam_count >= self.config["steam_budget"]:
                    self.deferred.append(dict(request, reason="steam_cycle_budget"))
                    continue
            try:
                result = self.fetch(self.journal, request, self.keys)
            except ValueError as exc:
                # Only allowlisted preflight messages; never persist exception text/URLs.
                known = {"missing STEAMAPIS_KEY": "missing_STEAMAPIS_KEY",
                         "missing DMarket credentials": "missing_DMarket_credentials"}
                self._failure(request, {"error": known.get(str(exc), "invalid_source_configuration")})
                continue
            self.count += 1
            if provider in self.provider_counts:
                self.steam_count += 1
                self.provider_counts[provider] += 1
            result = dict(result, **request)
            self.results.append(result)
            if result.get("error") or result.get("status") != 200:
                self._failure(request, result)
            else:
                for key in applicable:
                    self.sources.pop(key, None)

    def completed(self, requests):
        successful = {request_key(r) for r in self.results if r.get("status") == 200 and not r.get("error")}
        return all(request_key(r) in successful for r in requests)
