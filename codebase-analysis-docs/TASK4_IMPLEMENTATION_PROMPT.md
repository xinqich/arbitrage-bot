# Implementation prompt — Task 4: broader direct Steam collection

> Paste the section below into a fresh Claude Code session in `arbitrage-bot`.
> Everything the session needs is stated here; it starts cold.

---

## Context

You are working on **arbitrage-bot** (package `arbitrage_v2`, version 1.0.5), a local-only,
read-only CS2 market research and bookkeeping tool. It never places orders; every real trade is
manual. State is event-sourced into one append-only SQLite table (`data/research.sqlite3`)
protected by `RAISE(ABORT)` triggers.

Read these before writing code, in order:

1. `AGENTS.md` — the governing constraints. Short.
2. `arbitrage_v2/steam_public.py` — the file you are extending (247 lines).
3. `docs/DIRECT_STEAM_VARIANTS.md` — a qualification record from 2026-09-22 describing this exact
   feature. **Treat it as the specification.** It is the surviving record of a previous
   implementation whose code was lost in a repository reset; `docs/` is gitignored, which is why
   the document outlived the code it describes.
4. `arbitrage_v2/collection_batch.py` — `failure_scope`, `scope_key`, `request_for`, `ensure`.
5. `arbitrage_v2/prediction.py` — `_steam_book`, `_steam_observation`.

`docs/IMPLEMENTATION_STATUS.md` contains a paragraph describing version 1.0.6 as complete. **It is
not.** That paragraph and `DIRECT_STEAM_VARIANTS.md` describe the lost work. Verify any doc claim
against the code before relying on it.

## Ground rules

- **Code style.** Write new code in a clear, conventional style. The existing code is extremely
  dense (no spaces around operators, multiple statements per line, imports inside functions). Do
  **not** imitate it and do **not** restyle it. Change existing code only when genuinely necessary,
  and state the reason in one or two sentences before doing so.
- **Testing.** `& .\.venv\Scripts\python.exe -B -m unittest discover -s tests -q` — 274 tests,
  ~31s, currently green. Keep it green. Every test builds its own journal in a
  `TemporaryDirectory`; none touches the production journal.
- **No release ritual for now.** No version bump, no `RELEASE_*_CHECKS.md`, no backup ceremony
  until the feature actually works. Tests are the gate.
- **A production journal with real records exists** at `data/research.sqlite3`. Do not run
  `arbitrage-v2 web`, `collect`, `paper-step`, `route-event`, or `csfloat-collect` against it
  without asking. The local desk may be running and holding the OS collection lock.
- **Never weaken evidence requirements to make something parse.** An unsupported page must fail
  explicitly, scoped to that item. Substituting a neighbouring variant, a different provider's
  quantities, or a guessed price is out of bounds.

## What exists today

`arbitrage_v2/steam_public.py` reads **commodity** Steam market pages only:

- `listing_url()` builds `https://steamcommunity.com/market/listings/730/<title>?currency=1&l=english`.
- `ListingRedirect` permits redirects only within `/market/listings/730/` (`allowed_url`).
- `page_fields()` extracts `window.SSR.loaderData` and `window.SSR.renderContext`, then pulls the
  `['market','description',730,title]` and `['market','orderbook',730,title]` React-Query entries.
- `normalize_fields()` validates identity, USD (`eCurrency == 1`) at page and book level, the
  millisecond `dataUpdatedAt` order-book timestamp, and compact book consistency
  (`_book_rows`: positive pairs, correct sort, quantities summing to `cBuyOrders`/`cSellOrders`,
  best price matching `amtMaxBuyOrder`/`amtMinSellOrder`).
- It emits `provenance.book_quantity_semantics = 'incremental'`, which is what unlocks multi-level
  depth in `prediction._steam_book`. Providers without that field degrade to top-of-book only.

**Three hard gates currently reject every grouped (skin) page:**

| Gate | Location | Effect |
| --- | --- | --- |
| `listing[0].get('bCommodity') is not True` | `page_fields` | `unsupported_steam_listing_identity` |
| `LIMIT = 4_000_000` | module constant | grouped pages reach ~4.39 MB decoded |
| `queryKey == ['market', kind, app_id, title]` | `page_fields` | grouped order books are per-bucket, not per-title |

Plus `normalize_fields` requires `marketable is True`, which the qualification record shows is
correctly `False` for StatTrak and Souvenir group pages.

