"""The semantic half of the restraint classifier: six sigmoid heads on the bge-small embedding the router already loads.

Why this exists (docs/knowledge/restraint.md "Measurement"): the per-language word lists reached 63 % class-exact on a blind
English set because a paraphrase they were never shown ("I still can't believe she's gone", "twisted their ankle") is missed,
and no hand-widened list closes that. The repo's own answer to "a word list leaks a new phrasing every round" is a small
head on the embedding it already computes (``router_heads_numpy``), so this is that pattern: ``models/restraint_head.npz``
holds one logistic unit per class (6 x 384 weights, ~10 KB), trained by ``scripts/maintenance/train_restraint_head.py`` on
the dev split only and applied here as a plain numpy matrix product.

Rules this module keeps:

* **It can only ADD a class.** ``restraint.classify_full`` unions the head with the word list and the structured signals
  (the blueprint's union rule); a head that says nothing leaves the lexicon's answer untouched. A class is still only ever
  WITHHELD on, never delivered, so a wrong head costs a withheld row (shadow's behaviour), never a leak.
* **It never loads a model.** The embedding comes from ``semantic_router`` only when that module is already imported AND its
  model is already in memory (the live zoe-data process loads it at the first routed turn). Otherwise the head is inert and
  the classifier is the word list alone, byte for byte as before. Extra RAM in the service: the 10 KB matrix.
* **Hot paths read a cache, never embed.** ``classes(text, embed=False)`` answers only from the in-process cache; the write
  path (``restraint.stamp``) and the nightly thread pass call it with ``embed=True`` (one ~7 ms embedding off the voice path).
* **English-trained.** The embedding model is bge-small-en: another language's text falls back on its lexicon (the head is
  not claimed for them, and the held-out measure is English only).
* Fail-safe: any error (missing file, wrong shape, no embedder) returns ``()``.

Stdlib + numpy. No import of ``semantic_router`` at module import time.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

FORMAT = "zoe-restraint-head/1"
HEAD_PATH = Path(__file__).resolve().parent / "models" / "restraint_head.npz"
META_PATH = HEAD_PATH.with_suffix(".json")
CACHE_MAX = 4096

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {}                       # "head": (classes, W, b, thr, meta) | None (tried and absent)
_CACHE: "OrderedDict[str, tuple[str, ...]]" = OrderedDict()
_EMBEDDER: Optional[Callable[[str], Any]] = None  # tests and the eval harness inject one; production reads semantic_router


def _load() -> Optional[tuple]:
    if "head" in _STATE:
        return _STATE["head"]
    with _LOCK:
        if "head" in _STATE:
            return _STATE["head"]
        head = None
        try:
            meta = json.loads(META_PATH.read_text(encoding="utf-8"))
            data = np.load(HEAD_PATH)
            classes = tuple(meta["classes"])
            W, b, thr = (np.asarray(data[k], dtype=np.float32) for k in ("W", "b", "thr"))
            if meta.get("format") != FORMAT or W.shape != (len(classes), int(meta["dim"])) or b.shape != thr.shape != (len(classes),):
                raise ValueError("restraint head: shape/format mismatch")
            head = (classes, W, b, thr, meta)
        except Exception as exc:  # noqa: BLE001 - an absent or damaged head is "no head", never a broken classifier
            logger.info("restraint head unavailable: %r", exc)
        _STATE["head"] = head
        return head


def reset() -> None:
    """Forget the loaded head, the cache and any injected embedder (tests)."""
    global _EMBEDDER
    with _LOCK:
        _STATE.clear()
        _CACHE.clear()
        _EMBEDDER = None


def set_embedder(fn: Optional[Callable[[str], Any]]) -> None:
    """Inject ``text -> unit vector`` (tests, the eval harness). ``None`` returns to the live ``semantic_router`` lookup."""
    global _EMBEDDER
    _EMBEDDER = fn
    _CACHE.clear()


def _live_embedder() -> Optional[Callable[[str], Any]]:
    """``semantic_router.embed`` iff that module is imported and its model already loaded - never triggers a load."""
    if _EMBEDDER is not None:
        return _EMBEDDER
    sr = sys.modules.get("semantic_router")
    if sr is None or getattr(sr, "_MODEL", None) is None:
        return None
    return getattr(sr, "embed", None)


def available() -> bool:
    return _load() is not None


def describe() -> str:
    head = _load()
    if head is None:
        return "absent (word list only)"
    classes, W, _b, thr, meta = head
    return (f"{meta.get('format')} {len(classes)} classes x {W.shape[1]} dims, {HEAD_PATH.stat().st_size} bytes, "
            f"trained on {meta.get('n_train')} dev rows ({meta.get('train_sha', '')[:12]}), embedder {meta.get('embedder')}")


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def decide_from_vector(vec: Any) -> tuple[str, ...]:
    """The classes whose sigmoid clears its tuned threshold. Pure numpy; ``()`` when no head is shipped."""
    head = _load()
    if head is None:
        return ()
    classes, W, b, thr, _meta = head
    v = np.asarray(vec, dtype=np.float32).reshape(-1)
    if v.shape[0] != W.shape[1]:
        return ()
    n = float(np.linalg.norm(v))
    if n > 0:
        v = v / n
    z = W @ v + b
    p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
    return tuple(c for c, pi, ti in zip(classes, p, thr) if pi >= ti)


def classes(text: Any, *, embed: bool = False) -> tuple[str, ...]:
    """The head's classes for ``text`` (order of the head's own class list; ``restraint`` re-orders). ``embed=False`` answers
    from the cache only (the hot-path mode); ``embed=True`` may embed once and remember. Never raises."""
    raw = str(text or "").strip()
    if not raw or _load() is None:
        return ()
    key = _key(raw)
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    if not embed:
        return ()
    try:
        fn = _live_embedder()
        if fn is None:
            return ()
        out = decide_from_vector(fn(raw))
    except Exception as exc:  # noqa: BLE001
        logger.debug("restraint head: embed skipped: %r", exc)
        return ()
    _CACHE[key] = out
    while len(_CACHE) > CACHE_MAX:
        _CACHE.popitem(last=False)
    return out
