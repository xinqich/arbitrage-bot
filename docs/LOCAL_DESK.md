# Local desk guide

Open http://127.0.0.1:8765 or **Arbitrage V2 Local Desk** on the desktop. Closing the browser leaves collection running. Windows sign-in starts the worker; a deliberate Pause stays in place across restart.

## Overview

The collection bar shows its state and next check, with Pause/Resume and Check prices. The cards show real DMarket money, the full recorded Steam wallet, purchase cost of items held in active real routes, real/paper route counts and the shared request allowance. Fund cards open Funds; route counts open the matching Routes view.

Real and paper money are never added together. Reserved money is still in the DMarket wallet and is not invested item cost. Steam credit and item purchase costs are not a withdrawable cash valuation. A missing wallet record is shown as Not recorded, not zero. CSFloat remains a placeholder excluded from the remainder total.

## Routes

Choose **Real** or **Paper**. The selector controls active routes and the search's funding pool. Your choice is remembered. The page retains the latest search separately for each mode.

Select an active route to see its current step, original prediction and actions. **Record a real operation**, **Update / resolve route**, and **Correct a recorded operation** open only when requested. Use the actual-operation form for ordinary real receipts: it saves the money, items and stage together. General money updates use whole cents; ordinary receipt forms use USD. Pause automatic paper steps before manual paper updates.

Search recorded prices shows the best quantity for each pair and selling-method combination. Select a row for the v1-style detail view, fees, links and other quantities. All table amounts are totals for the displayed quantity. Sell total B is before fees; Sell net B is after fees. Predicted Profit includes any learned adjustment; the detail shows the unadjusted arithmetic separately. ROI is predicted profit divided by the initial purchase cost.

Steam and DMarket each support highest orders, lowest ask and midpoint estimates. Highest-order estimates walk observed depth. A midpoint is the best bid plus lowest ask divided by two, rounded down to whole cents before per-item fees. Lowest-ask and midpoint estimates assume a later sale; they do not prove demand or a fill. Missing history never excludes an otherwise priceable route.

**Start real trial** saves a plan and reserves funds. It places no order; record actual purchases and sales after doing them manually. **Start paper trial** uses the shared paper pool, and remains limited to supported buy-order scenarios. Lowest-ask/midpoint estimates explain why automatic paper entry is unavailable. The old Fracture trial retains its original rules and prediction; unavailable older price-breakdown fields say Not recorded.

Only fully resolved routes show P&L and prediction errors. Pending operations and leftovers require your decision. Closed results stay frozen; linked recovery preserves the original outcome. See [real operations](REAL_GROWTH_WORKFLOW.md).

## Funds

Balances appear first. Open the named actions below them to record changes, see history or make corrections.

- DMarket opening funds are recorded once. Later additions/withdrawals are changes, not full balances. A restriction release moves money between Tradable and Regular. Route sales belong to the route.
- Steam **Current full wallet balance** records what Steam actually shows at that date and time. It includes all transactions up to that time. Later recorded real Steam transactions update it automatically; unrelated additions or spending use **Outside change**, with a negative amount for spending. A later full balance reconciles the wallet again.
- Steam paper activity and internal recovery reassignment never change the full real wallet. A write-off does not pretend that leftover wallet credit was physically spent. Corrections use the effective receipt amounts. This wallet display does not give a route permission to spend unrelated Steam money.
- **Invested in active routes** is the recorded purchase cost of items still held. Purchases combine at weighted-average cost; partial sales remove their share of cost, and the last sale clears the remaining cents. Return-item cost is the Steam credit spent. Unmatched older manual records show an unknown cost. Recovery does not charge the old cost twice.
- **Receipt ID or note** accepts a receipt number or a label such as `Opening balance — 16 Sep`. It remains unique to help prevent double counting; it does not independently verify a marketplace receipt.

## Controls and Debug

Controls contains pause/resume/check and **Create backup**. Debug holds evidence health, source errors, provider request usage, observation gaps and worker logs. Guides and reports are also collapsed here. A short Overview notice points here when collection needs attention.

Research runs every six hours, with additional checks at paper eligibility times. Open routes get priority within saved total/provider allowances. Connection/server errors have bounded retries; authentication, format and exhausted-allowance errors need review. Missing time remains a gap. Restart after changing configuration or local keys.

History: `data/research.sqlite3`. Settings: `config/`. Process and log paths: `data/local_process.json`. The launcher stores only a credential-file path, not credential contents. The server binds to 127.0.0.1; actions require a local session. See [backup/restore](BACKUP_RESTORE.md) and [UI release checks](UI_REWORK.md).
