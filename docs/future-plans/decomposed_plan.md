We’ll use **hourly checks**, keeping the existing request spacing. More frequent checks do mean more requests, so this will be a monitored trial—not a test that deliberately keeps pushing through Steam’s restrictions.

This consolidates the latest decisions and replaces the earlier three-hour plan. It can be implemented as seven smaller tasks.

    ## Task 1 — Fix the existing collection failures

    - Fix the `worker_KeyError` caused by overwriting control settings with catalogue state.
    - Strengthen tests to check that the worker finishes successfully and schedules another check.
    - Separate item failures from endpoint and provider failures. One unsupported skin must not stop working case collection.
    - Preserve valid, unexpired evidence when a newer request fails; show the failure separately.
    - Ensure missing history does not prevent using an otherwise valid order book.

    **Done when:** repeated scans succeed, isolated failures stay isolated, and existing routes remain unchanged.

## Task 2 — Hourly scheduling and request controls

Replace the old restrictive configuration:

| Setting | New behaviour |
|---|---|
| Background interval | Every hour |
| Research batch size | 300 distinct item variants |
| Promising-item refresh target | One hour |
| Maximum detailed evidence age | Four hours |
| Direct Steam request spacing | At least 5 seconds |
| DMarket request spacing | At least two seconds |
| Maximum run duration | One hour |
| 15-total/5-Steam cycle limits | Removed |
| 1,000-request lifetime stops | Removed |

These settings remain configurable. The spacing values are starting choices, not proven safe Steam limits.

Scheduling:

- Keep one collection run at a time.
- Check active-route needs first, outside the research batch count but inside the run’s duration limit.
- Preserve checks at route unlock times.
- On startup, perform one check if collection is overdue. Do not replay every missed scan.
- Manual checks and unlock checks must not postpone the regular schedule.
- Pause must interrupt waiting between requests.
- If a run reaches the next scheduled check, do not start an overlapping run or accumulate catch-up runs.

Provider controls:

- On HTTP 429, stop that provider’s remaining requests for the run and honour `Retry-After`; otherwise record a 15-minute cooldown.
- Persist cooldowns across restarts. Manual checks do not bypass them.
- Keep bounded retries for connection/server failures, within the run’s time limit.
- Authentication/access failures stop only the affected provider or endpoint.
- Retain request totals as statistics rather than lifetime stopping conditions.

**Done when:** hourly scheduling, startup recovery, pause, cooldowns and non-overlapping runs pass controlled tests, including operation beyond the old limits.

## Task 3 — Broad screening using CSGO Trader

Add its Steam price file as a source of **screening hints**, separate from detailed evidence.

- Verify currency and price meaning before using the values.
- Match exact item names, preserving wear, StatTrak and Souvenir distinctions.
- Prefer the 24-hour summary, otherwise the seven-day summary, recording which was used.
- Refresh the file every hour.
- Stop using its prices for prioritization when the file is over 24 hours old.
- Never use these summaries as current order depth or final route prices.
- Keep items eligible when bulk prices or historical activity are missing.

Continue DMarket catalogue collection:

- Fetch up to ten pages per background run, within pacing and duration limits.
- Preserve pagination across restarts.
- Syncronize scraping the market on both Dmarket and Steam.
- Keep existing supported items and DMarket discoveries even when absent from CSGO Trader.

**Done when:** bulk data helps select candidates, while missing or stale bulk data cannot stop collection or alter route arithmetic.

## Task 4 — Broader direct Steam collection

Extend the existing Python reader.

- Support grouped skin pages and select the exact requested variant.
- Increase bounded response-size handling enough for verified current pages.
- Validate identity, currency, timestamps, price ordering and quantities.
- Read supported bid and ask levels, preserving per-item fees and quantity-dependent calculations.
- Allow valid detailed prices without sales history.
- Show specific reasons for ambiguous or unsupported matches.

Qualify representative cases, ordinary skins, StatTrak, Souvenir, stickers, capsules, agents and music kits.

Use direct Steam only for capabilities that pass qualification. Retain working, qualified SteamApis fallback where free access permits.

Select fresh, valid evidence across qualified sources without adding their quantities together. Changing sources must not replenish already consumed paper-trading depth.

**Done when:** existing coverage survives, additional noncase items work, and variant matching and quantity calculations pass tests.

## Task 5 — Progressive search and daily exploration rules

Each research batch contains up to **300 distinct item variants**, approximately four promising selections for every one overlooked selection.

Select candidates for both:

- Buying on DMarket and selling on Steam.
- Buying on Steam and selling back on DMarket.

After each batch, calculate routes using **all available fresh detailed evidence**, including combinations across batches.

If no route passes the existing positive-net rules:

- Continue with another batch while the run’s time limit and provider availability permit.
- Preserve progress rather than selecting the same unsuccessful batch again.
- Report when time, provider restrictions or exhausted candidates end the search.

Apply the agreed daily rules in `Europe/Chisinau` local time:

- A fully checked unsuccessful batch before 19:00 waits until the first check at or after 19:00.
- If unsuccessful again that evening—or first checked after 19:00—it waits until tomorrow.
- Missing essential prices means **incomplete**, not unsuccessful.
- Preserve exploration progress throughout the day; reset the daily selection record at midnight, or on startup if the PC was off.
- Preserve market history, predictions, funds and routes through that reset.
- Active-route checks remain exempt from these research deferrals. Аннотация 1

Deferral affects **collecting that batch again**, not using its existing fresh quotes. A later batch might supply the missing partner for a profitable route.

**Done when:** unsuccessful batches do not trap exploration, evening/day changes work correctly, and cross-batch routes remain discoverable

## Task 6 — Verification and installation

Run the existing suite and new tests covering:

- Worker recovery, hourly scheduling, startup, unlock checks and pause.
- Removal of old limits, request pacing, cooldowns and run duration.
- Progressive batches, evening deferrals, midnight reset and incomplete data.
- Bulk hints remaining separate from detailed evidence.
- Exact variants, multiple price levels and provider fallback.
- Unchanged fees, ranking, balances, frozen predictions and paper depth.
- Large-catalogue performance.

Compare old and new selection using the same recorded data and request budget. Measure useful fresh coverage, priceable routes, failures and request use—not just raw request volume.

Then perform a bounded live check. Stop affected collection on throttling; report whether the proposed operating pace actually works.

Back up before installation, verify an isolated copy of the journal, and confirm that existing funds, routes and deliberate pauses survive.

**Done when:** working coverage is preserved, broader support is demonstrated, calculations remain correct, and live limitations are reported honestly.

Remote hosting remains deferred in [REMOTE_COLLECTION.md](C:/Users/Legion/Documents/stuff/repos/arbitrage-bot-v2/docs/future-plans/REMOTE_COLLECTION.md). Automatic trial selection, CSFloat, other games, premium valuation and real trading remain outside these tasks.