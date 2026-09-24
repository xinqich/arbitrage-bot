# Bounded Steam request experiment

This separate diagnostic uses the bot's existing anonymous Steam market-page reader, User-Agent, redirects and strict price parser. It sends no credentials, makes no trades, changes no collection limits, and stores no diagnostic prices in the production evidence. Actual HTTP attempts, including redirects, count against the existing request allowance.

Pause collection in the local webpage and wait for any running scan to finish. The tool refuses to start while collection is running or a saved Steam provider/endpoint restriction exists. It checks that collection remains paused throughout the experiment. It leaves collection paused afterwards for review.

From the v2 directory:

```powershell
.\.venv\Scripts\python.exe scripts\steam_rate_probe.py --intervals 30,15,10,5,2,1 --samples 5
.\.venv\Scripts\python.exe scripts\steam_rate_probe.py --run --intervals 30,15,10,5,2,1 --samples 5 --http-budget 80
```

The first command prints a plan without network requests. The second runs it. Intervals are measured between the start of item checks; redirects happen as the bot normally follows them, and requests never overlap. At a stage boundary the previous slower interval is preserved. Slow responses can make the actual pace slower than the requested interval.

Stops at the first 401, 403, 429, other unsuccessful response, unusable/challenge page, exhausted request allowance, or operator resume. There are no retries. An absolute 15-minute limit and at most 100 HTTP attempts also apply. Press Ctrl+C to stop. Reports are saved incrementally under `data/steam-probes/` and include status codes, elapsed times and a small allowlist of headers. Cookies and response bodies are not saved. `Retry-After` may be missing even on 429; a missing header does not mean a zero wait.

The current configured direct-Steam seeds are four cases, used in rotation. Repeating a few pages can hit caches; this does not test a large set of distinct items or other Steam endpoints. Five checks at a pace are a small sample. A successful run establishes only that these particular checks succeeded at this time and address. It cannot establish an exact, permanent or generally safe limit, or justify immediately changing production pacing. Do not run again through a restriction, switch addresses, or change identity to bypass it. Review a supplied Retry-After and the bot's provider cooldown before normal collection resumes.
