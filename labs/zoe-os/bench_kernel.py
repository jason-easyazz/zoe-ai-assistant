"""[code] Seeded offline load evidence over the brain bridge's real authenticated wire.

Only aggregate numbers/statuses are emitted. Timed calls exclude stated setup calls.
Run from the worktree; scratch state stays under this lab and is removed on exit.
No model, service, environment configuration, third-party runtime, or live database.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import random
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from bench_support import ACTIONS, HERE, MEMBERS, Box
from zoe_os.spine import Refused
from zoe_os.wire import BAD_REQUEST, WireError

VERBS = (*ACTIONS, 'why', 'undo', 'forget', 'denied', 'malformed')
STAGES = ('before_intent', 'after_intent', 'during_effect', 'after_effect', 'during_receipt', 'after_receipt')


def stats(values):
    ordered = sorted(values)
    if not ordered:
        raise ValueError('at least one measurement required')
    return {'n': len(values), **{f'p{p}_us': round(ordered[max(0, math.ceil(len(ordered) * p / 100) - 1)], 2)
                               for p in (50, 90, 99)}, 'max_us': round(max(values), 2)}


@contextmanager
def box_at(root, name, **options):
    box = Box(Path(root) / name, **options)
    try:
        yield box
    finally:
        box.close()


class Work:
    def __init__(self, box, member=MEMBERS[0]):
        self.box, self.member = box, member
        self.voice, self.brain, self.panel = (box.client(n) for n in ('voice', 'brain', 'panel'))
        self.index = 0

    def cause(self):
        self.index += 1
        return self.voice.publish('utterance', subject=self.member,
                                  payload={'text': f'synthetic-{self.index}'}).seq

    def action(self, action='list.add', key=None):
        cause = self.cause()
        return self.brain.act(action, self.member, {'key': key or f'{self.member}-{cause}'}, caused_by=[cause])

    def prepare(self, verb):
        """Fixtures excluded from latency; mixed/throughput wall time includes them."""
        if verb in ACTIONS:
            cause = self.cause()
            key = f'{self.member}-{cause}'
            if verb == 'list.remove':
                assert self.brain.act('list.add', self.member, {'key': key}, caused_by=[cause]).verified
            return lambda: self.brain.act(verb, self.member, {'key': key}, caused_by=[cause])
        if verb in ('why', 'undo'):
            receipt = self.action()
            assert receipt.verified
            if verb == 'why':
                return lambda: self.panel.explain(receipt.seq, person=self.member)
            return lambda: self.panel.undo(self.member, actor=self.member)
        if verb == 'forget':
            cause = self.cause()
            # Scoped forget of an isolated event: bounded, no erasure of earlier evidence.
            return lambda: self.panel.forget(self.member, actor=self.member, seqs=[cause])
        if verb == 'denied':
            cause = self.cause()
            return lambda: self.brain.act('not.granted', self.member, {}, caused_by=[cause])
        return lambda: self.brain.call('act', unexpected=True)

    def run(self, verb, *, call=None):
        call = call or self.prepare(verb)
        start = time.perf_counter_ns()
        try:
            result = call()
        except Refused:
            if verb != 'denied':
                raise
        except WireError as exc:
            if verb != 'malformed' or exc.code != BAD_REQUEST:
                raise
        else:
            if verb in ('denied', 'malformed'):
                raise AssertionError('invalid call accepted')
            if verb in (*ACTIONS, 'undo') and not result.verified:
                raise AssertionError('unverified benchmark action')
            if verb == 'forget' and result['count'] != 1:
                raise AssertionError('scoped forget missed fixture')
        return (time.perf_counter_ns() - start) / 1000


def latency(root, n, *, profile=False, history_n=0):
    with box_at(root, 'latency', profile=profile) as box:
        work = Work(box)
        # Untimed real action history: events, links and completed intents all grow.
        for _ in range((history_n + 1) // 2):
            assert work.action().verified
        history_at_start = box.sup('position')
        out = {verb: stats([work.run(verb) for _ in range(n)]) for verb in VERBS}
        out['history_events_at_start'] = history_at_start
        if profile:
            profiles = {}
            for verb in VERBS:
                call = work.prepare(verb)
                box.sup('clear_profile')
                total = work.run(verb, call=call)
                counters = box.sup('profile')
                phases = {'decode': ('json_decode_all',), 'consent': ('consent',),
                          'SQL': ('sql_execute', 'sql_write', 'sql_executemany'),
                          'commit': ('sql_commit_sync_inclusive', 'sql_autocommit_sync_inclusive'),
                          'receipt': ('receipt_inclusive',)}
                profiles[verb] = {'total_us': total, 'phases_us': {
                    phase: sum(counters.get(label, [0, 0])[1] for label in labels)
                    for phase, labels in phases.items()}, 'counters': counters}
            out['profile'] = profiles
            out['profile_one_list_add'] = profiles['list.add']['counters']
            out['profile_top_costs'] = sorted(
                (name for name in out['profile_one_list_add'] if not name.endswith('_inclusive')
                 or name.startswith('sql_')),
                key=lambda name: out['profile_one_list_add'][name][1], reverse=True)
        return out


def assert_isolated(rows, expected):
    """Compare ACTUAL stored keys to parent-owned expected member/key pairs."""
    actual = {(p, key) for p, family, key in rows if family == 'list'}
    if actual != expected:
        raise AssertionError('cross-member or missing write detected')


def concurrency(root, n, counts=(1, 4, 16), *, misroute=False):
    results = {}
    for clients in counts:
        with box_at(root, f'clients-{clients}', misroute=misroute) as box:
            # Three persistent wire connections per Work; server has 32 slots.
            # Only the brain connection needs to be distinct; setup is sequential.
            voice = box.client('voice')
            causes = [voice.publish('utterance', subject=MEMBERS[i], payload={'text': 'synthetic'}).seq
                      for i in range(clients)]
            brains = [box.client('brain') for _ in range(clients)]
            barrier = threading.Barrier(clients)
            expected = {(MEMBERS[i], f'{MEMBERS[i]}-{j}') for i in range(clients) for j in range(n)}

            def worker(i):
                barrier.wait(timeout=10)
                times, denials = [], 0
                for j in range(n):
                    start = time.perf_counter_ns()
                    receipt = brains[i].act('list.add', MEMBERS[i], {'key': f'{MEMBERS[i]}-{j}'},
                                            caused_by=[causes[i]])
                    times.append((time.perf_counter_ns() - start) / 1000)
                    # A compromised caller tries another member with its own cause.
                    try:
                        brains[i].act('list.add', MEMBERS[(i + 1) % 16], {'key': 'forged'},
                                      caused_by=[causes[i]])
                    except Refused:
                        denials += 1
                    else:
                        raise AssertionError('cross-member proposal accepted')
                    # Misrouting may also invalidate read-back; inspect real rows regardless.
                    if not receipt.verified and not misroute:
                        raise AssertionError('write failed')
                return times, denials

            start = time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as pool:
                batches = list(pool.map(worker, range(clients)))
            elapsed = time.perf_counter() - start
            assert_isolated(box.sup('effects'), expected)
            samples = [t for times, _ in batches for t in times]
            results[str(clients)] = {**stats(samples), 'seconds': round(elapsed, 3),
                                     'wire_calls': 2 * clients * n,
                                     'calls_per_s': round(2 * clients * n / elapsed, 2),
                                     'cross_member_denied': sum(d for _, d in batches), 'isolated': True}
    return results


def rss(pid):
    for line in Path(f'/proc/{pid}/status').read_text().splitlines():
        if line.startswith('VmRSS:'):
            return int(line.split()[1]) * 1024
    raise RuntimeError('kernel RSS unavailable')


def db_bytes(box):
    return sum(p.stat().st_size for p in box.root.glob('record.db*'))


def soak(root, n, seed, *, limit_s=290):
    rng = random.Random(seed)
    with box_at(root, 'soak') as box:
        work = Work(box)
        start_rss, start_db = rss(box.proc.pid), db_bytes(box)
        start, samples, by_verb = time.perf_counter(), [], {v: [] for v in VERBS}
        wire_calls = 0
        completed, timed_out = 0, False
        quarters = [{v: [] for v in VERBS} for _ in range(4)]
        positions = [{} for _ in range(4)]
        for i in range(n):
            if time.perf_counter() - start > limit_s:
                timed_out = True
                break
            verb = rng.choice(VERBS)
            quarter = min(3, i * 4 // n)
            if not positions[quarter]:
                positions[quarter]['start_seq'] = box.sup('position')
            value = work.run(verb)
            samples.append(value)
            by_verb[verb].append(value)
            quarters[quarter][verb].append(value)
            completed += 1
            if i == n - 1 or min(3, (i + 1) * 4 // n) != quarter:
                positions[quarter]['end_seq'] = box.sup('position')
            wire_calls += 1 + (2 if verb in ('list.remove', 'why', 'undo') else 0 if verb == 'malformed' else 1)
        if not samples:
            return {'failed': 'wall_budget', 'mixed_calls': 0, 'requested_calls': n}
        window = max(1, completed // 10)
        if 'end_seq' not in positions[quarter]:
            positions[quarter]['end_seq'] = box.sup('position')
        first, last = stats(samples[:window]), stats(samples[-window:])
        return {**({'failed': 'wall_budget'} if timed_out else {}),
                'mixed_calls': completed, 'requested_calls': n, 'budget_s': limit_s,
                'quarters': [{**position, 'n': sum(map(len, q.values())),
                              'by_verb': {v: stats(s) for v, s in q.items() if s},
                              'overall': stats([s for values in q.values() for s in values])
                              if any(q.values()) else None} for q, position in zip(quarters, positions)],
                'wire_calls_including_setup': wire_calls,
                'seconds': round(time.perf_counter() - start, 3),
                'rss_start_bytes': start_rss, 'rss_end_bytes': rss(box.proc.pid),
                'db_start_bytes': start_db, 'db_end_bytes': db_bytes(box),
                'rss_growth_bytes': rss(box.proc.pid) - start_rss,
                'db_growth_bytes': db_bytes(box) - start_db,
                'first_decile': first, 'last_decile': last,
                'p50_drift_ratio': round(last['p50_us'] / first['p50_us'], 3),
                'by_verb': {v: stats(s) for v, s in by_verb.items() if s}}


def check_record(box, acknowledgements):
    with sqlite3.connect(f"file:{box.cfg['db']}?mode=ro", uri=True) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'corrupt record'
        assert db.execute('SELECT count(*) FROM intents WHERE receipt_seq IS NULL').fetchone()[0] == 0, 'open intent'
        assert db.execute("SELECT count(*) FROM intents i LEFT JOIN events e ON e.seq=i.receipt_seq "
                          "WHERE e.seq IS NULL OR e.kind!='action_receipt' OR e.tombstoned!=0").fetchone()[0] == 0, 'half receipt'
        events = {seq: json.loads(body) for seq, body in db.execute(
            "SELECT seq,payload FROM events WHERE kind='action_receipt' AND tombstoned=0")}
        assert all({'action', 'skill', 'verified', 'outcome', 'proof'} <= set(p) for p in events.values()), 'partial payload'
        assert len(events) == db.execute('SELECT count(*) FROM intents').fetchone()[0], 'orphan receipt'
        for seq in acknowledgements:
            assert events.get(seq, {}).get('verified') is True, 'lost acknowledged receipt'
    rows = {(p, key) for p, _, key in box.sup('effects')}
    assert all((MEMBERS[0], key) in rows for key in acknowledgements.values()), 'lost acknowledged write'


def crash(root, n, seed, *, volatile=False):
    rng, acknowledged, stages = random.Random(seed), {}, {s: 0 for s in STAGES}
    schedule = []
    while len(schedule) < n:
        batch = list(STAGES)
        rng.shuffle(batch)
        schedule.extend(batch)
    with box_at(root, 'crash', volatile=volatile) as box:
        for i in range(n):
            work = Work(box)
            key = f'{MEMBERS[0]}-ack-{i}'
            receipt = work.action(key=key)
            assert receipt.verified
            acknowledged[receipt.seq] = key
            # Separate member: a recovered uncertain receipt must not mask the undo fixture.
            victim = Work(box, MEMBERS[1])
            call = victim.prepare('list.add')
            stage = schedule[i]
            stages[stage] += 1
            box.sup('arm', stage=stage)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(call)
                try:
                    assert box.read() == {'stage': stage}, 'wrong crash point'
                finally:
                    box.proc.kill()
                    box.proc.wait(timeout=10)
                try:
                    future.result(timeout=10)
                except (ConnectionError, OSError, WireError):
                    pass
                else:
                    raise AssertionError('paused request unexpectedly acknowledged')
            assert box.proc.returncode == -9
            box.restart()
            check_record(box, acknowledged)
            assert box.client('panel').undo(MEMBERS[0], actor=MEMBERS[0]).verified, 'undo failed after restart'
            acknowledged.pop(receipt.seq)  # deliberately undone effects no longer expected
            assert (MEMBERS[0], 'list', key) not in map(tuple, box.sup('effects')), 'undo missed effect'
            # Keep all earlier acknowledged writes as survival sentinels across later kills.
            keep = Work(box).action(key=f'{MEMBERS[0]}-keep-{i}')
            acknowledged[keep.seq] = f'{MEMBERS[0]}-keep-{i}'
        return {'kills': n, 'stages': stages, 'integrity': True, 'no_half_receipt': True,
                'acknowledged_survive': True, 'undo_after_restart': True, 'sentinels': len(acknowledged)}


def cold(root, n):
    samples = []
    for i in range(n):
        with box_at(root, f'cold-{i}') as box:
            samples.append(box.cold_us)
    return stats(samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('all', 'latency', 'concurrency', 'soak', 'crash', 'cold'), default='all')
    parser.add_argument('--n', type=int, default=100)
    parser.add_argument('--soak-n', type=int, default=20000)
    parser.add_argument('--crashes', type=int, default=50)
    parser.add_argument('--cold-n', type=int, default=10)
    parser.add_argument('--seed', type=int, default=4721)
    parser.add_argument('--profile', action='store_true', help='one extra call per verb in latency/all modes')
    parser.add_argument('--history-n', type=int, default=0,
                        help='minimum untimed action-history events before latency measurements')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if min(args.n, args.soak_n, args.crashes, args.cold_n) < 1:
        parser.error('counts must be positive')
    if args.history_n < 0:
        parser.error('history count must be nonnegative')
    if args.profile and args.mode not in ('latency', 'all'):
        parser.error('--profile requires --mode latency or all')
    result = {'claim': '[lab]', 'seed': args.seed, 'driver': 'durable_synthetic_sqlite',
              'timing': 'warm wire calls; setup excluded from latency; included in throughput wall time'}
    modes = {'latency': lambda p: latency(p, args.n, profile=args.profile, history_n=args.history_n),
             'concurrency': lambda p: concurrency(p, args.n),
             'soak': lambda p: soak(p, args.soak_n, args.seed),
             'crash': lambda p: crash(p, args.crashes, args.seed),
             'cold': lambda p: cold(p, args.cold_n)}
    with tempfile.TemporaryDirectory(prefix='bk-', dir=HERE) as root:
        for mode, run in modes.items():
            if args.mode in ('all', mode):
                try:
                    result[mode] = run(root)
                    if mode == 'latency' and args.profile:
                        result['profile'] = result[mode]['profile']
                    print(f'[lab] {mode:12} completed', flush=True)
                except Exception as exc:
                    result[mode] = {'failed': type(exc).__name__}
                    # Fixed class only: no exception text/payload/credential/path in output.
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print('mode          p50_us / status')
    for mode in modes:
        if mode in result:
            print(f"{mode:13} {result[mode].get('p50_us', 'failed' if 'failed' in result[mode] else 'see JSON')}")
    if 'concurrency' in result and 'failed' not in result['concurrency']:
        print('clients       calls/s   p90_us    p99_us   isolated')
        for clients, row in result['concurrency'].items():
            print(f"{clients:13} {row['calls_per_s']:9.2f} {row['p90_us']:9.2f} {row['p99_us']:9.2f} {row['isolated']}")
    if 'soak' in result and 'failed' not in result['soak']:
        row = result['soak']
        print(f"soak growth: RSS={row['rss_growth_bytes']} DB={row['db_growth_bytes']} bytes; "
              f"p50 drift={row['p50_drift_ratio']}")
    if 'latency' in result and 'failed' not in result['latency']:
        print('verb          p50_us    p90_us    p99_us')
        for verb in VERBS:
            row = result['latency'][verb]
            print(f"{verb:13} {row['p50_us']:9.2f} {row['p90_us']:9.2f} {row['p99_us']:9.2f}")
    return int(any('failed' in result.get(m, {}) for m in modes))


if __name__ == '__main__':
    raise SystemExit(main())
