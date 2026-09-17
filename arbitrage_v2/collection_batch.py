"""Ordered read-only requests with shared limits and persistent source backoff.

The local worker owns the collection lock. A batch spans its paper checks and
research scan, so requesting a book twice cannot spend the allowance twice.
"""
from datetime import timedelta

from .collector import GAME_IDS, capture
from .evidence import stamp, utc
from .steam_public import capture_public


def request_for(watchlist, app_id, title, kind):
    provider = (watchlist.get("item_sources", {}).get(title, watchlist.get("steam_source", "steamapis"))
                if kind == "details" else "dmarket")
    request = {"provider": provider, "kind": kind, "app_id": app_id, "title": title}
    request_key(request)
    return request


def request_key(request):
    provider, kind, app_id, title = (request[k] for k in ("provider", "kind", "app_id", "title"))
    if (type(app_id) is not int or app_id not in GAME_IDS or not isinstance(title, str) or not title.strip()
            or (provider, kind) not in {("steam_public", "details"), ("steamapis", "details"),
                                        ("dmarket", "offers"), ("dmarket", "targets")}):
        raise ValueError("unsupported read-only collection request")
    return provider, kind, app_id, title


def fetch_request(journal, request, keys):
    if request["provider"] == "steam_public":
        return capture_public(journal, request["app_id"], request["title"])
    return capture(journal, request["provider"], request["kind"], request["app_id"], request["title"], keys)


class CollectionBatch:
    def __init__(self, journal, watchlist, config, keys, source_states, clock,
                 should_stop, fetch=fetch_request):
        self.journal, self.watchlist, self.config, self.keys = journal, watchlist, config, keys
        self.clock, self.should_stop, self.fetch = clock, should_stop, fetch
        self.sources = {p: dict(s) for p, s in source_states.items()}
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
        provider = request["provider"]
        self.failed.add(provider)
        retry = self.sources.get(provider, {}).get("retry_count", 0) + 1
        error, status = result.get("error"), result.get("status")
        transient = status not in {401, 403, 429} and (
            status in {500, 502, 503, 504} or (error or "").startswith("network_"))
        delays = self.config["retry_seconds"]
        retry_at = (stamp(utc(self.clock()) + timedelta(seconds=delays[retry-1]))
                    if transient and retry <= len(delays) else None)
        self.sources[provider] = dict(request, status=status, error=error or "unexpected_http_status",
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
            source = self.sources.get(provider)
            if provider in self.failed or (source and (not source.get("next_retry_at") or utc(self.clock()) < utc(source["next_retry_at"]))):
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
                self.sources.pop(provider, None)

    def completed(self, requests):
        successful = {request_key(r) for r in self.results if r.get("status") == 200 and not r.get("error")}
        return all(request_key(r) in successful for r in requests)
