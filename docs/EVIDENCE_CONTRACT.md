# Evidence and journal contracts

There are two independent local stores.

## Canonical offline evidence

The original init/ingest/status/inspect commands use data/evidence.sqlite3, schema 1. [The synthetic envelope](../examples/synthetic_evidence.json) demonstrates explicit currency/units, exact item identity, aware timestamps, and declared incremental/cumulative quantities.

This store retains exact imported envelope bytes and normalization hashes. Reported sale events need explicit source event IDs; repeated events deduplicate and conflicting identities roll back ingestion. Disappearing listings are never converted to sales. Unknown coverage stays unknown.

## Live market captures

Collection uses data/research.sqlite3, an append-only journal with category-tagged records. A request attempt is recorded before each market call. Captures retain provider, endpoint kind, item, request/retrieval times, status/error, input kind, and sanitized JSON payload. Decimal JSON numbers become exact decimal strings during archival parsing; this is a documented representation, not original HTTP bytes.

Credentials and request headers are not archived. Redirects are disabled. The DMarket adapter exposes only read-only offers, Targets, and fee endpoints. The default public Steam adapter reads only anonymous CS2 listing pages and permits canonical redirects only within that listing surface. It decodes embedded JSON without executing page scripts and archives only selected market fields plus a page hash and non-sensitive provenance. It requires USD at page, book, and chart levels, exact item identity, and matching book totals/best prices. The optional SteamApis adapter requests item details; only that source invokes the separate quota lookup.

Provider history points remain observations. The history command presents the latest reported row for each provider/evidence partition/item/timestamp without adding repeated quantities or claiming complete calendar-day coverage. `history --summary` shows source-specific ranges and counts; `--provider` and `--input-kind` filter explicitly. Rounded provider medians and higher-precision Steam chart medians are not merged. A provider aggregate is not an individual confirmed fill.

## Initial case adapter

- Restricts research to exact CS2 case titles identified as Steam commodities.
- Uses explicit USD units for normalized Steam books and cents for DMarket prices. The public page's integer-cent prices are converted exactly; fractional chart medians remain aggregate observations and are never treated as executable prices.
- Checks retrieval/source time against the decision time and chosen freshness bound.
- Uses only best-price quantity: cumulative and incremental interpretations agree at the first level. Duplicate/overlapping Target quantities are not added.
- Deduplicates DMarket offers by offer ID, keeps exact title/game, and requires current withdrawal/tradability flags.
- Uses only unconstrained matching Targets. Other item/attribute families remain unsupported.
- Preserves synthetic/recorded partitions.

Provider quotes are observations, not reservations, actual fills, or guarantees of future eligibility.

## Predictions, events, and outcomes

Predictions preserve source IDs, original calculations, assumptions, timestamps, and model version. Journal triggers reject ordinary updates/deletions. Explicit idempotency IDs detect conflicting retries. This is an application audit mechanism, not protection against an administrator replacing the database.

Route entry retains its prediction ID. Real-data predictions must have been recorded before entry. Manual events are chronological and carry references. Net movements have fee amounts recorded separately as information, so fees are not deducted twice.

Only resolution creates an outcome and prediction-error record. Completed routes require both Steam conversions, DMarket proceeds, no remaining assets/Wallet, and no pending operations. Liquidation and expressly confirmed residual write-offs are separate terminal dispositions.

The confirmation label means operator-recorded confirmation; automatic transaction reconciliation is not implemented. Paper and confirmed outcomes are never pooled for training. Synthetic and recorded evidence are also isolated.

Predictions start as conditional present-quote calculations. The first learning model adjusts return bias and estimates duration from resolved outcomes; it preserves the operational delay floor and reports sample count. Model updates do not change original predictions or turn a quote into an entry recommendation.

The public source timestamp denotes when the Steam server observed the query result. The underlying market cache age is not exposed. Its chart contains historical aggregate medians and reported purchase counts; a long timestamp span does not establish uninterrupted daily coverage. See [source qualification](STEAM_PUBLIC_SOURCE.md).
