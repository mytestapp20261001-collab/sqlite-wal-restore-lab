"""Offline, synthetic-only SQLite WAL restore lab. No database path input."""
import argparse
from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
import platform
import sqlite3
from tempfile import TemporaryDirectory

from examples.backup_api import backup_to_connection
from examples.main_file_copy import copy_main_file

SCHEMA_VERSION = 1
CASES = ("wal-live", "wal-checkpointed", "delete-quiescent")
METHODS = ("main-file-copy", "backup-api")
EXPECTED = {
    "stock": [["widget", 8]],
    "events": [[1, "widget", 5], [2, "widget", 3]],
    "markers": [[1, "baseline"], [2, "committed-sentinel"]],
}
BASELINE = {
    "stock": [["widget", 5]],
    "events": [[1, "widget", 5]],
    "markers": [[1, "baseline"]],
}


class SetupError(RuntimeError):
    """The controlled experiment's preconditions did not hold."""


def connect(path):
    return sqlite3.connect(path, timeout=1.0, isolation_level="DEFERRED")


def snapshot(connection):
    """Inspect the synthetic application's structure, content and invariant."""
    content = {
        "stock": [list(r) for r in connection.execute("SELECT sku, quantity FROM stock ORDER BY sku")],
        "events": [list(r) for r in connection.execute("SELECT id, sku, delta FROM events ORDER BY id")],
        "markers": [list(r) for r in connection.execute("SELECT id, label FROM markers ORDER BY id")],
    }
    integrity = [r[0] for r in connection.execute("PRAGMA integrity_check")]
    foreign_keys = [list(r) for r in connection.execute("PRAGMA foreign_key_check")]
    imbalance = [list(r) for r in connection.execute("""
        SELECT stock.sku, stock.quantity, COALESCE(SUM(events.delta), 0)
        FROM stock LEFT JOIN events ON events.sku = stock.sku
        GROUP BY stock.sku, stock.quantity
        HAVING stock.quantity != COALESCE(SUM(events.delta), 0)
    """)]
    checks = {
        "structural_integrity": integrity == ["ok"],
        "foreign_keys": not foreign_keys,
        "stock_matches_event_sum": not imbalance,
        "committed_sentinel": [2, "committed-sentinel"] in content["markers"],
        "expected_application_content": content == EXPECTED,
    }
    return {"passed": all(checks.values()), "checks": checks,
            "integrity_check": integrity, "foreign_key_violations": foreign_keys,
            "stock_imbalances": imbalance, "content": content}


@contextmanager
def fixture(root, case):
    """Create one owned synthetic database; retain its connection until exit."""
    if case not in CASES:
        raise ValueError("Unknown lab case")
    path = root / "source.sqlite"
    source = connect(path)
    try:
        requested = "delete" if case == "delete-quiescent" else "wal"
        actual = source.execute("PRAGMA journal_mode=" + requested).fetchone()[0]
        if actual != requested:
            raise SetupError("Requested journal mode is unavailable: " + requested)
        source.execute("PRAGMA foreign_keys=ON")
        source.execute("PRAGMA wal_autocheckpoint=0")
        source.executescript("""
            CREATE TABLE stock(sku TEXT PRIMARY KEY, quantity INTEGER NOT NULL CHECK(quantity >= 0));
            CREATE TABLE events(id INTEGER PRIMARY KEY, sku TEXT NOT NULL REFERENCES stock(sku), delta INTEGER NOT NULL);
            CREATE TABLE markers(id INTEGER PRIMARY KEY, label TEXT NOT NULL);
            INSERT INTO stock VALUES('widget', 5);
            INSERT INTO events VALUES(1, 'widget', 5);
            INSERT INTO markers VALUES(1, 'baseline');
        """)
        if requested == "wal":
            if source.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() != (0, 0, 0):
                raise SetupError("Could not establish the checkpointed baseline")
        source.execute("UPDATE stock SET quantity = 8 WHERE sku = 'widget'")
        source.execute("INSERT INTO events VALUES(2, 'widget', 3)")
        source.execute("INSERT INTO markers VALUES(2, 'committed-sentinel')")
        source.commit()
        if case == "wal-checkpointed":
            if source.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() != (0, 0, 0):
                raise SetupError("Could not establish the checkpointed control")
        wal = root / "source.sqlite-wal"
        wal_bytes = wal.stat().st_size if wal.exists() else 0
        if case == "wal-live" and wal_bytes <= 32:
            raise SetupError("Live WAL case did not retain WAL frames")
        if case != "wal-live" and wal_bytes != 0:
            raise SetupError("Quiescent control unexpectedly retained WAL frames")
        before = snapshot(source)
        if source.in_transaction or not before["passed"]:
            raise SetupError("Source's committed application state is incorrect")
        # A separate reader proves the marker is committed, not this writer's
        # pending view. Close it before copying; the source stays open.
        with ExitStack() as stack:
            reader = connect(path)
            stack.callback(reader.close)
            if snapshot(reader) != before:
                raise SetupError("Independent reader did not observe committed state")
        yield source, path, before, {
            "journal_mode": actual, "wal_bytes_before_backup": wal_bytes,
            "source_transaction_open": False, "independent_reader_confirmed": True,
            "source_connection_retained": True,
            "checkpoint_after_latest_commit": case == "wal-checkpointed",
            "concurrent_writes": False,
        }
    finally:
        source.close()


