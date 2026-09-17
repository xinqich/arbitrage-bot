# Discovery filter audit

September 15, 2026. Implements the corrected distinction between finding a possible opportunity and proving repeatable profit. This audit concerns v2, not the older v1 scanner.

## Findings and changes

| Finding in 0.7 | Change in 0.8 |
| --- | --- |
| No actual sales-count or completed-trial threshold was found in v2's discovery code. Missing outcomes already left return adjustment at zero and duration unknown. The planning language was nevertheless wrong. | Preserve that behavior and add direct tests: zero history/outcomes cannot exclude a priceable positive scenario or introduce an invented penalty. |
| Every item had to provide DMarket asks and bids plus Steam asks and bids before participating in any route. Missing an irrelevant book excluded the whole item. | Parse books separately; each scenario uses only its own four legs. For example, missing DMarket buy orders for A cannot prevent A being bought on DMarket and sold on Steam. Fresh-entry review checks the same necessary inputs. |
| Steam sales were limited to observed Steam bid depth. Destination purchase quantities were capped by DMarket bid depth. There was no separately priced listing alternative. | Keep the bid-depth calculation, and add Steam-listing, DMarket-listing and combined-listing scenarios. Each listing uses the lowest observed eligible ask as an explicit assumed selling price. Fees remain per item. The proposed sale quantity is an assumption; competing listings supply no buyer demand or fill evidence. |
| Only twenty quantity variants were returned to the page. This could hide other pairs or selling methods. | Return all calculated variants. The page initially shows the best amount for every pair and selling method, with all quantities available in an expandable table. Polling retains the open result while the saved search is unchanged. |
| Item exclusions and rejected quantity counts did not clearly explain which calculation failed. | Show affected books, missing/old/failed inputs, observed absence of eligible bids/listings, and scenario-specific budget or quantity limits. Missing data is never described as proven illiquidity. |

The bid-based prediction and paper engine retain `observed-depth-v2`. Listing scenarios use `listing-price-scenarios-v1`; bid-fill outcomes cannot calibrate them. Predictions store selling methods, per-leg source IDs/times, original costs/fees and unknown future demand/time. Asking-price estimates contain no recorded fills. Automatic paper-entry review and the lower-level paper configuration both explicitly refuse unsupported listing execution while leaving the opportunity visible.

## Check against saved market observations

At **19:24:08 UTC on September 15**, the old and new searches were run against the same copied live journal, roster, assumptions and freshness limit. Neither search contacted a market or changed the live journal.

| Comparison | 0.7 priced / positive scenarios | 0.8 priced / positive scenarios | Positive breakdown in 0.8 |
| --- | --- | --- | --- |
| 65 cents actually unallocated in the existing shared paper pool | 6 / 1 | 24 / 10 | Same 1 bid-based scenario, plus 9 listing scenarios |
| Hypothetical $10 comparison, not an added balance | 285 / 56 | 1,140 / 315 | Same 56 bid-based scenarios, plus 259 listing scenarios |

These counts include quantity and selling-method alternatives; they are **not counts of independent profitable routes or completed sales**. The larger numbers reflect additional assumptions being priced, not new evidence that buyers will pay those prices. In this snapshot, both noncase Steam sources needed refresh; the page now identifies the stale books instead of labelling the items unsellable. The copied-journal $10 search took about 16 seconds on this computer.

## Limits and remaining work

- No extra historical sale coverage is required for discovery. No success probability is invented.
- Observed purchase supply, whole-item affordability, exact identity, fees and declared costs still apply. Unknown costs leave net return unknown. Unpriceable inputs stay listed with the reason.
- Known future route delays remain waits. Entry currently prices unlocked, tradable, withdrawable DMarket offers; pricing locked entry offers needs a usable unlock-time contract and explicit delay support. The current filter does not claim such items can never sell.
- All sale scenarios depend on future prices and buyers. No guarantee of today's bids surviving a lock is required to show the conditional bid scenario.
- Listing execution is not automatically simulated: current asks alone cannot prove a listing sold. The v1.0 manual confirmed-route workflow must support recording actual receipts against the saved estimate. That workflow remains unfinished.
- Existing paper settings, consumed depth, original predictions and final-only outcomes remain unchanged. Shared paper funding still applies. Independent $10 alternatives and automatic selection remain M8 for v1.1.
- Fresh current estimates are implemented here. Old essential prices are identified for refresh; no automatic stale-price estimate is presented as current.
- This is the existing small CS2 roster, not a claim of full-market coverage or readiness for other games.