## Goal — corrected mechanism (Stage 0 findings, 2026-09-23)

> **`docs/DIRECT_STEAM_VARIANTS.md` is wrong about the follow-up mechanism.** It describes deriving
> a filtered *listing page* URL from the bucket's `Quality`/`Exterior` pairs. That does not work.
> Two URL shapes were tried live and both returned the same page with the same fallback bucket:
> `?filter={"Quality":["normal"],"Exterior":["WearCategory2"]}` and
> `?category_730_Quality[]=tag_normal&category_730_Exterior[]=tag_WearCategory2`
> (see `data/steam-probes/followup-probe-*.json`, both with `target_orderbook_present: false`).
> Treat the rest of that document as accurate and this section as superseding it.

The verified mechanism:

1. `GET /market/listings/730/<exact title>?currency=1&l=english` → **302** → group page
   (e.g. `/market/listings/730/G1807208B083004`).
2. That single page carries, for **every** bucket in the family:
   - a `['market','description',730,<bucket title>]` query with `market_hash_name`, `commodity`,
     `marketable`, `appid`;
   - a `['market','pricehistory',730,<bucket title>]` query;
   - an entry in `loaderData[3].buckets` with `bucket_id` (the exact market_hash_name),
     `filters` (the Quality/Exterior pairs), `min_price` (cents, as a string), `classid`.
3. It carries **exactly one** `['market','orderbook',730,<title>]` query — for
   `loaderData[3].initialFallbackBucketID`, which Steam chooses. You do not control it.
4. For any other bucket, the order book comes from a **separate lightweight JSON endpoint**:
   `GET /market/orderbook?q=Load&qp=[730,"<exact title>"]`
   → `{"data":{"success":true,"data":{"amtMaxBuyOrder":…,"amtMinSellOrder":…,"eCurrency":1,
   "cBuyOrders":…,"cSellOrders":…,"rgCompactBuyOrders":[…],"rgCompactSellOrders":[…]}}}`
   Identical field names to the commodity path. Confirmed anonymous (no cookies or session);
   `currency`/`l` params make no difference. The bucket `filters` pairs are **not** used to build
   this URL — use them only to confirm the requested title is a genuine bucket in this group.
5. Within a run, a cached group page may serve another title **only after that title's own
   description query validates**, and the derived capture keeps the **original** page retrieval and
   observation timestamps.
6. Direct Steam is the **preferred** detailed provider; SteamApis is a fallback used only after a
   direct item-format failure and only when its account response confirms overage is disabled.

Consequence: when the requested title **is** the fallback bucket, no second request is needed at
all. Otherwise it costs one small JSON call on top of the shared group page.

### Observed reference data (`AK-47 | Slate (Field-Tested)`, 3 captures)

| Property | Value |
| --- | --- |
| Group page | `G1807208B083004`, `bCommodity: false`, `success: true`, `appid: 730` |
| Buckets | 15 |
| `initialSelectedBucketID` | `null` |
| `initialFallbackBucketID` | `AK-47 \| Slate (Factory New)` |
| Encoded / decoded size | ~743 KB / ~4.18 MB |
| Inline scripts | 1 |
| `loaderData` rows | 4 — row 1 has `filterConfig`, row 3 has `bCommodity` |
| `filterConfig.currency.eCurrency` | `1` (USD) |
| Queries on page | 40 |
| `marketable` by family | plain wears **true**; `Souvenir …` and `StatTrak™ …` **false** |

The existing `page_fields` selectors (`'filterConfig' in row`, `'bCommodity' in row`) both still
match exactly one row on a group page, so only the `bCommodity is not True` rejection needs to
change.

## Stage 0 — COMPLETE

Done on 2026-09-23. Findings are in the Goal section above; raw captures are in
`data/steam-probes/grouped-discovery-*.json` and `followup-probe-*.json`. The probe scripts
(`scripts/steam_grouped_discovery.py`, `steam_grouped_followup_probe.py`,
`steam_orderbook_endpoint_probe.py`, `steam_redirect_probe.py`) are untracked and were written
for discovery only — they call `urlopen` directly and bypass the pacing and lock machinery, so do
not reuse them as a model for production code. Decide with the user whether to keep or delete them.

## Stage 0.5 — endpoint equivalence check — COMPLETE, PASSED

One assumption still needs converting into a measured fact: that `/market/orderbook` returns the
same book Steam's own page would show, rather than a different or more cached view.

