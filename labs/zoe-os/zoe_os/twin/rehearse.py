"""Rehearsal: run a changed (or new) skill against generated and recorded days BEFORE it is
trusted, and say what it would do differently, with any privacy failure first.

    result = rehearse(current=[LeavingCatch], changed=[MyLeavingCatch], generated=days)
    print(result.text)          # result.ok is False when the change breaks a privacy rule

Skills are given as CLASSES (or any no-argument callable): every day gets a fresh instance of
each version, so nothing a skill remembers leaks from one day into the next.

The report has three parts, in this order:
1. PRIVACY. Problems the changed version has that the current one does not. Four kinds:
   REFUSED  the skill tried something the kernel refused (read or act without a grant or a
            yes, more than its manifest, forgotten words...). The kernel held; the skill is
            still wrong.
   CROSSED  it made an offer or took an action for one person that derives from ANOTHER
            person's private event. The kernel allows this when both enabled the skill, which
            is exactly why a rehearsal has to look.
   LEAKED   an offer to one person contains words that are in other people's private events
            and not in their own.
   UNBACKED it claimed something was done with no verified receipt.
2. SUMMARY. Offers, actions, refusals, claims and findings for each version, and the change.
3. DIFFERENCES. Day by day, every line that only one version produced.

Deterministic: the same days and skills give the same text.
"""
from __future__ import annotations

import difflib
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..capability import Manifest
from ..events import Kind, Visibility
from ..kernel import Kernel
from ..replay import Recording, Report, build_report, load, play
from ..spine import _content, _strings

FORMAT = 1
COUNTS = ("offers", "actions", "refusals", "claims", "findings")
MIN_LEAK = 4        # shortest string worth calling a leak


@dataclass
class Rehearsal:
    text: str
    privacy: list[str]              # problems only the changed version has
    counts: dict[str, tuple[int, int]]      # name -> (current, changed)
    differing_days: int

    @property
    def ok(self) -> bool:
        return not self.privacy


def run_day(rec: Recording, skills: list) -> tuple[Report, list[str]]:
    """One day against one set of skill instances: the replay report and the privacy problems."""
    now = [rec.events[0]["t"] if rec.events else 0.0]
    with tempfile.TemporaryDirectory(prefix="zoe-os-rehearse-") as tmp:
        k = Kernel(str(Path(tmp) / "day.db"), rec.people, clock=lambda: now[0])
        try:
            load(k, rec, skills)
            facts = play(k, rec, now)
            return build_report(rec, skills, facts, ""), _privacy(k, facts, skills)
        finally:
            k.close()


def _privacy(k: Kernel, facts: list, skills: list) -> list[str]:
    names = {Manifest.from_dict(s.manifest).skill for s in skills}
    words: dict[str, set[str]] = {}             # person -> strings in their private first-hand events
    shared: set[str] = set()
    for _, _, e in facts:
        if k.enforcer.is_origin(e.source):
            found = {s for s in _content(e.payload) if len(s) >= MIN_LEAK}
            if e.visibility is Visibility.PRIVATE and e.subject:
                words.setdefault(e.subject, set()).update(found)
            else:
                shared |= found
    out: list[str] = []
    for index, t, e in facts:
        stamp, p = f"{_hms(t)} @{index:<5}", e.payload
        if e.kind is Kind.REFUSAL and p.get("who") in names:
            out.append(f"{stamp} REFUSED  {p['who']} tried to {p['attempted']}: {p['reason']}")
        elif e.kind is Kind.AUDIT_FINDING and p.get("who") in names and p.get("finding") == "claim_without_receipt":
            out.append(f"{stamp} UNBACKED {p['who']} claimed {p.get('action')} with no verified receipt")
        elif e.kind in (Kind.OFFER, Kind.ACTION_RECEIPT) and e.subject and \
                (e.source in names or p.get("skill") in names):
            who = e.source if e.kind is Kind.OFFER else p.get("skill")
            did = "made an offer to" if e.kind is Kind.OFFER else f"did {p.get('action')} for"
            for other in sorted(_others(k, e)):
                out.append(f"{stamp} CROSSED  {who} {did} {e.subject} from {other}'s private event")
            if e.kind is Kind.OFFER:
                mine = words.get(e.subject, set()) | shared
                text = " ".join(_strings(p))
                whose = [other for other, theirs in sorted(words.items())
                         if other != e.subject and any(w in text for w in theirs - mine)]
                if whose:       # which of them it came from cannot be told from the words alone
                    out.append(f"{stamp} LEAKED   {who} showed {e.subject} words that are in the private "
                               f"events of {', '.join(whose)} and not in their own")
    return out


