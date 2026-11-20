"""Reliable bounded-lifecycle workers; independent connections for each subsystem."""
import threading
from contextlib import closing
from src.db import create_database, Team
from src.tower.occupancy import process_requests_with_occupancy as process_requests
from src.integration.consumers import consume


class Runtime:
    def __init__(self, application, interval=0.1):
        self.application = application
        self.interval = interval
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.status = {}
        self.threads = []
        application.runtime = self

    def health(self):
        with self.lock:
            return {key:dict(value) for key,value in self.status.items()}

    def start(self):
        roles = {'tower':lambda db:process_requests(db,limit=100),
                 'radar':lambda db:consume(db,Team.RADAR),
                 'command':lambda db:consume(db,Team.COMMAND)}
        ready = []
        for role,work in roles.items():
            event = threading.Event(); ready.append(event)
            thread = threading.Thread(target=self._run,args=(role,work,event),name='skynet-'+role,daemon=True)
            self.threads.append(thread);thread.start()
        if not all(e.wait(10) for e in ready) or not all(v['healthy'] for v in self.health().values()):
            self.close()
            raise RuntimeError('Subsystem workers failed to start: '+str(self.health()))
        return self

    def _run(self, role, work, ready):
        try:
            with closing(create_database(self.application.database)) as db:
                while not self.stop_event.is_set():
                    try:
                        with self.application.lock:
                            work(db)
                        with self.lock:self.status[role] = {'healthy':True,'error':None}
                    except Exception as exc:
                        with self.lock:self.status[role] = {'healthy':False,'error':str(exc)}
                    finally:ready.set()
                    self.stop_event.wait(self.interval)
        except Exception as exc:
            with self.lock:self.status[role] = {'healthy':False,'error':str(exc)}
            ready.set()

    def close(self):
        self.stop_event.set()
        for thread in self.threads:thread.join(timeout=6)
