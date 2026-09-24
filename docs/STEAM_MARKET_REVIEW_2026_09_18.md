# steam-market source review — September 18, 2026

Requested: assess replacing SteamApis with https://github.com/vladpuz/steam-market for Steam discovery.

## Finding

Direct Steam search works without SteamApis credentials. The repository is not a working drop-in replacement for the bot's complete Steam evidence collection. Its search method is useful, but its listing-page parser expects old page markers absent from the current skin page tested. Do not change the working provider configuration to this package without adapting and verifying the new Steam page format.

Inspected upstream commit: `eb892dae0c90ed284a06adc416681be926ea525d` (February 12, 2026), package version 3.0.4. Source was downloaded and read; no package was installed or executed.

## Read-only live checks

| Check | Observed result |
| --- | --- |
| Anonymous `/market/search/render`, app 730, start 0, count 100 | HTTP 200, success true, 10 results, reported total 35,506 |
| Same search, start 10, count 100, explicit USD request | HTTP 200, success true, start 10, page size 10, 10 results |
| `AK-47 \| Slate (Field-Tested)` listing URL | HTTP 302 to a Steam bucket-group listing |
| Redirect destination, initial bounded read | HTTP 200; exceeded the initial 4 MB probe limit |
| Redirect destination, second bounded read | HTTP 200; complete 4,148,680-byte page; current server-rendered data present |

The complete skin page contains neither `Market_LoadOrderSpread(` nor `line1=`. Those are the exact markers used by the repository's item-ID and embedded-history extraction. Its methods would reject this captured page. The new page carries several wear/StatTrak identities; its embedded order book in this response belongs to Factory New, while the requested title was Field-Tested. An adapter must select and validate the exact variant instead of borrowing a different variant's prices.

At the observed 10-result page size, a pass over the reported result count would require approximately 3,551 search requests, before detailed price checks. This is not a throughput benchmark or a guarantee that the catalogue remains static. Search listing totals are not quantities available at the best price, and search alone does not supply the two-sided depth needed by route calculations.

Five Steam HTTP requests were made, plus two GitHub source requests. No login, cookies, trades, package installation, proxy rotation or rate-limit stress test was used. The anonymous public responses and probe scripts remain in the local temporary review directory; only request accounting and the qualification summary are appended to the bot's journal.

## Limits and current bot behavior

The library has no subscription quota, but it forwards requests to Steam. Its client code provides no throttle/retry queue and does not establish an unlimited upstream allowance. A replacement still needs bounded requests, caching and handling for refusals or throttling; no sustainable request rate was established by these few checks.

The bot already uses direct Steam pages for its case seeds. DMarket supplies the current discovery catalogue; SteamApis supplies detailed checks for most newly discovered titles. SteamApis catalogue access was denied during the earlier qualification. Replacing the library alone would not remove the bot's separate configured limits: 1,000 total requests, 1,000 public Steam requests, 100 SteamApis requests, 15 requests per research cycle and 5 Steam requests per cycle. These are local application limits, not verified provider account quotas.

## Recommended implementation direction

Extend the existing Python direct-Steam adapter rather than install this outdated listing parser unchanged:

1. Use paginated Steam search for title discovery and initial asking-price screening; persist the actual returned page size, progress and timestamps. Intersect exact names with DMarket availability.
2. Adapt detailed Steam collection to current item groups and exact wear/StatTrak/Souvenir variants. Validate currency, identity and bid/ask quantities on recorded responses before changing the default source.
3. Keep aggregate listings and chart history separate from executable depth or confirmed sales. Preserve existing routes and frozen evidence.
4. Keep bounded collection and backoff. Review the bot's local allowance separately from SteamApis access; do not promise unlimited or instantaneous full-market coverage.

This review changes no collection source, schedule, settings, prediction, route, funds or outcome.

## Source

[Inspected client implementation](https://github.com/vladpuz/steam-market/blob/eb892dae0c90ed284a06adc416681be926ea525d/src/SteamMarket.ts): `search`, `listings`, `itemOrdersHistogram` and the constructor. Live observations above are from the user's machine on September 18, not claims taken from the README.
