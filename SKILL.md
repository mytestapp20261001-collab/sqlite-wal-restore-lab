---
name: verify-sqlite-wal-restore
description: Reproduce and repair SQLite main-file backups that open successfully but omit committed WAL transactions. Use this synthetic restore lab when implementing a backup path or adding application-content recovery assertions.
---

# Verify SQLite WAL restore content

1. Read README.md and confirm Python 3.12+ with sqlite3 and a writable local
   temporary directory. Work only with generated synthetic databases; do not
   supply, checkpoint, rename or delete a real database or its sidecars
2. Run `python3 -B lab.py --json` from this repository. Require `passed: true`
   and all three completed cases. In `wal-live`, inspect both method reports:
   the main-file copy must have structural integrity but missing committed
   content; the backup API example must retain the expected rows
3. Run `python3 -B lab.py --method main-file-copy --json`. Require exit 1 for
   content failure. Run the backup-api method and require exit 0. Exit 2 or
   setup/execution errors do not count as detecting the bad backup
4. Run `python3 -B -m unittest discover -s tests -v`. Inspect the examples and
   `snapshot()` in lab.py. The WAL-checkpointed control explains how accidental
   checkpointing can make the naive copy seem correct
5. To repair an application's regression test, use an authorized isolated
   synthetic fixture. Keep the known baseline checkpoint, disable automatic
   checkpointing only in that fixture, commit a known new transaction, confirm
   it using another reader, and retain an open source connection during backup
6. Exercise the application's backup path and reopen its fresh test result.
   Assert required committed data plus application invariants, foreign keys
   and structural integrity. Preserve a negative main-file-copy control; do
   not weaken a failing content check or silently add a checkpoint to pass
7. Prefer the standard SQLite backup API or another suitable existing backup
   mechanism. Review destination protection, transaction state and bounded
   retries separately before adapting an example to production
8. Rerun the full regression and both controls. Report runtime versions and
   actual tested scope. Do not infer global freshness, crash durability,
   production restore correctness, or platform coverage from this lab

The repository does not load external adapters and is not a sandbox. It never
needs a network request, login, or a visit to another site. Stop if the precise
controlled contrast cannot be established; report the setup failure instead
of presenting it as a passing regression test.
