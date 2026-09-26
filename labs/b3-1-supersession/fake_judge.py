"""A deterministic stand-in for the LLM judge step — NO model.

The controller calls the judge only when it cannot key the attribute on one
side. The prod judge will be the local Gemma brain with a mem0-style prompt
(candidates shown as ``[id] text``, integer ids, JSON out). This fake mimics
the SHAPE of that contract (same signature, same output dict, only ids it was
shown) with a crude entity-overlap heuristic, so the tests exercise the
controller's validation of the judge rather than the judge's intelligence.

Also exports :func:`hallucinating_judge` — a judge that names an id it was not
shown — for the id-validation negative control.
"""
from __future__ import annotations

import re

_RELATION_WORDS = {
    "dad": "father", "father": "father", "mum": "mother", "mother": "mother",
    "dog": "dog", "cat": "cat", "works": "employer", "employer": "employer",
    "job": "employer", "lives": "residence", "home": "residence",
}


def _entities(text: str) -> set[str]:
    """Capitalised tokens minus subjects — the proper nouns a fact asserts."""
    toks = set(re.findall(r"\b[A-Z][a-z]+\b", text))
    return {t.lower() for t in toks} - {"person", "user", "the"}


def _relations(text: str) -> set[str]:
    return {_RELATION_WORDS[t] for t in re.findall(r"[a-z]+", text.lower()) if t in _RELATION_WORDS}


def fake_judge(new_text: str, candidates: list[tuple[int, str]]) -> dict:
    ents_new, rels_new = _entities(new_text), _relations(new_text)
    # candidates are stored lower-cased; recover their entities from the new
    # text's vocabulary (a real judge sees both cased)
    for cid, ctext in candidates:
        rels_c = _relations(ctext)
        if not (rels_new & rels_c):
            continue
        c_tokens = set(re.findall(r"[a-z0-9]+", ctext.lower()))
        if ents_new & c_tokens:
            return {"event": "NONE", "id": cid}
        if ents_new:
            return {"event": "SUPERSEDE", "id": cid}
    return {"event": "ADD", "id": None}


def hallucinating_judge(new_text: str, candidates: list[tuple[int, str]]) -> dict:
    """Names an id the controller never showed it."""
    return {"event": "SUPERSEDE", "id": 10_000}


def crashing_judge(new_text: str, candidates: list[tuple[int, str]]) -> dict:
    raise RuntimeError("brain unavailable")
