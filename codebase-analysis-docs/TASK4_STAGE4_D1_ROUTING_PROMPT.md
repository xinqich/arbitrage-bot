# Implementation prompt — Task 4, Stage 4 + D1: provider routing and consumed-depth protection

> Paste the section below into a fresh Claude Code session in `arbitrage-bot`.
> Everything the session needs is stated here; it starts cold.

---

## Context

You are working on **arbitrage-bot** (package `arbitrage_v2`), a local-only, read-only CS2 market
research and bookkeeping tool. It never places orders; every real trade is manual. State is
event-sourced into one append-only SQLite table (`data/research.sqlite3`) protected by
`RAISE(ABORT)` triggers.

The governing task is `codebase-analysis-docs/TASK4_IMPLEMENTATION_PROMPT.md` — **broader direct
Steam collection**. Read its Context, Ground rules, Stage 4, D1 and "Traps worth restating"
sections before writing code.

Task 4's stage status, as of commit `250ff87` (v1.0.5.3 plus its review fixes):

| Stage | Status |
| --- | --- |
| 0, 0.5 — discovery and endpoint equivalence | complete |
| 1 — parser extension | complete |
| 2 — failure scoping | complete |
| 5a — grouped-parser fixtures and tests | complete |
| 3 — run-local grouped-page reuse | complete |
| **4 — provider routing** | **this prompt** |
| **D1 — block paper steps on a provider switch** | **this prompt** |

This is the last implementation work in Task 4.

## Why these two ship together

They are one change. D1's failure mode — a provider switch silently replenishing already-consumed
paper-trading depth — is **unreachable today** because `request_for` forces every
catalogue-discovered title to SteamApis and everything else to the watchlist's Steam source, so one
title never gets `details` captures from both providers.

Stage 4 is exactly the change that makes both providers produce `details` for the same title.
Landing it without D1 opens the hole the spec explicitly forbids: *"changing sources must not
replenish already consumed paper-trading depth."* Do not split them.

## Before you start

Three Stage 3 review findings were fixed in commit `a38c1ca` and are already in the tree: the
`bCommodity` type check (`type(...) is not bool`, not a `(True, False)` membership test that
accepts `1`/`0`), `GroupPageCache.get` scanning newest-first, and the cache's corrected memory
figures (~6-8 MB per entry, ~25-30 MB for the cap of 4 — a `pickle.dumps` measurement had
understated it ~7x). D3a was then declined deliberately against the real number. Nothing there
needs redoing; it is noted only so you recognise that code as intentional.

## Scope

**In scope**

- `collection_batch.request_for`: stop forcing catalogue-discovered titles to SteamApis.
- A SteamApis fallback that fires only after a direct-Steam **item-format** failure, implemented as
  a visible second request in the batch.
- D1: `paper.step` returns `waiting_for_evidence` when the winning capture's provider differs from
  the one that fed a route's already-consumed depth.
- Tests for both.

**Out of scope**

