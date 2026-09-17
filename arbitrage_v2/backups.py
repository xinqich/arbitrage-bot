"""Checked operational backups. Restore is offline, explicit and paused on completion."""
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from uuid import uuid4

from . import __version__
from .collection_lock import collection_lock
from .journal import Journal
from .mandate import load_mandate
from .real_funds import status, assert_cash
from .routes import route_status
from .worker import control, controls

CONFIG_FILES = ('local.json', 'watchlist.json', 'mandate.json', 'research_assumptions.json', 'csfloat.json')
MEMBERS = {'research.sqlite3', *('config/'+name for name in CONFIG_FILES)}


def _hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _copy_database(source, destination):
    with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)


def inspect_journal(path, root):
    """Check storage and replay without modifying it or contacting a provider."""
    journal = Journal(path)
    with journal.connect() as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('journal integrity check failed')
        columns = [r[1] for r in db.execute('PRAGMA table_info(records)')]
        if columns != ['seq', 'id', 'category', 'recorded_at', 'payload']:
            raise ValueError('unsupported records table')
        triggers = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        if not {'records_no_update', 'records_no_delete'} <= triggers:
            raise ValueError('journal is missing its history protections')
        rows = db.execute('SELECT seq,id,category,recorded_at,payload FROM records ORDER BY seq').fetchall()
        for row in rows:
            if not isinstance(json.loads(row[4]), dict):
                raise ValueError('invalid journal record')
        funds = status(journal, load_mandate(Path(root)/'config/mandate.json'), db=db)
        if funds['configured']:
            assert_cash(journal, db)
    at = datetime.now(timezone.utc).isoformat()
    routes = [route_status(journal, r['route_id'], at) for r in journal.records('route')]
    return {'record_count':len(rows), 'rows_sha256':sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest(),
            'route_count':len(routes), 'outcome_count':len(journal.records('outcome')),
            'real_funds_configured':funds['configured'], 'integrity':'ok'}


def create_backup(journal, directory, root):
    """Online SQLite snapshot plus the named, public configuration files; no credentials."""
    directory, root = Path(directory).resolve(), Path(root).resolve()
    configs = {name:(root/'config'/name).read_bytes() for name in CONFIG_FILES}
    directory.mkdir(parents=True, exist_ok=False)  # Never overwrite an existing backup.
    (directory/'config').mkdir()
    for name, data in configs.items():
        (directory/'config'/name).write_bytes(data)
    _copy_database(journal.path, directory/'research.sqlite3')
    summary = inspect_journal(directory/'research.sqlite3', directory)
    if any((root/'config'/name).read_bytes() != data for name, data in configs.items()):
        raise ValueError('settings changed during backup; retry into a new directory')
    manifest = {'backup_schema':1, 'software_version':__version__,
                'created_at':datetime.now(timezone.utc).isoformat(), 'summary':summary,
                'files':{name:_hash(directory/name) for name in sorted(MEMBERS)}}
    # Manifest is written last: a failed/partial backup cannot pass the checker.
    (directory/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    check_backup(directory)
    return {'backup':str(directory), **summary}


def check_backup(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('backup_schema') != 1 or manifest.get('software_version') != __version__:
        raise ValueError('backup requires its matching software version')
    if set(manifest.get('files', {})) != MEMBERS:
        raise ValueError('backup file list is incomplete or unsupported')
    for name, expected in manifest['files'].items():
        path = directory/name
        if not path.resolve().is_relative_to(directory) or _hash(path) != expected:
            raise ValueError('backup file failed verification: '+name)
    summary = inspect_journal(directory/'research.sqlite3', directory)
    if summary != manifest.get('summary'):
        raise ValueError('backup replay differs from its manifest')
    return {'backup':str(directory), 'software_version':__version__, **summary}


def restore_backup(journal, directory, root, *, replace_current=False):
    """Replace a readable journal after preserving it. Do not run other journal writers."""
    if not replace_current:
        raise ValueError('restore replaces newer history; review the backup and use --replace-current')
    directory, root = Path(directory).resolve(), Path(root).resolve()
    if not journal.path.is_file():
        raise ValueError('current journal must exist; use a new initialized journal for a recovery rehearsal')
    with collection_lock(journal.path):  # Refuse while the local desk/collector is running.
        # Work on our own checked copy; never alter the supplied backup.
        with TemporaryDirectory(prefix='arbitrage-restore-') as temp:
            candidate = Path(temp)/'backup'
            candidate.mkdir()
            (candidate/'config').mkdir()
            for name in sorted(MEMBERS | {'manifest.json'}):
                (candidate/name).write_bytes((directory/name).read_bytes())
            verified = check_backup(candidate)
            if any((root/'config'/name).read_bytes() != (candidate/'config'/name).read_bytes()
                   for name in CONFIG_FILES):
                raise ValueError('current settings differ; compare the backup config before restoring')
            safety = journal.path.parent/'backups'/('before-restore-'+uuid4().hex)
            create_backup(journal, safety, root)
            restored = Journal(candidate/'research.sqlite3')
            control(restored, 'pause')
            restored.append('journal_restore', {'schema_version':1, 'at':datetime.now(timezone.utc).isoformat(),
                'source_backup':str(directory), 'source_rows_sha256':verified['rows_sha256'],
                'previous_state_backup':str(safety), 'note':'Restored history; reconcile later receipts before Resume.'})
            expected = inspect_journal(restored.path, root)
            # SQLite replaces the destination transactionally, including in WAL mode.
            _copy_database(restored.path, journal.path)
            if inspect_journal(journal.path, root) != expected or not controls(journal)['paused']:
                raise ValueError('restore verification failed; previous state is saved at '+str(safety))
    return {'restored_from':str(directory), 'previous_state_backup':str(safety), 'paused':True,
            'record_count':expected['record_count'],
            'next_step':'Start the local page, reconcile any newer receipts, then choose Resume.'}
