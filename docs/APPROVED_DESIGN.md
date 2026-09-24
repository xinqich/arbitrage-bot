# Approved design

Updated 2026-09-15. The authoritative scope and completion conditions are in [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md).

- Reliable net growth of $10 starting capital; no fixed profit target or end date.
- Continuous local market history. Predict net destination return and time with unknowns explicit.
- The user handles the start route manually. CSFloat entry and withdrawal development is postponed until requested again; API access is not a blocker for current work. Focus now on DMarket -> Steam -> DMarket growth. Separate paper funding per venue remains recorded; no automatic real trades.
- Freeze the original prediction/model/evidence at entry. Evaluate P&L and prediction errors only after resolution. Open routes show holdings, waits and overdue flags.
- The user manages risk and leftover balances. No automatic stop-loss, interim P&L, forced liquidation or automatic remainder write-off.
- DMarket Tradable funds count in growth outcomes, retaining their action restrictions. Manual Steam operations are acceptable.
- Keep paper, confirmed and synthetic results separate, including simulation engine versions. Real receipts and eligibility override modeled assumptions only in subsequent events, never by changing history.
- Monitor and control through a local webpage; resume after Windows sign-in unless deliberately paused. Closing the browser does not stop the worker.
- Broaden CS2 item types, then other games after checking source meanings, identity, fees and delays. Missing historical sales or unproven future demand is uncertainty, not proof that an item is unsellable. Missing essential inputs must be identified without fabricating values.
- Suggest potentially profitable routes from observed prices and explicit cost/exit assumptions before sufficient outcome history exists. Show uncertainty about future prices, demand and completion time. Only claims of demonstrated consistent profits require completed outcomes; that proof is not a discovery or v1.0 release gate.

New page-created paper trials freeze their selected outward/return pair as the return comparison basket. The older Fracture trial retains its existing four-case basket. Entry must pass a fresh recorded-evidence check, positive net arithmetic after declared costs, unused-offer checks and available paper funding. Neither condition is a recommendation to place real trades.

Release scope: v1.0 is the usable local DMarket growth tool with manual real operations and manually started paper trials. M8 automatic selection of 5-10 independent paper alternatives is approved for v1.1, using $10 initially and the saved pre-entry real budget while the user is in a route. Alternative results are not added together as account growth. See the release requirements in DEVELOPMENT_PLAN.md; CSFloat and the longer economic-proof milestone remain separate.

Planning correction: distinguish opportunity suggestions from recording actual or simulated fills. Missing history must not eliminate a priceable positive-net candidate or silently reduce its score below a cutoff. A listing-price exit can be shown as a separate assumption-based scenario; it is not an observed sale. Concrete constraints apply to the affected scenario/quantity. See the corrected planning rule in DEVELOPMENT_PLAN.md. Existing trial rules are unchanged. The version 0.8 audit and discovery implementation are recorded in DISCOVERY_FILTER_AUDIT.md; actual listing-paper execution remains unimplemented.


Version 0.9 implements the real-funds/manual-growth workflow. Opening a real plan reserves money and freezes its prediction without inventing a purchase. Actual receipts save cash, items and stage atomically; only explicit terminal resolution produces P&L. Unused plans can be cancelled without outcomes. Initial/additional funding stays outside route income. No real funds or transactions are created by installation. See [workflow](REAL_GROWTH_WORKFLOW.md) and [checks](RELEASE_0_9_CHECKS.md). Version 1.0 completes the software acceptance review and adds checked backup/restore operations; see RELEASE_1_0_CHECKS.md. M7 proof and M8 independent trials remain separate.
