"""The replay harness: record a day of household events, replay it, report what skills would do.

A RECORDING is a JSON-lines file. Line 1 is a header (people, and how each person answers
offers); every other line is one FIRST-HAND event (someone spoke, arrived, a calendar
changed) with its time. Nothing a skill produced is recorded: that is what a replay computes.

``replay(recording, skills)`` builds a fresh kernel in a temp dir, installs the given skills,
feeds the events in order with the kernel clock set to each event's own time, lets the
kernel route each offer and the recorded policy answer it, and returns a REPORT: one line per
offer, action, refusal, claim and audit finding, in the order they happened.

The report is the product. It is meant to be how a change is tested before it is trusted, so:
* it is DETERMINISTIC: the same recording and skills give the same bytes (no wall clock, no
  randomness, no sequence numbers, no temp paths; a line names the recording line that
  triggered it, ``@12``, which does not shift when another skill says more or less);
* it DIFFS well: one fact per line, fixed columns, totals at the end.
``diff(a, b)`` is a unified diff of two reports: the same day against two versions of a skill.

    python3 labs/zoe-os/zoe_os/replay.py DAY.jsonl pkg.mod:SkillClass [--against pkg.mod:Other]
"""
from __future__ import annotations

import difflib
import hashlib
import importlib
import json
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

if __name__ == "__main__":      # run as a script: make the package importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zoe_os.capability import ADULT, CHILD, Manifest, Person  # noqa: E402
from zoe_os.events import Event, Kind  # noqa: E402
from zoe_os.kernel import Kernel  # noqa: E402

FORMAT = 1
PANEL = "replay.panel"          # the surface that records each person's answer to an offer


@dataclass
class Recording:
    people: list[Person]
    events: list[dict]                                   # t, kind, source, subject, visibility, payload
    answers: dict[str, dict[str, str]] = field(default_factory=dict)   # person -> skill|"*" -> response
    no_accept: list[str] = field(default_factory=list)   # actions that need no yes (a light)

    def dumps(self) -> str:
        head = {"recording": FORMAT, "answers": self.answers, "no_accept": sorted(self.no_accept),
                "people": [{"id": p.id, "role": p.role, "guardians": list(p.guardians)}
                           for p in self.people]}
        return "".join(json.dumps(row, sort_keys=True) + "\n" for row in (head, *self.events))

    def save(self, path) -> None:
        Path(path).write_text(self.dumps())

    @classmethod
    def loads(cls, text: str) -> "Recording":
        head, *rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        if head.get("recording") != FORMAT:
            raise ValueError("not a recording this harness reads")
        people = [Person(p["id"], p.get("role", ADULT), tuple(p.get("guardians", ()))) for p in head["people"]]
        return cls(people, rows, head.get("answers", {}), head.get("no_accept", []))

    @classmethod
    def load(cls, path) -> "Recording":
        return cls.loads(Path(path).read_text())


def record(kernel: Kernel, **header) -> Recording:
    """Everything first-hand on a kernel's log, as a recording (kernel-internal read)."""
    rows = [{"t": e.ts, "kind": e.kind.value, "source": e.source, "subject": e.subject,
             "visibility": e.visibility.value, "payload": e.payload, "mentions": list(e.mentions)}
            for e in kernel.spine._fetch(0, None, None, 10_000_000)
            if kernel.enforcer.is_origin(e.source) and e.kind is not Kind.OFFER_RESPONSE]
    return Recording(list(kernel.grants.people.values()), rows, **header)


@dataclass
class Report:
    lines: list[str]
    counts: dict[str, int]

    @property
    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


class _World:
    """A stand-in for every device: it does what it is asked and can show that it did."""

    def __init__(self) -> None:
        self.done: list = []

    def do(self, person, args):
        self.done.append((person, args))

    def verify(self, person, args, result):
        return {"done": True}


