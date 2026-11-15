"""[lab] Offline, single-knob paired experiments. No production imports or processes."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics as stats
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
# Positive allowlist: editing surface metadata cannot grant access to another symbol.
FLUE = 'services/zoe-data/zoe_flue_client.py'
TTS = 'services/zoe-data/routers/voice_tts.py'
VAD = 'scripts/setup/zoe_voice_daemon.py'
ALLOWED = {
    'ZOE_FLUE_PREPOST_CONCURRENT': FLUE, 'ZOE_BRAIN_VOLATILE_AFTER_USER': FLUE,
    'ZOE_MEMORY_TICK_DEFER_S': 'services/zoe-data/memory_service.py',
    'ZOE_CONTINUITY_CEILING_S': FLUE, '_HOUSEHOLD_FETCH_BUDGET_S': FLUE,
    '_CONTINUITY_PORTRAIT_TIMEOUT_S': FLUE, '_PERSONA_REFRESH_BUDGET_S': FLUE,
    'ZOE_VOICE_FAST_FIRST_AUDIO': TTS, '_FIRST_UNIT_CLAUSE_MIN': TTS,
    '_FIRST_UNIT_SOFT_CAP': TTS, 'ZOE_FIRST_SOUND_CLAUSE': 'services/zoe-data/flag_registry.py',
    'ZOE_FIRST_SOUND_CLAUSE_MIN_CHARS': 'services/zoe-data/voice_first_sound.py',
    'ZOE_FIRST_SOUND_CLAUSE_MIN_WORDS': 'services/zoe-data/voice_first_sound.py',
    'ZOE_VAD_CLEAN_TAIL_MS': VAD, 'ZOE_VAD_CLEAN_FALL_CHUNKS': VAD,
    'ZOE_VAD_HESITATION_TAIL_MS': VAD,
}
DENIED = ('safety', 'floor', 'forget', 'erase', 'auth', 'permission', 'restraint',
          'medical', 'distress', 'model', 'backend', 'token', 'secret')


def load_surface(path=HERE / 'surface.json'):
    data = json.loads(Path(path).read_text())
    if not set(DENIED) <= set(data['denylist']):
        raise ValueError('denylist weakened')
    result = {}
    for knob in data['knobs']:
        name, file = knob['name'], knob['file']
        target = (name + '/' + file).lower()
        if any(word in target for word in DENIED) or ALLOWED.get(name) != file:
            raise ValueError('forbidden target')
        values = knob['values']
        if (name in result or knob['kind'] != ('env' if name.startswith('ZOE_') else 'constant')
                or not values or knob['default'] not in values or knob['line'] < 1
                or any(type(v) not in (bool, int, float) or not math.isfinite(v) for v in values)):
            raise ValueError('invalid surface')
        # Never propose a value under the immutable endpoint safety floor.
        if name == 'ZOE_VAD_CLEAN_TAIL_MS' and any(v != 0 and v < 500 for v in values):
            raise ValueError('safety floor')
        result[name] = knob
    return result


class Clock:
    def now(self):
        return datetime.now().astimezone()          # the box's own local time; no place name is written down

    def monotonic(self):
        return time.monotonic()


class FakeClock(Clock):
    def __init__(self, start):
        if start.tzinfo is None:
            raise ValueError('timezone required')
        self.start, self.elapsed = start, 0.0

    def now(self):
        return self.start + timedelta(seconds=self.elapsed)

    def monotonic(self):
        return self.elapsed

    def advance(self, seconds):
        self.elapsed += seconds


class Windows:
    def __init__(self, path=HERE / 'windows.json'):
        data = json.loads(Path(path).read_text())
        # "local" = the box's own zone (the default: the public repo names no place); a zone name is allowed for tests
        self.zone = None if data['timezone'] == 'local' else ZoneInfo(data['timezone'])
        self.days = {}
        for day, spans in data['weekdays'].items():
            if day not in [str(i) for i in range(7)]:
                raise ValueError('weekday')
            parsed = []
            for start, end in spans:
                def minute(s):
                    h, m = map(int, s.split(':'))
                    if not 0 <= h < 24 or not 0 <= m < 60:
                        raise ValueError('time')
                    return h * 60 + m
                a, b = minute(start), minute(end)
                if a >= b or any(a < y and b > x for x, y in [(115, 210), (240, 295)] + parsed):
                    raise ValueError('maintenance, overnight or overlapping window')
                parsed.append((a, b))
            self.days[int(day)] = parsed

    def remaining(self, now):
        if now.tzinfo is None:
            raise ValueError('timezone required')
        local = now.astimezone(self.zone)             # None = the system's local zone
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        for a, b in self.days.get(local.weekday(), []):
            if midnight + timedelta(minutes=a) <= local < midnight + timedelta(minutes=b):
                return ((midnight + timedelta(minutes=b)) - local).total_seconds()
        return 0.0


@dataclass(frozen=True)
class Measurement:
    turn: int
    ms: float
    outcome: int  # synthetic said-versus-did signature, never utterance/reply text


class BudgetExpired(Exception):
    pass


class Backend(Protocol):
    def measure(self, config: dict, turn: int, seed: int, deadline: float) -> Measurement:
        """Return one numeric turn before the cooperative monotonic deadline."""
        ...


class FakeBackend:
    """Synthetic ground truth: repeating helpful/harmful/inert/broken knob classes.

    Helpful changes save 20..40ms, harmful add 30ms, inert save zero,
    broken save 80ms but change turn zero's outcome. Independent arm noise ±8ms.
    These effects are invented, not predictions about the named real knobs.
    """
    def __init__(self, surface, clock, noise=8, turn_seconds=0.01):
        self.surface, self.clock = surface, clock
        self.noise, self.turn_seconds, self.calls = noise, turn_seconds, 0
        self.classes = {k: i % 4 for i, k in enumerate(surface)}

    def effect(self, name, value):
        knob = self.surface[name]
        if value == knob['default']:
            return 0.0
        index = knob['values'].index(value)
        return (20 + index * 2, -30, 0, 80)[self.classes[name]]

    def measure(self, config, turn, seed, deadline):
        if self.clock.monotonic() + self.turn_seconds >= deadline:
            raise BudgetExpired
        self.clock.advance(self.turn_seconds)
        self.calls += 1
        effect = sum(self.effect(k, v) for k, v in config.items())
        broken = any(self.classes[k] == 3 and v != self.surface[k]['default'] for k, v in config.items())
        noise = random.Random(seed).uniform(-self.noise, self.noise)
        return Measurement(turn, 1000 + turn * 3 - effect + noise, int(broken and turn == 0))


class ReplayBackend:
    """Intentionally unwired; see README for the instrument adapter contract."""
    def __init__(self, i_own_the_box=False):
        if not i_own_the_box:
            raise PermissionError('requires --i-own-the-box and an exclusive quiet window')
        raise NotImplementedError('replay adapter is a stub; no process was started')


def p90(values):
    return sorted(values)[math.ceil(len(values) * 0.9) - 1]


def verdict(before, after, threshold=15, p90_bound=5, alpha=0.01):
    if (not before or [m.turn for m in before] != [m.turn for m in after]
            or len(set(m.turn for m in before)) != len(before)
            or any(not math.isfinite(m.ms) or m.ms < 0 for m in before + after)):
        raise ValueError('invalid paired measurements')
    deltas = [a.ms - b.ms for a, b in zip(after, before)]
    delta = stats.median(deltas)
    positive, negative = sum(d < 0 for d in deltas), sum(d > 0 for d in deltas)
    n = positive + negative  # discard ties, exact one-sided binomial sign test
    p = sum(math.comb(n, i) for i in range(positive, n + 1)) / 2 ** n if n else 1
    if any(a.outcome != b.outcome for a, b in zip(after, before)):
        reason = 'outcome_changed'
    elif delta > -threshold:
        reason = 'below_threshold'
    elif p90([m.ms for m in after]) > p90([m.ms for m in before]) + p90_bound:
        reason = 'tail_regressed'
    elif p > alpha:
        reason = 'uncertain'
    else:
        reason = 'improved'
    return ('KEEP' if reason == 'improved' else 'DISCARD'), reason, delta, p


def candidates(surface, seed):
    ideas = [(k, v) for k, knob in surface.items() for v in knob['values'] if v != knob['default']]
    random.Random(seed).shuffle(ideas)
    # Cover every knob once before spending the window on additional values.
    seen, first, rest = set(), [], []
    for name, value in ideas:
        (rest if name in seen else first).append((name, value))
        seen.add(name)
    return first + rest


def load_ledger(path, surface):
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    allowed_keys = {'knob', 'value', 'n', 'before', 'after', 'delta_ms', 'verdict', 'reason', 'duration', 'p'}
    reasons = {'improved', 'outcome_changed', 'below_threshold', 'tail_regressed', 'uncertain', 'budget'}
    for row in rows:
        if (set(row) != allowed_keys or row['knob'] not in surface
                or row['value'] not in surface[row['knob']]['values']
                or row['verdict'] not in ('KEEP', 'DISCARD', 'ABANDON') or row['reason'] not in reasons
                or (row['verdict'] == 'KEEP') != (row['reason'] == 'improved')
                or type(row['value']) not in (bool, int, float)
                or type(row['n']) is not int or row['n'] < 0
                or type(row['duration']) not in (int, float) or not math.isfinite(row['duration'])
                or row['duration'] < 0
                or any(row[k] is not None and (type(row[k]) not in (int, float) or not math.isfinite(row[k]))
                       for k in ('before', 'after', 'delta_ms', 'p'))):
            raise ValueError('invalid ledger; no free text permitted')
    return rows


def run(surface, backend: Backend, clock, windows, output, *, seed=7, turns=64, cap=30,
        max_experiments=100, seconds=600):
    if (turns < 1 or max_experiments < 1
            or any(not math.isfinite(v) or v <= 0 for v in (cap, seconds))):
        raise ValueError('positive fixed budgets required')
    if not windows.remaining(clock.now()):
        raise ValueError('outside declared window')
    output = Path(output).resolve()
    if not output.is_relative_to(HERE):
        raise ValueError('artifacts must stay inside this lab')
    output.mkdir(parents=True, exist_ok=True)
    study = dict(seed=seed, turns=turns, cap=cap,
                 surface=hashlib.sha256(json.dumps(surface, sort_keys=True).encode()).hexdigest())
    study_path = output / 'study.json'
    if study_path.exists() and json.loads(study_path.read_text()) != study:
        raise ValueError('study seed, surface or fixed budget changed; use a new directory')
    study_path.write_text(json.dumps(study, sort_keys=True) + '\n')
    ledger = output / 'ledger.jsonl'
    rows = load_ledger(ledger, surface)
    best = {k: knob['default'] for k, knob in surface.items()}
    for row in rows:
        if row['verdict'] == 'KEEP':
            best[row['knob']] = row['value']
    tried = {(r['knob'], r['value']) for r in rows}
    end = clock.monotonic() + seconds
    wall_end = time.monotonic() + seconds
    added = []
    for name, value in candidates(surface, seed):
        if (name, value) in tried:
            continue
        remaining = min(windows.remaining(clock.now()), end - clock.monotonic(),
                        wall_end - time.monotonic(), cap)
        if remaining <= 0 or len(added) >= max_experiments:
            break
        start = clock.monotonic()
        deadline = start + remaining
        wall_deadline = min(wall_end, time.monotonic() + remaining)
        candidate = dict(best, **{name: value})
        before, after = [], []
        try:
            for turn in range(turns):
                pair = {}
                order = [False, True]
                random.Random(f'{seed}:{name}:{value}:{turn}').shuffle(order)
                for arm in order:
                    if (clock.monotonic() >= deadline or time.monotonic() >= wall_deadline
                            or not windows.remaining(clock.now())):
                        raise BudgetExpired
                    arm_seed = int.from_bytes(hashlib.sha256(f'{seed}:{name}:{value}:{turn}:{arm}'.encode()).digest()[:8], 'big')
                    pair[arm] = backend.measure(candidate if arm else best, turn, arm_seed, deadline)
                    if (clock.monotonic() >= deadline or time.monotonic() >= wall_deadline
                            or not windows.remaining(clock.now())):
                        raise BudgetExpired
                before.append(pair[False])
                after.append(pair[True])
            decision, reason, delta, p = verdict(before, after)
        except BudgetExpired:
            decision, reason, delta, p = 'ABANDON', 'budget', None, None
        row = dict(knob=name, value=value, n=len(before),
                   before=stats.median([m.ms for m in before]) if before else None,
                   after=stats.median([m.ms for m in after]) if after else None,
                   delta_ms=delta, verdict=decision, reason=reason,
                   duration=round(clock.monotonic() - start, 6), p=p)
        # JSON-only proposals include the full accumulated candidate for a human PR.
        if decision == 'KEEP':
            old = best[name]
            best = candidate
            patch = dict(target=surface[name]['file'], line=surface[name]['line'], knob=name,
                         old=old, value=value, candidate=best)
            (output / f'keep-{len(rows) + len(added):04d}.patch.json').write_text(json.dumps(patch, sort_keys=True) + '\n')
        with ledger.open('a') as stream:
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
        added.append(row)
        if decision == 'ABANDON':
            break
    return added


def tally(rows, backend):
    winners = {k for k, c in backend.classes.items() if c == 0}
    kept = [r for r in rows if r['verdict'] == 'KEEP']
    found = {r['knob'] for r in kept} & winners
    current, false_keeps = {}, 0
    for row in kept:
        name, value = row['knob'], row['value']
        old = current.get(name, backend.surface[name]['default'])
        false_keeps += backend.effect(name, value) <= backend.effect(name, old) or backend.classes[name] == 3
        current[name] = value
    return dict(experiments=len(rows), keeps=len(kept), real_wins_found=len(found),
                ground_truth_winning_knobs=len(winners), missed_wins=len(winners - found),
                false_keeps=false_keeps,
                proven_ms=round(-sum(r['delta_ms'] for r in kept), 3),
                ground_truth_ms=sum(backend.effect(k, next(r['value'] for r in reversed(kept) if r['knob'] == k)) for k in found))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['fake', 'replay'], default='fake')
    parser.add_argument('--i-own-the-box', action='store_true')
    parser.add_argument('--fake-start', help='aware ISO timestamp, synthetic clock ONLY')
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--turns', type=int, default=64)
    parser.add_argument('--cap', type=float, default=30)
    parser.add_argument('--seconds', type=float, default=600)
    parser.add_argument('--max-experiments', type=int, default=100)
    parser.add_argument('--output', type=Path, default=HERE / 'runs' / 'fake')
    args = parser.parse_args()
    if args.backend == 'replay':
        ReplayBackend(args.i_own_the_box)
    surface = load_surface()
    clock = FakeClock(datetime.fromisoformat(args.fake_start) if args.fake_start else Clock().now())
    backend = FakeBackend(surface, clock)
    rows = run(surface, backend, clock, Windows(), args.output, seed=args.seed, turns=args.turns,
               cap=args.cap, seconds=args.seconds, max_experiments=args.max_experiments)
    print(json.dumps(tally(rows, backend), sort_keys=True))


if __name__ == '__main__':
    main()
