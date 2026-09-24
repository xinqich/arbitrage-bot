# Follow-up observations — 2026-09-08

Evaluated at 2026-09-08T12:54:49.947907+00:00. The second bounded sample collected four SteamApis item responses and eight DMarket offer/Target responses. All twelve returned HTTP 200. The responses were appended to the existing research journal; no route was entered and no outcome was created.

The unchanged four-hour quote freshness limit admitted 0 complete item snapshots and produced 0 route predictions in this sample. The exclusions below reflect old Steam order books, not a claim that these items are unsellable.

| Item | Steam book timestamp (UTC) | Book age when retrieved | Latest reported history point (UTC) | Exclusion |
| --- | --- | ---: | --- | --- |
| Fracture Case | 2026-09-08T06:02:07.012Z | 6.83 h | 2026-09-08T09:00:00.000Z | stale_steam_book |
| Recoil Case | 2026-09-08T06:03:50.711Z | 6.80 h | 2026-09-08T09:00:00.000Z | stale_steam_book |
| Revolution Case | 2026-09-08T06:04:16.212Z | 6.79 h | 2026-09-08T10:00:00.000Z | stale_steam_book |
| Kilowatt Case | 2026-09-08T06:03:31.597Z | 6.80 h | 2026-09-08T10:00:00.000Z | stale_steam_book |

Both the histogram timestamp and the item-level `histogramUpdatedAt` field remain near 06:02–06:04 UTC. Price-history update times advanced independently. A successful fetch and newer price-history point do not refresh the order book.

The provider documents a typical CS2 refresh cadence of one to two hours, with actual update timestamps controlling what is current. This sample does not meet that typical cadence. The underlying cause is unverified; repeated requests cannot be assumed to force a refresh. [SteamApis item documentation](https://docs.steamapis.com/items/item)

Local history now contains two distinct provider price-history points for each title, from the two samples. These are aggregate observations, not deduplicated individual transaction confirmations or complete daily sale coverage.

The existing four-hour rule was preserved. This sample supports neither a current route recommendation nor a conclusion that no profitable route exists. The source needs to resume timely book updates, or a different source must be qualified for exact USD prices, quantities, timestamps, and item identity before it can support current quote decisions. A source replacement has not been established by this sample.

The new Wallet-based return workflow is implemented and covered by 63 passing tests, including synthetic route/event scenarios and preserved recorded fee fixtures. No real open Wallet position was created to exercise it. Its options remain conditional on future demand, and the original entry prediction stays frozen.

First economic milestone: still unproven. More complete outcomes after actual fees, costs, and delays are required. No continuous background collector or scheduler was started.

The included SteamApis allowance showed 284 requests remaining before the four item requests; overage was disabled. Credentials were read from the previously used explicit file and were not copied.

See [machine-readable evidence and exclusions](followup_screen_2026-09-08.json), [initial findings](INITIAL_FINDINGS.md), and [manual return workflow](ROUTE_WORKFLOW.md).
