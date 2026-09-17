# Mechanics and data qualification

Updated 2026-09-08. See [initial live findings](INITIAL_FINDINGS.md).

| Area | Known | Still unresolved |
| --- | --- | --- |
| Public Steam page | Anonymous USD book observations, internally consistent quantities, server query timestamps, and extended chart history | Underlying cache age not exposed; page-format stability; actual future fills and complete daily coverage |
| SteamApis details | Two samples of four CS2 titles; USD price scale checked; separate book/history update timestamps; two aggregate history points per title | Latest books were about 6.8 hours old despite successful retrieval; historical coverage, execution latency, lower-depth semantics, other account/endpoint price contracts |
| DMarket offers/Targets | Exact case titles, unique offer IDs, current withdrawal flags and unconstrained Target quantities observed | Future availability; account-specific transfer eligibility; constrained Targets outside this family |
| DMarket fees | Current default 10%, one-cent minimum; first 100 reductions retained | Complete item-specific fee applicability and future transaction fee |
| Steam fee model | USD per-item rounding verified against Steam client code; see [fee audit](STEAM_FEE_AUDIT.md) | Transaction-level verification of the applicable schedule/rounding |
| Timing | CS2 trade protection and third-party visibility restrictions documented | Per-item/account timestamps, transfer/sale/proceeds latency, expected full-cycle duration |
| Result balance | Restricted Tradable proceeds included by user instruction | Exact restriction expiries for each actual receipt |

Steam's terms restrict automated interaction with its services. This implementation provides user-operated Steam steps and sends no Steam trading actions. [Steam terms](https://store.steampowered.com/subscriber_agreement/#4)

DMarket Tradable funds currently support CS2 Market purchases while restricted; they do not support Targets, purchases from other games, or withdrawal until applicable protection ends. Reporting those proceeds as income does not remove the restrictions. [DMarket balance rules](https://support.dmarket.com/hc/en-us/articles/50280203883025-Tradable-balance-how-can-I-use-it)

Sources for contracts: [SteamApis item details](https://docs.steamapis.com/items/item), [DMarket API](https://docs.dmarket.com/v1/swagger.html), [DMarket fee guide](https://support.dmarket.com/hc/en-us/articles/25141430592401-DMarket-Fees).

The authenticated SteamApis account check reported the free endpoint plan and disabled overage billing. The collector queries that account endpoint before each CLI collection cycle, reserves 25 included requests, and keeps a separate persistent local allowance. Unknown quota disables Steam requests for that cycle. DMarket reads can still be collected. These are resource controls, not investment risk limits.

The four-title family was selected for exact commodity identity, observed demand, low unit cost, and successful provider access. It has not been selected because a profitable full cycle has been proven.

The [second live sample](FOLLOWUP_OBSERVATIONS_2026-09-08.md) showed that price history can refresh while order books remain stale. Use the timestamp of the data actually consumed by a decision, not the request completion time or an unrelated updated field.
