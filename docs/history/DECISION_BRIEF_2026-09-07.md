# Historical proposal — superseded where it conflicts with the approved design

See [current design](../APPROVED_DESIGN.md). This archive is context, not active instructions.

# V2 decision brief

Recorded: 2026-09-07.
Source: the greenfield proposal in the preceding conversation.
Purpose: make every recommendation traceable before implementation.

## Product objective

Increase settled, eligible DMarket balance through profitable item conversions between DMarket and Steam over a finite horizon. Count all costs and external funding. Do not treat a higher quoted route value, Steam Wallet remainder, restricted balance, or liquidation of previously uncounted inventory as new trading profit.

No profitable route family has been established yet. V1's results are evidence to investigate, not proof that v2 can earn money. An honest no-go or inconclusive result is preferable to changing acceptance rules until opportunities appear.

## Complete recommendation inventory

### 1. Operational mechanics before prices

- R01: Map listing, trading, deposit, and withdrawal eligibility for each game and acquisition method.
- R02: Model when proceeds become available, which balance receives them, restrictions on that balance, fees, and account requirements.
- R03: Treat current demand as evidence about now; it does not reserve a buyer after an unlock or settlement delay.
- R04: Compare games using those constraints. Do not assume CS2 is the best starting market.

### 2. Two conversions with inventory in between

- R05: Model DMarket dollars -> net Steam purchasing power -> net DMarket dollars. Use the product of conversion rates only as a screening approximation.
- R06: Exact decisions account for whole items, quantities, cent rounding, price depth, fees, costs, and unused wallet funds.
- R07: Maintain several credible return paths. Rechoose B when the actual Steam Wallet and market conditions are known.
- R08: Require evidence of plausible return paths before buying A. Do not accumulate Steam Wallet speculatively without an exit plan.

### 3. Inventory, execution, and a finite objective

- R09: Track available cash, order commitments, owned items, transfer/unlock waits, Steam Wallet, return items, and pending settlement.
- R10: Reconcile every transition; give pending operations deadlines and recovery decisions. A timeout cannot authorize a duplicate purchase.
- R11: Optimize net settled DMarket growth over a horizon with loss and tied-capital limits. Attribute subscriptions and infrastructure costs.
- R12: Separate deposits, preexisting inventory, transfers, expenses, and strategy P&L.
- R13: Stop opening new positions early enough to unwind. A target balance with uncounted outstanding losses is not success.

### 4. Small universe and verified data

- R14: Begin with a small, standardized item universe with observable demand at both venues; approximately 30 titles is a pilot scope, not an eligibility threshold.
- R15: Qualify data for identity, timestamped depth, quantity semantics, completed sales where claimed, freshness, cadence, quota, and raw-response retention.
- R16: Validate actual provider payloads before choosing or integrating a provider. Pay only when the validated strategy can plausibly cover the cost.
- R17: Collect local sale observations with provenance and deduplication. History informs demand and holding risk; disappearing orders are not completed sales.
- R18: Separate historical observations, executable prices, modeled fills, and confirmed transactions.

### 5. Distinct strategy types

- R19: Immediate order-taking needs supported prices/quantities, latency, fees, and fresh executable demand.
- R20: Holding-period trading needs future exit-price uncertainty, time-to-cash, downside, and demand deterioration estimates.
- R21: Passive orders need credible fill/queue/cancellation/adverse-selection modeling. Touching a price is not proof of a fill.
- R22: The old five-unit buffer and 2% stress are unvalidated assumptions. Calibrate policy to strategy and holding horizon instead of adopting them as universal facts.
- R23: Investigate passive acquisition only as a separately specified hypothesis when immediate conversion economics do not work.

### 6. Forward proof before execution

