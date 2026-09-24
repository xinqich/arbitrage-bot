# CS2 search expansion — 1.0.2

Implemented September 17–18, 2026. This extends discovery, not automated trading or proof of reliable profits.

## Rules

- All exact, supported CS2 market identities can enter the workflow. Categories are not restricted to cases. Wear, StatTrak and Souvenir names stay distinct. Attached stickers, floats and patterns receive no extra value. Reported skin floats must match the named wear; unknown or inconsistent identity remains a data problem.
- Item A's DMarket purchase and Item B's Steam purchase must each cost at least $0.10 per item. Exactly $0.10 qualifies. Search options can change this floor.
- Positive net DMarket estimates come first. Within those estimates: narrow-spread covered bids, other covered bids, midpoint, then lowest ask. Losses/unknown results remain reviewable below profitable estimates, not promoted because they have tight spreads.
- The route uses its weaker selling leg's priority. Within priority, maximize predicted DMarket gain, then prefer less initial spending, stable item names and quantities. Steam remainder is separate. Existing learned adjustments remain separately visible.
- The default narrow spread is strictly below 10% of the comparable lowest ask. Test the lowest bid required for the batch, not just the best bid. Walk each price level and charge each item's fees. Missing comparison ask lowers bid priority; missing history never excludes a priceable candidate.
- Keep the best quantity for each pair, scenario combination and priority on the page. Other quantities remain in details. Return quantities smaller than the best quantity inside the same priority are safely dominated by greater DMarket receipts; retain the separate narrow-spread boundary.
- All nine exit combinations remain supported as estimates. Listing and midpoint scenarios still cannot create automatic paper fills. Current bids are not a promise of buyers after the holding periods.

## Actual provider qualification and coverage

Thirteen read-only requests were used. These request records and actual price captures are included in the operational journal at installation so they count against existing allowances. No credentials, purchases, funding or trial entries were created.

| Check | Result |
| --- | --- |
| SteamApis full app price file | HTTP 403 on the existing account |
| SteamApis paginated item list | HTTP 403 on the existing account |
| DMarket grouped prices for named items | HTTP 200; explicit USD integer cents; aggregate counts are not best-price depth |
| DMarket grouped prices with game-only filter | HTTP 200; 100 catalogue entries and a next-page cursor |
| Capsule, agent and standalone sticker detailed checks | All nine individual Steam/DMarket requests succeeded; exact names and usable books were parsed |

Checked titles: `10 Year Birthday Sticker Capsule`, `1st Lieutenant Farlow | SWAT`, `Sticker | Baby Medusa`. Existing skin/capsule fixtures also remain covered. These checks establish source compatibility, not completed sales or profitability.

The installed fallback uses DMarket catalogue pages and the existing individual SteamApis item feed. It does not retry the denied Steam catalogue endpoints or require a subscription upgrade. The bulk Steam integration is unavailable under the current account, so complete market-wide price screening is not claimed. Official contracts: [SteamApis bulk list](https://docs.steamapis.com/items/app-list), [SteamApis item list](https://docs.steamapis.com/items/list), [DMarket API](https://docs.dmarket.com/v1/swagger.html).

One catalogue page is requested on each existing research cycle. Exact item checks use remaining requests, with active-route checks first. Four promising selections are followed by one least-recently-checked eligible item; selection history and catalogue cursor survive restart. The existing six-hour schedule, four-hour detailed-price freshness, 15-request cycle budget, five-Steam-request cycle budget and lifetime allowances are unchanged. Typical idle research can check four new item titles per cycle. Account limits can reduce this further.

Catalogue summaries only choose work; aggregate quantities never become order fills. The price search only joins freshly usable detailed books. Unchecked items, unknown Steam affordability, unavailable identities, provider errors and stale prices remain visible through coverage, catalogue browsing and calculation issues. A DMarket purchase budget is never substituted for a return item's Steam budget. No global optimum or exhaustive fresh market coverage is claimed.

## Implementation and compatibility

- In-memory capture index per scan, reused quantity quotes, transaction-batched prediction writes and pruning of dominated return sizes. Detailed calculation is limited to items with captured sources, not the entire catalogue's Cartesian product.
- Saved ranking version `cs2-sale-priority-v1`, settings, priorities, spreads, coverage, comparison-source references and selected return quantity limit. Entry checks revalidate both economic and ranking comparison evidence.
- Existing status response adds saved search settings and coverage. Session-protected `search_settings` action records preferences append-only. Read-only `/api/catalogue?offset=N` returns up to 100 summaries and detailed-check status.
- New paper trials retain their selected search preferences for later return choices. Older settings, predictions, routes, balances, corrections, outcomes and learning partitions remain unchanged. Manual actual receipts remain actual records even if a subsequent price differs from the discovery preference.
- CSFloat, other games, premium pricing, mixed-item baskets and automatic independent trial selection remain deferred.

## Verification

209 automated tests pass, including 26 new expansion tests. They cover price/spread boundaries, deep bid coverage, mixed scenarios, profitable-vs-loss ordering, restricted target attributes, decorated base-value offers, multiple categories, paper entry and a complete manual noncase cycle, source changes before entry, settings security, catalogue units/pagination/restart/failures, coverage with zero available funds, request priority and pause, and a 10,000-item catalogue check. Cached calculations and best quantity choices match exhaustive small-data reference results.

The September 18 isolated-journal check preserved all 5,356 existing rows, the original trial and prediction, real funds, held-item costs and Steam wallet records. Backup/restore and seven local HTTP endpoints passed. Current searches did not force a result from stale qualification prices or add funds: the recorded real balance was zero. No new provider requests, funding or trial entries were made during this check.

JavaScript syntax and the existing UI contract harness are checked. Visual browser testing was not requested and has not been performed. Installation verification and the before/after preservation results are recorded in `data/last_release.json`; the installation creates a checked operational backup.
