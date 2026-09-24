# Current mandate

The [approved design](APPROVED_DESIGN.md) replaces the earlier one-month/$2 configuration.

| Setting | Value |
| --- | --- |
| Starting capital | $10 / 1,000 cents |
| Objective | Reliable net growth, no fixed profit target |
| Collection | Continuous while the collector is running and data allowance is available |
| Evaluation | At individual route resolution |
| Fixed duration | None |
| Automated risk accounting / stop-loss | None; user-managed |
| DMarket proceeds counted | Regular plus restricted Tradable balance |
| Steam operations | Manual accepted |

[mandate.json](../config/mandate.json) is schema 3. The CLI rejects old calendar/loss-limit fields, preventing their accidental reintroduction. Inspect with `python -m arbitrage_v2 mandate`.

Manual management still requires accurate records. Asset quantities, net cash movements, references, and unlock dates support the next route step. They do not produce open-route P&L.

Final P&L is the sum of route-specific net DMarket movements plus attributable external costs. Fees already deducted from a net movement are not deducted again. Steam Wallet and unliquidated items do not become DMarket income. Remaining assets/Wallet must be resolved or explicitly written off, with the latter recorded as an outcome.

Tradable funds count toward the result but may currently fund only CS2 Market purchases, not Target purchases, other games, or withdrawals while restricted. The route view displays those rules. Account-specific availability and actual expiry must still be checked.

The request allowances in the watchlist govern provider usage, not trading risk or performance periods. The bot does not create subscriptions, place trades, or start a background service by loading this configuration.
