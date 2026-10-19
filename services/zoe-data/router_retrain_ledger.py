"""ROUTER RETRAIN LEDGER (Phase 3 of the self-correction loop): the on-box record of what the struggle-fed router retrain loop
(``scripts/maintenance/router_struggle_retrain.py``) trained on, what it adopted, and what has been forgotten since.
Contract: ``docs/architecture/struggle-detector.md`` -> *Phase 3*.

WHERE: ``$ZOE_ROUTER_RETRAIN_DIR`` (default ``~/.zoe/router_retrain``), outside every git checkout, mode 0600 / 0700. The ledger
(``ledger.json``) holds NO words: an example is ``{e: event id, u: user key, k: text key, s: train|holdout}`` where the keys are
truncated hashes. The words exist only for the length of one run, in that run's work dir (``work/<stamp>/*.texts.jsonl``, next to
an ``ids.json`` naming the event ids / user keys in it), and the run deletes them when it ends.

FORGET (called by ``struggle_proposals.forget_events`` / ``forget_user`` - i.e. from ``struggle_detector.erase_entity`` /
``delete_user`` - BEFORE the struggle rows go, with the rows' wording keys derived while they still exist; it RAISES on a failure so
the forget is not confirmed):
  * every run record that used one of the forgotten examples loses that example's entry and counts it in ``forgotten``; a record
    whose model is (or was, or is being swapped in as) SERVED - itself or through any generation it was warm-started from - is
    flagged ``needs_retrain``: weights cannot unlearn, so the next adopted generation, trained without it, is the remedy, an
    in-flight swap rolls back, and ``status()`` says so;
  * the example's text key, event id (and, for a deleted user, the user key) become TOMBSTONES - also when no run has recorded it
    yet: the collector drops a tombstoned example, so a forgotten turn never re-enters a training set, even if the same words are
    said again;
  * any work dir still holding one of those examples' words (a run in flight, or one that crashed before its cleanup) has its text
    files deleted. A run in flight re-checks the tombstones before it records or adopts (``tombstoned``) and refuses to adopt then.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

VERSION = 1
#: run records kept (an adopted / served generation's record is never dropped)
MAX_RUNS = 200
LEDGER = "ledger.json"
WORK = "work"
TEXT_SUFFIX = ".texts.jsonl"
IDS_FILE = "ids.json"


def root() -> Path:
    d = Path(os.environ.get("ZOE_ROUTER_RETRAIN_DIR") or (Path.home() / ".zoe" / "router_retrain")).expanduser()
    if os.environ.get("PYTEST_CURRENT_TEST") and d.resolve() == (Path.home() / ".zoe" / "router_retrain").resolve():
        # a test must never touch the live ledger (a tests/unit delete_user once tombstoned a synthetic user in it): fail loudly
        raise RuntimeError("router_retrain_ledger: refusing the LIVE ledger from a pytest session - pin ZOE_ROUTER_RETRAIN_DIR")
    return d


def text_key(text: str) -> str:
    """Stable, word-free key of a FAILED utterance: the struggle detector's own normalisation (wake word, contractions, number words,
    plurals, fillers folded), so re-spellings of one request share a key."""
    try:
        import struggle_signals as sig

        norm = sig.normalise(text) or str(text or "").strip().lower()
    except Exception:  # noqa: BLE001 - the script may run without zoe-data on the path; same fold, minus the detector's extras
        norm = " ".join(str(text or "").lower().split())
    return hashlib.sha256(f"router-retrain|{norm}".encode("utf-8")).hexdigest()[:16]


def user_key(user_id: str) -> str:
    return hashlib.sha256(f"router-retrain-user|{user_id or ''}".encode("utf-8")).hexdigest()[:16]


def _empty() -> dict[str, Any]:
    return {"version": VERSION, "served": None, "previous": None,
            "tombstones": {"event_ids": [], "text_keys": [], "user_keys": []}, "runs": []}


@contextlib.contextmanager
def _locked(create: bool) -> Iterator[Optional[Path]]:
    d = root()
    if not d.exists():
        if not create:
            yield None
            return
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    with open(d / ".lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield d
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _read(d: Path) -> dict[str, Any]:
    p = d / LEDGER
    if not p.exists():
        return _empty()
    data = json.loads(p.read_text())          # a corrupt ledger RAISES: the forget must not be confirmed over it
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ValueError("router retrain ledger: unknown format")
    base = _empty()
    base.update(data)
    for k in ("event_ids", "text_keys", "user_keys"):
        base["tombstones"].setdefault(k, [])
    return base


def _write(d: Path, data: dict[str, Any]) -> None:
    runs = data.get("runs") or []
    keep = {data.get("served"), data.get("previous")} - {None}
    if len(runs) > MAX_RUNS:
        extra = len(runs) - MAX_RUNS
        pruned = []
        for r in runs:
            if extra > 0 and r.get("gen") not in keep and r.get("status") != "adopted":
                extra -= 1
                continue
            pruned.append(r)
        data["runs"] = pruned
    tmp = d / f".{LEDGER}.tmp"
    tmp.write_text(json.dumps(data, sort_keys=True, indent=1))
    os.chmod(tmp, 0o600)
    os.replace(tmp, d / LEDGER)


def load() -> dict[str, Any]:
    """The whole ledger (no words in it). A missing dir is an empty ledger."""
    with _locked(create=False) as d:
        return _empty() if d is None else _read(d)


def update(fn) -> dict[str, Any]:
    """Read-modify-write under the lock; ``fn(ledger)`` mutates in place. Returns the new ledger."""
    with _locked(create=True) as d:
        data = _read(d)
        fn(data)
        _write(d, data)
        return data


def tombstoned(examples: Iterable[dict], ledger: Optional[dict] = None) -> list[dict]:
    """The examples (``{e, u, k}`` or seam rows carrying event_id / user_key / text_key) a forget has struck."""
    t = (ledger or load())["tombstones"]
    ev, tk, uk = set(t["event_ids"]), set(t["text_keys"]), set(t["user_keys"])
    out = []
    for x in examples:
        e = x.get("e", x.get("event_id"))
        k = x.get("k", x.get("text_key"))
        u = x.get("u", x.get("user_key"))
        if e in ev or k in tk or u in uk:
            out.append(x)
    return out


def _purge_work(d: Path, event_ids: set[str], ukey: Optional[str], text_keys: frozenset = frozenset()) -> int:
    """Delete the text files of every work dir that holds one of these examples. Returns files deleted."""
    gone = 0
    work = d / WORK
    if not work.exists():
        return 0
    for run_dir in work.iterdir():
        ids_path = run_dir / IDS_FILE
        texts = list(run_dir.glob(f"*{TEXT_SUFFIX}"))
        if not texts:
            continue
        try:
            ids = json.loads(ids_path.read_text())
            hit = (bool(set(ids.get("event_ids") or []) & event_ids) or bool(set(ids.get("text_keys") or []) & text_keys)
                   or (ukey is not None and ukey in set(ids.get("user_keys") or [])))
        except (OSError, ValueError):
            hit = True                         # cannot tell what it holds: it holds nothing after a forget
        if hit:
            for f in texts:
                f.unlink(missing_ok=True)
                gone += 1
    return gone


#: run statuses whose model was (or is about to be) served: a forgotten TRAIN example in one of them, or in any of its ancestors,
#: flags it for retrain. "adopting" is a candidate reserved for the swap (``router_struggle_retrain.adopt_generation``): a forget that
#: lands during the swap flags it, and the adoption rolls back when it sees the flag.
SERVED_STATUSES = ("adopting", "adopted", "rolled_back")


def lineage(data: dict, gen: Optional[str]) -> list[dict]:
    """The run records of ``gen`` and every generation it was warm-started from (``parent`` links), newest first. A generation holds
    what all its ancestors learned, so isolation and forget must look at the whole chain, not only the last record."""
    by_gen = {r.get("gen"): r for r in data.get("runs") or [] if r.get("gen")}
    out: list[dict] = []
    seen: set = set()
    while gen and gen in by_gen and gen not in seen:
        seen.add(gen)
        out.append(by_gen[gen])
        gen = by_gen[gen].get("parent")
    return out


def trained_keys(data: dict) -> set:
    """Every wording the served and the previous generation learned, through their WHOLE warm-start lineage."""
    keys: set = set()
    for head in (data.get("served"), data.get("previous")):
        for r in lineage(data, head):
            keys.update(x.get("k") for x in r.get("examples") or [] if x.get("s") == "train" and x.get("k"))
    return keys


def forget(event_ids: Iterable[str] = (), user_id: Optional[str] = None, text_keys: Iterable[str] = ()) -> dict[str, int]:
    """The forget path (see the module doc). RAISES on a failure. Counts only.

    ``text_keys`` are the forgotten turns' wording keys, derived by the caller WHILE the struggle rows still exist, so a turn forgotten
    before any run recorded it is still tombstoned by wording (the same words under a new event id never re-enter). Tombstones are
    hashes only, so a forget creates the ledger dir when there is something to tombstone."""
    ids = {str(i) for i in event_ids if i}
    tks = {str(k) for k in text_keys if k}
    ukey = user_key(user_id) if (user_id or "").strip() else None
    res = {"examples": 0, "flagged": 0, "texts_deleted": 0}
    if not ids and not tks and ukey is None:
        return res

    def _flag(r: dict) -> None:
        if not r.get("needs_retrain"):
            res["flagged"] += 1
        r["needs_retrain"] = True
        r["needs_retrain_since"] = round(time.time(), 3)

    def _apply(data: dict) -> None:
        t = data["tombstones"]
        runs = data.get("runs") or []
        keys: set[str] = set(tks)
        learned_in: set = set()                     # gens (or reserved stamps) whose model learned a forgotten example
        for r in runs:
            exs = r.get("examples") or []
            hit = [x for x in exs if x.get("e") in ids or x.get("k") in tks or (ukey is not None and x.get("u") == ukey)]
            if not hit:
                continue
            keys.update(x.get("k") for x in hit if x.get("k"))
            r["examples"] = [x for x in exs if x not in hit]
            r["forgotten"] = int(r.get("forgotten") or 0) + len(hit)
            res["examples"] += len(hit)
            if any(x.get("s") == "train" for x in hit):          # a held-out example was scored, never learned
                if r.get("gen"):
                    learned_in.add(r["gen"])
                if r.get("status") in SERVED_STATUSES or r.get("gen") in (data.get("served"), data.get("previous")):
                    _flag(r)
        if learned_in:
            # a descendant (served, previous, or a candidate being swapped in) inherited what its ancestor learned
            for r in runs:
                if r.get("status") in SERVED_STATUSES or r.get("gen") in (data.get("served"), data.get("previous")):
                    chain = {x.get("gen") for x in lineage(data, r.get("gen"))} | {r.get("parent")}
                    if chain & learned_in:
                        _flag(r)
        t["event_ids"] = sorted(set(t["event_ids"]) | ids)
        t["text_keys"] = sorted(set(t["text_keys"]) | keys)
        if ukey is not None:
            t["user_keys"] = sorted(set(t["user_keys"]) | {ukey})

    with _locked(create=True) as dd:
        data = _read(dd)
        _apply(data)
        _write(dd, data)
        res["texts_deleted"] = _purge_work(dd, ids, ukey, frozenset(tks))
    return res


def status() -> dict[str, Any]:
    """Counts-only summary for the CLI / the report."""
    data = load()
    runs = data.get("runs") or []
    by: dict[str, int] = {}
    for r in runs:
        by[str(r.get("status"))] = by.get(str(r.get("status")), 0) + 1
    served = next((r for r in runs if r.get("gen") and r.get("gen") == data.get("served")), None)
    return {"served": data.get("served"), "previous": data.get("previous"), "runs": len(runs), "by_status": by,
            "served_needs_retrain": bool(served and served.get("needs_retrain")),
            "tombstones": {k: len(v) for k, v in data["tombstones"].items()},
            "last": ({k: runs[-1].get(k) for k in ("stamp", "mode", "status", "outcome")} if runs else None)}
