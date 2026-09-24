# Remote market evidence collection

Saved: 2026-09-19
Status: Deferred future proposal. 

## Purpose

Keep collecting market evidence while the user's PC is off. When the local bot starts, it should be able to retrieve observations gathered during that time instead of starting with only its older local data.

## Proposed split

- A remote collector gathers and stores market evidence on the configured schedule.
- The local webpage downloads that evidence and continues to handle routes, funds, predictions and manual actions locally.
- Local and remote collection are coordinated so they do not independently make duplicate requests for the same work.

Remote collection does not require moving the local webpage or the user's financial journal onto a public server. Collecting market data does not itself enter trades, advance confirmed transactions, resolve routes or record profit.

## Implementation outline for later

1. Separate market collection and evidence storage from the local page and route management. Reuse the qualified provider readers and their request controls.
2. Run the collector on a suitable host, with persistent storage, restart recovery and visible collection failures.
3. Add an authenticated connection for the local bot to download observations since its last successful sync. Preserve original observation times, provider references and stable record IDs; retries must not duplicate evidence. Download time must not make an old quote appear fresh.
4. Establish one collection owner for each provider/workload. Local fallback must not begin automatically merely because the PC cannot reach a remote collector that may still be running.
5. Show remote collection health, last successful synchronization and evidence age in the local page. Preserve gaps when collection failed; never invent observations for missing periods.

## Constraints to preserve

- Provider restrictions still apply. Hosting elsewhere does not bypass Steam limits or guarantee reliable access; collection must be qualified on the chosen host's connection.
- Keep real records, paper activity and synthetic tests separate. Synchronizing evidence must not reset consumed paper depth or replay transactions.
- Keep original route predictions unchanged. Profit/loss and prediction evaluation remain tied to resolved routes.
- Store only credentials required for collection on the remote host, protect them and exclude them from logs and downloaded evidence. No Steam login is required by the current anonymous reader.
- Use actual source timestamps and the applicable freshness rules. Remote history helps continuity but does not guarantee profitable routes or complete market coverage.

## Decisions left for the implementation proposal

Choose the hosting service and acceptable cost, deployment method, evidence transfer interface, authentication, retention/backups, and the explicit handoff between remote collection and any local fallback. No free always-on hosting option is assumed or selected here.

Check intervals, daily candidate rules and shortlist sizes belong to the separate collection plan; this document does not settle or change them.

## Completion checks

- Evidence continues to be recorded while the local PC is off and appears locally after reconnection.
- Repeated or interrupted synchronization produces no duplicate evidence or paper fills.
- Restarting either side preserves collection progress, cooldowns and synchronization progress.
- Local and remote instances do not duplicate collection work.
- Stale evidence and remote failures are clearly reported, and existing funds, routes and frozen predictions remain unchanged.

See [approved design](../APPROVED_DESIGN.md) and [development plan](../DEVELOPMENT_PLAN.md) for the project's current rules. This is a future proposal, not a new release requirement.