- R24: Record each proposed purchase using only information available then. Freeze the decision record.
- R25: Evaluate subsequent steps only at their earliest actually permitted times, using then-available data and actual funding constraints.
- R26: Include order/item disappearance, fees, rounding, delays, failures, changed return paths, and residual inventory/wallet funds.
- R27: Predeclare the experiment's success, rejection, inconclusive, and abandonment criteria; do not loosen them after viewing outcomes.
- R28: If evidence shows no advantage, change the strategy/venue hypothesis or stop. More scanning is not proof of an edge.
- R29: Prepositioned inventory is a later hypothesis requiring more capital and explicit replenishment economics. It does not remove exposure or costs.

### 7. Authorization, technology, and delivery

- R30: Establish an authorized Steam execution path before unattended trading. Otherwise use user-operated Steam steps; a library's capabilities do not establish permission.
- R31: If unattended Steam operation is indispensable and unavailable on authorized terms, change direction instead of designing around restrictions.
- R32: Start with Python, SQLite, immutable raw snapshots, a scheduled collector, an inventory ledger, and a forward-testing engine.
- R33: Add larger databases/services only for demonstrated requirements. Prefer a CLI/report before a large dashboard.
- R34: The first economic milestone is one repeatable route family with credible net returns after delays and costs, not a larger route table.
- R35: Reuse validated v1 components selectively, with provenance and independent tests; do not inherit its strategy assumptions.
- R36: Keep data quality, economic evidence, operational permission, and execution readiness as separate gates.

## Additional constraints established by the repository review

These are engineering lessons, not proof that the strategy works:

- L01: Preserve exact title/attribute identity, integer-cent arithmetic, fee applicability, bounded discovery, and quota reserves.
- L02: Normalize source timestamps from their correct time basis. V1 demonstrated valid negative histogram ages matching explicit detail timestamps; receipt time is a validation bound, not a freshness reset.
- L03: Evaluate prices with sufficient demonstrated depth, including lower-priced qualifying DMarket Targets. Never borrow quantity from a lower price while claiming the highest price.
- L04: Never sum quantities with unknown overlap/cumulative semantics. Preserve conservative lower bounds and provenance.
- L05: Explain not-requested verification, failed requests, stale evidence, inadequate demand, and unprofitability separately.
- L06: Keep final reports consistent with final recalculation. Distinguish candidates, scenarios, items, independent observations, and cycles.

## Evidence and unresolved external rules

These primary sources were consulted in the preceding proposal on 2026-09-07. Recheck applicability and record dated evidence in milestone M1; these links are not an assertion that an account or item is currently eligible.

- Steam Trade Protection: https://help.steampowered.com/en/faqs/view/365F-4BEE-2AE2-7BDD
- DMarket restricted Tradable balance: https://support.dmarket.com/hc/en-us/articles/50280203883025-Tradable-balance-how-can-I-use-it
- DMarket fees: https://support.dmarket.com/hc/en-us/articles/25141430592401-DMarket-Fees
- DMarket API: https://docs.dmarket.com/v1/swagger.html
- Steam automation terms: https://store.steampowered.com/subscriber_agreement/#4
- SteamApis bulk and detail contracts: https://docs.steamapis.com/items/app-list and https://docs.steamapis.com/items/item

## Decisions still open

The user has specified $10 starting DMarket capital, reliability-first net growth with no fixed profit target, a maximum duration of one calendar month, and a $2 maximum acceptable loss against the original allocation. See the [current mandate](../EXPERIMENT_MANDATE.md), which supersedes fixed-profit-target assumptions in the original proposal. Do not inherit the old $6 scan cap.

Eligible balance type, tied-capital/data allocation, account capabilities, opening inventory/Wallet, terminal residuals, and whether user-operated Steam execution is acceptable remain unresolved. A peak-to-trough drawdown rule has not been specified separately from the opening-capital loss limit.

The initial experiment is read-only/paper. Engineering scaffolding and provider evaluation can proceed before these answers; freeze experiment parameters before its evaluation period and obtain explicit authorization before any real trade or purchase of services.

