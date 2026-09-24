# Backup and restore

In **Controls & reports**, choose **Create backup**. The page shows the saved folder when its checks pass. Collection can continue while it takes this snapshot. Copy that entire folder to another drive if you want protection from a failed disk.

Each new backup contains `research.sqlite3`, the five public runtime settings files, and a manifest of file checksums, record counts and software version. It includes funding, routes, receipts, original predictions, paper settings, source observations and request history. It excludes credentials, the Python environment, logs, source code and the older standalone offline `evidence.sqlite3` store. Keep the matching v2 code/environment separately. A folder without a valid manifest is an incomplete backup.

## Check or create a backup from PowerShell

Run these commands from `C:\Users\Legion\Documents\stuff\repos\arbitrage-bot-v2`. Replace the sample folder with a new name; existing backup folders are never overwritten.

```powershell
& .\.venv\Scripts\python.exe -B -m arbitrage_v2 backup data/backups/my-snapshot
& .\.venv\Scripts\python.exe -B -m arbitrage_v2 backup-check data/backups/my-snapshot
```

Checking reads the copy, verifies every file and replays routes and funds. It does not change current history or spend market requests. Settings must stay unchanged while a backup is being made.

## Restore an earlier snapshot

Restoring replaces the current history with the saved history. Events after that snapshot will no longer be in the active journal. The program first saves a checked **before-restore** copy of the current history, then restores the snapshot and leaves collection paused. The old and new receipts are never merged automatically. Compare the saved newer history and your marketplace receipts before deciding to resume.

1. Check the backup. It must come from the same software version, and its settings must match the current settings. If settings differ, compare the five files in its `config` folder before deciding which settings/history pair to use.
2. Pause collection on the page. Wait for any current price check to finish, then stop the program. Close other command-line tools that write to this journal.
3. Restore the chosen snapshot explicitly, then restart the page:

```powershell
.\scripts\local.ps1 -Action Stop
& .\.venv\Scripts\python.exe -B -m arbitrage_v2 restore data/backups/my-snapshot --replace-current
.\scripts\local.ps1 -Action Open
```

4. Read the command result for the safety backup path. Check route holdings, receipt dates, real funds and original predictions. Account for any transactions made after the snapshot, then choose **Resume** yourself. Restarting alone keeps the restored journal paused.

Restoration refuses to run while the desk or collector holds the journal lock. It also refuses an invalid/partial backup or a current journal that cannot be backed up safely. Keep damaged files for inspection rather than overwriting them. For an isolated rehearsal, initialize a new file and restore into it:

```powershell
& .\.venv\Scripts\python.exe -B -m arbitrage_v2 --journal data/rehearsal.sqlite3 journal-init
& .\.venv\Scripts\python.exe -B -m arbitrage_v2 --journal data/rehearsal.sqlite3 restore data/backups/my-snapshot --replace-current
```

This leaves `data/research.sqlite3` unchanged. Do not start a collector against a rehearsal copy. Older release folders have a different manifest format and are retained as installation archives; this checker deliberately does not treat them as new-format backups.

## What was checked for v1.0

Tests cover settings and data tampering, version mismatch, partial backups, a running-worker refusal, keeping newer records in a safety copy, and paused restart without replaying a price-check request. Funding, a real plan, a purchase receipt and the original prediction survive restoration exactly. The live experiment was copied and restored only into temporary test files during release checks; its active history was not rolled back.
