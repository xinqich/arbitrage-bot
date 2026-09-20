# Arbitrage Bot V2

Version **1.0.2** is the local DMarket growth tool: market evidence, route suggestions, paper trials and manual real transaction records. Profit/loss and prediction errors appear only after a route is resolved. Real trades remain manual.

## Open the program

Double-click **Arbitrage V2 Local Desk** on the desktop, or open http://127.0.0.1:8765 while the worker is running.

From this directory:

```powershell
.\scripts\local.ps1 -Action Open
```

The launcher starts the worker if needed and opens the page. Closing the browser leaves collection running. The installed startup shortcut starts it after Windows sign-in. A deliberate **Pause** stays in place after restart. Your computer must remain on for collection; missed time is recorded as a gap.

Use the page to see the next check, route holdings/unlock times, source errors, request totals, final results, and reports. Controls include **Check prices now**, **Pause**, **Resume**, and per-route paper controls. A price check requested while paused waits for Resume.

```powershell
.\scripts\local.ps1 -Action Status
.\scripts\local.ps1 -Action Stop
.\scripts\local.ps1 -Action InstallStartup
.\scripts\local.ps1 -Action RemoveStartup
```

`Stop` closes the worker process; use the page's Pause to save a deliberate pause across restarts. Set a different port in `config/local.json` if needed, then restart. There is no automatic port switching or public hosting.

## First real route

Record your actual DMarket regular and Tradable funds in **Funds**, then open **Routes**, select **Real**, and search. Review a candidate and choose **Start real trial** before buying manually. Record each completed operation in Routes; resolve the route after everything has settled. See the [step-by-step guide](docs/REAL_GROWTH_WORKFLOW.md).

Opening a plan sets money aside and saves the prediction. It does not buy anything. Missing sales history does not remove a priceable positive route; the page shows the unknowns and separates buy-order estimates from listing-price estimates.

In **Controls**, **Create backup** saves and checks history, routes and public settings. See [backup and restore](docs/BACKUP_RESTORE.md). A restore saves the replaced history separately and starts paused.

## What is implemented

- Shared local history and request totals. Hourly research checks up to 300 distinct variants, with active-route and unlock checks first. Requests are spaced, cooldowns survive restart, and a run lasts at most one hour. See [request controls](docs/HOURLY_COLLECTION.md).
- A failed market source pauses its own requests. Other sources and paper steps can continue when their required evidence is valid. Retries and source stops survive restart; the page shows the reason.
- Full qualified price levels, whole-item quantities, per-item Steam/DMarket fees, exact-title matching and offer deduplication. SteamApis depth remains top-level only because its lower-level quantity contract is not qualified.
- Review and open new paper growth routes from Search, with fresh evidence, available money and unique offers checked before entry. The selected pair is frozen for each new trial; the older trial keeps its original basket.
- Automatic paper steps for qualified CS2 case, capsule and ordinary-skin routes: partial Steam sales, return-item selection from its frozen basket, a fresh return wait, and sales against fresh DMarket demand. Repeated unchanged books cannot refill paper capacity.
- Open routes show holdings and waits. Leftovers require a user decision. Original predictions remain fixed; paper results never become confirmed results.
- Manual money/item/stage/closure forms, corrections to open records, and linked recovery of explicitly written-off holdings. Earlier records/outcomes remain intact.
- Separate source/destination accounting, including restricted DMarket Tradable and CSFloat deposited/pending/spendable/withdrawable funds. Internal payout transfers do not create additional profit.
- CS2 discovery now rotates through a DMarket catalogue and checks exact Steam/DMarket books. The default purchase floor is $0.10; covered bids rank before midpoint and listing assumptions. Existing request limits remain unchanged. See [coverage and verification](docs/CS2_SEARCH_EXPANSION.md).

## Still needs evidence

CSFloat development is postponed by the user, who will handle the start route manually. DMarket growth work continues. Start/Withdraw API searches report that deferral. A read-only authenticated CSFloat adapter is implemented, but its live response/eligibility contract, exit evidence and your payout facts are not qualified. The public minimum-price list is discovery only. No CSFloat paper entry is enabled on that basis.

Other games and more item types need qualification. Reliable profit and model improvement remain unproven until suitable completed outcomes exist. There is no automatic stop-loss, fixed evaluation window, or guaranteed profit.

See the [approved design](docs/APPROVED_DESIGN.md), [development plan](docs/DEVELOPMENT_PLAN.md), [implementation status](docs/IMPLEMENTATION_STATUS.md), and [local desk guide](docs/LOCAL_DESK.md).

## Developer commands

Python 3.11+; this checkout uses its existing independent `.venv`. No new dependency is needed for the page.

```powershell
& .\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
& .\.venv\Scripts\python.exe -B -m arbitrage_v2 --help
& .\.venv\Scripts\python.exe -B -m arbitrage_v2 worker-status
& .\.venv\Scripts\python.exe -B -m arbitrage_v2 search grow --mode confirmed
```

All previous evidence, collection and route CLI commands remain available. The local worker shares the collector's OS lock; stop it before running a separate foreground collector or paper-step process.

DMarket uses `DMARKET_PUBLIC_KEY` and `DMARKET_SECRET_KEY`; the exploratory SteamApis source uses `STEAMAPIS_KEY`. Supply environment variables or an explicit `--env-file` / launcher `-EnvFile` path. The saved launcher stores only that path, never credentials. V1 remains unchanged and no v1 module or database is imported.

When the user resumes CSFloat work, set `CSFLOAT_API_KEY` locally and run `csfloat-collect "Exact item title" --env-file C:\path\credentials.env` while the worker is stopped. It makes GET requests only and archives selected listing fields, not authorization headers. Payout inputs in `config/csfloat.json` remain unknown until verified. Do not put secrets in the webpage or commit them.

Schema 3 mandates remain readable. Use `mandate-convert old.json new.json` for an explicit schema 4 conversion; it preserves the old paper DMarket funding and does not invent CSFloat or confirmed funds. The active mandate has two separate pretend $10 experiments, as approved.

The [v1.0 acceptance record](docs/RELEASE_1_0_CHECKS.md) explains the completed checks and remaining limits. Automatic selection of independent paper trials is planned for v1.1. CSFloat, more games and proof of repeatable real profits remain later work.


Version 1.0.1 simplifies the local page, merges Routes and Search, adds midpoint comparisons, full Steam wallet records and held-item purchase costs. See [UI changes and checks](docs/UI_REWORK.md). Real/paper modes stay separate.

Version 1.0.2 expands item types and adds configurable spread/price ranking. SteamApis catalogue access is denied on the current account; the working DMarket catalogue fallback is gradual, with coverage shown explicitly.