There is exactly one case where both mechanisms return the **same title's** book — the fallback
bucket. Use it:

- For `AK-47 | Slate (Factory New)` (the observed `initialFallbackBucketID`), fetch the group page
  and the `/market/orderbook` endpoint back to back, and compare
  `amtMaxBuyOrder`, `amtMinSellOrder`, `cBuyOrders`, `cSellOrders` and the compact arrays.
- Repeat three times over ~15 minutes to confirm they move together rather than one lagging.
- Record the HTTP `Date` header and check for `Age` / `Cache-Control` on the endpoint responses.

This is roughly 9 requests. It is a genuine go/no-go: if the two disagree beyond plausible market
movement in the gap, the endpoint is not the page's data source and the whole approach needs
rethinking — better to learn that now than after the parser is written.

Run it against a **temporary journal**, never `data/research.sqlite3`, and ask the user to pause
collection first (Pause in the desk, or `arbitrage-v2 worker-control pause`). The production worker
issues its own Steam requests and spacing is tracked per-journal in `collection_state`.
`scripts/steam_rate_probe.py` is the precedent for a bounded diagnostic of this kind.

## Stage 1 — parser extension — COMPLETE (reviewed)

Extend `steam_public.py`. Prefer adding new functions over editing existing ones; `page_fields` and
`normalize_fields` will need changes, which is expected and justified (the `bCommodity` gate is the
feature).

- Add a grouped-page path that resolves the requested title to a bucket in `loaderData[3].buckets`
  and validates its `['market','description',730,<title>]` query (identity, `marketable is True`).
  A title with no matching bucket is an item-scoped failure, not a fallback to something nearby.
- If the requested title is `initialFallbackBucketID`, use the order book already embedded in the
  page — no second request.
- Otherwise issue **one** call to `GET /market/orderbook?q=Load&qp=[730,"<exact title>"]`.
  The bucket `filters` pairs confirm the title is a real bucket; they do not go in the URL.
- Unwrap the endpoint's double nesting (`data.data`) and check `data.success is true` before
  reading the book fields.
- `allowed_url` must accept `/market/orderbook` as well. Widen it deliberately and minimally —
  exact path match, https, `steamcommunity.com`, no fragment. It is the redirect guard, and
  loosening it too far is a security regression. Note this is a genuinely different path, not just
  a wider query string on `/market/listings/730/`.
- Keep validation parity with the commodity path: identity, USD at page and book level, millisecond
  observation timestamp, compact pair ordering, quantity sums, best-price agreement.
- Preserve `book_quantity_semantics = 'incremental'` so multi-level depth still works downstream.
- Raise `LIMIT` to 8 MiB for **both** encoded and decoded bodies, matching the qualification
  record's headroom over the observed 4,390,653-byte maximum. If a real capture ever exceeds 8 MiB,
  collection must stop and document it rather than raise the limit again.

## Stage 2 — correct failure scoping (do this first, before any collection run)

Stage 1 added the five named parser errors but did not wire them into scoping. As it stands,
Stage 1 made grouped failures **worse** than before: they are now provider-scoped where they used
to be item-scoped. Nothing else in this task matters until 2A and 2B land, and the collector must
not run against `data/research.sqlite3` until they do.

### 2A — item-scope the new parser error strings

`collection_batch.failure_scope` classifies by an explicit allow-list and **defaults to `provider`**:

```python
if (status in {400, 404, 422} or error in {'invalid_json_or_size',
        'invalid_or_unsupported_steam_page'}):
    return 'item'
return 'provider'          # <-- the default
```

None of the five strings Stage 1 introduced are in that set, so each one now yields
`scope_key = 'steam_public'` and stops **all** Steam collection for the batch. One StatTrak or
Souvenir title in the roster is enough. Before Stage 1 that same title produced
`invalid_or_unsupported_steam_page` and blocked only itself.

Add all five to the item-scope set:

    title_not_in_listing_group
    unsupported_item_quality
    malformed_orderbook_endpoint_response
    unreliable_orderbook_date_header
    stale_orderbook_endpoint_cache

Each is a fact about one item, or about one item's follow-up response. None of them indicates a
provider or endpoint problem: a page that parsed far enough to reach any of them has already proven
the provider is reachable and the endpoint is working.

Keep `steam_public._NAMED_PARSE_ERRORS` and this set in sync, and make the coupling impossible to
break silently — import the frozenset rather than retyping the strings, or add a test asserting the
two sets are equal. A sixth error string added later without a matching scoping entry reintroduces
exactly this bug.

