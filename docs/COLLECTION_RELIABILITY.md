# Collection reliability — task 1

Release: 1.0.3, September 19, 2026.

## Changes

- Catalogue state no longer replaces worker control state. Repeated catalogue scans finish and schedule their next check instead of stopping with worker_KeyError.
- Unsupported item pages, item-specific HTTP errors and malformed item responses stop only that item/request kind. Access denial for an endpoint stops that endpoint. Credentials, throttling, connection failures and server failures retain provider-level handling. Saved legacy failures are narrowed where their recorded request identifies the scope.
- A failed fetch no longer erases an earlier successful quote in the same recorded/synthetic evidence partition. Its original evidence ID, observation time and expiry are retained. An expired quote still fails validation. A successful newer empty or invalid book supersedes old prices; the selector never resurrects old quantities in its place.
- Indexed searches and direct entry/paper lookups use the same selection rule. Search snapshots report collection_warnings separately. Debug identifies the affected item or endpoint; failed captures remain in history.
- Steam chart history is optional. Missing, failed, malformed, non-USD or inconsistent chart history becomes unavailable/invalid while a separately verified USD order book remains usable. Identity, order-book timestamps and quantity validation are unchanged. Unknown history is not zero sales.

## Validation

222 automated tests pass, including repeated scans after restart, scoped failures, legacy stops, unchanged original predictions, indexed/direct quote agreement, expiry, evidence partitions, empty books, missing history, and no replenishment of consumed paper depth after fallback. The suite includes local HTTP workflows and the JavaScript DOM contract check; JavaScript syntax was also checked.

An isolated copy of the current journal is checked before installation, with three mocked scans and no external market requests. Installation retains a backup of the journal, public configuration and replaced files. Saved route/funds records and original journal rows must remain unchanged.

## Scope

This completes only task 1. Collection cadence, per-run budgets, lifetime allowances, broad Steam skin parsing, bulk screening, hourly checks and daily candidate rules are not changed by this release. Those belong to later tasks. Existing pauses are preserved; installation clears only the known repaired worker_KeyError stop when present.
