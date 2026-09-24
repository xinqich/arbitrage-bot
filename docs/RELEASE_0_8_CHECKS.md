# Version 0.8 checks

September 15, 2026. This update fixes opportunity discovery following the approved planning correction. See [the filter audit](DISCOVERY_FILTER_AUDIT.md) for the findings and saved-data comparison.

- Search no longer requires books unrelated to a route leg. Missing sales history, zero completed outcomes and unknown completion time do not exclude a priceable positive route.
- Bid-based estimates and clearly labelled listing-price estimates appear together. Listing quantities are assumptions, never buyer demand or recorded fills. Per-item fees, purchase depth, whole-item budgets and unknown-cost handling remain enforced.
- All priced quantity variants are available from the page. The initial table selects the best quantity for each pair and selling method; expanding the other quantities is optional. Missing input prices and concrete scenario limits remain visible.
- Automatic listing-paper execution is deliberately unimplemented. Both page entry and lower-level paper configuration refuse to attach the existing bid-fill engine to a listing prediction. Old bid-based trials keep their rules and history.
- The test suite covers no-history opportunities, irrelevant missing books, thin and absent bids, independently missing asking prices, stale essential prices, listing fees and supply limits, restrictions, identity, evidence partitions, entry validation, model separation and the full result list. The existing lifecycle, journal, fee and local-server tests remain part of the run.
- Publication backs up the real journal and previous changed files, compares every earlier row and checks the local page after a managed restart. No test journal is copied into the project and no new trial is opened during publication.

The installed check results and backup path are recorded in `data/last_release.json`. This is progress toward v1.0. Real-funds setup, real-budget search and the complete page workflow for confirmed routes remain required. No actual reliable-profit claim follows from these tests or hypothetical listing returns.
