"""Isolated demo identity, stale-client generation, and atomic administrative reset."""
from pathlib import Path
from uuid import uuid4
from src.shared.geometry import AIRPORT_ID
from src.shared.transactions import atomic_radar

DEFAULT_RESET_ROOT = Path(__file__).resolve().parents[2] / 'data' / 'command-demo'


def assert_safe_path(database, reset_root):
    if Path(reset_root).is_symlink():
        raise PermissionError('Reset is restricted to a real isolated demo directory, not a symlink')
    path, root = Path(database).resolve(), Path(reset_root).resolve()
    if not path.is_relative_to(root) or path == root or not path.is_file() or path.stat().st_nlink != 1:
        raise PermissionError('Reset is restricted to a marked, isolated prototype demo database')
    return path


def initialize_demo(db, database, reset_root):
    if db.get_shared_state('demo:generation') is None:
        db.upsert_shared_state('demo:generation',{'token':uuid4().hex,'resets':0},category='demo_settings')
    try:
        path = assert_safe_path(database,reset_root)
    except PermissionError:
        return
    if db.get_shared_state('demo:identity') is None:
        db.upsert_shared_state('demo:identity',{'path':str(path),'purpose':'skynet-isolated-demo'},category='demo_settings')


def generation(db):
    return db.get_shared_state('demo:generation')['payload']


def reset_demo(db, store, tower, database, reset_root):
    path = assert_safe_path(database,reset_root)
    identity = db.get_shared_state('demo:identity')
    if not identity or identity['payload'] != {'path':str(path),'purpose':'skynet-isolated-demo'}:
        raise PermissionError('Database identity does not authorize a demo reset')
    if [a['airport_id'] for a in tower.airports.list_airports()] != [AIRPORT_ID]:
        raise PermissionError('Reset requires the isolated single-airport demonstration')
    with atomic_radar(db,store):
        previous = generation(db)
        # Dependency order matters. Keep monotonic SQLite IDs to avoid reusing old
        # message identities. Existing Radar CRUD owns aircraft deletion/cascades.
        db.connection.execute('DELETE FROM integration_receipts')
        db.connection.execute('DELETE FROM tower_processed_messages')
        db.connection.execute('DELETE FROM messages')
        db.connection.execute('DELETE FROM tower_requests')
        for aircraft in store.list(): store.delete(aircraft.id)
        db.connection.execute("DELETE FROM shared_state WHERE key NOT IN ('demo:identity','demo:occupancy-version','demo:generation')")
        wanted = {f'R{i}' for i in range(1,11)}
        runways = tower.airports.list_runways(AIRPORT_ID)
        for runway in runways:
            if runway['id'] not in wanted: tower.airports.delete_runway(AIRPORT_ID,runway['id'])
        present = {r['id'] for r in runways}
        for i in range(1,11):
            name = f'R{i}'
            if name not in present: tower.airports.add_runway(AIRPORT_ID,name,mode='mixed')
            else: tower.airports.update_runway(AIRPORT_ID,name,available=True,mode='mixed')
        db.upsert_shared_state('demo:hold',{'enabled':False},category='demo_settings')
        db.upsert_shared_state('demo:generation',{'token':uuid4().hex,'resets':previous['resets']+1},category='demo_settings')