def run_case(case, methods=METHODS):
    """Run trusted built-in examples, confined to a fresh TemporaryDirectory."""
    if case not in CASES or not methods or any(m not in METHODS for m in methods):
        raise ValueError("Unknown case or method")
    result = {"case": case, "status": "setup_error", "methods": {}}
    temporary = None
    try:
        with TemporaryDirectory(prefix="sqlite-wal-restore-") as directory:
            temporary = Path(directory)
            with fixture(temporary, case) as (source, path, before, evidence):
                result["setup"] = evidence
                for method in methods:
                    destination = temporary / (method + ".sqlite")
                    if method == "main-file-copy":
                        copy_main_file(path, destination)
                    if method == "backup-api":
                        with ExitStack() as stack:
                            target = connect(destination)
                            stack.callback(target.close)
                            backup_to_connection(source, target)
                    # Inspect the resulting file through a fresh connection,
                    # after the backup connection has closed.
                    with ExitStack() as stack:
                        restored = connect(destination)
                        stack.callback(restored.close)
                        result["methods"][method] = snapshot(restored)
                result["source_unchanged"] = snapshot(source) == before
                if not result["source_unchanged"]:
                    raise SetupError("Source application state changed during the lab")
                result["status"] = "completed"
    except (SetupError, sqlite3.Error, OSError, ValueError) as exc:
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
        # Expected content failure is a completed result. Execution failures
        # must never masquerade as successful negative-control detection.
        result["status"] = "setup_error" if isinstance(exc, SetupError) else "execution_error"
    finally:
        result["temporary_files_cleaned"] = temporary is None or not temporary.exists()
    return result


def experiment_passed(cases):
    """Require the precise negative contrast plus both quiescent controls."""
    if len(cases) != len(CASES):
        return False
    by_id = {case["case"]: case for case in cases}
    if set(by_id) != set(CASES):
        return False
    for case_id in CASES:
        case = by_id[case_id]
        if case["status"] != "completed" or not case.get("source_unchanged") or not case["temporary_files_cleaned"]:
            return False
        if set(case["methods"]) != set(METHODS):
            return False
        for method in METHODS:
            observed = case["methods"][method]
            if case_id == "wal-live" and method == "main-file-copy":
                expected_checks = {"structural_integrity": True, "foreign_keys": True,
                                   "stock_matches_event_sum": True, "committed_sentinel": False,
                                   "expected_application_content": False}
                if observed["passed"] or observed["checks"] != expected_checks or observed["content"] != BASELINE:
                    return False
            elif not observed["passed"]:
                return False
    return True


def run(method="compare"):
    if method == "compare":
        cases = [run_case(case) for case in CASES]
        passed = experiment_passed(cases)
    elif method in METHODS:
        cases = [run_case("wal-live", (method,))]
        case = cases[0]
        passed = (case["status"] == "completed" and case.get("source_unchanged", False)
                  and case["temporary_files_cleaned"] and case["methods"][method]["passed"])
    else:
        raise ValueError("Unknown method")
    failed_execution = any(c["status"] != "completed" or not c["temporary_files_cleaned"] for c in cases)
    return {"schema_version": SCHEMA_VERSION, "method": method, "passed": passed,
            "status": "execution_error" if failed_execution else ("passed" if passed else "content_failed"),
            "runtime": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                        "platform": platform.system()},
            "cases": cases}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=("compare",) + METHODS, default="compare")
    parser.add_argument("--json", action="store_true", help="Emit the versioned machine-readable report")
    args = parser.parse_args(argv)
    report = run(args.method)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("SQLite WAL restore lab: " + report["status"])
        print("Python {python}; SQLite {sqlite}; {platform}".format(**report["runtime"]))
        for case in report["cases"]:
            print(case["case"] + ": " + case["status"])
            for method, value in case["methods"].items():
                checks = value["checks"]
                print("  {}: integrity={} sentinel={} expected-content={}".format(
                    method, checks["structural_integrity"], checks["committed_sentinel"], checks["expected_application_content"]))
            if "error" in case:
                print("  " + case["error"]["type"] + ": " + case["error"]["message"])
        print("This tests synthetic restore content, not production recovery or universal freshness.")
    return 2 if report["status"] == "execution_error" else (0 if report["passed"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
