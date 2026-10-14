#!/usr/bin/env python3
"""Regenerate Zoe's phrasing regression corpus from Home Assistant + OVOS sources.

Operator-run and offline from CI: the committed output under
``services/zoe-data/tests/fixtures/ha_intents/`` is what the ``ci_safe`` test
(``services/zoe-data/tests/test_ha_phrasing_corpus.py``) reads. Nothing here runs
at test time and hassil is never imported by the test.

Three sources, all pinned to an exact upstream commit (``PINS`` below):

* ``ha_test``   - Home Assistant's labelled English test sentences
  (``OHF-Voice/intents`` ``tests/en/<Intent>/*.yaml``), verbatim. A sentence whose
  slots carry ``conversation_command`` ("turn off the lights in 5 minutes") is
  labelled ``HassStartTimer.delayed_command``; a timer sentence with
  hours/minutes/seconds slots records the expected total ``seconds``.
* ``ha_sample`` - sentences sampled from HA's own hassil templates with HA's own
  tool (``python -m script.intentfest sample``), run inside the pinned checkout by
  a separate interpreter that has ``hassil==3.12.1`` (the version the intents repo
  pins). Wildcard templates are excluded by hassil's default. The first
  ``--sample-pool`` sentences per intent are taken, then ``--sample-per-intent`` of
  them are chosen with a fixed seed so the output is reproducible.
* ``ovos``      - the OVOS weather skill's ``locale/en-US/intents/*.intent``
  template lines (Apache-2.0), expanded by the small expander in this file (the
  ``.intent`` syntax is hassil-like but not hassil). At most ``--ovos-per-line``
  expansions per line, chosen with a fixed seed; required slots get one fixed
  public value (``{location}`` -> Paris, ``{unit}`` -> celsius).

Usage (sources are downloaded from codeload at the pinned commit unless given):

    uv venv /tmp/hassil-venv --python 3.10
    VIRTUAL_ENV=/tmp/hassil-venv uv pip install hassil==3.12.1 PyYAML==6.0.3 \\
        voluptuous==0.16.0 regex jinja2==3.1.6
    python3 scripts/maintenance/ha_intents_corpus.py --hassil-python /tmp/hassil-venv/bin/python

``--ha-src`` / ``--ovos-src`` point at already-extracted trees of the pinned
commits instead (for example ``opensrc path OHF-Voice/intents``); the script
cannot verify a tree's commit, so only pass a tree you know is the pinned one.

Bumping a pin: change ``PINS``, regenerate, then re-baseline the ratchet
(``ZOE_HA_CORPUS_REBASELINE=1`` on the test) in the SAME PR, and say in the PR
body which sentences moved.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "services" / "zoe-data" / "tests" / "fixtures" / "ha_intents"

PINS = {
    "ha": {
        "repo": "OHF-Voice/intents",
        "commit": "c602e0b215d9ae5e7b8e8b88a93027d55e989cab",
        "licence": "CC-BY-4.0",
    },
    "ovos": {
        "repo": "OpenVoiceOS/ovos-skill-weather",
        "commit": "991439b0968408e92f9de9b3176be4ddb18163e9",
        "licence": "Apache-2.0",
    },
}
HASSIL_VERSION = "3.12.1"
SEED = 20261014
OVOS_SLOT_VALUES = {"location": "Paris", "unit": "celsius"}
TIMER_INTENTS = {"HassStartTimer", "HassIncreaseTimer", "HassDecreaseTimer"}


def _download(repo: str, commit: str, dest: Path) -> Path:
    url = f"https://codeload.github.com/{repo}/tar.gz/{commit}"
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - fixed https URL
        data = resp.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            # Untrusted archive: refuse absolute paths, traversal and links.
            name = member.name
            if name.startswith("/") or ".." in Path(name).parts or member.issym() or member.islnk():
                raise SystemExit(f"refusing archive member {name!r} from {url}")
        tar.extractall(dest)  # noqa: S202 - members validated above
    roots = [p for p in dest.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise SystemExit(f"unexpected archive layout from {url}: {roots}")
    return roots[0]


# ── HA labelled test sentences ──────────────────────────────────────────────


def _expected_seconds(slots: dict) -> int | None:
    total, found = 0, False
    for key, mult in (("hours", 3600), ("minutes", 60), ("seconds", 1)):
        val = slots.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            total += int(round(val * mult))
            found = True
    return total if found else None


def ha_test_rows(ha_root: Path) -> list[dict]:
    rows: list[dict] = []
    tests_en = ha_root / "tests" / "en"
    for intent_dir in sorted(p for p in tests_en.iterdir() if p.is_dir()):
        intent = intent_dir.name
        for path in sorted(intent_dir.rglob("*.yaml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for block in doc.get("tests") or []:
                slots = block.get("slots") or {}
                label = intent
                if intent == "HassStartTimer" and "conversation_command" in slots:
                    label = "HassStartTimer.delayed_command"
                secs = _expected_seconds(slots) if intent in TIMER_INTENTS else None
                for sentence in block.get("sentences") or []:
                    row = {"src": "ha_test", "label": label, "text": str(sentence).strip()}
                    if secs is not None:
                        row["seconds"] = secs
                    rows.append(row)
    return rows


# ── HA hassil samples (HA's own sampler, separate interpreter) ──────────────


def ha_sample_rows(ha_root: Path, hassil_python: str, pool: int, per_intent: int, exclude: set[str]) -> list[dict]:
    proc = subprocess.run(  # noqa: S603 - operator-supplied interpreter, fixed argv
        [hassil_python, "-m", "script.intentfest", "sample", "--language", "en", "-n", str(pool)],
        cwd=ha_root, capture_output=True, text=True, check=True,
    )
    by_intent: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        text = " ".join(str(rec["text"]).split())
        if text.lower() in exclude:
            continue
        bucket = by_intent.setdefault(rec["intent"], [])
        if text not in bucket:
            bucket.append(text)
    rng = random.Random(SEED)
    rows: list[dict] = []
    for intent in sorted(by_intent):
        texts = by_intent[intent]
        chosen = texts if len(texts) <= per_intent else rng.sample(texts, per_intent)
        for text in sorted(chosen):
            rows.append({"src": "ha_sample", "label": intent, "text": text})
    return rows


# ── OVOS .intent expander ───────────────────────────────────────────────────
# Grammar: "(a|b|)" alternatives (nestable, empty alternative allowed),
# "[x]" optional, "{slot}" entity. Parsed to a tree of Seq/Alt nodes.


def _parse(template: str):
    pos = 0

    def seq(stop: str):
        nonlocal pos
        items: list = []
        buf = ""
        while pos < len(template):
            ch = template[pos]
            if ch in stop:
                break
            if ch in "([":
                if buf:
                    items.append(buf)
                    buf = ""
                close = ")" if ch == "(" else "]"
                pos += 1
                alts = [seq("|" + close)]
                while pos < len(template) and template[pos] == "|":
                    pos += 1
                    alts.append(seq("|" + close))
                if pos >= len(template) or template[pos] != close:
                    raise ValueError(f"unbalanced {ch!r} in {template!r}")
                pos += 1
                if ch == "[":
                    alts.append(("seq", []))
                items.append(("alt", alts))
                continue
            buf += ch
            pos += 1
        if buf:
            items.append(buf)
        return ("seq", items)

    tree = seq("")
    if pos != len(template):
        raise ValueError(f"unbalanced template {template!r}")
    return tree


def _count(node) -> int:
    if isinstance(node, str):
        return 1
    kind, body = node
    if kind == "seq":
        n = 1
        for item in body:
            n *= _count(item)
        return n
    return sum(_count(alt) for alt in body)


def _enumerate(node):
    if isinstance(node, str):
        yield node
        return
    kind, body = node
    if kind == "alt":
        for alt in body:
            yield from _enumerate(alt)
        return
    if not body:
        yield ""
        return
    head, rest = body[0], ("seq", body[1:])
    for h in _enumerate(head):
        for r in _enumerate(rest):
            yield h + r


def _random(node, rng: random.Random) -> str:
    if isinstance(node, str):
        return node
    kind, body = node
    if kind == "alt":
        return _random(rng.choice(body), rng)
    return "".join(_random(item, rng) for item in body)


def _fill_slots(text: str) -> str | None:
    def sub(m: re.Match) -> str:
        return OVOS_SLOT_VALUES.get(m.group(1), "\0")

    out = re.sub(r"\{(\w+)\}", sub, text)
    if "\0" in out:
        return None
    return " ".join(out.split())


def expand_template(template: str, cap: int, rng: random.Random) -> list[str]:
    tree = _parse(template)
    total = _count(tree)
    raw: list[str] = []
    if total <= cap:
        raw = list(_enumerate(tree))
    else:
        seen: set[str] = set()
        for _ in range(cap * 20):
            s = _random(tree, rng)
            if s not in seen:
                seen.add(s)
                raw.append(s)
            if len(raw) >= cap:
                break
    out: list[str] = []
    for s in raw:
        filled = _fill_slots(s)
        if filled and filled not in out:
            out.append(filled)
    return out


def ovos_rows(ovos_root: Path, per_line: int) -> list[dict]:
    rng = random.Random(SEED)
    rows: list[dict] = []
    seen: set[str] = set()
    for path in sorted((ovos_root / "locale" / "en-US" / "intents").glob("*.intent")):
        label = f"OVOS.{path.stem}"
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for text in expand_template(line, per_line, rng):
                key = text.lower()
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"src": "ovos", "label": label, "text": text})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ha-src", type=Path, help="extracted OHF-Voice/intents tree at the pinned commit")
    ap.add_argument("--ovos-src", type=Path, help="extracted ovos-skill-weather tree at the pinned commit")
    ap.add_argument("--hassil-python", help=f"interpreter with hassil=={HASSIL_VERSION} (for ha_sample rows)")
    ap.add_argument("--no-samples", action="store_true", help="skip ha_sample rows (the committed corpus has them)")
    ap.add_argument("--sample-pool", type=int, default=3000)
    ap.add_argument("--sample-per-intent", type=int, default=30)
    ap.add_argument("--ovos-per-line", type=int, default=3)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)

    if not args.no_samples and not args.hassil_python:
        ap.error("--hassil-python is required (or pass --no-samples, which changes the corpus)")

    with tempfile.TemporaryDirectory(prefix="ha-intents-") as tmp:
        tmp_path = Path(tmp)
        ha_root = args.ha_src or _download(PINS["ha"]["repo"], PINS["ha"]["commit"], tmp_path / "ha")
        ovos_root = args.ovos_src or _download(PINS["ovos"]["repo"], PINS["ovos"]["commit"], tmp_path / "ovos")

        test_rows = ha_test_rows(ha_root)
        sample_rows: list[dict] = []
        if not args.no_samples:
            exclude = {r["text"].lower() for r in test_rows}
            sample_rows = ha_sample_rows(ha_root, args.hassil_python, args.sample_pool, args.sample_per_intent, exclude)
        weather_rows = ovos_rows(ovos_root, args.ovos_per_line)

    rows = test_rows + sample_rows + weather_rows
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "en.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    sources = {
        "pins": PINS,
        "hassil": HASSIL_VERSION,
        "seed": SEED,
        "generator": "scripts/maintenance/ha_intents_corpus.py",
        "params": {
            "sample_pool": args.sample_pool,
            "sample_per_intent": 0 if args.no_samples else args.sample_per_intent,
            "ovos_per_line": args.ovos_per_line,
            "ovos_slot_values": OVOS_SLOT_VALUES,
        },
        "counts": {"ha_test": len(test_rows), "ha_sample": len(sample_rows), "ovos": len(weather_rows),
                   "total": len(rows)},
    }
    (args.out / "sources.json").write_text(json.dumps(sources, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(sources["counts"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
