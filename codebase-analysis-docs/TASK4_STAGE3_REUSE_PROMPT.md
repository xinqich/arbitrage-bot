# Implementation prompt — Task 4, Stage 3: run-local grouped-page reuse

> Paste the section below into a fresh Claude Code session in `arbitrage-bot`.
> Everything the session needs is stated here; it starts cold.

---

## Context

You are working on **arbitrage-bot** (package `arbitrage_v2`), a local-only, read-only CS2 market
research and bookkeeping tool. It never places orders; every real trade is manual. State is
event-sourced into one append-only SQLite table (`data/research.sqlite3`) protected by
`RAISE(ABORT)` triggers.

The governing task is `codebase-analysis-docs/TASK4_IMPLEMENTATION_PROMPT.md` — **broader direct
Steam collection**, i.e. teaching `steam_public.py` to read *grouped* (skin) market pages in
addition to *commodity* (case, sticker, capsule) pages. Read its Context, Ground rules, Goal,
Stage 3, D3 and "Traps worth restating" sections before writing code.

Task 4's stage status, as of commit `cd84b88` (v1.0.5.2):

| Stage | Status |
| --- | --- |
| 0 — live discovery of the grouped mechanism | complete |
| 0.5 — `/market/orderbook` equivalence check | complete, passed |
| 1 — parser extension in `steam_public.py` | complete |
| 2 — failure scoping (`2A`, `2B`) | complete |
| 5a — grouped-parser fixtures and tests | complete (310 tests green) |
| **3 — run-local grouped-page reuse** | **this prompt** |
| 4 — provider routing | not started |
| D1 — block paper steps on provider switch | not started |

## Why this matters

Per D3 in the governing prompt, this is **the single highest-value part of Task 4**, not an
optimisation. A grouped page is ~743 KB encoded / ~4.18 MB decoded and carries identity and price
history for *every* bucket in the family — 15 for `AK-47 | Slate`. Without reuse, collecting 15
variants re-downloads that page 15 times. With reuse it is one page plus small follow-up JSON calls.

## Scope

**In scope**

- Run-local caching of decoded grouped pages, so one fetch can serve every bucket in its family.
- Whatever refactor of `page_fields` makes per-title re-validation structural rather than
  disciplinary (see "Recommended shape").
- The two Stage 5 bullets that belong to this stage: *a group page serving a second title only
  after independent validation*, and *timestamp preservation across reuse*.

**Out of scope — do not start these**

- Stage 4 (`request_for` provider routing, SteamApis fallback).
- D1 (blocking `paper.step` on a provider switch).
- Any collection run against `data/research.sqlite3`.
- Persisting the cache anywhere. It is run-local and in-memory, full stop.

## Ground rules

- **Code style.** Write new code in a clear, conventional style. The existing code is extremely
  dense (no spaces around operators, multiple statements per line, imports inside functions). Do
  **not** imitate it and do **not** restyle it. Change existing code only when genuinely necessary,
  and state the reason in one or two sentences before doing so.
- **Testing.** `& .\.venv\Scripts\python.exe -B -m unittest discover -s tests -q` —
  **310 tests, ~40s, currently green.** Keep it green. Every test builds its own journal in a
  `TemporaryDirectory`; none touches the production journal.
- **Line endings.** The repository has a `.gitattributes`. Check that
  `git diff --ignore-cr-at-eol --numstat` matches `git diff --numstat` before finishing.
- **No release ritual.** No version bump, no `RELEASE_*_CHECKS.md`, no backup ceremony. Tests are
  the gate.
- **A production journal with real records exists** at `data/research.sqlite3`. Do not run
  `arbitrage-v2 web`, `collect`, `paper-step`, `route-event`, or `csfloat-collect` against it. The
  local desk may be running and holding the OS collection lock. Nothing in this stage needs network
  access or a real journal.
- **Never weaken evidence requirements to make reuse work.** A derived capture must clear exactly
  the same validation a freshly fetched one does. If reuse makes a check awkward, the check wins.

## Read before writing code

