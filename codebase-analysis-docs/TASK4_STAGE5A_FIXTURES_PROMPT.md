# Implementation prompt — Task 4, Stage 5a: retrofit grouped-page test coverage

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
addition to *commodity* (case, sticker, capsule) pages. Read its Context, Ground rules, Goal and
Stage 1 sections for the mechanism. Do not work ahead into its Stage 3, Stage 4 or D1.

Task 4's stage status, as of commit `3d00d00` (v1.0.5.1):

| Stage | Status |
| --- | --- |
| 0 — live discovery of the grouped mechanism | complete |
| 0.5 — `/market/orderbook` equivalence check | complete, passed |
| 1 — parser extension in `steam_public.py` | code landed, **effectively untested** |
| 2 — failure scoping (`2A`, `2B`) | complete, reviewed, committed |
| 3 — run-local group-page reuse | not started |
| 4 — provider routing | not started |
| 5 — tests and fixtures | not started — **this prompt is its first half** |
| D1 — block paper steps on provider switch | not started |

## Why this step exists

Stage 1 added roughly 375 lines to `arbitrage_v2/steam_public.py`: the grouped-page branch, bucket
resolution, the standalone `/market/orderbook` follow-up, a widened `allowed_url`, a raised size
`LIMIT`, and the whole D4 `Date`-header freshness mechanism.

`tests/test_steam_public.py` was **not touched**. It has 16 tests and contains no reference to
buckets, the follow-up endpoint, `orderbook_url`, the size bound, or any `Date`-header handling.
The only thing exercising any of the grouped path anywhere in the suite is one end-to-end test that
Stage 2 wrote for its own purposes, in a different file
(`tests/test_collection_reliability.py::GroupedFollowupTests`).

Stage 3 will build a run-local cache **on top of** this parser. Building a cache over an untested
parser means every bug you then hit is ambiguous about which layer produced it. So the coverage is
retrofitted first.

This is deliberately the *first half* of the governing prompt's Stage 5: parser-level fixtures and
tests only. The reuse, routing and provider-switch tests that Stage 5 also lists belong with the
stages that introduce those behaviours.

## Scope

**In scope**

- A reduced synthetic grouped-page fixture, plus the follow-up endpoint response fixture.
- Tests in `tests/test_steam_public.py` covering the Stage 1 surface, enumerated below.
- **Narrow fixes to `steam_public.py` for defects these tests expose.** That is the point of the
  exercise, not a scope violation. One known defect is listed below; expect others.

**Out of scope — do not start these**

- Stage 3 (run-local group-page caching / `collection_transport._context` reuse).
- Stage 4 (`request_for` provider routing, SteamApis fallback).
- D1 (blocking `paper.step` on a provider switch).
- Any collection run against `data/research.sqlite3`.
- Any restyling of existing code (see Ground rules).

## Ground rules

- **Code style.** Write new code in a clear, conventional style. The existing code is extremely
  dense (no spaces around operators, multiple statements per line, imports inside functions). Do
  **not** imitate it and do **not** restyle it. Change existing code only when genuinely necessary,
  and state the reason in one or two sentences before doing so.
- **Testing.** `& .\.venv\Scripts\python.exe -B -m unittest discover -s tests -q` —
  **277 tests, ~30s, currently green.** Keep it green. Every test builds its own journal in a
  `TemporaryDirectory`; none touches the production journal.
- **Line endings.** The repository now has a `.gitattributes`. Do not let an editor rewrite whole
  files; check that `git diff --ignore-cr-at-eol --numstat` matches `git diff --numstat` before
  finishing.
- **No release ritual.** No version bump, no `RELEASE_*_CHECKS.md`, no backup ceremony. Tests are
  the gate.
- **A production journal with real records exists** at `data/research.sqlite3`. Do not run
  `arbitrage-v2 web`, `collect`, `paper-step`, `route-event`, or `csfloat-collect` against it. The
  local desk may be running and holding the OS collection lock. Nothing in this stage needs network
  access or a real journal.
- **Never weaken evidence requirements to make a test pass.** If a test fails because the parser is
  right and your fixture is wrong, fix the fixture. If it fails because the parser is wrong, fix the
  parser and say so. Do not relax a validation to get green.

## Read before writing code

1. `AGENTS.md` — governing constraints. Short.
2. `codebase-analysis-docs/TASK4_IMPLEMENTATION_PROMPT.md` — Goal, Stage 1, D2, D4, and
   "Traps worth restating".
3. `arbitrage_v2/steam_public.py` — the file under test (414 lines).
4. `tests/test_steam_public.py` — the fixture convention you must follow.
5. `tests/test_collection_reliability.py::GroupedFollowupTests` — the one existing grouped test.

## The fixture convention

`tests/test_steam_public.py:22` loads fixtures by name:

