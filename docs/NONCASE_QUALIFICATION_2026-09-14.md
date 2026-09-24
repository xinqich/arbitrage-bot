# Initial noncase source qualification

Read-only checks on September 14, 2026. This is source/identity qualification, not evidence of completed profitable routes.

| Exact item | Steam source time (UTC) | Steam bid / ask | Relevant DMarket evidence |
| --- | --- | --- | --- |
| Paris 2023 Legends Sticker Capsule | 14:00:34 | $0.11 / $0.12, top quantities 157 / 75 | 20 eligible exact-title offers; general bids include 3 at $0.14, then 40 at $0.03. |
| AK-47 | Slate (Field-Tested) | 14:03:30 | $5.12 / $5.72, top quantities 8 / 1 | One ordinary eligible offer survived restrictions; general bids include 1 at $3.65 and 84 at $3.64. |

These were within the four-hour freshness limit when checked. They are historical snapshots now and are not usable as fresh prices indefinitely.

The public Steam requests for `Sticker Capsule` and the Slate skin returned HTTP 200 but only an application shell, without qualified market data. The existing SteamApis alternative returned explicit identities, commodity flags and order books. Its quantity semantics below the best level have not been independently qualified, so these items use only the top Steam level. DMarket levels retain their observed incremental quantities.

DMarket's broad `Sticker Capsule` query returned other capsules among the first 20 offers. Exact title checks correctly excluded them; a specific Paris title was then checked. Skin Targets with FT-0/FT-1 restrictions are excluded. Only the explicitly general `floatPartValue=any, paintSeed=any, phase=any` contract is admitted for the qualified skin. Locked offers, decorated/premium offers, and floats outside the Field-Tested range are excluded. No float/sticker premium is assumed in the route value.

Nine authenticated read-only market requests and two public page requests were made. The selected API fields are captured in the qualification archive and replayed in synthetic contract tests. The requests are included in the operational allowance when the release imports the archive. Raw account credentials were not saved.

The worker rotates one of these two titles in addition to the trial's four case titles. This is a small initial roster covering cheap and larger individual items; a broad catalogue or other games are not yet qualified. The existing paper basket remains unchanged.
