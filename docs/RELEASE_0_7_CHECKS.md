# Version 0.7 checks

September 15, 2026. Current work focuses on DMarket growth; CSFloat entry and withdrawal work is postponed by the user.

- The page now supports Search -> review prices/fees/waits/funding -> start a paper growth route. Opening it records a simulated purchase, holds the original prediction and freezes the selected comparison pair. It places no real order.
- Entry rechecks fresh recorded evidence, positive net arithmetic after declared costs, available paper money, supported account restrictions and unused offer IDs. Database transactions prevent partial entry records and competing entries spending the same funds. A repeated identical entry request is safe.
- Search excludes offers already used by paper entries. Earlier trials without exact entry fills conservatively exclude their original observed offer pool, without rewriting old records or inventing historical purchases.
- Qualified capsules and ordinary skins can complete the same paper lifecycle. Their resolved outcomes refine only their own family and mode. The original Fracture trial retains its original prediction and four-case basket.
- 127 automated tests pass, including new entry, concurrency, rollback, evidence, offer reuse, account-type, noncase lifecycle/learning and local HTTP cases.
- JavaScript syntax passes. Visual browser appearance remains untested; no real trade or new paper trial is created during installation.

Publication backs up the journal and previous files and verifies existing journal rows remain unchanged. The backup and checks are recorded in `data/last_release.json`. This is engineering progress, not proof of profitable routes.

## Installed update

Installed at 16:45 UTC on September 15. The page, JavaScript, CSS, status API and this report returned HTTP 200 after the managed restart. All 3,877 pre-existing journal rows, the original route/settings and saved controls were verified unchanged. SQLite integrity was `ok`. No new paper trial or market request was created by the update.

The live review API was also checked against the latest saved search: it correctly refused entry because those observations were older than the allowed freshness window. Reviewing it changed neither routes nor the request count. Controlled tests cover the successful entry path; no extra trial was opened in the real journal for testing.

Backup: `data/backups/release-0.7-20260915T164513Z/`. The worker is running unpaused. The original trial still holds 17 Fracture Cases. Its paper eligibility check remains 17:46:29 UTC (20:46 local); the next broader collection is 18:17:43 UTC (21:17 local). No economic outcome is established by these checks.
