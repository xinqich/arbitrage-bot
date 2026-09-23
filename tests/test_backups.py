from contextlib import closing, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from arbitrage_v2.__main__ import main
from arbitrage_v2.backups import create_backup, check_backup, restore_backup, CONFIG_FILES
from arbitrage_v2.collection_lock import collection_lock
from arbitrage_v2.journal import Journal
from arbitrage_v2.routes import _state
from arbitrage_v2.real_funds import status
from arbitrage_v2.worker import control, controls, Worker
import test_real_growth as fixtures


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(TemporaryDirectory())
        self.root = Path(self.temp)
        shutil.copytree(Path('config'), self.root/'config')
        self.journal = Journal(self.root/'journal.sqlite3')
        self.journal.initialize()
        self.journal.append('test_record', {'amount':12}, 'original')
        self.bundle = self.root/'saved'

    def rows(self, journal=None):
        with (journal or self.journal).connect() as db:
            return db.execute('SELECT * FROM records ORDER BY seq').fetchall()

    def test_snapshot_preserves_all_rows_and_settings_without_secret_files(self):
        (self.root/'config'/'private.env').write_text('TEST_SECRET=not-a-real-key')
        original = self.rows()
        result = create_backup(self.journal, self.bundle, self.root)
        self.assertEqual(result['record_count'], 1)
        self.assertEqual(self.rows(Journal(self.bundle/'research.sqlite3')), original)
        self.assertEqual({p.name for p in (self.bundle/'config').iterdir()}, set(CONFIG_FILES))
        self.assertFalse((self.bundle/'config/private.env').exists())
        self.journal.append('test_record', {'amount':3}, 'later')
        self.assertEqual(check_backup(self.bundle)['record_count'], 1)
        with self.assertRaises(FileExistsError):
            create_backup(self.journal, self.bundle, self.root)
        self.assertEqual(check_backup(self.bundle)['record_count'], 1)

    def test_restore_requires_explicit_replacement_and_stopped_worker(self):
        create_backup(self.journal, self.bundle, self.root)
        original = self.rows()
        with self.assertRaisesRegex(ValueError, 'replace-current'):
            restore_backup(self.journal, self.bundle, self.root)
        with collection_lock(self.journal.path):
            with self.assertRaisesRegex(ValueError, 'another collector'):
                restore_backup(self.journal, self.bundle, self.root, replace_current=True)
        self.assertEqual(self.rows(), original)

    def test_restore_preserves_newer_history_in_checked_safety_backup_and_starts_paused(self):
        control(self.journal, 'resume')
        control(self.journal, 'check_now')
        create_backup(self.journal, self.bundle, self.root)
        saved = self.rows()
        self.journal.append('test_record', {'amount':17}, 'later')
        newer = self.rows()
        result = restore_backup(self.journal, self.bundle, self.root, replace_current=True)
        self.assertEqual(self.rows()[:len(saved)], saved)
        self.assertEqual(self.rows(Journal(Path(result['previous_state_backup'])/'research.sqlite3')), newer)
        self.assertEqual(check_backup(result['previous_state_backup'])['record_count'], len(newer))
        self.assertEqual(self.rows(Journal(self.bundle/'research.sqlite3')), saved)
        self.assertTrue(controls(self.journal)['paused'])
        read = lambda n:json.loads((self.root/'config'/n).read_text())
        fetch = self.enterContext(patch('arbitrage_v2.worker.fetch_request'))
        worker = Worker(self.journal, read('watchlist.json'), read('research_assumptions.json'),
                        read('mandate.json'), read('local.json'), {}, fetch=fetch)
        worker.tick('2026-10-20T00:00:00+00:00')
        fetch.assert_not_called()
        self.assertEqual(self.journal.records('request_attempt'), [])

    def test_tamper_config_mismatch_and_version_mismatch_never_change_current_history(self):
        create_backup(self.journal, self.bundle, self.root)
        before = self.rows()
        settings = self.root/'config/local.json'
        settings.write_text(settings.read_text()+'\n')
        with self.assertRaisesRegex(ValueError, 'settings differ'):
            restore_backup(self.journal, self.bundle, self.root, replace_current=True)
        for path, error in ((self.bundle/'config/mandate.json','verification'),
                            (self.bundle/'manifest.json','matching software')):
            raw = path.read_bytes()
            if path.name == 'manifest.json':
                value = json.loads(raw); value['software_version'] = 'future-version'
                path.write_text(json.dumps(value))
            else:
                path.write_bytes(raw+b'\n')
            with self.assertRaisesRegex(ValueError, error):
                check_backup(self.bundle)
            path.write_bytes(raw)
        self.assertEqual(self.rows(), before)

    def test_invalid_journal_cannot_produce_a_valid_backup(self):
        with closing(sqlite3.connect(self.journal.path)) as db:
            db.execute('DROP TRIGGER records_no_update')
        with self.assertRaisesRegex(ValueError, 'history protections'):
            create_backup(self.journal, self.bundle, self.root)
        self.assertFalse((self.bundle/'manifest.json').exists())
        with self.assertRaises(OSError):
            check_backup(self.bundle)

    def test_cli_check_does_not_initialize_missing_target_and_search_mode_is_explicit(self):
        create_backup(self.journal, self.bundle, self.root)
        missing = self.root/'does-not-exist.sqlite3'
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(main(['--journal', str(missing), 'backup-check', str(self.bundle)]), 0)
        self.assertEqual(json.loads(output.getvalue())['integrity'], 'ok')
        self.assertFalse(missing.exists())
        with patch('arbitrage_v2.local_cli.search', return_value={'mode':'confirmed'}) as search:
            with redirect_stdout(StringIO()):
                self.assertEqual(main(['--journal', str(self.journal.path), 'search','grow','--mode','confirmed']), 0)
            self.assertEqual(search.call_args.kwargs['mode'], 'confirmed')

    def test_real_plan_partial_receipt_and_frozen_prediction_replay_through_restore(self):
        example = fixtures.RealGrowthTests()
        example.setUp()
        self.addCleanup(example.doCleanups)
        clock = self.enterContext(patch("arbitrage_v2.backups.datetime"))
        clock.now.return_value = fixtures.utc(example.at)+fixtures.timedelta(days=100)
        example.funding()
        request, prediction = example.open()
        example.buy()
        original = _state(example.journal, 'real-one')
        original_funds = status(example.journal, example.mandate)
        frozen = example.journal.get(request['prediction_id'])
        # Fixture settings describe this isolated test ledger, never the live experiment.
        (self.root/'config/mandate.json').write_text(json.dumps(example.mandate))
        create_backup(example.journal, self.bundle, self.root)
        example.advance()
        example.step('transfer_a', next_eligible_at=None)
        restore_backup(example.journal, self.bundle, self.root, replace_current=True)
        self.assertEqual(_state(example.journal, 'real-one'), original)
        self.assertEqual(status(example.journal, example.mandate), original_funds)
        self.assertEqual(example.journal.get(request['prediction_id']), frozen)
        self.assertEqual(example.journal.records('outcome'), [])

    def test_page_backup_uses_local_session_and_fixed_directory_without_market_calls(self):
        from http.client import HTTPConnection
        import threading
        from arbitrage_v2.web import create_server
        read = lambda n:json.loads((self.root/'config'/n).read_text())
        worker = Worker(self.journal, read('watchlist.json'), read('research_assumptions.json'),
                        read('mandate.json'), read('local.json'), {})
        server = create_server(worker, self.root, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        def call(body=None, headers=None):
            con = HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                con.request('POST' if body else 'GET', '/api/action' if body else '/api/session',
                            json.dumps(body) if body else None, headers or {})
                response = con.getresponse()
                return response.status, json.loads(response.read())
            finally:
                con.close()
        headers = {'Origin':f'http://127.0.0.1:{server.server_port}', 'Content-Type':'application/json',
                   'X-Local-Token':call()[1]['token']}
        original = self.rows()
        self.assertEqual(call({'action':'backup'})[0], 403)
        self.assertEqual(call({'action':'backup','directory':'anywhere'},headers)[0], 400)
        code, result = call({'action':'backup'},headers)
        self.assertEqual(code, 200, result)
        self.assertTrue(Path(result['backup']).is_relative_to(self.root/'data/backups'))
        self.assertEqual(check_backup(result['backup'])['record_count'], len(original))
        self.assertEqual(self.rows(), original)
