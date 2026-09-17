# Version 0.9 checks

September 16, 2026. Implements real DMarket funding, committed budgets, real-funds search and manual growth-route tracking from the local page. See [the workflow](REAL_GROWTH_WORKFLOW.md).

The update creates no actual funding entry, real transaction or extra paper trial. Real money stays unset until the user records it. The separate start route and CSFloat work remain manual/deferred.

## Automated checks

157 Python tests pass, including 15 new real-growth tests. They cover:

- no automatic conversion of paper money into real funds; one opening record; additions, regular withdrawals, restriction releases and append-only funding corrections;
- exact preserved predictions and account-specific commitments, concurrent plans competing for one budget, partial initial purchases, explicit release of unused commitments and cancellation without a trading outcome;
- actual receipts at changed prices and after old quotes expire, receipt retries, all-or-nothing money/item/step recording, actual unlock constraints, fee accounting and prevention of double-counted outside funding;
- a complete local HTTP workflow from initial funds through search, listing-estimate review, real plan, partial Steam sales, return purchase, DMarket receipts and final closure, protected by the local session and Origin checks;
- no open-route P&L, explicit leftovers/write-offs and pending-operation checks, valid and invalid corrections, closed-result protection and learning separated by real/paper mode and prediction engine;
- restoring a database backup with identical real balances, route state and original prediction.

The existing no-history discovery, fees, paper lifecycle, source-contract, scheduling, pause/restart, request-limit, duplicate and correction tests also remain in the suite. JavaScript syntax and HTML/JavaScript identifier checks are run separately. A copied live journal is used for local HTTP and old-trial preservation checks without adding test funds to the real project or contacting markets.

## Installation

The managed installation backs up the journal and changed files, checks their original hashes, restarts the local worker, and verifies that every earlier journal row, the original trial, controls and request counts remain intact. Concrete results and the backup path are recorded in `data/last_release.json`.

This is version 0.9, not a declaration of v1.0 or proven profit. The final v1.0 acceptance review remains: consolidate the release checklist, review the complete operator flow and its limitations, and verify recovery/operation instructions against the installed build. Browser appearance and optional WebMCP behavior have not been visually/runtime tested. No completed real trade is required just to finish that software acceptance review; M7 economic proof remains separate.
