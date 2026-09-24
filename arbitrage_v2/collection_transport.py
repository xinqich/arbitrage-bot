"""Shared request hooks, including Steam redirects. No credentials are recorded."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from uuid import uuid4

from .evidence import stamp, utc

_context = ContextVar('collection_request_context', default=None)


class CollectionStopped(Exception):
    """Pause or run deadline: not a provider failure."""


class SourceDeferred(Exception):
    """An unlock check blocked this source while another request was waiting."""


class RequestSatisfied(Exception):
    """A priority unlock check already fetched the pending research request."""


@contextmanager
def request_context(begin, remaining, complete=None, cache=None):
    token = _context.set({'begin': begin, 'remaining': remaining, 'complete': complete, 'cache': cache})
    try:
        yield
    finally:
        _context.reset(token)


def current_cache():
    """The run-scoped object CollectionBatch owns for grouped-page reuse (Stage 3),
    or None when capture_public is called outside a batch -- e.g. directly, as the
    unit tests do -- in which case there is no caching at all."""
    ctx = _context.get()
    return ctx.get('cache') if ctx else None


def prepare_request(journal, request, identifier=None):
    ctx = _context.get()
    if ctx:
        ctx['begin'](request)
        ctx['journal'], ctx['request'] = journal, request
    at = stamp(datetime.now(timezone.utc))
    journal.append('request_attempt', dict(request, started_at=at), 'attempt:' + (identifier or uuid4().hex))
    return min(20, max(0.001, ctx['remaining']())) if ctx else 20


def prepare_redirect(original_request):
    ctx = _context.get()
    if ctx:
        if ctx['complete']:
            ctx['complete'](ctx['request'])
        timeout = prepare_request(ctx['journal'], ctx['request'])
        # urllib reuses the original request's timeout when following a redirect.
        original_request.timeout = min(original_request.timeout, timeout)


def read_response(response, limit):
    """Bound streaming reads by the remaining run time, not by each chunk alone."""
    ctx = _context.get()
    chunks, size = [], 0
    while size < limit:
        if ctx:
            remaining = ctx['remaining']()
            if remaining <= 0:
                raise CollectionStopped()
            sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
            if sock is not None:
                sock.settimeout(min(20, remaining))
        chunk = (getattr(response, 'read1', None) or response.read)(min(65536, limit-size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b''.join(chunks)


def retry_after(headers):
    """Retain only a valid numeric delay or UTC date, never arbitrary header text."""
    value = (headers.get('Retry-After') if headers else None) or ''
    if len(value) > 128:
        return None
    if value.strip().isdigit():
        return int(value.strip())
    try:
        at = parsedate_to_datetime(value)
        return stamp(at) if at.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def cooldown_seconds(value, at, fallback):
    if type(value) is int and value >= 0:
        # datetime cannot represent arbitrarily large delays.
        return min(value, 10 * 365 * 86400)
    if isinstance(value, str):
        try:
            return max(0, (utc(value) - utc(at)).total_seconds())
        except (ValueError, TypeError):
            pass
    return fallback
