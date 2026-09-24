# Implementation status

Updated 2026-09-20. Version 1.0.5. Governed by [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md).

| Milestone | Engineering status | Evidence still needed |
| --- | --- | --- |
| M0 | Current design, schema-4 balances, explicit legacy conversion and backup/replay checks implemented. | No change to the existing route or its original prediction. |
| M1 | Full-level quantity calculations, per-item fees, source levels, deduplication, budget-aware alternatives and missing-cost handling implemented. | Discovery audit complete for the current CS2 roster: unrelated books no longer block a leg; listing-price assumptions are separate from bid-depth estimates; missing history does not exclude candidates. Automatic listing-paper execution remains unimplemented. Real funds/workflow are implemented in M6; see DISCOVERY_FILTER_AUDIT.md. |
| M2 | Forward paper steps, partial sales, fresh return choices, unlock waits, shared consumed depth and restart-safe transactions implemented. Collection now prioritizes the books needed by each current step; unrelated feed failures do not block it. | Existing live paper trial has not completed. Residual decisions stay manual. |
| M3 | Local page, background worker, Windows launcher/startup shortcuts, saved pause, local action security and visible health implemented. Source-specific stops/retries and targeted checks persist without postponing research or replaying consumed control commands. | Windows restart itself cannot be simulated as proof of a future sign-in; the saved startup shortcut is inspected. |
| M4 | Read-only CSFloat listing capture, conservative per-item fees, explicit payout-cost function and separate account types implemented. | Deferred by the user. The start route is manual; withdrawal work resumes when requested. Live contracts/payout facts are still unqualified, but no longer block current development. |
| M5 | One capsule and one ordinary skin passed initial live source/identity checks; bounded rotating exploration implemented. The page can review and enter a new paper trial in either qualified family; entry money, offer IDs and the selected return pair are recorded together. Resolved noncase paper outcomes can refine that family's later estimates. | More items/games and ongoing source reliability require qualification. SteamApis lower-level depth stays unsupported. |
| M6 | Manual event forms, immutable corrections, destination-specific closure, pending-fund checks, linked recoveries and learning partitions implemented. | Version 0.9 implements real starting funds, additions/withdrawals/restriction releases/corrections, account-specific commitments, real-budget search, confirmed route plans and atomic manual receipt forms including listing scenarios. Local HTTP full-cycle and backup-restore tests pass. The consolidated v1.0 software acceptance review is complete; actual money and receipts are still user-supplied, with no real funds entered by this release. |
| M7 | Completion criteria saved. | No completed real growth cycles or actual withdrawal proving net growth. Economic milestone remains unproven. |
| M8 | Planned for v1.1: automatically selected independent paper alternatives using the current or saved pre-entry reference budget. | Not implemented and not required for v1.0. Existing trials still share their paper funding pool. |

Validation: 274 automated tests pass in v2's Python environment. These include the existing fee audit, legacy CLI/evidence checks, multi-level quotes, controlled-clock complete paper cycle, unchanged-book/partial-sale/restart behavior, stale evidence, original asset-key compatibility, leftover stops, local HTTP access and action checks, manual corrections, source contracts, CSFloat payout cents, pending transfers and recovery double-count protection. JavaScript syntax and PowerShell parsing are checked separately. The new worker tests cover source isolation, frozen-basket requirements, pre-unlock/stale quotes, shared limits, request priority, pause during requests, independent retries and restart after unexpected errors. Browser appearance and optional WebMCP runtime behavior have not been visually/runtime tested.

At the September 15 19:14 UTC read, `paper-fracture-kilowatt-20260908` had advanced through the normal background checks to 52 Kilowatt Cases in the return-item wait. Its original prediction remains frozen; no outcome is recorded while it is unfinished. The separate paper CSFloat $10 experiment has no entered route. No new paper trial, confirmed transaction, investment, payout or completed real outcome is created by installing this release.

The new local worker replaces the old managed collection process, shares its OS lock and journal, and continues when the browser closes. Collection resumes after sign-in unless deliberately paused. The first economic milestone cannot be passed by code or positive present-price arithmetic.

