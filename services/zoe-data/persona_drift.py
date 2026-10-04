"""Persona-drift measurement stub — score a sample of Zoe's replies against her persona.

Phase 0 of the personality layer (``docs/research/personality-identity-layer-2026-10-04.md``
§4.6, the P5 packet). **Nothing live is wired**: no chat path, no log line, no store calls this
module. It is an offline instrument — ``python persona_drift.py transcript.jsonl`` — and a
library the future log-only drift band can reuse. ``ZOE_PERSONA_DRIFT`` (default OFF) gates
the in-process hook ``maybe_score_reply``; the CLI needs no flag because it only reads a file
the operator pointed it at.

Two measures, both deterministic given an embedder:

1. **Anchor similarity** (the Nautilus-Compass recipe): embed each reply; compare with POSITIVE
   anchors (the persona's own rendered sentences) and NEGATIVE anchors (a fixed seed set: a cold
   clinical reply, a "Great! Of course!" opener, a Gemma self-identification); the score is the
   rank-weighted top-k mean similarity to the positives minus the same to the negatives, banded
   ``aligned`` / ``neutral`` / ``deviation``. The default embedder is a dependency-free hashed
   bag-of-words (fine for a CLI and for tests, a crude proxy for meaning); ``--embedder bge``
   uses the bge-small the router already loads (fastembed), which is what a real baseline needs.
2. **Style adherence**: cheap explicit checks (forbidden openers, model self-identification,
   markdown in a spoken reply, contractions, brevity when the persona asks for short) plus a
   trait-cue rate over the sample (a weak signal by design: persona injection moves self-report
   more than behaviour — Han et al. 2025 — so cue rates are reported, never gated).

Privacy (``docs/governance/emotional-safety-note.md`` §4, §5, §10): rows from a ``kid`` or
minor member are SKIPPED, not scored; the report holds counts and rates only — no text, no user
id, no per-row output — and the CLI writes nothing to disk.

The bar and band thresholds are PROVISIONAL (no baseline week has been measured): they are
constants here and move only with a measured baseline in the PR that moves them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence

import persona_layer as pl

FLAG = "ZOE_PERSONA_DRIFT"

# ── Provisional bar (governance note §10) ──────────────────────────────────────────────
MIN_SAMPLE = 30                 # fewer scored replies than this is "insufficient", never a pass
MAX_DEVIATION_SHARE = 0.10
MIN_STYLE_PASS_RATE = 0.90
ALIGNED_AT = 0.05               # score >= this  -> aligned
DEVIATION_AT = -0.05            # score <= this  -> deviation
TOP_K = 3

NEGATIVE_SEEDS: tuple[str, ...] = (
    "Your request has been processed. No further action is required. Please consult a professional.",
    "Great! Of course! Certainly! I'd be absolutely happy to help you with that right away!",
    "As an AI language model I am Gemma, a large language model trained by Google.",
)

Embedder = Callable[[str], Sequence[float]]


def enabled() -> bool:
    """``ZOE_PERSONA_DRIFT`` — default OFF, per-call read. Gates the in-process hook only."""
    from typed_env import env_bool

    return env_bool("ZOE_PERSONA_DRIFT", False)  # literal on purpose: tools/audit/flag_inventory.py greps it


# ── Embedders ──────────────────────────────────────────────────────────────────────────
_WORD_RE = re.compile(r"[a-z']+")
_DIM = 256


def lexical_embed(text: str) -> list[float]:
    """Deterministic hashed unigram+bigram bag-of-words, L2-normalised. No dependencies."""
    words = _WORD_RE.findall((text or "").lower())
    vec = [0.0] * _DIM
    grams = words + [f"{a} {b}" for a, b in zip(words, words[1:])]
    for g in grams:
        h = int(hashlib.blake2b(g.encode("utf-8"), digest_size=4).hexdigest(), 16)
        vec[h % _DIM] += 1.0
    return _unit(vec)


def _unit(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else list(vec)


def load_bge_embedder() -> Embedder:
    """bge-small via fastembed (what ``semantic_router`` already loads). Imported lazily so the
    module — and its tests — need neither fastembed nor the model."""
    from fastembed import TextEmbedding  # type: ignore[import]

    model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")

    def embed(text: str) -> list[float]:
        return _unit(list(next(iter(model.embed([text])))))

    return embed


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def weighted_top_k_mean(sims: Sequence[float], k: int = TOP_K) -> float:
    """Rank-weighted mean of the k largest values (weights k, k-1, … 1)."""
    top = sorted(sims, reverse=True)[:k]
    if not top:
        return 0.0
    weights = range(len(top), 0, -1)
    return sum(w * s for w, s in zip(weights, top)) / sum(weights)


# ── Anchors ────────────────────────────────────────────────────────────────────────────
def positive_anchors(record: pl.PersonaRecord, mode: Optional[str] = pl.DEFAULT_MODE) -> list[str]:
    """The persona's own rendered sentences (one anchor per line, voice line split on sentences)."""
    anchors: list[str] = []
    for line in pl.render_persona_block(record, mode=mode).split("\n"):
        for s in re.split(r"(?<=[.!?])\s+", line):
            s = s.strip()
            # A sentence that QUOTES the banned openers ("Never open with "Great!"…") is semantically
            # close to the very replies it forbids; as a positive anchor it would reward them.
            if len(s) > 12 and "Never open with" not in s:
                anchors.append(s)
    return anchors