```python
def fixture(name='fracture_case'):
    return json.loads((ROOT/'tests/fixtures'/('steam_public_'+name+'.json')).read_text(encoding='utf-8'))
```

Each existing fixture (`steam_public_fracture_case.json`, `steam_public_kilowatt_case.json`) holds
`{"fields": <the output of page_fields>, "source": {"retrieved_at": ...}}`, and
`tests/test_steam_public.py:26`'s `page(fields)` helper rebuilds a minimal HTML envelope around
those fields for the commodity path. Note that `page()` deliberately seeds the envelope with
`sessionid: DO_NOT_ARCHIVE` and a `Set-Cookie` header, so the archive-hygiene tests have something
real to assert the absence of. Preserve that habit in the grouped equivalent.

**Build a reduced fixture, not a dump.** The real `AK-47 | Slate` group page is ~743 KB encoded /
~4.18 MB decoded with 15 buckets and 40 queries. Reduce it to 3 buckets and the queries they need.
Trim price history to a handful of points. Do not commit a multi-megabyte page.

Suggested shape — adapt if the code tells you otherwise:

- `tests/fixtures/steam_public_slate_group.json` — the grouped page: `page_currency`, the
  `loaderData` listing row (`bCommodity: false`, `success: true`, `appid: 730`, `buckets`,
  `initialFallbackBucketID`), and the `description` / `pricehistory` / `orderbook` queries.
  Three buckets, chosen to cover every branch in one page:
  - `AK-47 | Slate (Factory New)` — `Quality: normal`, **is** `initialFallbackBucketID`, so its
    order book is embedded in the page;
  - `AK-47 | Slate (Field-Tested)` — `Quality: normal`, not the fallback, so it needs the follow-up;
  - `StatTrak™ AK-47 | Slate (Field-Tested)` — `Quality: strange`, `marketable: false`, out of scope
    per D2.
- `tests/fixtures/steam_public_slate_orderbook_endpoint.json` — the follow-up response body, in its
  real double-nested `{"data": {"success": true, "data": {...}}}` shape.

Add a `group_page(...)` helper beside the existing `page()` helper, mirroring its structure.

`tests/test_collection_reliability.py` currently carries its own inline `group_page_body()` builder.
Once `group_page()` exists you may import it there instead (cross-test imports are already idiomatic
in this suite — see `import test_worker as worker_fixture`), but **do not change that test's
assertions**; it is committed and green. If deduplicating gets awkward, leave it alone and say so.

## Known defect to fix

`_parse_orderbook_endpoint` (`arbitrage_v2/steam_public.py:213`) unwraps `data.data` but **never
checks `data.success`**, which the governing prompt's Stage 1 bullet 4 requires:

> Unwrap the endpoint's double nesting (`data.data`) and check `data.success is true` before
> reading the book fields.

The flag sits at `json['data']['success']`, a sibling of the inner `data` object. Today a response
with `success: false` — or with no `success` key at all — parses happily as long as the seven book
fields are present. Stage 2's fixture has no `success` key and passes, which is how this survived.

Add the check. A missing or non-`True` `success` is `malformed_orderbook_endpoint_response`, which
is already in `_NAMED_PARSE_ERRORS` and already item-scoped, so no scoping change is needed.

## Coverage to write

Group these into classes however reads best. Each bullet is a fact to pin down, not necessarily one
test method.

**Variant selection**

- A non-fallback, marketable, `Quality: normal` bucket yields a validated, exact-variant book whose
  prices and quantities come from the follow-up response — not from the embedded fallback book.
  Make the two books differ, so a mix-up cannot pass.
- A title that **is** `initialFallbackBucketID` uses the embedded SSR book and issues **no** second
  request. Assert on the request count, not just the result.
- The follow-up URL is built by `orderbook_url(app_id, title)` from the title alone. Assert the
  bucket's `filters` pairs appear nowhere in the requested URL — that is the specific mistake
  `docs/DIRECT_STEAM_VARIANTS.md` documents and Stage 0 disproved.
- Exactly one follow-up request per capture.

**Explicit, item-scoped failure (D2)**

- A title with no matching bucket → `title_not_in_listing_group`. Assert it does **not** silently
  resolve to a neighbouring bucket — this is the substitution the governing prompt forbids.
- A `Quality: strange` (StatTrak) or `Quality: tournament` (Souvenir) bucket →
  `unsupported_item_quality`. Note the ordering: `_group_bucket`'s Quality check fires before
  `normalize_fields`'s `marketable` check, so that is the string you should see for a real StatTrak
  bucket. Pin the ordering down so a later refactor cannot quietly swap which error surfaces.
- A `Quality: normal` bucket that is nonetheless `marketable: false` → identity mismatch, collapsing
  to `invalid_or_unsupported_steam_page`. Both are item-scoped; assert via
  `collection_batch.failure_scope` that each is, so this stays wired to Stage 2.
