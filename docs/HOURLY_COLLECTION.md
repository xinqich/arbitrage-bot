# Hourly collection — task 2, version 1.0.4

The background worker checks once an hour. Regular times stay fixed when a manual or unlock check runs. If the PC was off, it performs one overdue check; it does not replay the missing hours. If a run crosses a regular time, that missed run is skipped, with no overlap or queued catch-up run.

## Controls

All controls are in `config/local.json`; restart the local desk after editing.

| Setting | Default |
| --- | --- |
| `collection_interval_seconds` | 3600 |
| `research_batch_size` | 300 distinct variants, separate from active-route checks |
| `research_refresh_seconds` | 3600 |
| `freshness_seconds` | 14400 |
| `request_spacing_seconds.steam_public` | 5 |
| `request_spacing_seconds.dmarket` | 2 |
| `request_spacing_seconds.steamapis` | 30 for the existing optional service |
| `max_run_seconds` | 3600, including active checks and calculation |
| `retry_seconds` | 60, 300, 900 |
| `rate_limit_cooldown_seconds` | 900 when Retry-After is missing/invalid |

The spacing choices are not proven safe Steam limits. The worker conservatively waits from the previous response's completion. Steam redirects also pass through the same wait and count as requests. Pause and shutdown interrupt waits; HTTP timeouts and streaming reads use the remaining run time. The deadline also interrupts background route calculations; interrupted searches do not replace the last complete report.

Research selects items that would miss their refresh target before the next regular run. This avoids skipping every second hourly check because the last quote arrived a few seconds after the previous run started. A refresh target does not change the four-hour evidence validity rule, or guarantee provider access. Existing paper trials retain their frozen evidence rules and original predictions.

Enabled paper routes and unresolved real routes get their current-step checks first. Real routes only receive prices: purchases, sales and receipts remain manual. Unlocks reached during research are checked before further research requests. The research count is distinct item identities, not HTTP requests; each can need Steam details and two DMarket requests. Active requirements are outside that count, but inside the same time limit. Unfinished candidates stay eligible.

## Provider failures and usage

- A 429 ends that provider's remaining work for the run. Retry-After supports seconds and HTTP dates. A valid long cooldown is not shortened to fit the next hour.
- Cooldowns and last-request times are saved as requests happen. Restart and manual checks cannot bypass them. Healthy providers remain available.
- Connection and HTTP 5xx errors get up to three scheduled retries. A retry outside its original run's time window is not scheduled. Once that sequence ends, the next regular research run can try again.
- Access failures stop the affected provider/endpoint; unsupported item failures stay item-specific. A manual check can retry access/format blocks, while respecting cooldowns and pending retries.
- Old `request_budget`, `steam_budget` and watchlist lifetime allowances do not stop the worker. They are removed from the shipped files. Request attempts remain statistics; the page shows **Requests made**. Existing status keys for allowances remain, with `null` meaning no local lifetime cap.
- The old one-shot `collect --request-budget/--steam-budget` arguments still bound that explicitly requested invocation. That command shares pacing, cooldowns and the duration limit, and cannot bypass the local worker's OS collection lock.
- No billing setting is changed. Before using optional SteamApis details, collection checks that the account explicitly has overage billing disabled. Unknown/enabled overage prevents that optional source from running; other sources continue. Collection no longer infers a free quota from the old unrelated 500-request subscription calculation.

This task does not replace the catalogue provider or broaden direct Steam parsing. Some discovered items still require SteamApis access. A 300-item ceiling therefore does not mean 300 successfully priced items, or complete market coverage. Daily reconsideration rules and the remote collector remain future tasks.

## Verification

Controlled tests cover the defaults, 300 research variants plus active needs, continued collection beyond 1,000 historical attempts and beyond the old 15/5 cycle caps, spacing across endpoints and restarts, redirect spacing, interrupted waits, hourly anchors, overdue startup, concurrent ticks, long runs crossing regular times, unlock checks during research, persisted 429 cooldowns, bounded retries, and real-route price checks without transactions.

All 249 automated tests passed, including 21 new controlled scheduling/transport checks. JavaScript syntax passed. Three mocked scans on an isolated copy made 1,446 test requests and preserved all 6,989 original rows, recorded funds, route state and predictions; SQLite integrity passed. No market requests were made for those tests. Installation makes a database, configuration and changed-software backup first.