def _others(k: Kernel, ev) -> set[str]:
    """The people, other than the event's own subject, whose PRIVATE events it derives from."""
    seen, frontier, others = {ev.seq}, list(ev.caused_by), set()
    while frontier:
        seq = frontier.pop()
        if seq in seen:
            continue
        seen.add(seq)
        cause = k.spine._get(seq)
        if cause is None:
            continue
        if cause.visibility is Visibility.PRIVATE and cause.subject and cause.subject != ev.subject:
            others.add(cause.subject)
        frontier += cause.caused_by
    return others


def _hms(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M:%S")


def _label(rec: Recording, n: int) -> str:
    m = rec.meta
    if not m.get("day"):
        return f"recorded day {n}"
    return f"{m['day']} {m.get('weekday', '')}".strip() + (f" [{m['awkward']}]" if m.get("awkward") else "")


def _body(report: Report) -> list[str]:
    return [line[11:] for line in report.lines if not line.startswith("#")]      # drop the date


def rehearse(current: list, changed: list, *, generated: list[Recording] = (),
             recorded: list[Recording] = ()) -> Rehearsal:
    days = [(rec, _label(rec, n)) for n, rec in enumerate([*generated, *recorded], start=1)]
    totals = {name: [0, 0] for name in COUNTS}
    new_privacy: list[str] = []
    old_privacy = 0
    sections: list[str] = []
    for rec, label in days:
        (a, a_priv), (b, b_priv) = (run_day(rec, [make() for make in version]) for version in (current, changed))
        for name in COUNTS:
            totals[name][0] += a.counts[name]
            totals[name][1] += b.counts[name]
        old_privacy += len(a_priv)
        new_privacy += [f"{label} {line}" for line in b_priv if line not in a_priv]
        delta = [line for line in difflib.unified_diff(_body(a), _body(b), lineterm="", n=0)
                 if line[:1] in "+-" and line[:3] not in ("+++", "---")]
        if delta:
            sections += [f"== {label}"] + [f"{line[0]} {line[1:]}" for line in delta]

    def names(version) -> str:
        return ", ".join(sorted(Manifest.from_dict(make.manifest).skill for make in version)) or "(nothing)"

    seeds = sorted({str(rec.meta["seed"]) for rec in generated if "seed" in rec.meta})
    out = [f"# zoe-os rehearsal v{FORMAT}",
           f"# current: {names(current)} | changed: {names(changed)}",
           f"# days: {len(generated)} generated" + (f" (seed {', '.join(seeds)})" if seeds else "")
           + f", {len(recorded)} recorded"]
    if new_privacy:
        out.append(f"PRIVACY: {len(new_privacy)} new problem{'s' if len(new_privacy) > 1 else ''} in the "
                   f"changed version ({old_privacy} already in the current one). DO NOT TRUST THIS CHANGE.")
        out += [f"  {line}" for line in new_privacy]
    else:
        out.append(f"PRIVACY: no new problem in the changed version ({old_privacy} already in the current one).")
    out.append(f"SUMMARY  {'':<10}{'current':>9}{'changed':>9}{'change':>9}")
    out += [f"  {name:<17}{a:>9}{b:>9}{b - a:>+9}" for name, (a, b) in totals.items()]
    out.append(f"DIFFERENCES: {sum(1 for s in sections if s.startswith('== '))} of {len(days)} days differ")
    out += sections
    return Rehearsal("\n".join(out) + "\n", new_privacy, {k: tuple(v) for k, v in totals.items()},
                     sum(1 for s in sections if s.startswith("== ")))
