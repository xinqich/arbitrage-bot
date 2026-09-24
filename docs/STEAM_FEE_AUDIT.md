# Steam fee audit — 2026-09-08

The \$7.08 Steam Wallet projection for twelve Fracture Cases at a \$0.66 buyer price is consistent with Steam's published USD fee calculation. The initial findings omitted the per-item calculation and its source; that explanation has been added. No monetary prediction was changed by this audit.

## Unit calculation

Steam's [market FAQ](https://help.steampowered.com/en/faqs/view/61F0-72B7-9A18-C70B) specifies a 5% Steam fee (minimum \$0.01) and a 10% CS2 fee. Its [public fee code](https://community.fastly.steamstatic.com/public/javascript/economy_common.js) supplies the rounding detail: each positive-rate fee is floored separately and subject to the currency minimum. For this audit, USD uses a one-cent minimum and increment. The fees are computed from the seller's amount, then added to form the buyer's price.

| Amount | Per case | Twelve cases |
| --- | ---: | ---: |
| Seller receives | \$0.59 | \$7.08 |
| Steam fee: floor(59 cents x 5%) | \$0.02 | \$0.24 |
| CS2 fee: floor(59 cents x 10%) | \$0.05 | \$0.60 |
| Buyer pays | \$0.66 | \$7.92 |

The [bulk-sale code](https://community.fastly.steamstatic.com/public/javascript/market_multisell.js) converts the unit buyer price to a unit seller receipt in `PricePaidChanged`, then `UpdateOrderTotal` multiplies the seller's unit amount by quantity. Selecting twelve items together does not merge them into one \$7.92 item for fee calculation.

`7.92 / 1.15 - 0.01 = 6.876956...` is an approximation applied to the combined gross. It does not reproduce Steam's separate, per-item rounding. Even Steam's fee calculation on a hypothetical single \$7.92 item produces \$6.89, a different result from the twelve actual \$0.66 items.

## Verification

On 2026-09-08, the pure fee functions in the downloaded Steam script were run in Node's isolated JavaScript context. No Steam account, cookies, sale, or purchase was used. Inputs specified the 5%/10% rates and one-cent minimum/increment. All 998 integer buyer-price ceilings from 3 through 1,000 cents matched v2's `steam_net` result. Below three cents, a positive USD CS2 sale cannot cover both minimum fees; those invalid prices were outside this comparison.

The [reference fixture](../tests/fixtures/steam_usd_fee_reference.json) records the URL, retrieval-check time, source SHA-256, explicit wallet parameters, and nineteen independently produced outputs, including fee-minimum and rounding-gap boundaries. The SHA-256 identifies the decompressed script audited; the live URL may change. To reproduce the comparison, evaluate that script's `GetItemPriceFromTotal(gross, wallet)` with the fixture wallet and compare its integer output with `steam_net(gross, 500, 1000, 1, 1)`.

[Regression tests](../tests/test_steam_fees.py) check the reference outputs and replay the reported twelve-case route through the whole-item return purchase. Expected amounts remain: \\$6.60 DMarket entry, \$7.08 Steam receipt, 36 return cases costing \$6.84, \$0.24 residual Wallet, and \$14.40 conditional DMarket receipt. The \$7.80 conditional net increase retains the original DMarket fee and zero-other-cost assumptions.

Published prediction records, their original assumptions/model versions, and the journal are preserved. This is verification of the USD quote arithmetic; it is not a completed sale or evidence that future quotes will remain available. Before a real sale, use the actual account currency and Steam confirmation amount; only confirmed net movements determine a resolved route's actual result.
