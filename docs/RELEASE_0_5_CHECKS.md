# Version 0.5 checks

Verified September 15, 2026.

- 98 unique automated tests pass, including 77 existing tests. JavaScript syntax and PowerShell parsing pass.
- The real journal was replayed from a SQLite backup. The original trial's asset key `730:Fracture Case` is supported by the automatic paper engine.
- All 3,086 pre-release prediction/route/event/outcome records were checked unchanged after publication. SQLite integrity check returned `ok`.
- Initial backup: `data/backups/release-0.5-20260915T061610Z/research.sqlite3`; the same directory contains previous changed files and a manifest. No v1 file was edited; its Git status/diff had no changes.
- Local HTML, CSS, JavaScript and status API returned HTTP 200. Every fixed JavaScript element reference exists in the HTML. The page is bound to 127.0.0.1:8765.
- Pause was saved, the managed worker was restarted, and pause remained active. Launching again reused the same PID. Desktop and sign-in shortcut targets/arguments were inspected. A full Windows reboot/sign-in was not performed.
- The worker was resumed. At 06:17:34 UTC it completed 15 successful requests and produced 16 conditional choices using the remaining 65 paper DMarket cents. This is an operational check, not a trading result.
- Start and Withdraw page searches return explicit CSFloat evidence/payout blockers. Grow returns conditional results. The stale Slate observation was excluded; acceptance rules were not relaxed.
- The paper trial still has 17 Fracture Cases. Its next eligibility is September 15 at 17:46:29 UTC / 20:46:29 Chisinau. No outcome or confirmed trade was created.
- Local POST checks cover required session token and Origin, unexpected fields, allowed report paths and Host restrictions. The app's user flows and layout were not visually tested in a browser. Optional WebMCP runtime registration remains unverified.

The worker remains running and unpaused. Its next ordinary check follows the six-hour schedule, with a separate check due at paper eligibility. Closing the browser does not stop it. No fixed time limit or automated loss rule was introduced.
