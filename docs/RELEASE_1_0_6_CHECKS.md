# Version 1.0.6 checks

September 24, 2026. Implements Task 4: the direct Steam reader now reads grouped (skin) market pages in addition to commodity pages, with run-local page reuse and direct Steam as the preferred detailed provider. See [the qualification record](DIRECT_STEAM_VARIANTS.md).

The update creates no actual funding entry, real transaction or extra paper trial. It adds no automated order placement; every real trade remains manual. Existing prediction, route, paper, balances, scheduling, pacing and manual-trading rules are unchanged.

## What changed from 1.0.5

- Grouped listing pages parse to an **exact variant** order book. A requested title is resolved against the page's own bucket list; the fallback bucket's book ships embedded in the page, and every other bucket's book comes from one standalone `/market/orderbook` call. Bucket filter pairs confirm identity only and never build a request URL.
- `LIMIT` is 8 MiB on both encoded and decoded bodies, against a ~4.2 MB observed maximum.
- Five named grouped-parser errors (`title_not_in_listing_group`, `unsupported_item_quality`, `malformed_orderbook_endpoint_response`, `unreliable_orderbook_date_header`, `stale_orderbook_endpoint_cache`) are **item-scoped**, so one unsupported title no longer stops all Steam collection for a batch. The scoping set is imported from the parser rather than retyped, so a later error string cannot drift out of sync.
- `details_followup` is a registered request kind, paced, counted and journalled like any other request.
- Decoded grouped pages are reused **within one run only**, never persisted. A cached page serves a second title only after that title's own queries validate independently, and the order book is always fetched fresh on reuse, so derived captures carry current price evidence and the original page retrieval time is recorded separately in provenance.
- Direct Steam is the preferred detailed provider, including for catalogue-discovered titles. SteamApis is a fallback used only after a direct **item-scoped** failure, as a visible second request in the batch, and only when the account response confirms overage is disabled.
- A provider switch can no longer replenish already-consumed paper depth. When the winning capture's provider differs from the one that fed a route's consumed pool, `paper.step` returns `waiting_for_evidence` with a named reason instead of guessing. Routes with nothing consumed, or whose consuming history predates this release, are not blocked.
- Both order-book paths keep `book_timestamp_kind: steam_server_query_observed_at` and `underlying_market_cache_age: not_exposed`. A new `book_timestamp_source` records which mechanism produced the timestamp (`ssr_query_dataUpdatedAt` or `http_date_header`), and the follow-up path rejects an implausible or cache-rewritten `Date`.

## Automated checks

**340 Python tests pass**, 66 more than the 274 in 1.0.5. The new coverage includes:

- exact variant selection; the fallback bucket using the embedded book and issuing no follow-up; the follow-up URL built from the title alone, with bucket filter pairs absent from it;
- `title_not_in_listing_group`, `unsupported_item_quality` and not-marketable variants failing explicitly and item-scoped, with no substitution of a neighbouring bucket, and a duplicate `bucket_id` failing rather than picking the first match;
- malformed follow-up responses, a missing or false `success` flag, currency mismatch, and compact-book sort, quantity-total and best-price failures all failing closed;
- all five `Date`-header outcomes including the 120-second boundary and a naive `Date` treated as UTC, plus timestamp-label parity across the commodity and grouped paths;
- run-local reuse: two titles in one family costing one page request, per-title re-validation before a cached page serves a second title, original page retrieval time preserved across reuse, capacity eviction, a fresh batch starting cold, and no cache object reaching the journal;
- catalogue-discovered titles resolving through the watchlist Steam source, per-title `item_sources` overrides still winning, the SteamApis fallback firing only on item-scoped failures and only behind the free-access check, and item scope keys including the provider;
- provider-switch blocking: a blocked step recording no route event, paper book entry or decision; the same provider advancing normally; nothing-consumed and pre-release histories not blocked; and the reason reaching `worker_health`.

The commodity path is unchanged and its existing fixtures still pass. The 8 MiB bound, the redirect guard's exact `/market/orderbook` path match, and archive hygiene across the follow-up are covered directly.

