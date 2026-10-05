# SQLite WAL restore lab

A copied SQLite database can open normally and return `integrity_check = ok`
while missing a committed transaction. Reproduce that false confidence, repair
it with SQLite's backup API, and keep the content checks as a regression test.

This is a small **synthetic repair lab**, not a backup program for your files.
It accepts no database path, uses only Python's standard library, creates its
own temporary databases, and makes no network requests.

## Run

Python 3.12+ with its `sqlite3` module and a writable local temporary directory:

```sh
python3 -B lab.py
python3 -B lab.py --json
python3 -B -m unittest discover -s tests -v
```

No pip install, SQLite CLI, server, account, or API key is needed. The tests run
offline after Python is installed. Execute from this repository, or invoke
`lab.py` by its absolute path. All generated databases and sidecars are cleaned
when the command exits normally, including handled setup/execution failures.
As with any process, force-kill, power loss, or filesystem failure can prevent
cleanup. Python bytecode caches are avoided by the shown `-B` option.

Expected text output includes:

```text
wal-live: completed
  main-file-copy: integrity=True sentinel=False expected-content=False
  backup-api: integrity=True sentinel=True expected-content=True
```

Default exit 0 means **the experiment detected the bad copy and verified the
fixed example and controls**. It does not mean the bad backup passed.

Run the examples individually on the live-WAL fixture:

```sh
python3 -B lab.py --method backup-api --json       # exit 0
python3 -B lab.py --method main-file-copy --json   # expected exit 1
```

Exit 1 is a completed content-check failure. Exit 2 is setup/execution failure
(or invalid CLI input). Treat an unavailable WAL mode, failed checkpoint, failed
copy, or failed cleanup as a broken/unsupported experiment, not evidence that a
bad backup was detected. Reports distinguish per-case `setup_error` and
`execution_error`; the top-level status groups both as `execution_error`.

## The controlled contrast

The fixture models one inventory item, its event ledger, and commit markers.
A baseline contains quantity 5, an event of +5, and the baseline marker. The
next committed transaction changes quantity to 8, adds +3 and a sentinel.

1. `wal-live`: create the baseline in WAL mode and checkpoint it. Disable
   automatic checkpointing, commit the second transaction, confirm its content
   through a separate reader, and keep the source connection open. Copy only
   the main file. It is structurally sound and its old quantity still matches
   its old event sum, but it lacks the committed sentinel and expected content.
   Backing up through the standard API includes the latest committed state
2. `wal-checkpointed`: explicitly checkpoint after the second commit, then run
   both examples without concurrent writes. Both pass. This control proves
   why an accidentally checkpointed test can hide the original bug
3. `delete-quiescent`: use rollback-journal DELETE mode, commit, and run both
   examples without concurrent writes. Both pass. This is a quiescent control,
   **not** an endorsement of copying a live rollback-journal database

There are no race-generating sleeps, threads, production inputs, or manual WAL
deletions. Every case owns a new directory. The source remains open throughout
both backup operations; its application snapshot is checked again afterward.
This checks logical source content, not byte-for-byte filesystem immutability.

The report keeps these independent checks visible:

- Physical/structural `PRAGMA integrity_check`
- `PRAGMA foreign_key_check`
- Application invariant: inventory equals the sum of recorded events
- Known committed sentinel
- Exact expected application rows

Neither structural integrity nor an internally consistent old ledger proves
freshness. The known expected transaction makes this fixture's assertion
possible. Production applications need their own recovery point and content
contract; copying this sentinel alone would not prove theirs.

## Repair an application's test

Read the small contrast in [examples/main_file_copy.py](examples/main_file_copy.py)
and [examples/backup_api.py](examples/backup_api.py), then the fixture and
`snapshot()` in [lab.py](lab.py).

In an isolated test using synthetic application data, replace the naive copy
with your application's backup path. Preserve the open source connection,
known checkpoint, separately confirmed committed transaction, and fresh output
location. Reopen the result and assert the application's expected committed
content as well as its structural and semantic invariants. Keep the bad-copy
control so a checkpoint or setup change cannot silently weaken the test.

The API example rejects pending source/destination transactions. It is not a
complete production recipe: output publication, destination overwrite policy,
locking/retry deadlines, encryption, retention, power-loss durability and
restore deployment remain your application's responsibility. This lab loads
only its two trusted built-in examples and does not run arbitrary adapters.
See [SKILL.md](SKILL.md) for a task-focused agent workflow; it is a repository
instruction file, not an automatically installed personal skill.

## JSON contract

For valid CLI arguments, `--json` emits exactly one object on stdout. Invalid
arguments emit usage on stderr and no JSON report. `schema_version` is `1`; runtime
versions are recorded rather than assumed. `method`, `passed`, `status` and
`cases` are stable top-level fields. Each completed case contains `setup`,
`methods`, `source_unchanged`, and `temporary_files_cleaned`. Method reports
include `checks`, `content`, `integrity_check`, `foreign_key_violations` and
`stock_imbalances`. Error cases include an `error` object and may lack fields
whose work never completed. No temporary paths or identifiers are emitted in
successful reports; operating-system exception text can include a temporary
path on failure. Error text is diagnostic, not a stable API.

## Verification and scope

Locally exercised on Linux with Python 3.12.14 / SQLite 3.53.1 and Python
3.13.5 / SQLite 3.46.1. The workflow runs a modest Linux Python 3.12.14 and
3.13.5 matrix, records its actual linked SQLite versions, and tests both CLI
exit controls. CI runtime installation uses the runner's network; the lab and
its tests do not. There are no package dependencies, caches, uploaded
artifacts, secrets, or write-permission steps.

This demonstrates the chosen synthetic states. It does not test concurrent
checkpoint races, crash recovery, hostile databases, network filesystems,
Windows/macOS behavior, all SQLite builds, or an application's backup system.
It is not a sandbox, data-recovery certification, or a claim of adoption.

## Existing tools and references

- [SQLite WAL documentation](https://www.sqlite.org/wal.html) explains committed
  changes in WAL and why the sidecar belongs with the database
- [SQLite Online Backup API](https://www.sqlite.org/backup.html) supplies the
  actual mechanism. [Python's backup method](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)
  exposes it without another package
- [VACUUM INTO](https://www.sqlite.org/lang_vacuum.html#vacuuminto) and
  [sqlite3_rsync](https://sqlite.org/rsync.html) are other existing backup choices
- [Litestream](https://litestream.io/) handles SQLite replication;
  [RestoreVerify](https://github.com/RestoreVerify/restoreverify) offers broader
  backup/restore verification workflows

This project adds a compact deliberate-failure regression example. It does not
replace these tools or claim a new backup algorithm.

## Maintenance

Initial scope is deliberately small. Concrete reproduction failures and useful
integration feedback can justify changes. Quiet issue activity does not establish
use or lack of use; no telemetry is collected. No expanded database engine,
platform matrix, or indefinite feature roadmap is promised. MIT licensed.

---

Maintained by the MyTest project. Optional unrelated break:
[MyTest](https://mytest.app/play-guide), a voluntary rock-paper-scissors page.
Opening it is never needed to run or repair this lab. The lab and agent workflow
do not contact MyTest.
