# Manual route workflow

No command here sends a marketplace action. Use --mode paper for modeled outcomes and --mode confirmed only for transactions actually performed and confirmed by the operator. Synthetic input cannot become a confirmed route.

## Enter

Run a screen and select a prediction ID. route-open records the chosen original prediction and entry time. A later model cannot rewrite that prediction. For real-data routes, the prediction must already exist before the entry time.

An initial prediction may have predicted_duration_seconds=null. In that case the route view says completion time is unknown. A known delay floor is not misrepresented as an expected completion date.

## Record events

Each JSON event needs unique event_id, route_id, ISO at, kind, and a reference to the operation/evidence. References identify individual fills or movements; do not reuse one for distinct transactions. Retrying the same exact event is idempotent.

A purchase debit (net of any fees):
```json
{"event_id":"route1-purchase","route_id":"route1","at":"2026-09-08T12:00:00Z","kind":"movement","reference":"purchase-confirmation-id","account":"dmarket_regular","net_delta_cents":-55,"fee_cents":0}
```

Record the received item:
```json
{"event_id":"route1-asset-a","route_id":"route1","at":"2026-09-08T12:00:00Z","kind":"asset","reference":"purchase-confirmation-id","asset_key":"730:Fracture Case","quantity_delta":1}
```

Record the current restriction from the actual item:
```json
{"event_id":"route1-lock","route_id":"route1","at":"2026-09-08T12:00:00Z","kind":"progress","reference":"inventory-observation-id","stage":"steam_locked","next_eligible_at":"2026-09-15T12:00:00Z","note":"Example only: use the actual item's eligibility timestamp."}
```

Supported stages: entered, awaiting_transfer, steam_locked, awaiting_steam_sale, steam_wallet, return_item_locked, awaiting_dmarket_sale, awaiting_settlement.

As operations occur, record item removals/additions and net movements in steam_wallet, dmarket_regular, dmarket_tradable, or external_cost. Steam Wallet cannot be spent before funds have been recorded for that route. external_cost accepts expenses, not deposits masquerading as income.

B may differ from the original prediction: record the actual purchased item. Use the dedicated return review below to compare the basket with the Wallet funds actually recorded for this route.

Clear/update the progress restriction when observed eligibility changes. These manual timestamps are operational records; the software does not independently verify venue/account eligibility.

## Review the return item

After the outward items have been sold, record their asset removals, the actual net Steam Wallet receipt, and a progress event setting `stage=steam_wallet`. Set `next_eligible_at` to a known pending-funds/account delay, or clear it when availability has been checked. An initial forecast of Wallet receipts is not spendable recorded money.

```powershell
python -m arbitrage_v2 route-returns my-route config/watchlist.json --policy config/research_assumptions.json
```

This appends an immutable `return_review` linked to the original prediction and the route events it used. Each option shows the return item and whole-item quantity, Wallet spend/remainder, conditional DMarket receipts after its fees, source observations, and an explicit delay assumption. No option is an execution recommendation. The review uses only Steam sell listings and DMarket unconstrained Targets; missing DMarket offers or Steam buy orders do not block this return step.

The options compete for the same Wallet. They cannot all be executed together. A new review uses only the unspent balance recorded for this route; it cannot borrow another route's funds. Held items, the wrong route stage, no recorded Wallet, or a recorded delay still in progress produce explicit blockers. Finish or reconcile the current leg before generating another purchase choice.

Remaining operating costs default to unknown. Supply `--remaining-cost-cents N` when you have an estimate, including an explicit zero only if appropriate. The full-route `other_cost_cents` assumption is not charged again at this step. The field `predicted_receipts_after_remaining_costs_cents` is a conditional future receipt, not interim P&L. Residual Wallet is separate and still requires disposition at resolution.

The research policy's `minimum_return_delay_seconds=864000` is the ten-day CS2 visibility assumption for a new Steam purchase; it is not a completion forecast. Earlier policies without that field leave the delay unknown. Actual purchase restrictions and item timestamps still need manual verification. The complete-cycle duration model is not reused as a remaining-leg forecast.

`routes` shows the recorded Wallet and the latest return-review ID. Its refresh flag is set if route events change, relevant newer captures arrive, the quote freshness limit expires, or the route closes. A stored review is a historical decision aid. Collect again and rerun the review before acting; verify the actual venue price, fees, and eligibility manually.

Read a saved review with `python -m arbitrage_v2 return-review REVIEW_ID`. This displays the original recorded review; it does not refresh its prices or route state.

Record the chosen purchase as a Wallet debit plus the actual return asset addition, then update progress to `return_item_locked` with its observed eligibility timestamp. The original entry prediction remains unchanged. Return reviews do not generate outcomes, calculate prediction errors, or train the model. These occur only at route resolution.

## Resolve

After both conversions finish, all holdings/Wallet are cleared, proceeds are received, and no operation is pending:
```json
{"event_id":"route1-close","route_id":"route1","at":"2026-09-26T12:00:00Z","kind":"resolve","reference":"final-reconciliation-id","resolution":"completed","residual_disposition":"none","pending_operations":0,"confirmed":true}
```

Alternative resolutions are liquidated and write_off. If holdings or Wallet remain, residual_disposition=written_off is an explicit acknowledgement that they receive no value in this route's result. Merely labeling a route abandoned is unsupported.

A route with a write-off must not later claim recovered value as unexplained new profit. A future recovery/reallocation should preserve the original loss and its provenance; automated recovery attribution is not implemented.

Open route views show progress, holdings, eligibility and overdue status, with actual_profit=null. Closure calculates net P&L and errors in net return, entry cost, Steam receipts, return purchases, DMarket receipts, and duration (when the original duration was known).

## Refine future predictions

Later screens train only from resolved outcomes available at that time, for the same family and mode. Explicit inspection:
```powershell
python -m arbitrage_v2 train --mode paper --input-kind recorded
python -m arbitrage_v2 refine PREDICTION_ID MODEL_ID --mode paper
```

A mode or evidence-partition mismatch is rejected. No outcomes means no fitted duration and no claim of improved accuracy. The current model is a simple mean-error baseline; inspect subsequent original-versus-actual errors before judging its reliability.
