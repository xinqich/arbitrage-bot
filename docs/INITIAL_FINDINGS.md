# Initial family findings — 2026-09-08

Family: standardized CS2 cases. This is a bounded first sample, not an economic validation.

## Live observations

| Item | DMarket best eligible offer | Steam observed buy order | Steam observed sell listing | DMarket unconstrained Target |
| --- | ---: | ---: | ---: | ---: |
| Fracture Case | $0.55 | $0.66 | $0.70 | $0.51 |
| Recoil Case | $0.46 | $0.39 | $0.40 | $0.39 |
| Revolution Case | $0.60 | $0.30 | $0.31 | $0.45 |
| Kilowatt Case | $0.46 | $0.18 | $0.19 | $0.45 |

All three endpoint types returned HTTP 200 for each title. Original capture IDs, retrieval times, source times, and results are in data/research.sqlite3 and [the screen export](initial_screen.json). No credentials are included.

SteamApis histograms were roughly an hour old at retrieval. Decimal prices were in dollars in these responses, unlike the integer-price example in the provider documentation. Quantities grew down the histogram; the adapter uses only best-price quantity because cumulative/incremental interpretations agree there. It does not sum uncertain lower depth.

Each item returned just one price-history point. These are preserved as provider observations; they do not establish complete daily coverage or individual fills.

The DMarket fee response gave default fraction 0.1 and minimum 1 cent. The first 100 fee reductions contained none of these titles, which does not prove that no applicable reduction exists elsewhere.

## Conditional screen

109 alternative quantity/A/B combinations fit the observed best-level capacity and $10 screen budget; 21 were positive under the declared assumptions. They are alternatives sharing capital and demand, not 109 independent opportunities or completed cycles.

The largest projected net amount was Fracture Case -> Kilowatt Case:
- Buy 12 Fracture Cases for $6.60.
- Sell 12 cases at the observed $0.66 Steam buyer price: $7.92 gross, $0.84 total Steam/CS2 fees, and $7.08 Wallet received (12 x $0.59).
- Buy 36 Kilowatt Cases for $6.84, leaving $0.24 Wallet.
- At the observed DMarket Target and 10% fee baseline, receive $14.40.
- Conditional net DMarket increase: $7.80; remaining Wallet receives no profit credit.

Fee audit (2026-09-08): Steam calculates the two fees separately for each item, then multiplies seller proceeds by quantity. With USD cent rounding, a $0.59 seller receipt incurs floor(59 x 5%) = 2 cents Steam fee and floor(59 x 10%) = 5 cents CS2 fee. Thus $0.59 + $0.02 + $0.05 = $0.66 per case; twelve receipts total $7.08. The approximation $7.92 / 1.15 - $0.01 = $6.876956... applies rounding to the combined gross and is not Steam's per-item calculation. The original $7.08 figure is supported by the published client code; this audit adds the missing explanation and verification. [Steam fee code](https://community.fastly.steamstatic.com/public/javascript/economy_common.js), [Steam bulk-sale code](https://community.fastly.steamstatic.com/public/javascript/market_multisell.js), [reproducible audit and scope](STEAM_FEE_AUDIT.md).

This is not an entry recommendation. Future demand is not reserved, those quantities/prices can change, other operating costs are set to zero only for this optimistic screen, and a remaining Wallet balance needs explicit disposition before route resolution.

As an independent plausibility check, Steam's public Kilowatt page later showed a $0.20 starting sell price, close to but different from the provider's $0.19 observation. This supports the price scale while demonstrating that even the best quote changed. It does not validate future exits. [Steam listing](https://steamcommunity.com/market/listings/730/Kilowatt%20Case)

## Timing and next evidence

For this CS2 hypothesis, the initial delay floor is seven days before the outward Steam sale plus ten days before the newly acquired return item becomes visible to DMarket. These are explicit assumptions to verify for actual items, not an exact 17-day cycle. Selling, transfers, and proceeds can take longer. Restricted Tradable proceeds are included under the approved mandate. [Steam protection](https://help.steampowered.com/en/faqs/view/365F-4BEE-2AE2-7BDD), [DMarket visibility](https://support.dmarket.com/hc/en-us/articles/25903434429841-Item-visibility-issue)

Expected completion time is therefore initially unknown. There are no resolved paper or confirmed cycles from which to calibrate it. The first economic milestone remains open.

The useful next evidence is repeated snapshots for this family and outcomes of separately chosen manual/paper routes. These initial results justify observation, not a claim of reliable profit.