def _clock(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def replay(rec: Recording, skills: list, *, label: str = "") -> Report:
    """Run ``skills`` (instances with a ``manifest`` and ``on_event``) over the recording."""
    now = [rec.events[0]["t"] if rec.events else 0.0]
    with tempfile.TemporaryDirectory(prefix="zoe-os-replay-") as tmp:
        k = Kernel(str(Path(tmp) / "replay.db"), rec.people, clock=lambda: now[0])
        try:
            return _run(k, rec, skills, now, label)
        finally:
            k.close()


def _run(k: Kernel, rec: Recording, skills: list, now: list, label: str) -> Report:
    origins: dict[str, set[str]] = {PANEL: {"offer_response"}}
    for row in rec.events:
        origins.setdefault(row["source"], set()).add(row["kind"])
    for source, kinds in sorted(origins.items()):
        k.grants.install({"skill": source, "emits": sorted(kinds), "origin": True})
    world = _World()
    for skill in skills:
        k.install(skill)
        m = Manifest.from_dict(skill.manifest)
        for action in sorted(m.acts):
            k.actions.register(action, world.do, world.verify, needs_accept=action not in rec.no_accept)
        for p in rec.people:                      # every member enabled every skill under test
            if p.role == ADULT:
                k.grants.grant(p.id, m.skill)
            elif p.role == CHILD and p.guardians:
                k.grants.grant(p.id, m.skill, guardian=p.guardians[0])
    facts, answered = [], set()
    for index, row in enumerate(rec.events, start=2):           # the file's line number
        now[0] = row["t"]
        before = k.spine.last_seq
        k.spine.publish(Event(row["kind"], row["source"], row.get("subject"),
                              row.get("visibility", "private"), row["payload"], row.get("mentions", ())))
        _settle(k, rec, row["t"], answered)
        k.auditor.scan()
        facts += [(index, row["t"], e) for e in k.spine._fetch(before, None, None, 100_000)]
    return _report(rec, skills, facts, label)


def _settle(k: Kernel, rec: Recording, t: float, answered: set[int]) -> None:
    """Pump until quiet; route each new offer; answer it the way the recording says."""
    when = datetime.fromtimestamp(t, timezone.utc)
    for _ in range(50):
        k.pump()
        offers = [o for o in k.spine._fetch(0, frozenset({Kind.OFFER}), None, 100_000)
                  if o.kind is Kind.OFFER and o.seq not in answered]
        if not offers:
            return
        for offer in offers:
            answered.add(offer.seq)
            k.attention.route(offer, hour=when.hour, day=when.toordinal())
            policy = rec.answers.get(offer.subject or "", {})
            response = policy.get(offer.source, policy.get("*"))
            if response and offer.subject:
                k.spine.publish(Event(Kind.OFFER_RESPONSE, PANEL, offer.subject, caused_by=(offer.seq,),
                                      payload={"offer_id": offer.payload["offer_id"], "response": response}))


def _report(rec: Recording, skills: list, facts: list, label: str) -> Report:
    names = sorted(Manifest.from_dict(s.manifest).skill for s in skills)
    counts = {key: 0 for key in ("offers", "actions", "refusals", "claims", "findings")}
    per = {name: dict.fromkeys(("offers", "actions", "refusals"), 0) for name in names}
    channel = {e.caused_by[0]: e.payload["channel"] for _, _, e in facts if e.kind is Kind.ATTENTION}
    answer = {e.caused_by[0]: e.payload["response"] for _, _, e in facts if e.kind is Kind.OFFER_RESPONSE}
    body: list[str] = []

    def add(key: str, who: str, index: int, t: float, text: str) -> None:
        counts[key] += 1
        if who in per and key in per[who]:
            per[who][key] += 1
        body.append(f"{_clock(t)} @{index:<5} {key.upper()[:-1]:<8} {text}")

    for index, t, e in facts:
        p = e.payload
        if e.kind is Kind.OFFER:
            add("offers", e.source, index, t,
                f"{e.source} -> {e.subject or 'household'} [{channel.get(e.seq, 'unrouted')}, "
                f"{answer.get(e.seq, 'unanswered')}] {json.dumps(p.get('text', ''))}")
        elif e.kind is Kind.ACTION_RECEIPT:
            add("actions", p.get("skill", ""), index, t,
                f"{p.get('skill')} {p.get('action')} for {e.subject} [{p.get('outcome')}]")
        elif e.kind is Kind.REFUSAL:
            add("refusals", p.get("who", ""), index, t, f"{p['who']}: {p['attempted']}: {p['reason']}")
        elif e.kind is Kind.CLAIM:
            add("claims", e.source, index, t, f"{e.source} claims {p.get('action')} for {e.subject}")
        elif e.kind is Kind.AUDIT_FINDING:
            add("findings", p.get("who", ""), index, t, f"{p.get('who')}: {p.get('finding')} {p.get('action')}")
    digest = hashlib.sha256(rec.dumps().encode()).hexdigest()[:16]
    head = [f"# zoe-os replay report v{FORMAT}" + (f" ({label})" if label else ""),
            f"# recording {digest}: {len(rec.events)} events, {len(rec.people)} people",
            f"# skills: {', '.join(names)}"]
    tail = ["# totals: " + " ".join(f"{key}={counts[key]}" for key in counts)]
    tail += [f"# {name}: " + " ".join(f"{key}={per[name][key]}" for key in per[name]) for name in names]
    return Report(head + body + tail, counts)


def diff(a: Report, b: Report, *, a_name: str = "current", b_name: str = "changed") -> str:
    """A unified diff of two reports. '' when the two versions behave the same."""
    lines = difflib.unified_diff([x for x in a.lines if not x.startswith("# zoe-os")],
                                 [x for x in b.lines if not x.startswith("# zoe-os")],
                                 a_name, b_name, lineterm="", n=0)
    return "".join(line + "\n" for line in lines)


def _load_skill(spec: str):
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    rec = Recording.load(argv[0])
    split = argv.index("--against") if "--against" in argv else len(argv)
    current = replay(rec, [_load_skill(s) for s in argv[1:split]])
    if split == len(argv):
        sys.stdout.write(current.text)
        return 0
    changed = replay(rec, [_load_skill(s) for s in argv[split + 1:]])
    out = diff(current, changed)
    sys.stdout.write(out or "no difference\n")
    return 1 if out else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