- A duplicate `bucket_id` in `buckets` must fail, not pick the first match.

**Follow-up response validation**

- Body that is not JSON; `data` missing; `data.data` missing; `data.data` not a dict; any one of the
  seven `_ORDERBOOK_FIELDS` missing; `success` missing; `success: false` — each →
  `malformed_orderbook_endpoint_response`.
- A follow-up book that parses but fails `_book_rows` (bad sort, quantity totals disagreeing with
  `cBuyOrders`/`cSellOrders`, best price disagreeing with `amtMaxBuyOrder`/`amtMinSellOrder`,
  odd-length compact array, zero or negative values) fails closed, exactly as the commodity path
  already does at `tests/test_steam_public.py:116`.
- `eCurrency != 1` on the follow-up book → `explicit_USD_currency_required`.

**D4 — timestamp basis**

`_endpoint_orderbook_timestamp` (`steam_public.py:280`) has five outcomes. Cover all five:

| Condition | Expected |
| --- | --- |
| Plausible `Date`, no `Age` | `observed` = `Date`, `book_timestamp_source` = `http_date_header` |
| `Date` absent | falls back to retrieval time, source = `retrieval_time_fallback` |
| `Date` unparseable | falls back to retrieval time, source = `retrieval_time_fallback` |
| `Age` present and non-zero | `stale_orderbook_endpoint_cache` |
| `Date` after retrieval, or >120 s before it | `unreliable_orderbook_date_header` |

Also assert the boundary at exactly 120 s, and that a `Date` with no timezone is treated as UTC
rather than local time.

And assert D4's parity claim directly: `book_timestamp_kind` is `steam_server_query_observed_at`
and `underlying_market_cache_age` is `not_exposed` on **both** paths, while `book_timestamp_source`
distinguishes them (`ssr_query_dataUpdatedAt` vs `http_date_header`). The commodity path's existing
behaviour must be unchanged — pin that too, since D4 explicitly forbids touching it.

**Provenance parity (the trap)**

- A grouped capture emits `book_quantity_semantics: 'incremental'`. `prediction._steam_book`
  silently degrades to top-of-book when that field is absent, so a regression here would be
  invisible except as quietly shallower depth. Assert the field, and assert multi-level depth
  actually survives into `prediction._steam_book`.
- A grouped capture survives `prediction._steam_observation`, which independently re-validates
  identity and freshness from the stored payload at use time. A payload that parses at capture time
  can still be rejected later; prove this one is not.

**Bounds and surface**

- `LIMIT` is enforced on both the encoded and the decoded body (`_read_body` checks both, around the
  gzip step) and on the page passed to `page_fields`. Over-limit → `steam_page_too_large`.
  **Settle the constant while you are here:** `LIMIT = 8_000_000` is 8 MB, but the governing prompt
  specifies 8 MiB (8,388,608). Headroom over the observed 4,390,653-byte maximum is ample either
  way — pick one deliberately, and have the test assert the number you picked.
- `allowed_url` accepts `/market/orderbook` as an **exact** path match and still rejects
  `/market/orderbookX`, `/market/orderbook/anything`, a URL with a fragment, `http://`, and any
  other host. Extend `test_redirects_cannot_leave_public_listing_surface`
  (`tests/test_steam_public.py:151`) rather than writing a parallel one.
- The follow-up response's own final URL is checked against `allowed_url` too — a follow-up that
  redirects off-surface → `unexpected_steam_redirect`.
- `_decode_after` requires **exactly one** regex match. A grouped page carrying two
  `window.SSR.loaderData` assignments → `missing_or_ambiguous_steam_page_data`.

**Archive hygiene**

- A grouped capture, including its follow-up, archives only selected market fields plus the page
  hash and non-sensitive provenance. No raw HTML, no cookies, no request headers. Follow the
  `DO_NOT_ARCHIVE` sentinel pattern at `tests/test_steam_public.py:160` and seed the sentinel into
  the follow-up response as well as the page.
- A grouped, non-fallback capture writes **two** `request_attempt` records (`details` then
  `details_followup`); the fallback case writes **one**.

## Definition of done

- The full suite is green with the new tests added:
  `& .\.venv\Scripts\python.exe -B -m unittest discover -s tests -q`. All 277 existing tests still
  pass — the commodity path must be provably unchanged.
- Every branch in `_group_bucket`, `_group_queries`, `_parse_orderbook_endpoint` and
  `_endpoint_orderbook_timestamp` is reached by at least one test.
- The `data.success` check exists and is covered.
- `LIMIT` is a deliberate, asserted number.
- Fixtures are small, synthetic and readable; no multi-megabyte page is committed.
- Any parser defect the tests exposed is fixed narrowly, and **reported in your summary** — list
  each one, what it would have done in production, and the fix. That list is the real deliverable
  of this stage; the tests are how you find it.
- Stage 3, Stage 4 and D1 remain untouched.
