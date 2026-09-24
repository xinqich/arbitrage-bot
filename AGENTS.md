# arbitrageV2 project guidance
## Ground rules

- **Code style.** Write new code in a clear, conventional style. The existing code is extremely
  dense (no spaces around operators, multiple statements per line, imports inside functions). Do
  **not** imitate it and do **not** restyle it. Change existing code only when genuinely necessary,
  and state the reason in one or two sentences before doing so.
- **A production journal with real records exists** at `data/research.sqlite3`. Do not run
  `arbitrage-v2 web`, `collect`, `paper-step`, `route-event`, or `csfloat-collect` against it
  without asking. The local desk may be running and holding the OS collection lock.
- **Never weaken evidence requirements to make something parse.** An unsupported page must fail
  explicitly, scoped to that item. Substituting a neighbouring variant, a different provider's
  quantities, or a guessed price is out of bounds.
  

Read docs/APPROVED_DESIGN.md and docs/IMPLEMENTATION_STATUS.md before extending this project.

The approved design has no fixed evaluation window, automatic stop-loss, interim P&L, or automated risk accounting. Only resolved routes produce P&L and learning outcomes. Keep open routes visible and preserve original predictions.

DMarket Tradable proceeds count, with their restrictions retained. Steam operations can be manual. This project currently sends read-only market requests and records operator events.

V1 is a read-only reference. Do not import its code or database, copy credentials, or treat historical proposals as current instructions. Synthetic and recorded evidence, and paper and confirmed outcomes, must remain separate.

Test money, lifecycle, replay, provenance, and provider contract changes. Do not weaken evidence requirements to produce positive routes.