A further 6 tests (`tests/test_steam_live_qualification.py`, 346 total) cover the pre-release live-qualification script itself, added when three reporting bugs in it were fixed — see [Limits and next work](#limits-and-next-work).

## Live qualification

A bounded, anonymous, read-only run against real Steam market data on 2026-09-24, recorded in `data/steam-probes/20260924T151835Z-live-qual-1b79a720.json`. Background collection was paused throughout. **12 HTTP requests** against a budget of 30, at the configured 5-second spacing. No go/no-go condition fired and the run completed.

| Check | Result |
| --- | --- |
| Real grouped families | `AK-47 \| Slate` (15 buckets, fallback `Factory New`, 40 queries of which 31 retained) and `AK-47 \| Redline` (10 buckets, fallback `Minimal Wear`, 30 queries of which 21 retained). Both decoded within the 8 MiB bound, each yielding exactly one SSR loader and render-context match. |
| Exact variant books | Seven marketable wears across the two families produced validated exact-variant books, all with multi-level depth surviving into `prediction._steam_book`. |
| Timestamp mechanism | Fallback buckets used `ssr_query_dataUpdatedAt`; every follow-up used `http_date_header`. No `unreliable_orderbook_date_header` or `stale_orderbook_endpoint_cache` fired in normal operation. |
| Unsupported categories | `StatTrak™ AK-47 \| Slate (Battle-Scarred)` and `Souvenir AK-47 \| Slate (Battle-Scarred)` both failed `unsupported_item_quality`. The agent `Sir Bloody Miami Darryl` failed `invalid_or_unsupported_steam_page`. All item-scoped, as designed in D2. |
| Commodity control | `Fracture Case` parsed unchanged, with `ssr_query_dataUpdatedAt` and multi-level depth. |
| Reuse economics | 2 grouped page requests and 6 follow-ups served 9 variants, with 5 cache hits. 10.18 MB transferred against a 22.81 MB counterfactual of one page load per variant — **12.63 MB saved**. |
| Production journal | Verified untouched before and after the run. |

## Limits and next work

The `20260924T151835Z` report above already reflects the run that was asked for. Three things
it recorded were wrong or missing because of bugs in the qualification script itself, not in
the direct-Steam reader being qualified. All three are now fixed in
`scripts/steam_live_qualification.py` and covered by `tests/test_steam_live_qualification.py`
(6 tests, 346 total in the suite); none required a new run against Steam to fix, since the
script's own logic, not its network use, was at fault.

- **Retained cache weight.** The 48-bytes-per-entry the report recorded was `sys.getsizeof()` on the bare `GroupPageEntry` shell — the call site in `summarize_cache` never invoked the script's own recursive `deep_sizeof` walk. That call site is now correct. A reconstruction built through the real `GroupPageCache.put` path, matching the qualified page's own shape (15 buckets, 25,359 retained history points), measures **~6.9 MB per entry, ~28 MB for the cap of 4** — consistent with the `GroupPageCache` docstring's existing ~6–8 MB / ~25–30 MB figure, so that docstring is unchanged. The `20260924T151835Z` report's own 48-byte figures remain uncorrected artifacts of the pre-fix script; the next live run will record the real number directly.
- **Per-title book levels and failure scope.** `build_title_report` now records `failure_scope` for every title with an error (`item`, matching decision D2, for both `unsupported_item_quality` cases and the agent's `invalid_or_unsupported_steam_page`), and explicit `buy_levels` / `sell_levels` / `multi_level_depth` fields (null on failure) for every title rather than omitting them silently. The existing report predates this fix, so its title rows do not carry the new fields; the underlying per-title depth counts for the seven successful captures were already present (`buy_levels`/`sell_levels`) and are unaffected.
- **The `busy` precondition.** `preconditions` now records `busy_verified_via`, explicitly distinguishing a live `/api/status` check (`web_status_endpoint`) from the accepted substitute used when the desk isn't running (`accepted_substitute: worker_paused_and_desk_unreachable`) instead of a silent `false`. The `20260924T151835Z` run used the substitute; that remains correct evidence, just now labelled explicitly instead of implicitly.

D3's residual question — whether `research_batch_size` (300) or `request_spacing_seconds['steam_public']` (5 s) should change — is deliberately left open. One run over two families is not a coverage sample, and both values stay as they are.

StatTrak, Souvenir and agent pages remain unsupported by decision, not by defect; Steam marks those pages not marketable and chasing them is out of scope. CSFloat, other games and wider qualified item coverage remain later work. M7 economic proof is still open, and the existing paper trial remains unfinished with its original settings and prediction. No automated loss limit or calendar-based evaluation was added.
