"""Join unchanged Radar store methods to a managed shared-database transaction.

AircraftStore uses raw connection contexts that otherwise commit the caller's
transaction. This narrowly scoped connection facade suppresses only that context
manager's commit/rollback; all SQL and all CRUD remain Radar's original methods.
Construct stores/services BEFORE entering this scope. Never share a store/thread.
"""
from contextlib import contextmanager


class _JoinedConnection:
    def __init__(self, connection):
        self._connection = connection

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@contextmanager
def atomic_radar(db, store):
    original = store.connection
    with db.transaction():
        store.connection = _JoinedConnection(original)
        try:
            yield
        finally:
            store.connection = original
