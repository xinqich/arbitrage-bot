# Version 1.0 acceptance checks — 2026-09-16

The v1.0 software requirements in DEVELOPMENT_PLAN.md are complete. This is the local DMarket growth tool with manual real trades and manually opened paper trials. It can suggest conditional positive-net routes before a sales history or completed trial exists. It does not establish reliable profits; M7 requires actual reconciled outcomes.

## Acceptance evidence

| Requirement | Check and result |
| --- | --- |
| Funds and complete manual growth route | The local HTTP workflow records actual starting funds, searches with real available funds, reviews a listing estimate, opens a plan, records outward purchase, transfers, partial Steam sales, return purchase and DMarket sale, then closes explicitly. Known fees, actual unlocks, outside funding and leftover decisions are covered separately. All pass with labelled test receipts in temporary journals. No actual purchase is needed for this software check. |
| Opportunity discovery | Separate current-bid and listing-price estimates, quantity/depth checks, missing prices, no-history positive opportunities, uncalibrated duration, qualified noncase identities and original evidence links are covered by the existing discovery and entry tests. No new history threshold, invented probability or liquidity cutoff was added. |
| Money and outcome correctness | Account restrictions, concurrent commitments, duplicate retries, atomic failed operations, corrections, pending operations, cancellation and write-offs are tested. Frozen predictions remain unchanged. P&L and price/cost/time errors appear only after resolution; learning stays in the matching real/paper/source/engine group. |
| Collection and restart | Existing tests cover persisted pause, source-specific failures, bounded retries, request allowances, priority for open routes, unlock checks, shared depth, missed time, stale evidence and consumed control commands. Live publication checks compare all existing route/control/request/outcome records before and after the managed restart. |
| Operational recovery | New backup/create/check/restore checks cover immutable snapshots, excluded credentials, altered files, incompatible software/settings, incomplete copies, running-worker refusal, safety copies of newer history, paused restart without market calls, and real plan/receipt/prediction replay. The existing live experiment is also restored into an isolated copy for comparison. No active live journal is rolled back. |
| Local page | JavaScript and PowerShell syntax checks, HTML identifier/form checks, local-only session protection, HTTP assets/reports, and the complete HTTP manual workflow pass. The new backup button calls a session-protected endpoint with a server-chosen local path. Version labels agree. |

The consolidated Python suite has **165 passing tests**. It includes the full prior suite plus eight backup/restore checks. Details of the copied-journal and installed checks are saved in the release's `data/last_release.json` and matching backup manifest. Installation preserves the existing journal row-for-row and creates no trades, funding or new trials.

## Changes from 0.9

- **Create backup** in Controls & reports; `backup`, `backup-check` and explicit offline `restore` commands. Restoration preserves the replaced state and remains paused until the user chooses Resume.
- Updated real-operation and recovery instructions; removed obsolete current instructions about the Fracture holdings and unfinished real-funds support.
- Version 1.0.0 is consistent in package, API and page. CLI searches accept `--mode confirmed` for real funds.
- Shortcut creation chooses the installed Windows PowerShell executable even when invoked from PowerShell 7.

## Limits and next work

Visual browser interaction and optional WebMCP browser support have not been tested. The ordinary HTTP workflow is tested. The saved Windows startup shortcut has been inspected, and managed restart is tested; this is not an actual Windows reboot test. External providers can still change formats, stop serving a source or exhaust the saved allowance; the page reports those conditions.

No real starting balance or real trade has been invented for this release. The existing paper trial remains unfinished, with its original settings and prediction. Current prices are assumptions about future exits; listing prices do not prove sales. Automatic paper execution of listing estimates remains unsupported, while manual real routes can use those estimates and later record actual receipts.

Automatic selection of 5–10 independent paper alternatives is M8 for v1.1. CSFloat, other games and wider qualified item coverage remain later work. M7 economic proof is still open. No automated loss limit or calendar-based evaluation was added.
