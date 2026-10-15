"""The STT-snap corpus evaluation, shared by ``test_stt_snap.py`` and the report CLI below.

Synthetic mishears of the HA/OVOS phrasing corpus (``fixtures/ha_intents``): ONE of the first three words of a corpus
sentence is swapped for a sound-alike a speech recogniser plausibly hears instead (``SOUND_ALIKES``, an independent
hand table of STT confusions - NOT the snap's own vocabulary or cost, so the snap is not graded on its own rules).
The choice of word and sound-alike is random with a FIXED seed, so the set is the same on every run.

Each mishear is scored against the source sentence's label in ``fixtures/ha_intents/mapping.yaml``:

* still_claimed  the misheard words are claimed by a matcher anyway (the snap never runs)
* abstain        unclaimed, and the snap declined
* recovered      the snap's command is one the label accepts (a timer also carries the right length)
* wrong          the snap's command is not one the label accepts
* wrong_write    a wrong snap that writes or actuates (``mapping.yaml`` ``writes``) - THE gate: must stay 0

    python3 services/zoe-data/tests/stt_snap_eval.py            # print the report
"""

from __future__ import annotations

import logging
import random
import re
import sys
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import stt_snap  # noqa: E402
import test_ha_phrasing_corpus as corpus  # noqa: E402

SEED = 20261014
#: mishears drawn per corpus sentence (each a different word/sound-alike where the sentence offers one)
PER_SENTENCE = 2

#: Plausible STT confusions for words that open Zoe's commands. Hand-made from how a recogniser mishears speech
#: (homophones, a dropped / added final consonant, a non-rhotic vowel), independent of ``stt_snap``'s vocabulary.
SOUND_ALIKES: dict[str, tuple[str, ...]] = {
    "turn": ("two", "to", "tern", "torn", "ten"),
    "off": ("of",),
    "set": ("sit", "said", "sat"),
    "play": ("pray", "plate", "played"),
    "pause": ("paws", "pours", "pose"),
    "add": ("had", "ad", "at"),
    "timer": ("time", "timber", "tiger"),
    "timers": ("times", "tigers"),
    "cancel": ("council", "cancer", "counsel"),
    "what's": ("watts", "what", "was"),
    "time": ("thyme", "tine"),
    "weather": ("whether", "wetter", "feather"),
    "remove": ("remote", "removed"),
    "start": ("star", "stat", "started"),
    "resume": ("presume", "assume", "resumed"),
    "is": ("his", "as"),
    "lights": ("lice", "lites", "likes"),
    "light": ("lite", "like", "lie"),
    "next": ("necks", "nest"),
    "skip": ("ship", "skips", "kip"),
    "volume": ("volumes", "valium"),
    "music": ("musics", "magic"),
    "tell": ("tail", "tall"),
    "rain": ("reign", "rein", "rainy"),
    "kitchen": ("kitten", "chicken"),
    "hour": ("our",),
    "previous": ("previews",),
    "check": ("cheque", "chick"),
    "show": ("so", "shore"),
    "increase": ("increased",),
    "decrease": ("decreased", "the crease"),
    "will": ("well", "wheel"),
    "how": ("who",),
    "stop": ("stock", "stab", "top"),
    "continue": ("continued",),
    "give": ("gave", "gift"),
    "put": ("but", "putt"),
    "take": ("tape", "cake"),
}

#: Real sentences that merely START like a command word (or mention one) - never commands. Invented phrasings; the
#: snap must decline every one (abstain), whatever the matchers do with them.
HARD_NEGATIVES: tuple[str, ...] = (
    "two people are coming",
    "to be honest",
    "two of my friends are here",
    "to be fair it was raining",
    "too late now",
    "two days ago",
    "to the shops and back",
    "two coffees please",
    "turn around",
    "time flies",
    "set the table",
    "said the man",
    "play nice",
    "had a good day",
    "at the moment",
    "two more weeks",
    "to me that sounds fine",
    "to do list is long",
    "ten minutes ago",
    "our dog is outside",
    "his name is Tom",
    "wetter than yesterday",
    "whether or not",
    "council meeting tonight",
    "cancer research",
    "remote is lost",
    "star wars",
    "kitten is asleep",
    "chicken for dinner",
    "magic tricks",
    "the crease in my shirt",
    "lice in the school",
    "torn jeans",
    "pray for rain",
    "tell me something",
    "what's up",
    "who are you",
    "watts in a kilowatt",
    "ship it",
    "necks are sore",
)


