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
* **Nothing on a turn embeds.** ``classes(text, embed=False)`` answers only from the in-process cache. The one place that
  embeds is ``lookup(embed=True)``, and it runs only OFF the event loop: ``defer`` hands a text to ONE small daemon worker
  thread (the write path, ``restraint.stamp``), and ``warm`` is called through ``asyncio.to_thread`` (the nightly thread pass).
  Measured before this was so (2026-10-12): an inline embed in ``stamp`` stalled the event loop 11 ms median, 32 ms worst, on
  every memory write. **Behaviour change, stated:** a row stamped at write time may LACK the head's classes until the worker has
  filled the cache (a row stamped before that carries ``LEXICON_VERSION``, which readers ignore, so they recompute and see the
  head's answer once it is cached); the cache is per process, so after a restart the head returns for a row only when it is
  re-stamped or re-warmed (the restamp pass in the open-problems ledger).
* **English-trained.** The embedding model is bge-small-en: another language's text falls back on its lexicon (the head is
  not claimed for them, and the held-out measure is English only).
* Fail-safe: any error (missing file, wrong shape, no embedder) returns ``()``.

Stdlib + numpy. No import of ``semantic_router`` at module import time.
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
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
_CACHE_LOCK = threading.Lock()                     # the worker thread fills the cache while turns read it
DEFER_MAX = 256                                    # texts waiting for the worker; beyond it a text is dropped (it is re-derivable)
_QUEUE: "queue.Queue[str]" = queue.Queue(maxsize=DEFER_MAX)
_WORKER: Optional[threading.Thread] = None
_STATE: dict[str, Any] = {}                       # "head": (classes, W, b, thr, meta) | None (tried and absent)
_CACHE: "OrderedDict[str, tuple[str, ...]]" = OrderedDict()
_MISMATCH_LOGGED = False                           # the model-identity warning is logged once per process
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
            # each array is checked against the class count on its own: a chained ``b.shape != thr.shape != (n,)`` only rejects
            # when BOTH adjacent comparisons are true, so equal-but-wrong shapes (e.g. both (1,)) slipped through and
            # broadcasting + zip then evaluated just the first class
            n = len(classes)
            if (meta.get("format") != FORMAT or W.shape != (n, int(meta["dim"]))
                    or b.shape != (n,) or thr.shape != (n,)):
                raise ValueError("restraint head: shape/format mismatch")
            head = (classes, W, b, thr, meta)
        except Exception as exc:  # noqa: BLE001 - an absent or damaged head is "no head", never a broken classifier
            logger.info("restraint head unavailable: %r", exc)
        _STATE["head"] = head
        return head


def reset() -> None:
    """Forget the loaded head, the cache and any injected embedder (tests)."""
    global _EMBEDDER, _MISMATCH_LOGGED
    _MISMATCH_LOGGED = False
    with _LOCK:
        _STATE.clear()
        _EMBEDDER = None
    with _CACHE_LOCK:
        _CACHE.clear()
    while True:                                    # a text still queued from before must not be embedded into the next test
        try:
            _QUEUE.get_nowait()
            _QUEUE.task_done()
        except queue.Empty:
            break


def set_embedder(fn: Optional[Callable[[str], Any]]) -> None:
    """Inject ``text -> unit vector`` (tests, the eval harness). ``None`` returns to the live ``semantic_router`` lookup."""
    global _EMBEDDER
    _EMBEDDER = fn
    with _CACHE_LOCK:
        _CACHE.clear()


def _live_embedder() -> Optional[Callable[[str], Any]]:
    """``semantic_router.embed`` iff that module is imported and its model already loaded - never triggers a load."""
    if _EMBEDDER is not None:
        return _EMBEDDER
    sr = sys.modules.get("semantic_router")
    if sr is None or getattr(sr, "_MODEL", None) is None:
        return None
    # The weights were fitted on ONE embedding space. ``ZOE_ROUTER_MODEL`` can point the router at another model of the same
    # width, and a matrix product against the wrong space is confident nonsense, so the router's model name must equal the
    # one the head records; a mismatch (or an unnamed model) is "no head", with one log line.
    head = _load()
    want = str(head[4].get("embedder") or "") if head else ""
    have = str(getattr(sr, "_MODEL_NAME", "") or "")
    if not want or have != want:
        global _MISMATCH_LOGGED
        if not _MISMATCH_LOGGED:
            _MISMATCH_LOGGED = True
            logger.warning("restraint head disabled: semantic_router model %r is not the head's embedder %r", have, want)
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


def lookup(text: Any, *, embed: bool = False) -> Optional[tuple[str, ...]]:
    """The head's classes for ``text`` (order of the head's own class list; ``restraint`` re-orders), or ``None`` when the head
    did NOT run (no head file, no usable embedder, an embedder error, nothing cached): ``()`` means it ran and found nothing.
    The difference matters to a caller that stamps a version. ``embed=False`` answers from the cache only (the hot-path mode);
    ``embed=True`` may embed once and remember. Never raises."""
    raw = str(text or "").strip()
    if not raw or _load() is None:
        return None
    key = _key(raw)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
            return hit
    if not embed:
        return None
    try:
        fn = _live_embedder()
        if fn is None:
            return None
        out = decide_from_vector(fn(raw))
    except Exception as exc:  # noqa: BLE001
        logger.debug("restraint head: embed skipped: %r", exc)
        return None
    with _CACHE_LOCK:
        _CACHE[key] = out
        while len(_CACHE) > CACHE_MAX:
            _CACHE.popitem(last=False)
    return out


def classes(text: Any, *, embed: bool = False) -> tuple[str, ...]:
    """``lookup`` with "did not run" folded into "found nothing" (the union only ever adds)."""
    return lookup(text, embed=embed) or ()


def warm(texts: Any) -> int:
    """Embed every not-yet-cached text, SYNCHRONOUSLY, and return how many were classified by the head. For a worker thread or
    ``asyncio.to_thread`` only (the nightly thread pass): never call it on the event loop. Never raises."""
    n = 0
    for t in texts or ():
        if lookup(t, embed=True) is not None:
            n += 1
    return n


def _run_worker() -> None:
    while True:
        text = _QUEUE.get()
        try:
            lookup(text, embed=True)
        except Exception as exc:  # noqa: BLE001 - the worker outlives any one text
            logger.debug("restraint head worker: %r", exc)
        finally:
            _QUEUE.task_done()


def defer(text: Any) -> bool:
    """Queue ``text`` for the background worker, which embeds it off the event loop and fills the cache; True when queued.
    Constant time and non-blocking (a full queue drops the text, which a later stamp or the nightly pass re-derives). Starts the
    one daemon worker on first use. Never raises and never embeds on the calling thread - this is what ``stamp`` calls."""
    global _WORKER
    try:
        raw = str(text or "").strip()
        if not raw or _load() is None:
            return False
        with _CACHE_LOCK:
            if _key(raw) in _CACHE:
                return False
        with _LOCK:
            if _WORKER is None or not _WORKER.is_alive():
                _WORKER = threading.Thread(target=_run_worker, name="restraint-head", daemon=True)
                _WORKER.start()
        _QUEUE.put_nowait(raw)
        return True
    except queue.Full:
        return False
    except Exception as exc:  # noqa: BLE001
        logger.debug("restraint head: defer skipped: %r", exc)
        return False


def drain(timeout: float = 5.0) -> bool:
    """Wait until the worker has finished everything queued (tests). True when idle."""
    done = threading.Event()

    def _wait() -> None:
        _QUEUE.join()
        done.set()
    threading.Thread(target=_wait, daemon=True).start()
    return done.wait(timeout)