- Any collection run against `data/research.sqlite3`.
- Changing the capacity key, or adding any field to `paper_settings` (see "Why blocking, not
  pinning" below).
- Revisiting D2 (StatTrak / Souvenir / agents stay unsupported on the direct path).

## Ground rules

- **Code style.** Write new code in a clear, conventional style. The existing code is extremely
  dense (no spaces around operators, multiple statements per line, imports inside functions). Do
  **not** imitate it and do **not** restyle it. Change existing code only when genuinely necessary,
  and state the reason in one or two sentences before doing so. Stage 4 and D1 both require editing
  existing code — that is expected and justified here.
- **Testing.** `& .\.venv\Scripts\python.exe -B -m unittest discover -s tests -q` —
  **326 tests, ~32s, currently green.** Keep it green.
- **Line endings.** Check that `git diff --ignore-cr-at-eol --numstat` matches
  `git diff --numstat` before finishing.
- **No release ritual.** No version bump, no `RELEASE_*_CHECKS.md`, no backup ceremony.
- **A production journal with real records exists** at `data/research.sqlite3`, including a live
  paper trial (`paper-fracture-kilowatt-20260908`). Do not run `arbitrage-v2 web`, `collect`,
  `paper-step`, `route-event`, or `csfloat-collect` against it. Nothing here needs network access
  or a real journal.
- **Never weaken evidence requirements.** Blocking a step is the correct outcome when provenance is
  ambiguous. Do not reconcile depth across providers to keep a route moving.

## Read before writing code

1. `AGENTS.md` — governing constraints. Short.
2. `codebase-analysis-docs/TASK4_IMPLEMENTATION_PROMPT.md` — Stage 4, D1, D2, Traps.
3. `arbitrage_v2/collection_batch.py` — `request_for`, `request_key`, `failure_scope`, `scope_key`,
   `CollectionBatch.ensure`.
4. `arbitrage_v2/collector.py::free_access`.
5. `arbitrage_v2/paper.py` — `_capacity` (line 57), `step`, and the `consume` call at line 213.
6. `arbitrage_v2/depth.py` — `available`, `consume`.
7. `arbitrage_v2/prediction.py` — `steam_sale_snapshot`, `return_snapshot`, `destination_snapshot`,
   `_market_captures`.
8. `arbitrage_v2/capture_selection.py::select_captures`.

---

# Stage 4 — provider routing

## The current behaviour is the opposite of the spec

`collection_batch.request_for` (line 20):

```python
provider = (watchlist.get("item_sources", {}).get(title, watchlist.get("steam_source", "steamapis"))
            if kind == "details" else "dmarket")
if (kind == 'details' and watchlist.get('catalogue',{}).get('enabled')
        and title not in {r['title'] for r in watchlist['items']}
        and title not in watchlist.get('item_sources',{})):
    provider='steamapis'
```

Every catalogue-discovered title is forced to SteamApis. The spec says direct Steam is **preferred**
and SteamApis is a fallback used only after a direct item-format failure, and only when the account
response confirms overage is disabled.

**Make the minimal change:** drop the catalogue override so catalogue-discovered titles resolve the
same way watchlist items do. Leave the `steam_source` default and the per-title `item_sources`
override alone — those are operator settings, and changing their meaning is not part of this task.

## The fallback is a visible second request

Per the governing prompt: *"Design the fallback as a visible second request in the batch, not a
hidden inner call, so it is paced, counted, and recorded like any other request."*

- **Trigger:** a `steam_public` `details` request whose failure is **item-scoped** —
  i.e. `failure_scope(request, result) == 'item'`. Do **not** fall back on provider- or
  endpoint-scoped failures: a 429, a 5xx or a `network_*` means Steam is unreachable or throttling,
  not that this item needs a different source, and the whole provider is already deferred.
- Note this deliberately *does* include D2's unsupported categories. A StatTrak or Souvenir title
  fails the direct path item-scoped, and handing it to SteamApis is correct — D2 says only that
  those pages are not a defect *of the direct parser*, not that the item may never be sourced
  elsewhere.
- **Do not** fall back when the title already routes to `steamapis` (there is nothing to fall back
  to), and fire the fallback **at most once** per title per run.
- Implement it inside `CollectionBatch.ensure` so it flows through `begin_request` — paced,
  counted, stop-checked, and journalled as a `request_attempt` like any other request. `ensure`
  iterates a fixed list, so either collect fallbacks and drain them after the main loop or switch
  to an index-based loop; say which you chose and why.

## Two properties to verify rather than assume

- `scope_key(request, 'item')` begins with `request['provider']`, so a direct-Steam item failure
  produces `steam_public:details:...` and cannot block a `steamapis:details:...` attempt for the
  same title. The governing prompt asks you to *verify that holds* — assert it in a test, do not
  just reason about it.
- `ensure` already runs the `free_access` / `steamapis_verified` gate for any `steamapis` provider
  request, so a fallback inherits the overage check without new code. Confirm `overageEnabled is
  False` is still required before a fallback can proceed, and that a failed check records a failure
  rather than silently skipping.

Note SteamApis spacing is 30 s (`request_spacing_seconds`), so fallbacks are not free in wall-clock
terms. Do not adjust spacing or `research_batch_size` — D3 settled that those stay as they are
until real coverage numbers exist.

---

# D1 — a provider switch must not replenish consumed depth

## The mechanism, stated precisely

The interaction is not obvious, so here it is end to end:

- `capture_selection.select_captures` keys on `(app_id, title, kind)` and partitions its
  success-fallback by `input_kind` — **not by provider**. The newest capture wins, so the effective
  source of a `details` book can flip between runs.
- `paper._capacity` (line 57) builds its key as
  `f'{ENGINE}:{snapshot["input_kind"]}:730:{snapshot["title"]}:{name}'` — **also no provider**.
- `depth.available` replenishes capacity on any observed positive change in visible quantity
  (`change = new - old["visible"]`, then `max(0, old["available"] + change)`).

So when a route has consumed depth from a multi-level `steam_public` book and a `steamapis` capture
later wins selection, the old `level_id`s vanish and new ones appear as a positive change —
**silently refilling consumed paper capacity.**

**Only Steam-sourced books are at risk.** `steam_bid` (from `steam_sale_snapshot`) and `steam_ask`
(from `return_snapshot`) come from a `details` capture, whose provider can be `steam_public` or
`steamapis`. `dmarket_bid` comes from a `targets` capture, which is always DMarket. Scope the check
accordingly.

## What to implement

1. **Carry the provider on the snapshot.** `steam_sale_snapshot` and `return_snapshot` in
   `prediction.py` already hold the capture (`capture['provider']`). Record which provider produced
   each Steam-sourced book.

2. **Record it when capacity is consumed.** `depth.consume` writes the `paper_book` payload. Put
   the provider at the **payload top level**, beside `key` / `state` / `fills`.

   **Never put it inside `state`.** `depth.available` iterates `state.keys() | observed.keys()` and
   treats every key as a `level_id`, then reads `old["visible"]` — a `provider` key there raises a
   `TypeError`/`KeyError` on a string. This is the easiest way to break this stage.

3. **Compare on the next step.** Add a helper to `depth.py` (e.g.
   `consumed_provider(journal, key, db)`) rather than changing `available`'s return contract, which
   `paper._capacity` unpacks as a 2-tuple.

   It must return the provider recorded on the most recent `paper_book` event for that key **that
   actually consumed depth** — i.e. one with non-empty `fills`. `paper.step` calls `consume` at
   line 213 unconditionally, including when `fill["fills"]` is empty, so *"an event exists"* is not
   the same as *"depth was consumed"*. **A route with no consumed depth has nothing to protect —
   do not block it.**

4. **Backward compatibility matters here.** A live paper trial
   (`paper-fracture-kilowatt-20260908`) already has `paper_book` events, and none of them carry a
   provider. Treat a missing provider as **unknown, and do not block** — recording starts from the
   next consume. Blocking on `None != 'steam_public'` would freeze every existing route on its next
   step.

5. **Where to raise.** `_capacity` already loads the `paper_book` state and is called from all three
   stages that consume depth (paper.py lines 140, 154, 189). Raising
   `ValueError('<named_reason>')` from there gives the required shape for free:

   - the `except (ValueError, KeyError, TypeError)` at paper.py:209 turns it into
     `{"route_id": …, "status": "waiting_for_evidence", "reason": str(exc)}`;
   - the `steam_wallet` comparison loop (lines 153–171) turns it into `waiting_for_evidence` with a
     `missing: [{"title": …, "reason": …}]` entry.

   Both are existing, operator-visible shapes. Choose a reason string that names the cause
   unambiguously — an operator reading it should understand that the source changed and the route is
   deliberately held, not that evidence is merely missing.

6. **Keep the capacity key unchanged.** Adding provider to
   `f'{ENGINE}:{input_kind}:730:{title}:{name}'` would give a switched route its own fresh, full
   pool — the exact opposite of the requirement.

7. **Surfacing needs no new plumbing.** `worker.py:375` already puts the step results into
   `worker_health` as `paper_steps`, so a returned `reason` reaches the Debug panel the same way
   every other paper-step status does. Do not build a parallel path.

## Why blocking, not pinning — do not re-litigate

Pinning the provider in `paper_settings` would add a field to a record written once at entry and
then frozen, and the existing live trial has no such field — that needs a migration or a default,
which is out of scope before v1. Blocking needs no schema change, is reversible (pinning can be
layered on later if blocking proves noisy), and matches how `paper.step` already handles inventory
ambiguity with `manual_decision` / `waiting_for_evidence` instead of guessing.

---

## Coverage to write

**Stage 4**

- A catalogue-discovered title resolves to the watchlist's Steam source, not unconditionally to
  SteamApis. Cover a watchlist with `catalogue.enabled` true, and one with an `item_sources`
  override, so the operator settings are shown still to win.
- An item-scoped `steam_public` `details` failure produces a second, visible `steamapis` `details`
  request in the same batch: assert it appears in `batch.results` **and** as a `request_attempt`,
  and that it went through pacing/counting rather than being an inner call.
- A provider-scoped failure (429, 5xx, `network_*`) produces **no** fallback.
- No fallback when the title already routes to `steamapis`; no more than one fallback per title.
- The fallback is still gated on `free_access` confirming `overageEnabled is False`, and a failed
  check records a failure.
- Assert directly that `scope_key` item-scope includes the provider, so the direct failure does not
  defer the fallback.

**D1**

- A route that consumed `steam_public` depth, then has a `steamapis` `details` capture win
  selection, returns `waiting_for_evidence` with the named reason — and records **nothing**: no
  movement, no asset change, no new `paper_book` event, no `paper_decision`.
- The same route with the same provider advances normally.
- A route with a provider difference but **no consumed depth** is not blocked.
- A route whose prior `paper_book` events carry no provider at all is **not** blocked (the live-trial
  shape).
- The provider is recorded at the `paper_book` payload top level and **not** inside `state`; assert
  `depth.available` still works unchanged after a consume.
- The capacity key is byte-for-byte what it was before this change.
- The reason reaches `worker_health['paper_steps']`.
- Synthetic and recorded evidence stay separated — `input_kind` is already in the capacity key, and
  this change must not blur that.

## Definition of done

- Full suite green: `& .\.venv\Scripts\python.exe -B -m unittest discover -s tests -q`. All 326
  existing tests still pass.
- Direct Steam is primary; the SteamApis fallback fires only on a direct **item-scoped** failure and
  only with overage confirmed disabled, and is visible in the batch as its own paced, counted,
  journalled request.
- No provider switch can replenish consumed paper depth; the block is explicit, named, and visible
  to the operator.
- Existing routes with provider-less `paper_book` history keep working.
- The capacity key and `paper_settings` are both unchanged.
- Your summary states: the reason string you chose, how you drained the fallback requests in
  `ensure`, and anything you found that contradicts this prompt.