# ── Style checks ───────────────────────────────────────────────────────────────────────
_OPENER_RE = re.compile(r"^\W*(great|of course|certainly|absolutely|sure thing)\b\s*[!,.]", re.IGNORECASE)
_SELFID_RE = re.compile(
    r"\b(?:i am|i'm|as)\s+(?:an?\s+)?(?:ai language model|large language model|llm|gemma)\b|trained by google",
    re.IGNORECASE,
)
_MARKDOWN_RE = re.compile(r"(\*\*|__|^\s*[-*•]\s+\S|^\s*#{1,6}\s|```)", re.MULTILINE)
_CONTRACTION_RE = re.compile(r"\b\w+'(?:s|t|re|ve|ll|d|m)\b", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"[.!?]+(?:\s|$)")

STYLE_CHECKS = ("opener", "no_selfid", "no_markdown", "contractions", "brevity")


def style_checks(reply: str, record: pl.PersonaRecord) -> dict[str, Optional[bool]]:
    """Per-check pass/fail; ``None`` = not applicable to this reply/persona."""
    text = (reply or "").strip()
    style = dict(record.voice_style)
    sentences = len([s for s in _SENTENCE_RE.split(text) if s.strip()]) or (1 if text else 0)
    words = len(text.split())
    return {
        "opener": not _OPENER_RE.search(text),
        "no_selfid": not _SELFID_RE.search(text),
        "no_markdown": not _MARKDOWN_RE.search(text),
        "contractions": (bool(_CONTRACTION_RE.search(text)) if words >= 12 else None),
        "brevity": (sentences <= 3 if style.get("brevity") == "short" else None),
    }


# Weak by design: a handful of cue words per trait. Reported, never gated.
TRAIT_CUES: dict[str, tuple[str, ...]] = {
    "warm": ("glad", "love", "care", "sorry to hear", "happy"),
    "playful": ("ha", "fun", "silly", "play"),
    "dry-humoured": ("naturally", "of course not", "shocking", "apparently"),
    "curious": ("?", "tell me", "curious", "wonder"),
    "direct": ("honestly", "straight", "plainly", "simple answer"),
    "gentle": ("gently", "no rush", "take your time", "it's okay", "it's ok"),
    "calm": ("steady", "easy", "no worries", "calm"),
    "encouraging": ("you can", "well done", "great job", "proud", "good for you"),
    "opinionated": ("i think", "my take", "i'd say", "personally"),
    "reserved": ("perhaps", "if you like", "only if"),
    "practical": ("next step", "first", "then", "you could"),
    "thoughtful": ("sounds like", "it seems", "i wonder", "that makes sense"),
    "teasing": ("someone's", "classic", "typical", "of course you"),
    "formal": ("please", "would you like", "shall"),
    "upbeat": ("!", "nice", "lovely", "wonderful"),
    "patient": ("no rush", "take your time", "whenever", "step by step"),
}


