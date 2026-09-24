# UI rework — version 1.0.1

Implemented the approved UI plan on 2026-09-16. This is a local interface and supporting-data update; automatic independent paper trials remain M8/v1.1.

## Implemented

- Compact Overview: real wallet, recorded full Steam wallet, held-item purchase cost, separate route counts, request allowance and collection bar. Evidence health moved to Controls / Debug.
- Routes and Search merged. Remembered Real/Paper selection, latest searches separated by mode, compact active routes, 13-column comparison table, selected-route actions and v1-style details. Older `#search` links still work.
- All requested table totals, gross/net distinction and ROI; both selling methods identified. The best quantity is shown first, with other quantities in the selected detail. Original prices, links, historical activity, model adjustments and fee levels are available without permanent diagnostic clutter.
- Funds opens with balances. Funding operations, history, corrections and the new full Steam wallet records are collapsed. Receipt fields now use “Receipt ID or note.”
- Midpoint estimates for both sale legs; separately versioned from bid/listing calculations. Missing/inconsistent midpoint inputs do not erase other scenarios. Assumed-price paper fills remain unsupported and are explained at entry.
- Weighted-average acquisition cost for held items, including partials, corrections, rounding and linked recovery. Unknown older acquisitions stay unknown. This adds no open-route P&L.
- Append-only Steam balance/adjustment/correction records. A balance snapshot covers receipts at or before its timestamp; later effective real Steam movements update it once. Paper movements and recovery reassignment are excluded. Negative derived balances request reconciliation instead of becoming a fabricated zero.

## Interfaces and compatibility

The existing status response adds `searches` by mode, `holding_costs`, and `steam_wallet`. Read-only `GET /api/route-detail` accepts exactly one `prediction_id` or `route_id`; its quotes come only from saved source records. Protected action types `steam_wallet` and `correct_steam_wallet` add the new wallet records. Existing actions remain supported.

The original journal, predictions and outcome calculation remain intact. Existing discovery-version predictions remain readable and real-entry compatible with their existing requirements. New midpoint calculations use `midpoint-price-scenarios-v1`; outcomes from earlier engines do not calibrate them. New discovery records use `separate-sale-scenarios-v2`.

## Verification

The complete Python suite includes the prior 165 tests plus midpoint/holding/wallet/detail checks and a JavaScript DOM contract test. The DOM test executes the actual page script against the actual HTML structure with isolated test data: mode separation, 13 columns, details, entry availability, alias navigation, saved selection/forms during refresh, and Steam submission are checked. It is not a screenshot or real-browser rendering test.

Release checks also read the running experiment into a temporary journal and compare its frozen prediction, original records, real funding and trial holdings. HTTP assets, reports, read-only details and new status projections are checked without market requests. Backup/restore is rehearsed on copies only. JavaScript syntax, unique IDs, label references, keyboard-accessible native controls and responsive CSS breakpoints are checked. No visual browser session is claimed.

Publication saves the old files and database before replacement, preserves every prior record and restarts the owned worker. Exact installed checks, file hashes and backup locations are in `data/last_release.json`.