1. `AGENTS.md` — governing constraints. Short.
2. `codebase-analysis-docs/TASK4_IMPLEMENTATION_PROMPT.md` — Goal, Stage 3, D3, D4, Traps.
3. `arbitrage_v2/steam_public.py` — `page_fields`, `_group_queries`, `_group_bucket`,
   `_required_query`, `_optional_pricehistory`, `normalize_fields`, `_fetch_group_orderbook`,
   `capture_public`.
4. `arbitrage_v2/collection_transport.py` — the whole file is 100 lines; `_context`,
   `request_context`, `prepare_request`.
5. `arbitrage_v2/collection_batch.py` — `CollectionBatch.ensure`, `begin_request`, `fetch_request`.
6. `arbitrage_v2/capture_selection.py::select_captures` and
   `arbitrage_v2/prediction.py::_market_captures` — how a capture's timestamps are consumed.
7. `tests/test_steam_public.py` — the grouped fixtures and helpers 5a built.

## The mechanism, stated precisely

Four facts that determine the design. Get these wrong and the stage does not work.

### 1. You cannot key the cache on the group URL

`capture_public` requests `listing_url(app_id, title)` for the *exact variant title*. Steam answers
**302 → the group page** (e.g. `/market/listings/730/G1807208B083004`). You only learn the group's
identity *from the redirect*, i.e. after paying for the request you were trying to avoid.

So the cache is keyed by **bucket title**: a cached page's `loaderData[3].buckets` lists every
`bucket_id` in the family, and each of those titles maps to that cached page. A request for title
`T` is a cache hit when some retained page lists `T` among its buckets. Build the index at insert
time, from the buckets the page declares.

### 2. Parsing is not what makes the retained data small

The governing prompt says to "cache the extracted per-title fields rather than raw bytes". Take the
intent, not the literal claim: **Python objects for the same JSON are typically larger than the
encoded bytes, not smaller.** The reduction has to come from *discarding queries you will never
use*, not from the act of parsing.

A real group page carries ~40 queries for 15 buckets. You need:

- the `loaderData` listing row (`bCommodity`, `success`, `appid`, `buckets`,
  `initialFallbackBucketID`);
- `page_currency`;
- one `['market','description',730,<bucket>]` query per bucket;
- optionally `['market','pricehistory',730,<bucket>]` per bucket — **this is the bulk**; the
  commodity fixture `steam_public_fracture_case.json` alone carries 2,936 history points;
- the single embedded `['market','orderbook',730,<fallback bucket>]` query.

Everything else on the page (cookie preferences, asset schema, store item, and so on) is dropped.

**Measure before you choose a bound.** The raw captures are in
`data/steam-probes/grouped-discovery-*.json`. Load one, build your retained structure from it, and
measure it (`len(pickle.dumps(entry))` is a fair proxy; a recursive `sys.getsizeof` walk is better).
State the measured number in your summary and pick the cap from it. If retaining history for all 15
buckets blows the ceiling, see D3a below.

### 3. Timestamps: the trap that will bite you

`normalize_fields(fields, retrieved_at)` uses `retrieved_at` for three different checks:

| Check | Location | Wants |
| --- | --- | --- |
| embedded book not in the future | `dataUpdatedAt` branch | the time *the page* was fetched |
| `_endpoint_orderbook_timestamp` | follow-up branch | the time *the follow-up* was fetched |
| `_history_points` freshness | history branch | the time the page was fetched |

Today those are the same instant, so one parameter serves all three. **Reuse splits them.**

The sharp edge: `_endpoint_orderbook_timestamp` rejects a `Date` header *later* than `retrieved_at`
as `unreliable_orderbook_date_header`. If you naively pass the cached page's original retrieval time
`T0` into `normalize_fields` while the follow-up was fetched now at `T1`, then `Date ≈ T1 > T0` and
**every cached non-fallback capture fails**. The recommended shape below makes this collapse
cleanly; if you deviate from it, handle the split explicitly.

### 4. `retrieved_at` on the capture record is load-bearing

It is not just bookkeeping. Two places consume it:

- `capture_selection.select_captures:18` orders captures by `retrieved_at` — newest wins.
- `prediction._market_captures:88` rejects a capture whose `retrieved_at` is older than
  `max_age_seconds` (`freshness_seconds`, default 14400 s).