Test: assert each of the five maps to `'item'` scope, and assert the two sets match.

### 2B — register `details_followup` as a request kind

`_fetch_group_orderbook` calls `prepare_request` with `kind='details_followup'`, which reaches
`Batch.begin_request`:

```python
if request['kind'] != 'account' and any(request_key(r) == request_key(request)
        and r.get('status') == 200 and not r.get('error') for r in self.results[before:]):
```

`request_key` raises `ValueError("unsupported read-only collection request")` for any
`(provider, kind)` pair outside its whitelist, and `("steam_public", "details_followup")` is not in
it. This survives today only because `any()` over an empty slice never evaluates the generator body.
It fires the moment the `check_unlocks` hook (`worker.py:305`) produces a result while the follow-up
is waiting on request spacing — and `capture_public` then swallows the `ValueError` as a generic
`invalid_or_unsupported_steam_page`, so the item fails intermittently, with a misleading reason and
no trace of the real cause.

Fix:

- Add `("steam_public", "details_followup")` to the whitelist in `request_key`.
- Classify `details_followup` failures as item-scoped in `failure_scope`, in the same change as 2A.

**Do not** make the follow-up reuse `kind='details'` instead. That would make
`request_key(followup) == request_key(parent)`, so an unlock hook that happened to fetch the same
item would raise `RequestSatisfied` mid-capture and abandon a page already downloaded.

One thing to notice but leave alone: `_failure` is always called with the outer `kind='details'`
request, so nothing ever writes `steam_public:details_followup:*` scope keys, which makes
`begin_request`'s endpoint- and item-scope deferral checks dead for the follow-up. Provider-scope
deferral still applies, so pacing and 429 handling are intact. Leave it that way unless a test shows
otherwise — adding follow-up-specific scope keys would give a switched-source route its own fresh
failure budget, which is the same class of mistake D1 rules out.

Test: a batch run with a `before_request` hook that appends a result, covering a grouped
non-fallback title, asserting the follow-up completes rather than failing generically.

## Stage 3 — run-local grouped-page reuse

Cache decoded group pages **for the duration of one run only**, never persisted.

- `CollectionBatch` is the run-scoped object; `collection_transport._context` (a `ContextVar`) is
  already the run-scoped channel and avoids changing the `fetch_request(journal, request, keys)`
  signature. Prefer that over a new parameter.
- Re-validate per title: a cached page may serve a second title only after that title's own
  description and order-book queries validate independently.
- A capture derived from a cached page must record the **original** retrieval time and embedded
  observation timestamp — not the time of reuse. Faking freshness here would silently corrupt every
  downstream age check.
- **Bound the cache.** A group page is ~4 MB decoded; caching many is a memory problem. Cache the
  extracted per-title fields rather than raw bytes, and cap the number of retained pages (4 is a
  reasonable starting point). State the bound explicitly.

## Stage 4 — provider routing

`collection_batch.request_for` currently does the opposite of the spec:

```python
if (kind == 'details' and watchlist.get('catalogue',{}).get('enabled')
        and title not in {r['title'] for r in watchlist['items']}
        and title not in watchlist.get('item_sources',{})):
    provider='steamapis'
```

Every catalogue-discovered title is forced to SteamApis. The spec says direct Steam is preferred and
SteamApis is a fallback only after a direct item-format failure. Changing this is a justified edit
to existing code.

Design the fallback as a **visible second request in the batch**, not a hidden inner call, so it is
paced, counted, and recorded like any other request. Note that `scope_key` for item scope already
includes the provider, so a direct-Steam item failure does not block the SteamApis attempt for the
same title — verify that holds.

Keep `collector.free_access` gating intact: SteamApis may only be used when the account response
confirms `overageEnabled is False`.

## Stage 5 — tests and fixtures

- Build a **reduced synthetic fixture** reproducing the grouped-page SSR shape. Do not commit a
  4 MB page dump. The lost work used a "sanitized replay subset" for exactly this reason.
- Existing fixtures live directly in `tests/fixtures/` and are loaded by
  `tests/test_steam_public.py:23` as `steam_public_<name>.json`. Follow that convention.
- Cover: exact variant selection; a group page serving a second title only after independent
  validation; timestamp preservation across reuse; `not_marketable` variants failing explicitly;
  malformed pages failing with an item-scoped reason; the 8 MiB bound on both encoded and decoded
  bodies; and the redirect guard still rejecting off-surface URLs.