@dataclass
class Report:
    counts: Counter = field(default_factory=Counter)
    by_safety: Counter = field(default_factory=Counter)
    examples: dict[str, list[str]] = field(default_factory=dict)

    def add(self, metric: str, example: str | None = None) -> None:
        self.counts[metric] += 1
        if example is not None:
            bucket = self.examples.setdefault(metric, [])
            if len(bucket) < 8:
                bucket.append(example)


@contextmanager
def quiet():
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


def _tokens(core: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"[a-z0-9']+", core)]


def mishears(rows: list[dict], *, seed: int = SEED, per_sentence: int = PER_SENTENCE) -> list[dict]:
    """Deterministic synthetic mishears: ``{"text", "label", "src_text", "seconds", "swap"}`` per draw."""
    from command_shapes import command_core

    rng = random.Random(seed)
    out: list[dict] = []
    for row in rows:
        core = command_core(row["text"])
        options = [(s, e, w, alt) for (s, e, w) in _tokens(core)[:stt_snap.POSITIONS]
                   for alt in SOUND_ALIKES.get(w, ())]
        if not options:
            continue
        for s, e, w, alt in rng.sample(options, min(per_sentence, len(options))):
            out.append({"text": core[:s] + alt + core[e:], "label": row["label"], "src_text": row["text"],
                        "seconds": row.get("seconds"), "swap": f"{w}->{alt}"})
    return out


def correct_commands(rows: list[dict] | None = None, mapping: dict | None = None) -> list[str]:
    """The corpus sentences the voice cascade claims CORRECTLY: the phrasings Zoe's matchers actually handle."""
    rows = corpus.load_rows() if rows is None else rows
    mapping = corpus.load_mapping() if mapping is None else mapping
    out = []
    with quiet():
        for row in rows:
            claim = stt_snap.claim_of(row["text"])
            if claim is not None and _accepts(mapping, row["label"], row["text"], claim, row.get("seconds")):
                out.append(row["text"])
    return out


def derived_vocabulary_json() -> str:
    """The exact content ``lexicons_data/stt_snap_vocab.json`` must hold (regenerated, drift-pinned by the test)."""
    import json

    data = {"_generated_by": "services/zoe-data/tests/stt_snap_eval.py --write-vocab",
            "_source": "HA/OVOS phrasing corpus sentences the voice cascade claims correctly + command_shapes verbs",
            **stt_snap.derive_vocabulary(correct_commands())}
    # one key per line (a generated file: compact, but a regeneration still diffs key by key)
    body = ",\n".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in sorted(data.items()))
    return "{\n" + body + "\n}\n"


def _accepts(mapping: dict, label: str, text: str, claim: stt_snap.Claim, seconds) -> bool:
    spec = mapping["labels"][(mapping.get("relabel") or {}).get(text, label)]
    ok = corpus._matches(claim.tag, spec.get("accept") or [])
    if ok and spec.get("check_seconds") and seconds is not None:
        ok = claim.seconds == seconds
    return ok