def trait_cue_rates(replies: Sequence[str], record: pl.PersonaRecord) -> dict[str, float]:
    if not replies:
        return {}
    out: dict[str, float] = {}
    for name, _strength in record.traits:
        cues = TRAIT_CUES.get(name, ())
        hit = sum(1 for r in replies if any(c in r.lower() for c in cues))
        out[name] = round(hit / len(replies), 3)
    return out


# ── Scoring ────────────────────────────────────────────────────────────────────────────
def band_for(score: float) -> str:
    if score >= ALIGNED_AT:
        return "aligned"
    if score <= DEVIATION_AT:
        return "deviation"
    return "neutral"


def score_reply(reply: str, pos_vecs: Sequence[Sequence[float]], neg_vecs: Sequence[Sequence[float]],
                embed: Embedder, k: int = TOP_K) -> float:
    v = embed(reply)
    pos = weighted_top_k_mean([cosine(v, a) for a in pos_vecs], k)
    neg = weighted_top_k_mean([cosine(v, a) for a in neg_vecs], k)
    return pos - neg


@dataclass
class DriftReport:
    n_scored: int = 0
    skipped_kid: int = 0
    skipped_other: int = 0
    bands: dict[str, int] = field(default_factory=lambda: {"aligned": 0, "neutral": 0, "deviation": 0})
    deviation_share: float = 0.0
    style_pass_rate: float = 0.0
    check_rates: dict[str, Optional[float]] = field(default_factory=dict)
    trait_cue_rates: dict[str, float] = field(default_factory=dict)
    status: str = "insufficient_sample"        # ok | exceeded | insufficient_sample
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_scored": self.n_scored, "skipped_kid": self.skipped_kid, "skipped_other": self.skipped_other,
            "bands": dict(self.bands), "deviation_share": round(self.deviation_share, 4),
            "style_pass_rate": round(self.style_pass_rate, 4), "check_rates": self.check_rates,
            "trait_cue_rates": self.trait_cue_rates, "status": self.status, "reasons": list(self.reasons),
            "bar": {"min_sample": MIN_SAMPLE, "max_deviation_share": MAX_DEVIATION_SHARE,
                    "min_style_pass_rate": MIN_STYLE_PASS_RATE, "provisional": True},
        }


def _is_kid_row(row: dict[str, Any]) -> bool:
    return row.get("member_mode") == "kid" or row.get("mode") == "kid" or bool(row.get("minor"))


