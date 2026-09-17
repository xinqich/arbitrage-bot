# Public Steam source qualification — 2026-09-08

The anonymous Steam Community Market page provides an alternative to the stale SteamApis books observed earlier. Two direct reads at 13:21 UTC returned explicit USD order books with server query timestamps less than two seconds before retrieval. This source supplies observations for research, not an execution guarantee.

| Item | Best buyer price and quantity | Best seller price and quantity | Chart points |
| --- | --- | --- | ---: |
| Fracture Case | $0.69 × 58 | $0.71 × 604 | 2,936 |
| Kilowatt Case | $0.18 × 20,198 | $0.19 × 446 | 1,657 |

Sources: [Fracture listing](https://steamcommunity.com/market/listings/730/Fracture%20Case?currency=1&l=english), [Kilowatt listing](https://steamcommunity.com/market/listings/730/Kilowatt%20Case?currency=1&l=english). These numbers describe the captured pages and will change.

## Contract checked

The parser reads the page's embedded JSON data and does not execute JavaScript. It selects the exact app/title description, order book, and price-history queries. The page currency, book currency, and chart currency must all identify USD; description identity and commodity/marketable flags must match. Redirects may remain only within Steam's HTTPS CS2 listing surface. Login pages, other hosts, unsupported structures, and mismatches fail closed.

Order-book prices are integer cents. The packed arrays alternate price and quantity. Both complete books were checked against their reported total quantities and best-price fields. Prices must be uniquely sorted and quantities positive. V2 continues using only best-level quantity; no acceptance rule was relaxed or lower depth silently added.

The order-book timestamp comes from the Steam server's query observation timestamp in the page. Fetching an old page does not replace that timestamp with the current clock. This is a different timestamp meaning from a provider's last scraper update and is recorded as such. Steam's underlying market cache age is not exposed. Actual venue confirmation is still necessary before a manual action.

The chart series contains aggregate medians and reported purchase counts, including fractional-cent medians. These prices are retained as observations, not rounded into executable quotes. Historical chart points are not individual transaction confirmations; their range and count do not prove complete daily coverage. Provider and synthetic partitions are separate, and repeated captures update the latest version per source/item/timestamp without multiplying quantities.

The archive keeps selected market fields, normalized observations, query timestamps, requested/final URLs, a hash of the decoded page, and HTTP date/age when provided. Global page state, cookies, account data, and page scripts are not archived. Recorded market-field fixtures cover two real pages; test-generated routes remain isolated from real observations.

## Provider cross-check

The SteamApis dedicated histogram endpoint for Fracture Case returned one snapshot, timestamped 06:02 UTC, with no further page. Its dedicated prices endpoint returned a one-row array, despite the documentation describing a price-history object. These checks did not repair the missing freshness or history. Three bounded diagnostic requests were used, including one repeated price request after the unexpected shape was discovered. [Histogram contract](https://docs.steamapis.com/items/histograms), [price-history contract](https://docs.steamapis.com/items/prices).

The default watchlist now selects `steam_public`. Public collection uses no Steam login, SteamApis key, or paid provider quota. DMarket requests remain authenticated read-only API calls. `--steam-source steamapis` deliberately selects the old provider; it is not an automatic fallback that can conceal public-source failures. Existing source timestamps, old predictions, and the journal history remain intact.

## Operational limits

An OS lock prevents duplicate collectors for a journal. The foreground watch loop stops on errors or resource exhaustion. Local allowances govern request use, not capital risk or experiment duration. The six-hour collection example fits the current bounded four-item research setup; it does not evaluate P&L on a six-hour schedule.

No complete profitable route has been demonstrated by this source qualification. The first milestone still requires complete outcomes after applicable costs and real delays, followed by evidence of repeatability.