- Re-run the full suite. All 274 existing tests must still pass — `test_steam_public.py`,
  `test_collection_reliability.py` and `test_hourly_collection.py` are the ones most likely to
  catch a regression.

## Decision points

**D1 and D2 are already decided — implement them as stated. D3 is still open.**

### D1. Provider switching vs. consumed paper depth (highest risk) — DECIDED

The spec says *"changing sources must not replenish already consumed paper-trading depth."* The
current code cannot honour that, and the interaction is not obvious:

- `capture_selection.select_captures` keys on `(app_id, title, kind)` and partitions its
  success-fallback by `input_kind` — **not by provider**. The newest capture wins, so the effective
  source can flip between runs.
- `paper._capacity` builds its key as
  `f'{ENGINE}:{input_kind}:730:{title}:{book_name}'` — **also no provider**.
- `depth.available` replenishes capacity on any observed positive change in visible quantity.

So when a route has consumed depth from a multi-level `steam_public` book and a `steamapis` capture
later wins selection, the old `level_id`s vanish and new ones appear as a positive change —
**silently refilling consumed paper capacity.** This is precisely the failure the spec warns about,
and it becomes reachable as soon as both providers produce `details` for the same title.

Options:

| Option | Effect | Assessment |
| --- | --- | --- |
| Add provider to the capacity key | Each provider gets its own pool | **Worse** — a switch hands the route a fresh, full pool |
| Make selection provider-stable | Prefer the last-used provider unless it fails | Helps, but does not prevent the switch |
| Pin the provider per paper route | Record it in `paper_settings`; require matching snapshots | Clean, but adds a field to a frozen record |
| Block the step on a provider change | `waiting_for_evidence` with an explicit reason | **CHOSEN** |

**Decision: block the step.** When the winning capture's provider differs from the one that fed a
route's already-consumed pool, `paper.step` returns `waiting_for_evidence` with a named, operator-
visible reason. Do not attempt to reconcile depth across providers.

Rationale, so you do not re-litigate it: pinning the provider in `paper_settings` would add a field
to a record that is written once at entry and frozen, and the existing live paper trial
(`paper-fracture-kilowatt-20260908`) has no such field — that needs a migration or default, which
is out of scope before v1. Blocking needs no schema change, is reversible (pinning can be layered
on later if blocking proves noisy), and matches how `paper.step` already handles inventory
ambiguity with `manual_decision` / `waiting_for_evidence` instead of guessing.

Implementation notes:

- The provider that produced each consumed level must be discoverable. `depth.available` state is
  keyed by `level_id` only, so record the producing provider in the `paper_book` state payload
  when capacity is consumed, and compare on the next step.
- Keep the capacity key itself unchanged (`{ENGINE}:{input_kind}:730:{title}:{book_name}`).
  Adding provider to the key would hand a switched route a fresh full pool — the opposite of the
  requirement.
- A route with no consumed depth yet has nothing to protect; do not block it.
- Surface the reason in `worker_health` / the Debug panel the same way other paper-step statuses
  appear.

### D2. Scope of "additional noncase items work" — DECIDED

The spec lists eight categories to qualify. The qualification record shows only some are
*supportable*:

| Category | Qualification result |
| --- | --- |
| Cases, stickers, capsules, music kits | commodity — already work today |
| Ordinary skins (`AK-47 \| Slate` FN/FT) | grouped, marketable — **the actual new capability** |
| StatTrak | grouped, `not_marketable` — **unsupported** |
| Souvenir | grouped, `not_marketable` — **unsupported** |
| Agents (`Sir Bloody Miami Darryl`) | `malformed_market_data` — **unsupported** |

Qualifying a category includes concluding it is unsupported.

**Decision: marketable grouped skins only.** Task 4 is done when ordinary marketable grouped skins
yield exact-variant books and StatTrak, Souvenir and agents fail explicitly with item-scoped
reasons. Do **not** treat those three as defects to solve; Steam appears to genuinely mark those
pages not marketable, and chasing them is out of scope.

### D3. Request budget interaction — OPEN, raise with the user

**Revised after Stage 0 — this is much less severe than first estimated.** A single ~4.2 MB group
page carries identity and price history for **all 15 buckets** in the family. So the cost is one
group page per *family*, plus one small JSON call per variant — and zero extra calls when the
variant happens to be the fallback bucket. Fifteen `AK-47 | Slate` variants cost 1 page + 14 small
calls, not 15 page loads.

