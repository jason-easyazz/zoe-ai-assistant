"""[code] Offline benchmark process/driver fixture; never imported by production.

Durable synthetic effects use the kernel's SQLite connection, in their OWN transaction,
like an external store. This does not make the existing in-memory brain drivers durable.
Supervisor commands and crash hooks are deliberately absent from the public wire.
"""
from __future__ import annotations

import collections
import json
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

from zoe_os.capability import Person
from zoe_os.client import Client
from zoe_os.kernel import Kernel
from zoe_os.server import KernelServer

HERE = Path(__file__).resolve().parent
MEMBERS = tuple(f"member-{i}" for i in range(16))
ACTIONS = ("list.add", "list.remove", "remind.set", "alarm.set")


class Meter:
    """Inclusive timings; SQL commit includes SQLite sync work, not isolated fsync."""
    def __init__(self):
        self.values = collections.defaultdict(lambda: [0, 0.0])

    def wrap(self, label, fn):
        def measured(*args, **kwargs):
            start = time.perf_counter_ns()
            try:
                return fn(*args, **kwargs)
            finally:
                row = self.values[label]
                row[0] += 1
                row[1] += (time.perf_counter_ns() - start) / 1000
        return measured


class Database:
    def __init__(self, db, meter):
        self.db, self.meter = db, meter

    def __getattr__(self, name):
        return getattr(self.db, name)

    def execute(self, sql, *args):
        if sql == "COMMIT":
            label = "sql_commit_sync_inclusive"
        elif sql.startswith(('INSERT', 'UPDATE', 'DELETE')):
            label = "sql_write" if self.db.in_transaction else "sql_autocommit_sync_inclusive"
        else:
            label = "sql_execute"
        return self.meter.wrap(label, self.db.execute)(sql, *args)

    def executemany(self, *args):
        return self.meter.wrap("sql_executemany", self.db.executemany)(*args)


class Effects:
    def __init__(self, kernel, pause, *, volatile=False, misroute=False):
        self.db, self.pause = kernel.spine._db, pause
        self.volatile, self.misroute, self.memory = volatile, misroute, {}
        self.db.execute("CREATE TABLE IF NOT EXISTS bench_effects "
                        "(person TEXT, family TEXT, key TEXT, value TEXT, PRIMARY KEY(person,family,key))")
        for action in ACTIONS:
            family = action.split('.')[0]
            kernel.actions.register(
                action, lambda p, a, act=action: self.do(p, a, act),
                lambda p, a, r, act=action: self.verify(p, a, act), needs_accept=False,
                snapshot=lambda p, a, f=family: {"value": self.get(p, f, a['key'])},
                undo=lambda p, a, b, f=family: self.put(p, f, a['key'], b['value']),
                params={"key": {"type": "string", "required": True}}, doc="Synthetic benchmark action")

    def get(self, person, family, key):
        if self.volatile:
            return self.memory.get((person, family, key))
        row = self.db.execute("SELECT value FROM bench_effects WHERE person=? AND family=? AND key=?",
                              (person, family, key)).fetchone()
        return row[0] if row else None

    def put(self, person, family, key, value):
        if self.misroute:
            person = MEMBERS[(MEMBERS.index(person) + 1) % len(MEMBERS)]
        if self.volatile:
            if value is None:
                self.memory.pop((person, family, key), None)
            else:
                self.memory[person, family, key] = value
            return
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("DELETE FROM bench_effects WHERE person=? AND family=? AND key=?",
                            (person, family, key))
            self.pause("during_effect")
            if value is not None:
                self.db.execute("INSERT INTO bench_effects VALUES (?,?,?,?)", (person, family, key, value))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def do(self, person, args, action):
        self.put(person, action.split('.')[0], args['key'], None if action == "list.remove" else args['key'])
        self.pause("after_effect")

    def verify(self, person, args, action):
        expected = None if action == "list.remove" else args['key']
        return {"matched": True} if self.get(person, action.split('.')[0], args['key']) == expected else None