See [LOCAL_DESK.md](LOCAL_DESK.md), [NONCASE_QUALIFICATION_2026-09-14.md](NONCASE_QUALIFICATION_2026-09-14.md), [CSFLOAT_FEASIBILITY_2026-09-14.md](CSFLOAT_FEASIBILITY_2026-09-14.md), and [STEAM_FEE_AUDIT.md](STEAM_FEE_AUDIT.md). V1 remains unchanged.


Version 1.0 closes the operational recovery gap: a checked backup button, backup/check/restore commands, a safety copy before replacement, and paused restart after restore. New checks cover damaged/partial copies, incompatible settings/version, live-worker exclusion, immutable real-plan replay and preservation of newer history. Package, API and page version labels agree. CLI search can explicitly use confirmed funds. Windows shortcut creation now selects the Windows PowerShell executable even when invoked from PowerShell 7.

See [v1.0 acceptance](RELEASE_1_0_CHECKS.md), [real operations](REAL_GROWTH_WORKFLOW.md), and [backup/restore](BACKUP_RESTORE.md). No actual funds or transactions are invented by this release. The existing paper trial continues under its original rules. M8 automatic independent paper trials is the next planned software milestone for v1.1; M4 CSFloat remains deferred and M7 real economic proof remains open.

Earlier installation records remain in RELEASE_0_5_CHECKS.md through RELEASE_0_9_CHECKS.md and data/backups. They describe their own dated states, not current instructions.


Version 1.0.1 implements the approved UI rework, midpoint estimates at both exits, held-item purchase costs and full Steam wallet records. Current recorded DMarket funds and the older paper trial are preserved; missing old quote details are not invented. See [UI rework and checks](UI_REWORK.md).


Version 1.0.2 completes broad CS2 category support and priority-based search. DMarket catalogue pagination works; both SteamApis catalogue endpoints returned HTTP 403. Discovery uses the working fallback with partial coverage shown, within unchanged budgets. See [CS2 search expansion](CS2_SEARCH_EXPANSION.md). This supersedes M5's earlier two-item roster limitation, not M7 economic proof or M8 automatic trial selection.


Version 1.0.3 completes collection reliability task 1: fixes the catalogue worker crash, isolates item/endpoint/provider failures, preserves unexpired quotes after failed requests, and makes Steam chart history optional. Existing cadence and request limits remain unchanged pending task 2. See [collection reliability and checks](COLLECTION_RELIABILITY.md).


Version 1.0.4 completes task 2: hourly scheduling, up to 300 research variants, configurable spacing and duration, restart-safe cooldowns, no overlapping/catch-up runs, and no old lifetime/cycle request stops. Active real routes receive priority evidence checks without automatic transactions. The optional SteamApis source requires a successful check that overage billing is disabled; its old inferred quota is not used by collection. Saved predictions and existing route rules are unchanged. See [hourly collection and checks](HOURLY_COLLECTION.md).


Version 1.0.5 implements Task 3: the CSGO Trader name catalogue, separate screening storage, per-source ageing, ten-page DMarket traversal and balanced outward/return/exploration selection. Public Steam summary prices stay disabled because their buyer-fee meaning could not be verified; exact names remain useful for exploration. Fresh detailed prices and valid DMarket hints can still rank candidates. Direct Steam parser expansion remains Task 4. See [screening implementation, qualification and checks](CSGOTRADER_SCREENING.md).


September 22 repair: the repository reset retained the screening modules but lost their worker, request dispatch, settings, catalogue and page integration. Those Task 3 connections are restored from the verified September 20 release. Task 4 parser and provider-switching work remain excluded. Validation for this repair is recorded with the installation backup.


Version 1.0.6 (currently v1.0.5.4; release ritual not initiated yet) completes Task 4's direct Steam reader extension. The anonymous reader has bounded encoded/decoded gzip handling, a versioned sanitized SSR envelope, exact grouped-bucket validation, one observed-filter follow-up, item-scoped stable parser detail errors, and run-local validated grouped-page reuse. Direct Steam is primary; SteamApis remains a separately verified no-overage fallback. The fresh qualification matrix and limits are recorded in DIRECT_STEAM_VARIANTS.md. Existing prediction, route, paper, balances, scheduling, pacing and manual-trading rules are unchanged.