Separately, `prediction._steam_observation:104` checks freshness against
`payload.result.histogram.date` — the *observation* timestamp, which is the book's.

`max_run_seconds` defaults to **3600**, so a run-local cached page can be **up to an hour old** by
the time it is reused. Writing `retrieved_at = now()` on a capture whose evidence is an hour stale
is exactly the "silently corrupt every downstream age check" the governing prompt warns about. Do
not do it, and do not paper over it by narrowing the window — decide honestly what each timestamp
means and record the rest in provenance.

## Recommended shape

Implement this unless you find a concrete reason not to; if you deviate, say why in one or two
sentences.

### On a cache hit, always fetch the order book fresh

A cached page's *embedded* order book belongs to the fallback bucket and is as old as the page. If
you serve it from cache you are publishing an hour-old book as a new capture, and you inherit the
whole staleness argument above.

Instead: **the embedded book is used only on the fetch that produced the page.** Any capture derived
from a *cached* page — including one for the fallback bucket — takes its book from a fresh
`GET /market/orderbook?q=Load&qp=[730,"<exact title>"]`.

This costs one small JSON call in the reuse case and buys three things:

1. Every derived capture's book is genuinely fresh, so `histogram.date` is honest and
   `retrieved_at = now()` is defensible — the price evidence really was observed now.
2. The timestamp split in fact 3 collapses: the book always comes from the endpoint on a cache hit,
   so `normalize_fields(fields, now)` stays correct. `_history_points` still passes, because a
   cached history observed at `T0` is in the past relative to `now`.
3. The stop/pause gate keeps working. `_fetch_group_orderbook` calls
   `prepare_request(..., kind='details_followup')`, which reaches `CollectionBatch.begin_request`
   and therefore honours pausing, the run deadline, source deferral, pacing and counting. A capture
   that made *no* HTTP request at all would bypass that gate entirely — a hole you would then have
   to plug by hand.

Note this changes D3's arithmetic slightly, in a direction worth stating in your summary: 15 Slate
variants cost **1 page + 15 follow-ups**, not 1 page + 14. That is still an enormous improvement on
15 page loads, and the extra call is a few hundred bytes.

### Split `page_fields` so per-title validation is structural

The governing prompt requires that "a cached page may serve a second title only after that title's
own description and order-book queries validate independently." Guarantee that by construction, not
by remembering to do it:

- **`decode_group_page(body, app_id)`** → the reusable, page-level structure: listing row,
  `page_currency`, and the retained queries. Validates only page-level identity
  (`success is True`, `appid`, `bCommodity`). Knows nothing about any particular title.
- **`select_title_fields(page, app_id, title)`** → the existing per-title work: `_group_bucket`,
  the `description` query, `_optional_pricehistory`, and the fallback/follow-up decision. Returns
  the same `{'app_id', 'title', 'page_currency', 'queries'}` shape `page_fields` returns today.

`page_fields` then becomes decode-then-select for a fresh body, and the cache path is
select-against-a-retained-page. Both routes run the identical selection code, so "re-validate per
title" cannot be skipped by accident.

Commodity pages keep their existing path untouched and **never enter the cache**.

### Where the cache lives

`CollectionBatch` is the run-scoped object and owns the cache. `collection_transport._context` is
the run-scoped channel that already reaches `steam_public` without changing
`fetch_request(journal, request, keys)`. Put a reference to the batch's cache object into the
context dict alongside `begin` / `remaining` / `complete`.

Note `request_context(begin, remaining, complete=None)` builds a **fresh dict per call**, so the
cache must be an object owned by the batch and passed in — not state stored in the dict itself.
Extend the signature deliberately and minimally.

Requirements:

- Never persisted, never written to the journal, never shared between runs. A new `CollectionBatch`
  starts cold. Prove it in a test.
- Bounded: cap the number of retained pages (**4** is the governing prompt's suggested starting
  point) with a clear eviction policy. State the policy and the measured per-entry size.
- When no context is set — `capture_public` called directly, as `tests/test_steam_public.py` does —
  everything must work exactly as it does today, with no caching. Do not make the cache mandatory.

### Provenance

A derived capture must be auditable as derived. Record at least:

- `derived_from_cached_page: true`
- `page_retrieved_at` — the original `T0`, so an auditor can see how old the page-sourced fields are
- the **same** `decoded_body_sha256` as the capture that fetched the page, because it is the same
  page
- the original `final_url`

and keep the existing follow-up provenance (`followup_url`, `followup_final_url`,
`followup_response_date`, `followup_response_age`).

## Decision points

### D3a — price history for cached buckets

If your measurement shows retaining history for every bucket on 4 pages is too large, the fallback
is to retain history only for buckets actually requested, and let derived captures for other buckets
carry `history_status: 'unavailable'` — a shape `normalize_fields` already supports.

That is acceptable **only if it is explicit**: recorded in provenance, visible, never silent. Do not
let a derived capture quietly lose its price history while a freshly fetched sibling keeps it, with
nothing in the record saying why.

Report your measurement and which branch you took.

### D3b — a cached page that cannot serve a title

If a title *is* listed in a cached page's buckets but its own `description` query is missing or
failed, that is `missing_or_failed_steam_market_query` — an **item-scoped failure**. Do **not**
silently re-fetch the page to try again: that doubles the cost on exactly the malformed pages you
least want to hammer, and makes behaviour non-deterministic.

A title that is *not* in any cached page's buckets is simply a cache **miss**, not an error. It
takes the ordinary fresh-fetch path.

## Coverage to write

Follow the conventions 5a established in `tests/test_steam_public.py` — the
`steam_public_slate_group.json` / `steam_public_slate_orderbook_endpoint.json` fixtures and the
`group_page(...)` helper. Batch-level tests belong in `tests/test_collection_reliability.py`
alongside `GroupedFollowupTests`.

**Reuse works**

- Two titles from the same family, collected in one run, produce **one** page request. Assert on
  `request_attempt` records, not just on results: expect one `details` attempt for the first title,
  none for the second, and one `details_followup` per title.
- The second title's capture carries that title's own book, description and identity — not the
  first title's. Make the two books differ so a mix-up cannot pass.
- A title in a different family is a cache miss and fetches its own page.

**Re-validation is per title (Stage 5 bullet)**

- A cached page whose second title's `description` query is missing or `status != 'success'` fails
  that title item-scoped, and does **not** serve the first title's description in its place.
- A title absent from the cached page's `buckets` never resolves to a neighbouring bucket.
- A not-marketable or non-`normal` `Quality` bucket fails from the cache path with the same string
  it fails with on the fresh path.
- Assert via `collection_batch.failure_scope` that derived-capture failures are item-scoped, so this
  stays wired to Stage 2.

**Timestamps are preserved (Stage 5 bullet)**

- A derived capture records `page_retrieved_at == T0` in provenance while its
  `histogram.date` comes from the fresh follow-up, not from `T0`.
- A derived capture's `decoded_body_sha256` equals the original page capture's.
- Advance the fake clock substantially between the two collections and assert the derived capture
  does not claim the page was fetched at reuse time.
- A derived capture survives `prediction._steam_observation` and `_market_captures` freshness at
  reuse time, and `select_captures` orders it correctly against its sibling.

**Bounds and lifecycle**

- The cap holds: after N+1 distinct group pages, the least-recently-used entry is gone and a title
  from it refetches. Assert the refetch happens.
- A fresh `CollectionBatch` starts cold — no entry survives from a previous batch.
- `capture_public` called with no request context behaves exactly as today.
- Commodity captures neither populate nor consult the cache.
- Nothing cache-related is ever written to the journal. Assert no cache payload appears in any
  record.

## Definition of done

- Full suite green with the new tests: `& .\.venv\Scripts\python.exe -B -m unittest discover -s
  tests -q`. All 310 existing tests still pass — the commodity path and the single-title grouped
  path must both be provably unchanged.
- Collecting N variants of one family in one run costs **one** grouped page request, and the saving
  is demonstrated by a test that counts `request_attempt` records.
- Derived captures re-validate per title and carry honest, auditable timestamps.
- The cache is bounded by a stated, measured number and cannot outlive its run.
- Your summary states: the measured retained-entry size, the cap and eviction policy you chose, the
  D3a branch you took, and the revised per-family request arithmetic.
- Stage 4 and D1 remain untouched.
