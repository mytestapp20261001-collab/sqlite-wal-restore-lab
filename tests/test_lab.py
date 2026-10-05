import copy
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import lab
from examples.backup_api import backup_to_connection

ROOT = Path(__file__).resolve().parents[1]


class RestoreLabTests(unittest.TestCase):
    def test_live_wal_exposes_readable_stale_copy(self):
        case = lab.run_case('wal-live')
        self.assertEqual(case['status'], 'completed')
        self.assertGreater(case['setup']['wal_bytes_before_backup'], 32)
        self.assertTrue(case['setup']['independent_reader_confirmed'])
        bad = case['methods']['main-file-copy']
        self.assertEqual(bad['integrity_check'], ['ok'])
        self.assertTrue(bad['checks']['stock_matches_event_sum'])
        self.assertEqual(bad['content'], lab.BASELINE)
        self.assertFalse(bad['checks']['committed_sentinel'])
        self.assertFalse(bad['passed'])
        self.assertEqual(case['methods']['backup-api']['content'], lab.EXPECTED)
        self.assertTrue(case['methods']['backup-api']['passed'])
        self.assertTrue(case['source_unchanged'])
        self.assertTrue(case['temporary_files_cleaned'])

    def test_checkpointed_control(self):
        case = lab.run_case('wal-checkpointed')
        self.assertEqual(case['status'], 'completed')
        self.assertEqual(case['setup']['wal_bytes_before_backup'], 0)
        self.assertTrue(case['setup']['checkpoint_after_latest_commit'])
        self.assertTrue(all(m['passed'] for m in case['methods'].values()))

    def test_delete_mode_quiescent_control(self):
        case = lab.run_case('delete-quiescent')
        self.assertEqual(case['setup']['journal_mode'], 'delete')
        self.assertEqual(case['setup']['wal_bytes_before_backup'], 0)
        self.assertFalse(case['setup']['concurrent_writes'])
        self.assertTrue(all(m['passed'] for m in case['methods'].values()))

    def test_repeated_runs_are_isolated(self):
        first = lab.run()
        for _ in range(3):
            self.assertEqual(lab.run(), first)
        self.assertTrue(first['passed'])

    def test_snapshot_catches_semantic_invariant_break(self):
        with TemporaryDirectory() as directory:
            with lab.fixture(Path(directory), 'wal-live') as (source, _, _, _):
                source.execute("UPDATE stock SET quantity=9")
                source.commit()
                result = lab.snapshot(source)
                self.assertEqual(result['integrity_check'], ['ok'])
                self.assertFalse(result['checks']['stock_matches_event_sum'])
                self.assertFalse(result['passed'])

    def test_snapshot_catches_foreign_key_violation(self):
        with TemporaryDirectory() as directory:
            with lab.fixture(Path(directory), 'wal-live') as (source, _, _, _):
                source.execute('PRAGMA foreign_keys=OFF')
                source.execute("INSERT INTO events VALUES(3, 'missing', 1)")
                source.commit()
                result = lab.snapshot(source)
                self.assertFalse(result['checks']['foreign_keys'])
                self.assertFalse(result['passed'])

    def test_backup_rejects_uncommitted_source(self):
        with TemporaryDirectory() as directory:
            with lab.fixture(Path(directory), 'wal-live') as (source, _, _, _):
                target = lab.connect(Path(directory) / 'target.sqlite')
                try:
                    source.execute("INSERT INTO markers VALUES(3, 'pending')")
                    with self.assertRaises(ValueError):
                        backup_to_connection(source, target)
                    source.rollback()
                finally:
                    target.close()

    def test_backup_rejects_uncommitted_destination(self):
        source = sqlite3.connect(':memory:')
        destination = sqlite3.connect(':memory:')
        try:
            destination.execute('CREATE TABLE t(x)')
            destination.execute('INSERT INTO t VALUES(1)')
            with self.assertRaises(ValueError):
                backup_to_connection(source, destination)
        finally:
            source.close()
            destination.close()

    def test_setup_error_is_not_successful_negative_control(self):
        @contextmanager
        def unavailable(*_):
            raise lab.SetupError('WAL unavailable for controlled test')
            yield
        with patch.object(lab, 'fixture', unavailable):
            result = lab.run()
        self.assertFalse(result['passed'])
        self.assertEqual(result['status'], 'execution_error')
        self.assertTrue(all(c['status'] == 'setup_error' for c in result['cases']))
        self.assertTrue(all(c['temporary_files_cleaned'] for c in result['cases']))

    def test_io_failure_is_reported_and_cleaned(self):
        owned = []
        original = lab.TemporaryDirectory
        def capture(**kwargs):
            temporary = original(**kwargs)
            owned.append(Path(temporary.name))
            return temporary
        with patch.object(lab, 'TemporaryDirectory', capture), patch.object(lab, 'copy_main_file', side_effect=OSError('synthetic write failure')):
            result = lab.run('main-file-copy')
        self.assertEqual(result['status'], 'execution_error')
        self.assertEqual(result['cases'][0]['status'], 'execution_error')
        self.assertFalse(result['passed'])
        self.assertTrue(owned)
        self.assertTrue(all(not path.exists() for path in owned))

    def test_no_false_pass_when_expected_bad_copy_becomes_good(self):
        cases = lab.run()['cases']
        cases[0]['methods']['main-file-copy'] = copy.deepcopy(cases[0]['methods']['backup-api'])
        self.assertFalse(lab.experiment_passed(cases))

    def test_no_false_pass_on_missing_control_or_source_mutation(self):
        cases = lab.run()['cases']
        self.assertFalse(lab.experiment_passed(cases[:-1]))
        cases[0]['source_unchanged'] = False
        self.assertFalse(lab.experiment_passed(cases))

    def test_cli_json_and_exit_codes(self):
        for method, code in [('compare', 0), ('backup-api', 0), ('main-file-copy', 1)]:
            with self.subTest(method=method):
                process = subprocess.run([sys.executable, '-B', str(ROOT / 'lab.py'), '--json', '--method', method], capture_output=True, text=True, timeout=15)
                self.assertEqual(process.returncode, code, process.stderr)
                report = json.loads(process.stdout)
                self.assertEqual(report['schema_version'], 1)
                self.assertEqual(report['method'], method)
                self.assertEqual(report['passed'], code == 0)
                self.assertEqual(process.stderr, '')

    def test_cli_no_existing_database_path_input(self):
        with TemporaryDirectory() as directory:
            untouched = Path(directory) / 'important.sqlite'
            untouched.write_bytes(b'never open this file')
            process = subprocess.run([sys.executable, '-B', str(ROOT / 'lab.py'), str(untouched)], capture_output=True, timeout=15)
            self.assertEqual(process.returncode, 2)
            self.assertEqual(untouched.read_bytes(), b'never open this file')
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ['important.sqlite'])

    def test_invalid_internal_choice_is_rejected(self):
        for function, argument in [(lab.run, 'custom-adapter'), (lab.run_case, 'unknown-case')]:
            with self.assertRaises(ValueError):
                function(argument)


if __name__ == '__main__':
    unittest.main()