def child():
    cfg = json.loads(sys.stdin.readline())
    os.umask(0o077)
    k = Kernel(cfg['db'], [Person(p) for p in MEMBERS])
    meter = Meter()
    if cfg.get('profile'):
        k.spine._db = Database(k.spine._db, meter)
        # JSON decoding includes wire parsing AND database payload decoding, explicitly labelled.
        json.loads = meter.wrap('json_decode_all', json.loads)
        k.actions._authorize = meter.wrap('consent', k.actions._authorize)
        k.actions._close_intent = meter.wrap('receipt_inclusive', k.actions._close_intent)
        k.spine._append = meter.wrap('append_inclusive', k.spine._append)
    armed = {'stage': None}

    def pause(stage):
        if armed['stage'] == stage:
            print(json.dumps({'stage': stage}), flush=True)
            threading.Event().wait()  # supervisor kills this process; never a public API

    effects = Effects(k, pause, volatile=cfg.get('volatile'), misroute=cfg.get('misroute'))
    plain_open, plain_close = k.actions._open_intent, k.actions._close_intent

    def opened(*a, **kw):
        pause('before_intent')
        result = plain_open(*a, **kw)
        pause('after_intent')
        return result

    def closed(intent, receipt):
        if armed['stage'] == 'during_receipt':
            return k.spine._kernel_append(receipt, lambda seq: pause('during_receipt'))
        result = plain_close(intent, receipt)
        pause('after_receipt')
        return result

    k.actions._open_intent, k.actions._close_intent = opened, closed
    for m in ({'skill': 'voice', 'origin': True, 'emits': ['utterance']},
              {'skill': 'panel'}, {'skill': 'brain', 'reads': {'utterance': 'private'}, 'acts': ACTIONS}):
        k.grants.install(m)
    for person in MEMBERS:
        k.grants.grant(person, 'brain')
    srv = KernelServer(k, cfg['socket'])
    credentials = {n: srv.issue(n, surface=n == 'panel').hex() for n in ('voice', 'panel', 'brain')}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(json.dumps({'credentials': credentials}), flush=True)
    for line in sys.stdin:
        cmd = json.loads(line)
        with k.spine._lock:
            if cmd['op'] == 'arm':
                armed['stage'] = cmd['stage']
                out = {}
            elif cmd['op'] == 'profile':
                out = dict(meter.values)
            elif cmd['op'] == 'clear_profile':
                meter.values.clear()
                out = {}
            elif cmd['op'] == 'effects':
                out = list(effects.memory) if effects.volatile else k.spine._db.execute(
                    'SELECT person, family, key FROM bench_effects ORDER BY person,family,key').fetchall()
            else:
                raise ValueError('unknown supervisor operation')
        print(json.dumps(out), flush=True)
    srv.shutdown()
    k.close()


class Box:
    """One child, bounded startup/read time, private scratch state owned by the caller."""
    def __init__(self, root, **options):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.cfg = {'db': str(self.root / 'record.db'), 'socket': str(self.root / 's'), **options}
        self.clients = []
        self.start()

    def read(self):
        if not select.select([self.proc.stdout], [], [], 10)[0]:
            raise TimeoutError('benchmark child response deadline')
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError('benchmark child exited before response')
        return json.loads(line)

    def start(self):
        start = time.perf_counter_ns()
        self.proc = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve())],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                     env={'PATH': '/usr/bin:/bin'})
        try:
            self.proc.stdin.write(json.dumps(self.cfg) + '\n')
            self.proc.stdin.flush()
            ready = self.read()
            if ready.get('startup_error') == 'PermissionError':
                raise PermissionError('sandbox denied benchmark socket bind')
            self.creds = ready['credentials']
            self.client('brain').describe()
            self.cold_us = (time.perf_counter_ns() - start) / 1000
        except BaseException:
            self.close()
            raise

    def client(self, name):
        client = Client(self.cfg['socket'], name, bytes.fromhex(self.creds[name]))
        self.clients.append(client)
        return client

    def sup(self, op, **args):
        self.proc.stdin.write(json.dumps({'op': op, **args}) + '\n')
        self.proc.stdin.flush()
        return self.read()

    def close(self):
        for c in self.clients:
            c.close()
        self.clients.clear()
        if getattr(self, 'proc', None):
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait(timeout=10)
            self.proc.stdin.close()
            self.proc.stdout.close()

    def restart(self):
        self.close()
        self.start()


if __name__ == '__main__':
    try:
        child()
    except PermissionError:
        print(json.dumps({'startup_error': 'PermissionError'}), flush=True)
        raise SystemExit(1)
