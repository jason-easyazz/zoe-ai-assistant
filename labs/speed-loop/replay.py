"""[code] Guarded child-process adapter. No pipeline imports in the driver."""
from __future__ import annotations
import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import signal
import statistics as stats
import subprocess
import sys
import threading
import time
from datetime import datetime

import loop as sl

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AUTO = {'ZOE_FLUE_PREPOST_CONCURRENT', 'ZOE_BRAIN_VOLATILE_AFTER_USER',
        'ZOE_MEMORY_TICK_DEFER_S', 'ZOE_CONTINUITY_CEILING_S'}
DEFAULTS = {'ZOE_FLUE_PREPOST_CONCURRENT': False, 'ZOE_BRAIN_VOLATILE_AFTER_USER': False,
            'ZOE_MEMORY_TICK_DEFER_S': 0, 'ZOE_CONTINUITY_CEILING_S': 3}
TIMINGS = ('stt_ms', 'resolve_ms', 'brain_ms', 'e2e_ms')

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def classification(knob):
    if knob['stage'] == 'endpoint':
        return 'c'
    return 'b' if knob['kind'] == 'constant' else 'a'


def revision():
    """Bind dirty worktrees too: HEAD plus all source bytes, never environment values."""
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    h = hashlib.sha256(head.encode())
    for base in (ROOT / 'services/zoe-data', ROOT / 'scripts', HERE):
        for p in sorted(base.rglob('*.py')):
            h.update(str(p.relative_to(ROOT)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def corpus_default():
    """Read the instrument default as AST data; importing the probe has side effects."""
    import ast
    p = ROOT / 'scripts/maintenance/voice_regression_probe.py'
    tree = ast.parse(p.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'DEFAULT_SAMPLE_DIR' for t in n.targets))
    return Path(os.environ.get('ZOE_VOICE_SAMPLE_DIR') or ast.literal_eval(node.value.values[-1]))


def select_corpus(directory, count, seed):
    files = sorted(directory.glob('*.wav'))
    # Pin content hashes; names never leave this process or appear in results.
    rows = sorted((hashlib.sha256(p.read_bytes()).hexdigest(), p) for p in files)
    random.Random(seed).shuffle(rows)
    if len(rows) < count:
        raise ValueError('corpus_too_small')
    return rows[:count]


def conversation_age(log, now):
    """Inspect only timestamp + logger prefix, never retain a message body."""
    latest = None
    # Explicit bounded tail; an unreadable/unrecognised log refuses, never means idle.
    with Path(log).open('rb') as stream:
        stream.seek(max(0, stream.seek(0, 2) - 2_000_000))
        for raw in stream:
            # Bytes regex on the header only: nothing after the logger name is decoded or kept.
            m = re.match(rb'^(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)([+-]\d{4})?(?:[,\.]\d+)?\s+(?:\[?(?:INFO|DEBUG|WARNING)\]?\s+)[\[ ]*([\w.]+)', raw[:120])
            if m and m[3].decode('ascii', 'replace') in {'routers.voice_tts', 'zoe_flue_client', 'brain_dispatch'}:
                zone = m[2].decode('ascii') if m[2] else ''
                if zone:  # Python 3.10's fromisoformat needs +HH:MM, not +HHMM
                    zone = zone[:3] + ':' + zone[3:]
                stamp = datetime.fromisoformat(m[1].decode('ascii') + zone).astimezone()
                latest = max(latest, stamp) if latest else stamp
    if latest is None:
        raise ValueError('activity_unknown')
    return (now - latest).total_seconds()


class Guards:
    def __init__(self, windows, activity_log, *, min_mem=1500, idle_minutes=10,
                 night=None, clock=None):
        self.windows, self.activity_log = windows, activity_log
        self.min_mem, self.idle_minutes = min_mem, idle_minutes
        self.night = Path(night) if night else ROOT / 'scripts/landing/night_busy.sh'
        self.clock = clock or sl.Clock()

    def inspect(self, running=False):
        now = self.clock.now()
        checks = {}
        checks['window'] = self.windows.remaining(now) > 0
        try:
            rc = subprocess.run(['bash', str(self.night), 'voice'], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=5).returncode
            checks['night'] = rc == 1  # 0 BUSY; any other code is unknown
        except (OSError, subprocess.TimeoutExpired):
            checks['night'] = False
        memory = next(int(s.split()[1]) / 1024 for s in Path('/proc/meminfo').read_text().splitlines()
                      if s.startswith('MemAvailable:'))
        # The child consumes its admitted budget; don't demand that budget twice.
        checks['memory'] = memory >= (min(800, self.min_mem) if running else self.min_mem)
        try:
            checks['idle'] = conversation_age(self.activity_log, now) >= self.idle_minutes * 60
        except (OSError, ValueError):
            checks['idle'] = False
        return checks

    def require(self):
        if not all(self.inspect().values()):
            raise PermissionError('guard_refused')

    def watch(self):
        if not all(self.inspect(running=True).values()):
            raise PermissionError('guard_refused')


class ReplayBackend:
    def __init__(self, guards, *, python=sys.executable, service_dir=None, env_source=None,
                 fake=False, lock=Path('/tmp/zoe-voice-harness.lock')):
        self.guards, self.python = guards, python
        self.service_dir = Path(service_dir or ROOT / 'services/zoe-data')
        self.env_source, self.fake, self.lock = env_source, fake, Path(lock)
        self.cache, self.calls = {}, 0

    @contextlib.contextmanager
    def experiment(self, cap):
        self.guards.require()
        deadline = time.monotonic() + min(cap, self.guards.windows.remaining(self.guards.clock.now()))
        with self.lock.open('a') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PermissionError('lock_busy') from None
            try:
                self.guards.require()
                yield deadline
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def measure(self, config, turn, clip, deadline):
        self.guards.require()
        remaining = min(deadline - time.monotonic(),
                        self.guards.windows.remaining(self.guards.clock.now()))
        if remaining <= 0:
            raise sl.BudgetExpired
        env = os.environ.copy()
        # Candidate overrides are CHILD ONLY. Baseline explicitly pins ALL automatic flags.
        env.pop('ZOE_REPLAY_FAULT', None)
        env.update({k: str(int(v)) if type(v) is bool else str(v)
                    for k, v in dict(DEFAULTS, **config).items() if k in AUTO})
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        cmd = [self.python, str(HERE / ('fake_instrument.py' if self.fake else 'instrument.py'))]
        payload = dict(turn=turn, clip=str(clip), service_dir=str(self.service_dir), env_source=self.env_source)
        child = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, env=env, start_new_session=True)
        def kill_group():
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        watchdog = threading.Timer(remaining, kill_group)
        watchdog.daemon = True
        watchdog.start()
        try:
            # Poll guards while child runs: a new household turn cancels this experiment.
            child.stdin.write(json.dumps(payload)); child.stdin.close(); child.stdin = None
            next_watch = time.monotonic()
            while child.poll() is None:
                if time.monotonic() >= deadline:
                    raise sl.BudgetExpired
                if time.monotonic() >= next_watch:
                    getattr(self.guards, 'watch', self.guards.require)()
                    next_watch = time.monotonic() + 2
                time.sleep(min(.1, max(.001, deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                raise sl.BudgetExpired
            raw = child.stdout.read(65537)
            if child.returncode or len(raw) > 65536:
                raise ValueError('instrument_failed')
            row = clean(json.loads(raw), turn)
            self.calls += 1
            return row
        finally:
            watchdog.cancel()
            # Also kill descendants left behind by a child which already exited.
            kill_group()
            child.wait()
            child.stdout.close()


def clean(data, turn):
    """Positive output schema: even planted payload text cannot reach artifacts."""
    if data.get('turn') != turn or data.get('memory_recall') != 'ok' or data.get('fault'):
        raise ValueError('invalid_instrument')
    values = {k: data[k] for k in TIMINGS}
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values.values()):
        raise ValueError('invalid_timing')
    if abs(values['e2e_ms'] - sum(values[k] for k in TIMINGS[:3])) > 1:
        raise ValueError('invalid_total')
    if data.get('verdict') not in {'OK', 'EMPTY', 'CANT_DO', 'ERROR', 'DEFERRED'}:
        raise ValueError('invalid_outcome')
    # Hash ALL outcome fields, never store a label supplied by a child.
    outcome = digest([data['verdict'], data.get('route'), data.get('heard_hash')])
    if data.get('verdict') in {'ERROR', 'DEFERRED'} or not data.get('route') or not data.get('heard_hash'):
        raise ValueError('incomplete_outcome')
    return dict(turn=turn, **values, outcome=outcome)


def measurements(rows):
    return [sl.Measurement(r['turn'], r['e2e_ms'], r['outcome']) for r in rows]


def impossible_to_keep(before, after, total, alpha=.01):
    # Even if ALL remaining pairs favour B, the fixed-N sign test cannot pass.
    wins = sum(b['e2e_ms'] < a['e2e_ms'] for a, b in zip(before, after))
    losses = sum(b['e2e_ms'] > a['e2e_ms'] for a, b in zip(before, after))
    best_wins = wins + total - len(before)
    n = best_wins + losses
    p = sum(math.comb(n, i) for i in range(best_wins, n + 1)) / 2 ** n if n else 1
    return p > alpha


def paired(backend, config, candidate, clips, deadline, *, reuse=False, early=True):
    before, after = [], []
    key = digest([config, [cid for cid, _ in clips]])
    cache = backend.cache.get(key) if reuse else None
    for i, (_, clip) in enumerate(clips):
        # Logical A B A B. Reuse only the first A; ALWAYS take a fresh A drift sentinel.
        a1 = cache[i] if cache else backend.measure(config, i, clip, deadline)
        b1 = backend.measure(candidate, i, clip, deadline)
        a2 = backend.measure(config, i, clip, deadline)
        b2 = backend.measure(candidate, i, clip, deadline)
        if len({a1['outcome'], a2['outcome'], b1['outcome'], b2['outcome']}) != 1:
            return before, after, 'outcome_changed'
        if cache and abs(a1['e2e_ms'] - a2['e2e_ms']) > 15:
            backend.cache.clear()
            raise ValueError('baseline_drift')
        def average(x, y):
            return dict(turn=i, outcome=x['outcome'], **{k: (x[k] + y[k]) / 2 for k in TIMINGS})
        before.append(average(a1, a2)); after.append(average(b1, b2))
        if early and impossible_to_keep(before, after, len(clips)):
            return before, after, 'losing'
    backend.cache[key] = before
    return before, after, None


def sample_size(sigma, effect):
    """Approx median test planning: normal noise, one-sided .01, 80% power.

    Sign test loses ~pi/2 efficiency vs mean; minimum 7 for exact p <= .01.
    This is a planning approximation, never a KEEP guarantee.
    """
    return max(7, math.ceil(math.pi / 2 * ((2.32635 + .84162) * sigma / effect) ** 2))


def read_ledger(path, key, surface):
    rows = [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []
    for r in rows:
        if (set(r) != {'key', 'knob', 'value', 'n', 'verdict', 'reason', 'delta_ms', 'p', 'before', 'after'}
                or r['key'] != key or r['knob'] not in AUTO
                or type(r['value']) not in (int, float, bool)
                or r['value'] not in surface[r['knob']]['values']
                or r['verdict'] not in {'KEEP', 'DISCARD'}
                or r['reason'] not in {'improved', 'outcome_changed', 'losing', 'incomplete',
                                       'below_threshold', 'tail_regressed', 'uncertain', 'stage_regressed'}
                or (r['verdict'] == 'KEEP') != (r['reason'] == 'improved')
                or type(r['n']) is not int or r['n'] < 0
                or any(r[k] is not None and (type(r[k]) not in (int, float)
                       or not math.isfinite(r[k])) for k in ('delta_ms', 'p'))):
            raise ValueError('invalid_ledger')
        for side in ('before', 'after'):
            if len(r[side]) != r['n']:
                raise ValueError('invalid_ledger')
            for m in r[side]:
                if (set(m) != {'turn', 'outcome', *TIMINGS}
                        or type(m['turn']) is not int
                        or not re.fullmatch('[0-9a-f]{64}', m['outcome'])
                        or any(type(m[k]) not in (float, int) or not math.isfinite(m[k])
                               or m[k] < 0 for k in TIMINGS)):
                    raise ValueError('invalid_ledger')
    return rows


def calibrate(backend, config, clips, cap, key, repeats=3):
    deltas, false_keeps, stages = [], 0, set()
    for _ in range(repeats):
        with backend.experiment(cap) as deadline:
            a, b, reason = paired(backend, config, config, clips, deadline, early=False)
        if reason:
            raise ValueError('unstable_calibration')
        deltas.extend(y['e2e_ms'] - x['e2e_ms'] for x, y in zip(a, b))
        false_keeps += sl.verdict(measurements(a), measurements(b))[0] == 'KEEP'
        if any(x['brain_ms'] > 0 for x in a):
            stages.update({'brain', 'prepost', 'memory'})
    sigma = stats.stdev(deltas) if len(deltas) > 1 else 0
    return dict(key=key, n=len(deltas), repeats=repeats, sigma_ms=sigma,
                false_keeps=false_keeps, false_keep_rate=false_keeps / repeats,
                turns_15=sample_size(sigma, 15), turns_30=sample_size(sigma, 30),
                stages=sorted(stages))


def ordered(surface, stages, seed=7):
    ideas = [(k, v) for k, v in sl.candidates(surface, seed)
             if k in AUTO and surface[k]['stage'] in stages]
    # [inferred] ranked potential saving / expected per-turn cost; calibrate later.
    gains = {'prepost': 400, 'brain': 100, 'memory': 30}
    return sorted(ideas, key=lambda kv: (-gains[surface[kv[0]]['stage']] *
                  abs(float(kv[1]) - float(surface[kv[0]]['default'])) /
                  max(1, max(map(float, surface[kv[0]]['values']))), kv[0]))


def proposal(output, name, value, surface, row):
    """Ready-to-apply product DEFAULT patch, written as data, never applied."""
    import ast
    import difflib
    sl.verify_source(surface[name])
    p = ROOT / 'services/zoe-data/flag_registry.py'
    original = p.read_text()
    calls = [n for n in ast.walk(ast.parse(original)) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == '_f' and len(n.args) >= 3
             and isinstance(n.args[0], ast.Constant) and n.args[0].value == name]
    if len(calls) != 1 or name not in AUTO:
        raise ValueError('invalid_proposal')
    node = calls[0].args[2]
    if not isinstance(node, ast.Constant) or node.lineno != node.end_lineno:
        raise ValueError('invalid_proposal')
    lines = original.splitlines(True)
    line = lines[node.lineno - 1]
    lines[node.lineno - 1] = line[:node.col_offset] + repr(value) + line[node.end_col_offset:]
    patch = ''.join(difflib.unified_diff(original.splitlines(True), lines, n=0,
                    fromfile='a/services/zoe-data/flag_registry.py',
                    tofile='b/services/zoe-data/flag_registry.py'))
    stem = output / ('keep-' + digest([name, value])[:12])
    stem.with_suffix('.patch').write_text(patch)
    stem.with_suffix('.pr.md').write_text(
        f'[lab] Retain measured candidate {name}\n\n'
        f'Paired pre-TTS saving: {-row["delta_ms"]:.3f} ms; p={row["p"]:.6g}.\n'
        'Patch changes the registered product default; explicit deployment env values override it.\n'
        'Owner must review accumulated patches together, confirm on held-out turns, run the voice\n'
        'regression gate and listen before deployment. No live setting was changed.\n')


def session(args):
    surface = sl.load_surface()
    windows = sl.Windows()
    guards = Guards(windows, args.activity_log, min_mem=args.min_mem, idle_minutes=args.idle_minutes)
    backend = ReplayBackend(guards, python=args.python, service_dir=args.service_dir, env_source=args.env_source)
    if args.dry_run:
        checks = guards.inspect()  # Walk every guard, even if an earlier one fails.
        try:
            with backend.lock.open('a') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                checks['lock'] = True
        except (OSError, BlockingIOError):
            checks['lock'] = False
        print(json.dumps({'guards': checks, 'would_run': 'child instrument; A B A B; child env only',
                          'automatic_knobs': sorted(AUTO)}, sort_keys=True))
        return
    guards.require()
    output = args.output.resolve()
    if not output.is_relative_to(HERE / 'runs'):
        raise ValueError('output_outside_lab')
    output.mkdir(parents=True, exist_ok=True)
    # Prevent two drivers from sharing a ledger between experiment lock holds.
    with (output / 'session.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        clips = select_corpus(corpus_default(), args.turns, args.seed)
        config = {k: v['default'] for k, v in surface.items() if k in AUTO}
        env_hash = hashlib.sha256(Path(args.env_source).read_bytes()).hexdigest() if args.env_source else None
        inherited = digest({k: v for k, v in os.environ.items()
                            if k.startswith('ZOE_') or k.endswith(('_URL', '_TOKEN'))})
        key = digest([revision(), config, [c for c, _ in clips], env_hash, inherited,
                      args.python, str(backend.service_dir), args.seed])
        calpath = output / 'calibration.json'
        cal = json.loads(calpath.read_text()) if calpath.exists() else None
        if args.calibrate or not cal or cal.get('key') != key:
            cal = calibrate(backend, config, clips, args.cap, key)
            calpath.write_text(json.dumps(cal, sort_keys=True) + '\n')
        if args.calibrate:
            return
        if cal['false_keeps'] or args.turns < cal['turns_15']:
            raise ValueError('calibration_insufficient')
        ledger = output / 'replay-ledger.jsonl'
        rows = read_ledger(ledger, key, surface)
        tried = {(r['knob'], r['value']) for r in rows}
        for row in rows:
            if row['verdict'] == 'KEEP':
                config[row['knob']] = row['value']
        added = 0
        try:
            for name, value in ordered(surface, cal['stages'], args.seed):
                if added >= args.max_experiments or not windows.remaining(guards.clock.now()):
                    break
                if (name, value) in tried:
                    continue
                candidate = dict(config, **{name: value})
                a, b = [], []
                try:
                    with backend.experiment(args.cap) as deadline:
                        a, b, reason = paired(backend, config, candidate, clips, deadline,
                                              reuse=added % args.refresh_every != 0)
                except (sl.BudgetExpired, PermissionError, ValueError, OSError):
                    reason = 'incomplete'
                if reason:
                    decision, delta, p = 'DISCARD', None, None
                else:
                    decision, reason, delta, p = sl.verdict(measurements(a), measurements(b))
                    if decision == 'KEEP' and any(
                            stats.median(y[k] - x[k] for x, y in zip(a, b)) > 5
                            for k in TIMINGS[:3]):
                        decision, reason = 'DISCARD', 'stage_regressed'
                row = dict(key=key, knob=name, value=value, n=len(a), verdict=decision,
                           reason=reason, delta_ms=delta, p=p, before=a, after=b)
                # Every persisted child row came through clean(), no raw payloads.
                with ledger.open('a') as stream:
                    stream.write(json.dumps(row, allow_nan=False, sort_keys=True) + '\n')
                rows.append(row); added += 1
                if decision == 'KEEP':
                    config = candidate
                    proposal(output, name, value, surface, row)
                if reason == 'incomplete':
                    break
        finally:
            summary(output, rows, surface)


def summary(output, rows, surface):
    kept = [r for r in rows if r['verdict'] == 'KEEP']
    text = f'[lab] Morning summary\n\nExperiments: {len(rows)}; kept: {len(kept)}; discarded: {len(rows)-len(kept)}.\n\n'
    text += '| Knob | Measured ms | Running arithmetic total ms |\n|---|---:|---:|\n'
    total = 0
    for r in kept:
        total -= r['delta_ms']
        text += f'| {r["knob"]} | {-r["delta_ms"]:.3f} | {total:.3f} |\n'
    text += '\n[inferred] Sequential deltas; combined gain still needs independent confirmation.\n\nNeeds owner flip / code patch (b):\n'
    text += '\n'.join('- ' + k for k, v in surface.items() if classification(v) == 'b')
    text += '\n\nPanel only (c):\n' + '\n'.join('- ' + k for k,v in surface.items() if classification(v) == 'c')
    text += '\n\nUnexercised TTS flags (a):\n' + '\n'.join('- ' + k for k,v in surface.items() if classification(v) == 'a' and k not in AUTO)
    (output / 'morning.md').write_text(text + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--calibrate', action='store_true')
    p.add_argument('--python', default=str(Path.home() / '.zoe/venvs/zoe-data-py312/bin/python'))
    p.add_argument('--service-dir', type=Path, default=ROOT / 'services/zoe-data')
    p.add_argument('--env-source', help='private service env file, read only in child')
    p.add_argument('--activity-log', type=Path, default=Path.home() / '.zoe-logs/zoe-data.app.log')
    p.add_argument('--output', type=Path, default=HERE / 'runs/window')
    p.add_argument('--turns', type=int, default=64)
    p.add_argument('--seed', type=int, default=7)
    p.add_argument('--cap', type=float, default=3600)
    p.add_argument('--max-experiments', type=int, default=20)
    p.add_argument('--refresh-every', type=int, default=3)
    p.add_argument('--min-mem', type=int, default=1500)
    p.add_argument('--idle-minutes', type=int, default=10)
    args = p.parse_args()
    if min(args.turns, args.cap, args.max_experiments, args.refresh_every) <= 0 or args.min_mem < 1500 or args.idle_minutes < 10:
        p.error('invalid budget or weakened guard')
    try:
        session(args)
    except (ValueError, PermissionError, sl.BudgetExpired, OSError, TypeError, KeyError, AttributeError):
        # Never expose exception text: library errors can carry household text or paths.
        print('[lab] refused or incomplete; no speed claim', file=sys.stderr)
        return 2
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
