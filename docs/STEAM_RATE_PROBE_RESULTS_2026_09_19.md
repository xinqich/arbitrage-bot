# Steam request experiment — 19 September 2026

## Result

The selected bounded experiment completed without an observed restriction. It does not establish Steam's exact rate limit.

| Requested interval between item checks | Successful checks |
| --- | --- |
| 30 seconds | 5 / 5 |
| 15 seconds | 5 / 5 |
| 10 seconds | 5 / 5 |
| 5 seconds | 5 / 5 |
| 2 seconds | 5 / 5 |
| 1 second | 5 / 5 |

- Started 2026-09-19 13:20:11 UTC; finished 13:25:27 UTC (16:20–16:25 local).
- 30 item checks, each with one HTTP 302 redirect followed by HTTP 200: 60 actual HTTP requests.
- All final pages passed the bot's existing price parser. No 401, 403, 429, challenge or parser failure occurred.
- No response included Retry-After. Because no 429 occurred, this run tells us nothing about what Steam would send on a 429.
- Requests were sequential. In the final stage, actual intervals between check starts were approximately 1.02–1.27 seconds, limited by response time. Redirect requests occurred within each check.
- The same four configured cases were checked in rotation: Fracture, Recoil, Revolution and Kilowatt.
- Background collection was paused for the experiment and restored to its existing schedule, without an extra scan. Its next check remained 21:29:58 local. Production pacing, providers and limits were not changed.

## Interpretation

Thirty seconds was not needed for these particular requests during this short test. That supports testing a faster pace; it does not prove one-second collection is safe over hours or over hundreds of different items. Repeated pages, caches, endpoint differences, other traffic and longer counting windows remain untested. A rate probe measures what happened in its sample, rather than discovering a permanent exact threshold.

Any production change should therefore be a separate decision, with automatic stopping/backoff retained. This experiment stopped at the user-selected final stage; it did not keep increasing traffic to force a restriction.

## Validation and records

- All 228 automated tests passed, including six probe tests for timing, budgets, stopping, redirects, safe headers and Retry-After parsing.
- All 6,871 original journal records were preserved exactly. Only 60 diagnostic request attempts, two collection-control events and one schedule-preserving worker-health record were appended. No evidence captures, route events, transactions or outcomes were added.
- Diagnostic requests count against the existing Steam and overall allowances: Steam public count increased from 150 to 210; overall count from 535 to 595.
- [Raw request report](../data/steam-probes/20260919T132011Z-a4e1f479.json).
- [Backup and preservation check](../data/backups/steam-probe-9d392abea07d/experiment-result.json).
- [Probe instructions](STEAM_RATE_PROBE.md).

HTTP specifies Retry-After as optional on 429: [RFC 6585, section 4](https://www.rfc-editor.org/rfc/rfc6585#section-4). Missing Retry-After must not be interpreted as zero waiting time.
