# Direct Steam variants — qualification record

**Everything below is describing a feature that has been lost to a repo reset. Data from this file must not be used as documentation on what is present in the project, only as a specification for a missing feature.**

Date: 2026-09-22 (UTC). Qualification used a separate temporary SQLite journal and the normal `CollectionBatch` / `request_context` transport hooks. Requests were anonymous HTTP GET requests with `currency=1` and `l=english`; no cookies, account values, JavaScript execution, proxy, or Steam login was used. The temporary journal is not the production journal.

## Commands

```powershell
# Separate temporary journal; production journal is never supplied here.
& .\.venv\Scripts\python.exe -B -c "... CollectionBatch(temp_journal, ..., {'steamapis_fallback_enabled':False}, ...).ensure(matrix)"
```

The matrix used the normal `CollectionBatch.ensure` request context, so every initial request, Steam redirect, and filtered follow-up was counted and paced from the previous completion. It made 25 direct Steam HTTP requests. All requests used `currency=1`, `l=english`, `Accept-Encoding: gzip`, and the normal anonymous headers. No cookies, login, proxy, browser automation, or JavaScript execution was used. Raw responses were not retained; the sanitized replay subset is [qualification_matrix_2026-09-22.json](../tests/fixtures/steam_public/qualification_matrix_2026-09-22.json).

## Results

| Exact name | Format / result | Exact book | History |
| --- | --- | --- | --- |
| Kilowatt Case | commodity | complete_reported_book | available |
| AK-47 \| Slate (Field-Tested) | grouped variant; observed `Quality=normal`, `Exterior=WearCategory2` follow-up | complete_reported_book | available |
| AK-47 \| Slate (Factory New) | grouped variant; independently valid query from the same fresh group page | complete_reported_book | available |
| StatTrak™ AK-47 \| Slate (Field-Tested) | grouped page, `not_marketable` | unsupported | unavailable |
| Souvenir AWP \| Pink DDPAT (Field-Tested) | grouped page, `not_marketable` | unsupported | unavailable |
| Sticker \| Crown (Foil) | commodity | complete_reported_book | available |
| Paris 2023 Legends Sticker Capsule | commodity | complete_reported_book | available |
| Sir Bloody Miami Darryl \| The Professionals | unsupported public SSR shape, `malformed_market_data` | unsupported | unavailable |
| Music Kit \| Denzel Curry, ULTIMATE | commodity | complete_reported_book | available |

The largest fresh decoded grouped page was 4,390,653 bytes (Souvenir); the largest successful grouped page was 4,143,632 bytes. Both remain below the 8 MiB encoded and decoded limits. The archived roughly 5.64 MB page remains research evidence only. If a future qualified capture exceeds 8 MiB, collection must stop and document it before either limit changes.

For every successful book, page and book `eCurrency` were integer `1` (USD cents); reported best prices agreed with the first compact level; compact pairs were price/quantity increments; and visible compact quantities summed to the reported side total. This supports `complete_reported_book`, not an assertion of unbounded market depth. Empty sides remain empty. The embedded millisecond order-book timestamp is the histogram observation time; download/retrieval time is never substituted. Chart history is independently optional and does not invalidate a book.

The grouped Slate request demonstrated the exact safe sequence: the initial redirected group page supplied one exact bucket and its observed string pairs, then one filtered URL was derived from those pairs only. No float, seed, sticker, attached-asset, or neighbouring-wear data was used. A group page can serve another title from the same run only after that title's exact description and order-book query independently validate; it retains the original retrieval and embedded observation timestamps.

Direct Steam is the preferred detailed provider. SteamApis is a fallback only after direct item-format failure and its existing account response explicitly confirms overage is disabled. Each capture stays provider-isolated; no bid/ask level or quantity is ever combined across providers. Unsupported variants remain explicit item failures rather than substitutions.
