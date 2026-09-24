Why Task 4 turns this from theoretical into likely

Today the two sources barely overlap — the Steam website handles your four watchlist cases, SteamApis handles catalogue-discovered items, and request_for keeps them in separate lanes. Task 4 deliberately makes the Steam website primary for everything, with SteamApis as the backup when a page won't parse. So both sources will routinely have data for the same item, and switching back and forth becomes normal rather than rare.

The three options, plainly
	What it does	Cost
A — Stop and ask (what I picked)	If the source changed since the trial last used up orders, the trial pauses and says so in Debug. You look and decide.	A trial can sit idle if sources flip-flop
B — Lock the source in	When a trial starts, write down which source it uses; refuse anything else for its whole life.	Needs a new field on a record written once and never changed — your existing live trial doesn't have it, so it needs a migration
C — Just prefer one	Try to stay on the Steam website; fall back quietly when it fails.	Doesn't actually prevent the bug, just makes it rarer

I chose A because it needs no change to already-frozen records, it's reversible (B can be layered on later if A proves annoying), and pausing-with-a-reason is how the paper engine already handles every other situation it can't resolve on its own.

Happy to switch to B if you'd rather the trials never stall — the migration is small, it just means touching a frozen record type, which is the thing we agreed to avoid before v1. Say the word and I'll update the prompt.