This makes **run-local group-page reuse (Stage 3) the single highest-value part of this task**, not
an optimisation. Without it, each variant re-downloads 4.2 MB.

Residual question for the user: whether to adjust `research_batch_size` or per-request spacing
alongside. Recommend leaving both alone until real coverage numbers exist.

### D4. Timestamp basis for the follow-up book — DECIDED

The follow-up endpoint carries no `dataUpdatedAt`. The previous session asked whether to relabel
the derived book's `book_timestamp_kind` as something weaker, e.g. `retrieval_time_only`.

**Do neither. Use the HTTP `Date` header and keep the existing label.** The premise behind the
question — that the SSR path's `dataUpdatedAt` is a stronger, market-side observation timestamp —
does not survive measurement.

Across all three captures in `data/steam-probes/grouped-discovery-*.json`:

| Capture | HTTP `Date` | orderbook `dataUpdatedAt` | all-40-query spread | `Date` − `dataUpdatedAt` |
| --- | --- | --- | ---: | ---: |
| 11:32 | 11:32:50 | 11:32:49.830 | 147 ms | +0.17 s |
| 12:20 | 12:20:14 | 12:20:13.733 | 332 ms | +0.27 s |
| 13:11 | 13:11:52 | 13:11:52.306 | 395 ms | −0.31 s |

Every query on the page — cookie preferences, asset schema, store item, description, price history,
order book — is stamped within a few hundred milliseconds of the same instant, and that instant
equals the HTTP `Date` header to within its one-second resolution. `dataUpdatedAt` is React Query's
"when did this query resolve" value captured during server-side render. It records **when Steam's
server answered**, not when the market last observed the book.

The existing code already says as much: `normalize_fields` emits
`underlying_market_cache_age: 'not_exposed'` alongside the timestamp. That caveat is the honest
part, and it applies identically to both paths.

So the `Date` header on `/market/orderbook` is the same class of fact, obtained a different way.
Relabelling it `retrieval_time_only` would wrongly imply the commodity path guarantees something
stronger; that would be inaccurate in the other direction and would make the two books look
incomparable when they are not.

Implement it as:

- `book_timestamp_kind: 'steam_server_query_observed_at'` — unchanged, both paths.
- Add `book_timestamp_source: 'ssr_query_dataUpdatedAt' | 'http_date_header'` so the mechanism is
  recorded and auditable.
- Keep `underlying_market_cache_age: 'not_exposed'` on both.

Because a `Date` header is weaker than an embedded value in one specific way — an intermediary
cache can rewrite it — add these integrity checks, and fail the item (item-scoped) if any trips:

- reject a `Date` later than our own retrieval time;
- reject a `Date` more than 120 s before retrieval (suggests a cached response);
- reject if an `Age` header is present and non-zero;
- fall back to retrieval time if `Date` is absent or unparseable, and record that in
  `book_timestamp_source`.

Do **not** change the commodity path's existing behaviour while doing this.

## Definition of done

- Existing coverage survives: all 274 tests green, commodity items unchanged.
- An ordinary marketable grouped skin yields a validated, exact-variant order book with correct
  multi-level depth and the embedded observation timestamp.
- StatTrak, Souvenir and malformed pages fail with specific, **item-scoped** reasons.
- Run-local group-page reuse preserves original timestamps and re-validates per title.
- Direct Steam is primary; SteamApis fallback fires only on direct item-format failure and only
  with overage confirmed disabled.
- No provider switch can replenish consumed paper depth (per the D1 decision).

## Traps worth restating

- New error strings default to **provider** scope in `failure_scope`. Add them to the item set.
- `_steam_book` silently degrades to top-of-book when `book_quantity_semantics` is absent. Keep
  emitting it.
- `_decode_after` requires **exactly one** regex match and raises
  `missing_or_ambiguous_steam_page_data` otherwise. Grouped pages may contain more SSR blocks.
- `prediction._steam_observation` independently re-validates identity and freshness from the stored
  payload. A payload that parses at capture time can still be rejected at use time.
- Money is integer cents everywhere; `money.exact_integer` rejects `float` and `bool`. JSON is
  parsed with `parse_float=str`.
- `capture_public` must keep archiving only selected market fields plus a page hash and
  non-sensitive provenance — never raw HTML, cookies, or request headers.