def evaluate(rows: list[dict] | None = None, mapping: dict | None = None) -> Report:
    """Score the snap on every synthetic mishear. A wrong snap that lands EXACTLY on the claim the matchers already make
    for the correctly-heard sentence is ``*_inherited``: the snap restored the words, and the matcher's own wrong claim is
    the phrasing corpus' ratchet to own (``test_ha_phrasing_corpus.py``), not the snap's."""
    rows = corpus.load_rows() if rows is None else rows
    mapping = corpus.load_mapping() if mapping is None else mapping
    rep = Report()
    cache: dict[str, stt_snap.Claim | None] = {}

    def claim(text: str, context=None):
        if context is not None:
            return stt_snap.claim_of(text, context)
        if text not in cache:
            cache[text] = stt_snap.claim_of(text)
        return cache[text]

    with quiet():
        for m in mishears(rows):
            rep.add("n")
            clean = claim(m["src_text"])
            if clean is not None and _accepts(mapping, m["label"], m["src_text"], clean, m["seconds"]):
                rep.add("clean_correct")
            if claim(m["text"]) is not None:
                rep.add("still_claimed")
                continue
            if clean is not None and _accepts(mapping, m["label"], m["src_text"], clean, m["seconds"]):
                rep.add("recoverable")
            d = stt_snap.decide(m["text"], claim_fn=claim)
            if d.snap is None:
                rep.add("abstain")
                rep.add(f"abstain:{d.reason}")
                continue
            shown = f"{m['text']!r} -> {d.snap.text!r} ({d.snap.intent}, {d.snap.safety}) [{m['label']}]"
            rep.by_safety[d.snap.safety] += 1
            if _accepts(mapping, m["label"], m["src_text"], d.snap.claim, m["seconds"]):
                rep.add("recovered")
                rep.add(f"recovered:{d.snap.safety}")
                continue
            inherited = clean is not None and clean.tag == d.snap.intent
            suffix = "_inherited" if inherited else ""
            rep.add("wrong" + suffix, shown)
            if corpus._matches(d.snap.intent, mapping["writes"]):
                rep.add("wrong_write" + suffix, shown)
                rep.add(f"wrong_write{suffix}:{d.snap.safety}", shown)
        for text in HARD_NEGATIVES:
            d = stt_snap.decide(text, claim_fn=claim)
            rep.add("hard_negative")
            if d.snap is not None:
                rep.add("hard_negative_snapped", f"{text!r} -> {d.snap.text!r} ({d.snap.intent})")
    return rep


def render(rep: Report) -> str:
    c = rep.counts
    unclaimed = c["n"] - c["still_claimed"]
    lines = [
        f"STT snap over synthetic corpus mishears (seed {SEED}, {PER_SENTENCE}/sentence): n={c['n']}"
        f" still_claimed={c['still_claimed']} unclaimed={unclaimed} recoverable={c['recoverable']}"
        " (unclaimed, and the matchers get the correctly-heard sentence right)",
        f"  recovered={c['recovered']} ({100 * c['recovered'] / max(1, c['recoverable']):.1f}% of recoverable)"
        f" [read={c['recovered:read']} act={c['recovered:act']} confirm={c['recovered:confirm']}]",
        f"  wrong={c['wrong']} wrong_write={c['wrong_write']}"
        f" [act={c['wrong_write:act']} confirm={c['wrong_write:confirm']}] abstain={c['abstain']}",
        f"  inherited from the matchers' own claim on the clean sentence: wrong={c['wrong_inherited']}"
        f" wrong_write={c['wrong_write_inherited']} [act={c['wrong_write_inherited:act']}"
        f" confirm={c['wrong_write_inherited:confirm']}]",
        "  abstain reasons: " + ", ".join(f"{k.split(':', 1)[1]}={v}" for k, v in sorted(c.items())
                                         if k.startswith("abstain:")),
        f"  hard negatives: {c['hard_negative']} checked, {c['hard_negative_snapped']} snapped",
    ]
    for metric in ("wrong_write", "wrong", "hard_negative_snapped", "wrong_write_inherited"):
        for ex in rep.examples.get(metric, []):
            lines.append(f"    {metric}: {ex}")
    return "\n".join(lines)


if __name__ == "__main__":
    if "--write-vocab" in sys.argv:
        stt_snap.VOCAB_FILE.write_text(derived_vocabulary_json(), encoding="utf-8")
        stt_snap.vocabulary.cache_clear()
        print(f"wrote {stt_snap.VOCAB_FILE}")
    print(render(evaluate()))