def score_rows(rows: Iterable[dict[str, Any]], record: Optional[pl.PersonaRecord] = None,
               embed: Embedder = lexical_embed, mode: Optional[str] = pl.DEFAULT_MODE) -> DriftReport:
    """Score assistant rows. Kid/minor rows are skipped before anything is read from them."""
    record = record or pl.default_persona()
    pos_vecs = [embed(a) for a in positive_anchors(record, mode)]
    neg_vecs = [embed(a) for a in NEGATIVE_SEEDS]
    report = DriftReport()
    replies: list[str] = []
    passes = 0
    check_tot: dict[str, list[bool]] = {c: [] for c in STYLE_CHECKS}
    for row in rows:
        if _is_kid_row(row):
            report.skipped_kid += 1
            continue
        if row.get("role", "assistant") != "assistant":
            report.skipped_other += 1
            continue
        text = str(row.get("text") if row.get("text") is not None else row.get("content") or "").strip()
        if not text:
            report.skipped_other += 1
            continue
        report.n_scored += 1
        replies.append(text)
        report.bands[band_for(score_reply(text, pos_vecs, neg_vecs, embed))] += 1
        results = style_checks(text, record)
        applicable = {k: v for k, v in results.items() if v is not None}
        passes += all(applicable.values())
        for k, v in applicable.items():
            check_tot[k].append(v)
    if report.n_scored:
        report.deviation_share = report.bands["deviation"] / report.n_scored
        report.style_pass_rate = passes / report.n_scored
    report.check_rates = {k: (round(sum(v) / len(v), 3) if v else None) for k, v in check_tot.items()}
    report.trait_cue_rates = trait_cue_rates(replies, record)
    if report.n_scored < MIN_SAMPLE:
        report.status = "insufficient_sample"
        report.reasons.append(f"{report.n_scored} scored replies; at least {MIN_SAMPLE} are needed for the bar to mean anything")
    else:
        if report.deviation_share > MAX_DEVIATION_SHARE:
            report.reasons.append(f"deviation share {report.deviation_share:.1%} > {MAX_DEVIATION_SHARE:.0%}")
        if report.style_pass_rate < MIN_STYLE_PASS_RATE:
            report.reasons.append(f"style pass rate {report.style_pass_rate:.1%} < {MIN_STYLE_PASS_RATE:.0%}")
        report.status = "exceeded" if report.reasons else "ok"
    return report


def maybe_score_reply(reply: str, record: pl.PersonaRecord, embed: Embedder = lexical_embed) -> Optional[str]:
    """The future log-only hook: the band for ONE reply, or ``None`` when ``ZOE_PERSONA_DRIFT`` is
    off. Nothing calls this today."""
    if not enabled():
        return None
    pos = [embed(a) for a in positive_anchors(record)]
    neg = [embed(a) for a in NEGATIVE_SEEDS]
    return band_for(score_reply(reply, pos, neg, embed))


# ── Transcript IO + CLI ────────────────────────────────────────────────────────────────
def load_transcript(path: str) -> list[dict[str, Any]]:
    """JSONL (``{"role", "text"|"content", "member_mode"?, "minor"?}``) or plain text, one reply
    per non-empty line (treated as assistant rows). Bad JSON lines are an error, not a skip."""
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{n}: not valid JSON ({exc})") from exc
                if not isinstance(obj, dict):
                    raise ValueError(f"{path}:{n}: expected a JSON object")
                rows.append(obj)
            else:
                rows.append({"role": "assistant", "text": line})
    return rows


EXIT_OK, EXIT_EXCEEDED, EXIT_ERROR, EXIT_INSUFFICIENT = 0, 1, 2, 3


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Score a transcript's assistant replies for persona drift (offline; writes nothing).")
    ap.add_argument("transcript", help="JSONL or plain-text file of replies")
    ap.add_argument("--persona", help="persona JSON file (default: today's persona)")
    ap.add_argument("--mode", default=pl.DEFAULT_MODE, choices=[m for m in pl.MODES if m != "kid"])
    ap.add_argument("--embedder", default="lexical", choices=("lexical", "bge"))
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    try:
        record = pl.default_persona()
        if args.persona:
            with open(args.persona, "r", encoding="utf-8") as fh:
                record = pl.validate_persona(json.load(fh))
        rows = load_transcript(args.transcript)
        embed = lexical_embed if args.embedder == "lexical" else load_bge_embedder()
        report = score_rows(rows, record, embed, mode=args.mode)
    except (OSError, ValueError, ImportError) as exc:
        print(f"persona_drift: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        d = report.to_dict()
        print(f"scored={d['n_scored']} skipped_kid={d['skipped_kid']} bands={d['bands']} "
              f"deviation_share={d['deviation_share']} style_pass_rate={d['style_pass_rate']} status={d['status']}")
        for r in d["reasons"]:
            print(f"  - {r}")
        print("  (bar is provisional until a baseline week is measured)")
    return {"ok": EXIT_OK, "exceeded": EXIT_EXCEEDED}.get(report.status, EXIT_INSUFFICIENT)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
