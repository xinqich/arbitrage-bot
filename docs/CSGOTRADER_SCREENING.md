# Broad screening — Task 3, version 1.0.5

CSGO Trader adds item names to the research catalogue. DMarket pages and usable detailed prices help choose which items to check. Screening records are separate from order books, sales activity and route predictions.

## Provider qualification, September 20, 2026

The public `https://prices.csgotrader.app/latest/steam.json` returned HTTP 200 with 34,413 exact names. The new reader handled its gzip encoding and selected 300 names in 0.735 seconds, including the network request, in an isolated journal. The response's Last-Modified was saved as September 20, 05:11 UTC; the check completed at 09:11 UTC. These are a dated sample, not a promised count or update schedule.

The [current provider definitions](https://github.com/gergelyszabo94/csgo-trader-extension/blob/master/extension/src/utils/static/pricing.js) identify `last_24h` and `last_7d` as Steam sales-history periods. The [consumer](https://github.com/gergelyszabo94/csgo-trader-extension/blob/master/extension/src/utils/pricing.js) applies the selected exchange rate directly to the values; the public exchange-rates file uses USD = 1. Values are USD major units, not cents.

The [last public producer](https://github.com/gergelyszabo94/csgo-trader-extension/blob/28e571c9aa851677adf512eb4ed6c42ae9c02f51/backend/priceScraper/priceScraper.py), in `fetch_steamapis`, copied the legacy SteamApis `prices.safe_ts` fields into `steam.json`. That historical code does not establish the current producer or whether those prices include buyer fees. The separate CSGO Trader price algorithm is not the definition of `steam.json`. Current SteamApis v2 documentation is also not proof of the old `safe_ts` contract.

**Price hints from CSGO Trader remain disabled because buyer-fee meaning is unverified.** Its names still enter exploration, with wear, StatTrak and Souvenir unchanged. The page and API report this limitation. No average, median, order depth or seller receipt is guessed. No extra account or key is needed for this public file.

The reader records a valid positive 24-hour value per item, falling back to seven days. Missing, non-finite or invalid values remain missing. Raw USD summaries and their period are saved separately; converting them to whole cents floors only a potential screening approximation, never a final route price. Numerical use requires a reviewed provider-contract change establishing buyer-fee meaning. Tests use an explicitly artificial qualified contract to check that future path; production does not enable it through an unchecked settings switch.

## What happens in a run

1. Check active-route needs and preserve unlock-time priority.
2. Continue DMarket catalogue paging, up to ten pages total, including any catalogue retry. Stop at the end of the pass. Keep the cursor across restarts. A repeated cursor stops paging and records a warning; the next run can start a new pass.
3. Check the public Steam file each regular hourly run. Extra manual checks within the hour reuse the current file. Send conditional headers when available. Provider errors remain independent.
4. Combine existing supported names, DMarket discoveries, public Steam names and previously detailed names. Prefer fresh validated detailed prices over summaries.
5. Repeat outward, outward, return, return, exploration. A full 300-item batch aims for 120/120/60 distinct items. If a group lacks scored candidates, fill from the remaining groups, ending with oldest-checked exploration. A missing price is not a zero or an exclusion. Names present only in CSGO Trader can be explored.
6. Request each selected item's Steam details, DMarket offers and DMarket orders together. Shared requests are deduplicated. Checks that reach a route unlock retain priority.

Outward scores approximate Steam receipts after per-item fees per DMarket dollar spent. Return scores approximate DMarket receipts after fees per Steam dollar spent. Use fresh detailed bids, then fresh detailed listings; when no detailed sale price is usable, prefer a summary bid over a summary listing. Save the source, timestamp, selected period and assumed selling method. Unknown Steam fee meaning currently prevents numerical use of its public summaries. The 120/120/60 target therefore depends on how many items have usable detailed prices.

Detailed checks still decide purchase minimums, affordability, order quantities, matching and route entry. No-history items stay eligible. Screening does not change sale methods, final ranking, fees, fills or route arithmetic. Current quotes still do not guarantee an exit after holding periods.

## Age, persistence and controls

Steam file age comes from Last-Modified, not download time. An unchanged body or HTTP 304 keeps the original publication time. Missing, invalid or future publication times disable its price hints. A successful changed file replaces the previous price map; missing old names remain in the catalogue without reusing their old prices. Failed refreshes retain the last valid file with its original age and show the failure separately.

DMarket hints age from each item's own page collection time. A newer page cannot refresh an older item's age. Both sources' screening prices expire after 24 hours. Names remain. Detailed evidence keeps its separate four-hour maximum age.

New public settings in `config/local.json` (restart after editing):

| Setting | Default |
| --- | --- |
| `csgotrader_enabled` | `true` |
| `screening_refresh_seconds` | 3600 |
| `screening_max_age_seconds` | 86400 |
| `screening_max_bytes` | 16000000, for both compressed and decoded responses |
| `catalogue_pages_per_run` | 10 |
| `catalogue_page_size` | 100, provider maximum 100 |
| `screening_selection_pattern` | outward, outward, returning, returning, exploration |
| `request_spacing_seconds.csgotrader` | 5 |

Task 2 settings still control the research count, refresh target, provider spacing, cooldowns, retries, pause and shared one-hour deadline. No allowance is increased. A provider's 429 cooldown survives restart and manual checks. A 304 is a successful check, not a failed request or a new price observation.

Screening snapshots, name additions, checks and selection explanations use separate append-only journal categories. Repeated unchanged files do not store another full snapshot. The Steam projection loads the latest price file and name additions; the DMarket projection applies only new catalogue records. Selection sorts bounded queues instead of repeatedly searching the full catalogue. `/api/catalogue` includes Steam-only names, and coverage remains explicitly partial.

## Scope and checks

Task 4 will broaden the direct Steam parser. This release keeps the current provider routing, so discovering an item does not mean its detailed prices can yet be collected. Daily 19:00/midnight rules, remote hosting and automatic trial entry are not included.

Tests cover exact variants, invalid/missing prices, per-item period fallback, unknown and stale publication times, unchanged downloads, 304, bounded gzip decoding, independent provider failures, cooldowns, pagination restart/end/loops, per-page ageing, fresh detailed-price preference, labelled bid/listing assumptions, balanced selection and 35,000-name performance. A route-calculation regression changes bulk hints while keeping detailed evidence identical and checks identical predictions. Existing fee, paper, real-funds, local HTTP, lifecycle and scheduling tests remain required.

Release qualification uses a separate copy of the live journal, verifies original rows and funds after repeated mocked scans, and makes no real market requests or transactions in that rehearsal. Installation backs up the journal, configuration and changed software before replacement. The public-file qualification above uses a different isolated journal and is never copied into live route history.

The automated suite passes 274 tests, including 25 new screening and coordination checks. JavaScript syntax passes. Three mocked runs against the live-journal copy preserve all 22,039 original records and recorded funds, with 34,413 public names available for selection; the copy passes SQLite integrity checks. This rehearsal makes 1,806 mock requests and zero real market requests. Installation details and the backup path are recorded in `data/last_release.json`.


September 22 repair: the repository reset retained the screening modules but lost their worker, request dispatch, settings, catalogue and page integration. Those Task 3 connections are restored from the verified September 20 release. Task 4 parser and provider-switching work remain excluded. Validation for this repair is recorded with the installation backup.
