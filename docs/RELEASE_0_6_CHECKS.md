# Version 0.6 checks

September 15, 2026. This release continues M2/M3 in the development plan.

## Problem and change

The previous worker required every core Steam/DMarket capture to succeed before calling the paper engine. That could block an outward Steam sale because unrelated DMarket evidence failed. A failed source could also end collection before a healthy source was reached. Unexpected exceptions lost consumed control IDs, allowing the same Resume command to trigger repeated attempts.

The worker now requests each open route's necessary books first, shares duplicate requests across steps/routes/research, and leaves source failures separate. Temporary failures get bounded retries; other failures remain stopped until manual review and retry. The original paper engine still validates freshness, post-unlock source time, evidence partition, identity, all return-basket titles and available depth. No missing quote is filled in or ignored to produce a profitable choice.

Targeted checks preserve the next six-hour research time. The total, Steam and provider allowances are unchanged. Paper completion, residual handling and outcome learning rules are unchanged.

## Validation

- 113 automated tests pass, including existing fee, lifecycle, provenance, accounting, recovery and local HTTP checks.
- New cases cover independent source failures, one-request DMarket exits, complete return-basket requirements, fresh retrievals with pre-unlock quotes, priority with a tight budget, duplicate requests, persistent provider/total limits, pause during a request, bounded retries across restart, successful recovery, missing credentials and consumed control IDs after exceptions.
- JavaScript syntax passes. The page reports source stops/retry times, skipped checks, the broader research time and missing paper-step evidence using text nodes.
- Publication backs up the journal and changed files, checks for concurrent source edits, and verifies pre-existing journal rows remain unchanged. The manifest in `data/last_release.json` records backup and verification details.

The code and offline tests do not prove profitable trading. The existing recorded paper trial is still awaiting its first Steam sale eligibility at September 15, 17:46:29 UTC. CSFloat live qualification still requires authorized account access and payout facts. No real transaction is placed by this release. Browser appearance has not been visually tested.

## Installed update

At 10:03 UTC, the managed worker was restarted with version 0.6. The local page, scripts, styles, status API and this report returned HTTP 200. All 3,838 pre-existing journal rows were checked unchanged, including the original prediction, paper settings and three trial events. SQLite integrity was `ok`. The saved controls were retained, and no market request was spent during the update.

Backup: `data/backups/release-0.6-20260915T100311Z/`. The worker is running unpaused. The next broader collection remains September 15 at 12:17:34 UTC (15:17 local), with the separate paper eligibility check at 17:46:29 UTC (20:46 local). The trial still holds 17 Fracture Cases, with no resolved outcome.
