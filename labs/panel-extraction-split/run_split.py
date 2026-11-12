#!/usr/bin/env python3
"""Panel-extraction split lab: on panel-style transcripts, where between "turns exist" and "candidate written" does the
idle-consolidation path lose facts?

Drives the REAL `memory_idle_consolidation.consolidate_session` (and, in --model mode, the REAL
`memory_digest._extract_facts_with_gemma` -> `_facts_call`) on the invented sessions in fixtures.py.

ISOLATION IS FAIL-CLOSED. Before ANY production module is imported this script
  1. creates a managed store dir under the fixed root /tmp (TMPDIR ignored; refused inside a git work tree or the live
     checkout; removed at exit) and points every store / DB env name at it or at an invalid value;
  2. installs guards that RAISE `GuardTrip` (a BaseException, so the production code's broad `except Exception` cannot
     swallow it) and RECORD the attempt when anything tries to build: an asyncpg pool or connection, a chromadb client
     (PersistentClient / HttpClient / Client / EphemeralClient / AsyncHttpClient / CloudClient), a sqlite3 file outside the
     managed temp dir, or any outbound socket connection (TCP or AF_UNIX). The ONE allowed connection is the local brain
     at 127.0.0.1:11434, and only inside a model call in --model mode;
  3. on EVERY exit path (`_run_guarded`: normal return, refusal, uncaught GuardTrip, any exception) asserts that no
     guard tripped and that no production store object exists (`db_pool._pool`, `memory_service._service_singleton`,
     `memory_service._AUDIT_CLIENTS`). A run that fails this check exits 4. `--selftest` proves each guard trips, then
     resets the deliberate trips so its own exit assertion covers only what follows.
The memory service / ingest step and the forget ledger are replaced by in-memory stand-ins (an ingest RECORDER and an
empty ledger) after import; the guards are what make that replacement safe rather than merely hopeful.

Modes
  (default)       deterministic gates only, NO model call; the extractor is a capture stub returning [].
  --model         also calls the local brain once per session (hard cap 20 calls per process, niced), only while holding
                  /tmp/zoe-voice-harness.lock, only when `scripts/landing/night_busy.sh voice` exits non-zero and no
                  land_voice_pr.sh process exists (checked before EVERY call).
  --rescore [PATH] no model: re-apply the real deterministic functions to stored model items and print the three
                  scores per class (raw model hit / survives the deterministic gates / handed
                  to the ingest recorder). Without PATH it reads `stored_run.py`, the sanitized synthetic run
                  behind README.md, so the README tables reproduce with no model call.
  --selftest      prove every guard trips; exits 0 only if all do.

Output: `--out` takes a file NAME, never a path (omitted: a generated name; empty: refused). The JSON is written into a
NEWLY CREATED directory `/tmp/panel-split-out-*` with O_CREAT|O_EXCL|O_NOFOLLOW, so it can never overwrite an existing file or follow a symlink;
a name with a path separator, `..` or a leading dot is refused. The script prints where it wrote.
Synthetic data only; never reads household content.
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import fcntl
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ZD = REPO / "services" / "zoe-data"
MODEL_CALL_CAP = 20
LOCK = "/tmp/zoe-voice-harness.lock"
BRAIN = ("127.0.0.1", 11434)

# ===== 1. managed store dir + env pins (BEFORE any production import) ==================================================
SAFE_ROOT = "/tmp"                                  # fixed parent for every lab directory; TMPDIR is NOT honoured
_LIVE_CHECKOUT = os.path.realpath(os.path.expanduser("~/assistant"))


def _unsafe_reason(path: str) -> str | None:
    """Why ``path`` may not hold lab files, or None. Refused: anything inside the live checkout (~/assistant), this
    repository, or ANY git work tree."""
    rp = os.path.realpath(path)
    for root in (_LIVE_CHECKOUT, os.path.realpath(str(REPO))):
        if rp == root or rp.startswith(root + os.sep):
            return f"inside {root}"
    try:
        probe = subprocess.run(["git", "-C", rp, "rev-parse", "--is-inside-work-tree"],
                               capture_output=True, text=True, timeout=10)
        if probe.returncode == 0 and probe.stdout.strip() == "true":
            return "inside a git work tree"
    except (OSError, subprocess.SubprocessError):
        return "could not check for a git work tree"
    return None


def _managed_dir(prefix: str) -> str:
    """A NEW directory under the fixed SAFE_ROOT (``dir=`` is explicit, so TMPDIR cannot move it), refused - and
    removed again - if its resolved location is inside a git work tree or the live checkout."""
    if _unsafe_reason(SAFE_ROOT):
        raise SystemExit(f"REFUSED: {SAFE_ROOT} is {_unsafe_reason(SAFE_ROOT)}")
    d = tempfile.mkdtemp(prefix=prefix, dir=SAFE_ROOT)
    why = _unsafe_reason(d)
    if why:
        shutil.rmtree(d, ignore_errors=True)
        raise SystemExit(f"REFUSED: lab directory would be {why}")
    return d


_STORE = _managed_dir("panel-split-stores-")
atexit.register(shutil.rmtree, _STORE, ignore_errors=True)
_INVALID_DSN = "postgresql://lab-guard.invalid:1/no_such_db"
_FILE_PINS = (
    ("MEMPALACE_DATA_DIR", "mempalace"), ("ZOE_VOICE_STT_LOG", "voice_stt.jsonl"),
    ("ZOE_MEMORY_REJECT_LEDGER", "memory-reject-ledger.json"), ("ZOE_TIMER_STORE", "timers.json"),
    ("ZOE_LOG_DIR", "logs"), ("ZOE_ROUTER_HEAD_LOG", "router_head_shadow.jsonl"),
    ("ZOE_INTENT_MISS_LOG", "intent-misses.jsonl"), ("ZOE_RECALL_GATE_TRACE_DIR", "recall-gate-trace"),
    ("ZOE_ADHOC_BACKUP_DIR", "adhoc-backups"), ("ZOE_ROUTER_RETRAIN_DIR", "router_retrain"),
    ("ZOE_OMI_REPASS_DIR", "omi-repass"), ("ZOE_WARM_SPEAKER_STATE", "warm-speaker.json"),
    ("ZOE_OMI_AUDIT_PATH", "omi-audit.jsonl"), ("ZOE_OMI_AUDIO_DIR", "omi-recordings"),
    ("ZOE_CHANGE_NOTICES_STATE_DIR", "change-notices"), ("ZOE_DEPLOY_STATE_DIR", "deploy"),
    ("NIGHT_DIR", "night-window"), ("ZOE_PIPELINE_STORE_PATH", "engineering_pipeline_runs.jsonl"),
    ("ZOE_VOICE_GREETING_STATE_PATH", "voice_greeting_state.json"), ("ZOE_BLUETOOTH_AUDIT_PATH", "bluetooth-audit.jsonl"),
)
for _var, _leaf in _FILE_PINS:
    os.environ[_var] = os.path.join(_STORE, _leaf)
for _var in ("POSTGRES_URL", "DATABASE_URL", "ZOE_DATABASE_URL", "POSTGRES_DSN"):
    os.environ[_var] = _INVALID_DSN
os.environ["ZOE_HARNESS"] = "1"                     # live_store_guard: this process is not the service
os.environ["ZOE_STRUCTURAL_VERIFIER"] = "off"
os.environ["ZOE_BRAIN_SLOT_GUARD"] = "off"
os.environ["GEMMA_SERVER_URL"] = "http://%s:%d" % BRAIN   # the only reachable endpoint, and only inside a --model call


# ===== 2. guards (BEFORE any production import) =======================================================================
class GuardTrip(BaseException):
    """A production store / network constructor was reached. BaseException: `except Exception` cannot swallow it."""


TRIPS: list[str] = []
ALLOW_BRAIN = {"on": False, "connects": 0}


def _trip(what: str):
    TRIPS.append(what)
    raise GuardTrip(f"lab guard: {what} refused")


def _install_guards() -> None:
    import asyncpg

    def _no_pool(*a, **k):
        _trip("asyncpg.create_pool")

    def _no_conn(*a, **k):
        _trip("asyncpg.connect")

    asyncpg.create_pool = _no_pool
    asyncpg.connect = _no_conn
    try:
        import asyncpg.pool as _ap
        _ap.create_pool = _no_pool
    except Exception:  # noqa: BLE001
        pass

    import chromadb
    for name in ("PersistentClient", "HttpClient", "Client", "EphemeralClient", "AsyncHttpClient", "CloudClient"):
        if hasattr(chromadb, name):
            setattr(chromadb, name, (lambda n: (lambda *a, **k: _trip(f"chromadb.{n}")))(name))

    real_sqlite_connect = sqlite3.connect
    store_root = os.path.realpath(_STORE) + os.sep

    def _sqlite(database, *a, **k):
        p = str(database)
        if p == ":memory:" or p.startswith("file::memory:") or os.path.realpath(p).startswith(store_root):
            return real_sqlite_connect(database, *a, **k)
        _trip("sqlite3.connect outside the lab temp dir")

    sqlite3.connect = _sqlite

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def _allowed(sock, address) -> bool:
        return (ALLOW_BRAIN["on"] and sock.family == socket.AF_INET and isinstance(address, tuple)
                and tuple(address[:2]) == BRAIN)

    def _connect(self, address):
        if not _allowed(self, address):
            _trip(f"socket.connect family={int(self.family)}")
        ALLOW_BRAIN["connects"] += 1
        return real_connect(self, address)

    def _connect_ex(self, address):
        if not _allowed(self, address):
            _trip(f"socket.connect_ex family={int(self.family)}")
        ALLOW_BRAIN["connects"] += 1
        return real_connect_ex(self, address)

    socket.socket.connect = _connect
    socket.socket.connect_ex = _connect_ex


_install_guards()

sys.path.insert(0, str(ZD))
sys.path.insert(0, str(HERE))

import fixtures  # noqa: E402  (synthetic data, not production)


def assert_no_store_constructed() -> list[str]:
    """Empty list = clean. Called at the end of every run."""
    bad = list(TRIPS)
    for mod, attr in (("db_pool", "_pool"), ("memory_service", "_service_singleton")):
        m = sys.modules.get(mod)
        if m is not None and getattr(m, attr, None) is not None:
            bad.append(f"{mod}.{attr} constructed")
    ms = sys.modules.get("memory_service")
    if ms is not None and getattr(ms, "_AUDIT_CLIENTS", None):
        bad.append("memory_service._AUDIT_CLIENTS not empty")
    return bad


# ===== helpers ========================================================================================================

def asr_lower(text: str) -> str:
    """The lower-case, unpunctuated variant (what a bare ASR without a punctuator would emit)."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", text.lower())).strip()


class _Row(dict):
    pass


def rows_for(session: dict, variant: str) -> list:
    meta = json.dumps({"user_id": fixtures.SPEAKER, "traffic_class": "household"})
    out = []
    for i, (role, text) in enumerate(session["turns"]):
        content = asr_lower(text) if (variant == "lower" and role == "user") else text
        out.append(_Row(role=role, content=content, metadata=meta, at=f"2026-11-11T09:00:{i:02d}+00:00"))
    return out


class _FakeConn:
    def __init__(self, rows):
        self.rows, self.writes = rows, []

    async def fetch(self, q, *a):
        return self.rows

    async def execute(self, q, *a):
        self.writes.append(a)


def _ctx(conn):
    class _C:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False
    return lambda: _C()


def matches(expect: dict, text: str) -> bool:
    t = (text or "").lower()
    return all(m in t for m in expect["must"])


def score_session(session: dict, variant: str, items: list, ingested_texts: list | None) -> dict:
    """Three SEPARATE numbers per expected fact, from the real deterministic functions:
       raw    - some model item carries the expected content (`must` tokens);
       gated  - such an item also survives the real deterministic gates of the idle path: its source turn is kept by
                `own_words.filter_turns` (pre-model, `memory_idle_consolidation.py:357`), its text is non-empty
                (`_fact_text`, `:399-401`) and `memory_quality.is_storable_fact` accepts it (`:403-413`);
       ingest - such an item was handed to the ingest recorder: the recorder's texts when the run stored them, else
                derived from the loop's rule (every non-empty, storable item goes to ingest, `:398-423`).
    Plus `wrong_owner`: an item whose quote lies inside a span the fixture labels as someone else's words."""
    import memory_quality
    import own_words
    import memory_idle_consolidation as mic
    user_turns = [asr_lower(t) if variant == "lower" else t for r, t in session["turns"] if r == "user"]
    kept_turns = own_words.filter_turns(list(user_turns), "panel_split_lab")
    squash = lambda t: re.sub(r"\s+", " ", str(t or "")).strip().lower()  # noqa: E731
    per_fact = []
    for e in session["expect"]:
        raw = gated = ingest = False
        for it in items:
            t = mic._fact_text(it)
            if not t or not matches(e, t):
                continue
            raw = True
            quote = squash(it.get("quote") if isinstance(it, dict) else "")
            turn_kept = (not quote) or any(quote in squash(k) for k in kept_turns)
            storable = memory_quality.is_storable_fact(t)[0]
            gated = gated or (turn_kept and storable)
            if ingested_texts is None:
                ingest = ingest or storable
        if ingested_texts is not None:
            ingest = any(matches(e, x) for x in ingested_texts)
        per_fact.append({"raw": raw, "gated": gated, "ingest": ingest})
    wrong_owner = []
    for it in items:
        q = squash(it.get("quote") if isinstance(it, dict) else "")
        if q and any(q in squash(f) or squash(f) in q for f in session.get("foreign_spans", [])):
            wrong_owner.append(mic._fact_text(it))
    return {"per_fact": per_fact, "wrong_owner": wrong_owner}


# ===== preflight for the model stage ==================================================================================

def preflight() -> str | None:
    """None when a model call is allowed now, else the reason it is not."""
    rc = subprocess.run(["bash", str(REPO / "scripts/landing/night_busy.sh"), "voice"], capture_output=True).returncode
    if rc == 0:
        return "night_busy.sh voice exited 0 (pause)"
    ps = subprocess.run(["pgrep", "-f", "[l]and_voice_pr\\.sh"], capture_output=True, text=True)
    if ps.stdout.strip():
        return "a land_voice_pr.sh process is running"
    return None


# ===== the run ========================================================================================================

async def run(mode: str, variants: list[str], only: list[str] | None) -> dict:
    import memory_idle_consolidation as mic
    import memory_digest
    import memory_forgotten
    import memory_service
    import expert_dispatch
    import own_words
    import memory_quality
    import memory_authority
    from date_locale import normalize_numeric_dates

    calls = {"n": 0}
    real_extract = memory_digest._extract_facts_with_gemma
    real_facts_call = memory_digest._facts_call
    seen: dict = {}

    async def _keep(uid, turns, strict=False, **kw):      # empty forget ledger stand-in
        return list(turns), 0

    async def _capped_facts_call(transcript, timeout):
        if calls["n"] >= MODEL_CALL_CAP:
            raise RuntimeError("lab model-call cap reached")
        why = preflight()
        if why:
            raise RuntimeError(f"model call refused: {why}")
        calls["n"] += 1
        t0 = time.monotonic()
        ALLOW_BRAIN["on"] = True
        try:
            out = await real_facts_call(transcript, timeout)
        finally:
            ALLOW_BRAIN["on"] = False
        seen["latency_s"] = round(time.monotonic() - t0, 2)
        return out

    async def _extract(text):
        seen["transcript"] = text
        seen["model_sees"] = normalize_numeric_dates(text)
        if mode != "model":
            return []
        seen["raw"] = await real_extract(text)
        return seen["raw"]

    ingested: list = []

    async def _ingest(svc, text, **kw):                  # the ingest RECORDER: nothing is written anywhere
        ingested.append({"fact": text, "anchor": kw.get("anchor_text")})
        return "stored"

    memory_forgotten.keep_unforgotten = _keep
    memory_digest._extract_facts_with_gemma = _extract
    memory_digest._facts_call = _capped_facts_call
    memory_service.get_memory_service = lambda: object()
    expert_dispatch._ingest_or_supersede = _ingest

    results = []
    for variant in variants:
        for s in fixtures.SESSIONS:
            if only and s["id"] not in only:
                continue
            seen.clear()
            ingested.clear()
            rows = rows_for(s, variant)
            user_in = [r["content"] for r in rows if r["role"] == "user"]
            owner = mic._resolve_owner(rows, "guest")
            own_drop = []
            for t in user_in:
                o = own_words.analyze(t)
                if o.changed:
                    own_drop.append({"turn": t, "reasons": list(o.reasons), "kept": o.text if o.has_own else ""})
            stored, err = None, None
            try:
                stored = await mic.consolidate_session(f"voice-panel-lab-{s['id']}", "guest",
                                                       get_ctx=_ctx(_FakeConn(rows)))
            except Exception as exc:  # noqa: BLE001  (a GuardTrip is a BaseException and is NOT caught here)
                err = f"{type(exc).__name__}: {exc}"
            model_sees = seen.get("model_sees")
            rec = {
                "id": s["id"], "group": s["group"], "variant": variant,
                "rows": len(rows), "user_turns_in": len(user_in),
                "gate_min_turns": len(rows) >= mic.MIN_TURNS, "gate_owner": owner,
                "own_words_changed": own_drop,
                "reached_model": model_sees is not None,
                "model_sees_chars": len(model_sees or ""),
                "expected_words_reach_model": [bool(model_sees) and all(m in model_sees.lower() for m in e["must"])
                                               for e in s["expect"]],
                "model_sees": model_sees, "error": err,
            }
            if mode == "model":
                raw = seen.get("raw") or []
                post = []
                for item in raw:
                    text = mic._fact_text(item)
                    ok, why = memory_quality.is_storable_fact(text) if text else (False, "empty")
                    anchor = memory_digest.fact_anchor(item, seen.get("transcript") or "")
                    verdict = memory_authority.check_observation(text, anchor) if text else None
                    post.append({"fact": text, "quote": item.get("quote") if isinstance(item, dict) else None,
                                 "storable": ok, "storable_reason": why, "quote_verbatim": anchor is not None,
                                 "observation": getattr(verdict, "kind", None),
                                 "observation_reasons": list(getattr(verdict, "reasons", ()) or ())})
                ing = [x["fact"] for x in ingested]
                rec.update({"latency_s": seen.get("latency_s"), "model_items": len(raw), "post": post,
                            "ingested": ing, "would_ingest": len(ing), "stored_return": stored,
                            "score": score_session(s, variant, raw, ing)})
            results.append(rec)
    return {"mode": mode, "model_calls": calls["n"], "brain_connects": ALLOW_BRAIN["connects"],
            "results": results, "min_turns": mic.MIN_TURNS}


def _synthetic_provenance(runs: list) -> list[str]:
    """Every stored quote must be words from a fixture user turn (either style): the stored run holds nothing but the
    invented household. Returns the session ids that fail."""
    by_id = {s["id"]: s for s in fixtures.SESSIONS}
    squash = lambda t: re.sub(r"\s+", " ", str(t or "")).strip().lower()  # noqa: E731
    bad = []
    for run_ in runs:
        for r in run_["results"]:
            turns = [t for role, t in by_id[r["id"]]["turns"] if role == "user"]
            pool = [squash(t) for t in turns] + [squash(asr_lower(t)) for t in turns]
            for p in r.get("post", []):
                if p.get("quote") and not any(squash(p["quote"]) in t for t in pool):
                    bad.append(r["id"])
    return bad


def rescore(path: str | None) -> dict:
    """Re-derive the three scores from a stored run (no model). Default: the committed `stored_run.STORED_RUN`.
    A PATH may be a previous run's JSON: one run or {"model": [run, ...]}."""
    import memory_quality
    if path:
        data = json.loads(Path(path).read_text())
        source = str(path)
    else:
        from stored_run import STORED_RUN as data
        source = "stored_run.py"
    runs = data["model"] if isinstance(data, dict) and "model" in data else [data]
    not_synthetic = _synthetic_provenance(runs)
    if not_synthetic:
        raise SystemExit(f"REFUSED: stored quotes not found in fixtures.py for {sorted(set(not_synthetic))}")
    by_id = {s["id"]: s for s in fixtures.SESSIONS}
    rows, mismatch = [], []
    for run_ in runs:
        for r in run_["results"]:
            s = by_id[r["id"]]
            items = [{"fact": p["fact"], "quote": p.get("quote")} for p in r.get("post", [])]
            rec_ing = r.get("ingested")                  # recorded on runs made with this version; older runs: derived
            sc = score_session(s, r["variant"], items, rec_ing)
            derived_n = sum(1 for p in r.get("post", []) if p["fact"] and memory_quality.is_storable_fact(p["fact"])[0])
            if r.get("would_ingest") is not None and derived_n != r["would_ingest"]:
                mismatch.append(r["id"])
            rows.append({"id": r["id"], "group": r["group"], "variant": r["variant"], "items": len(items),
                         "ingest_source": "recorded" if rec_ing is not None else "derived (count cross-checked)", **sc})
    classes: dict = {}
    for x in rows:
        if x["variant"] != "asr":
            continue
        c = classes.setdefault(x["group"], {"sessions": 0, "with_items": 0, "facts": 0, "raw": 0, "gated": 0,
                                            "ingest": 0, "wrong_owner": 0})
        c["sessions"] += 1
        c["with_items"] += x["items"] > 0
        c["wrong_owner"] += bool(x["wrong_owner"])
        for f in x["per_fact"]:
            c["facts"] += 1
            c["raw"] += f["raw"]
            c["gated"] += f["gated"]
            c["ingest"] += f["ingest"]
    return {"source": source, "sessions": rows, "classes_asr": classes, "ingest_count_mismatch": mismatch}


def selftest() -> int:
    """Every guard must trip. Exit 0 only if all do."""
    checks = []

    def expect_trip(name, fn):
        before = len(TRIPS)
        try:
            fn()
            checks.append((name, False, "did not raise"))
        except GuardTrip:
            checks.append((name, len(TRIPS) == before + 1, "tripped"))
        except BaseException as exc:  # noqa: BLE001
            checks.append((name, False, f"raised {type(exc).__name__} instead"))

    import asyncpg
    import chromadb
    expect_trip("asyncpg.create_pool", lambda: asyncpg.create_pool(dsn=_INVALID_DSN))
    expect_trip("asyncpg.connect", lambda: asyncpg.connect(dsn=_INVALID_DSN))
    expect_trip("chromadb.PersistentClient", lambda: chromadb.PersistentClient(path=os.path.join(_STORE, "c")))
    expect_trip("chromadb.HttpClient", lambda: chromadb.HttpClient(host="127.0.0.1", port=8000))
    expect_trip("sqlite3 file outside temp", lambda: sqlite3.connect(os.path.join(tempfile.gettempdir(), "panel-split-x.db")))
    expect_trip("socket tcp postgres port", lambda: socket.create_connection(("127.0.0.1", 5432), timeout=1))
    expect_trip("socket tcp brain while not inside a model call", lambda: socket.create_connection(BRAIN, timeout=1))

    def _unix():
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect("/var/run/postgresql/.s.PGSQL.5432")
        finally:
            s.close()
    expect_trip("socket AF_UNIX", _unix)

    import db_pool
    expect_trip("db_pool.init_pool (production code)", lambda: asyncio.run(db_pool.init_pool()))
    import memory_service
    expect_trip("memory_service._palace_client (production code)",
                lambda: memory_service._palace_client(os.environ["MEMPALACE_DATA_DIR"]))
    ok_mem = sqlite3.connect(":memory:").execute("select 1").fetchone() == (1,)
    checks.append(("sqlite3 :memory: still allowed (guard is not a blanket break)", ok_mem, ""))
    # --out: a name only, into a fresh managed dir; never a path, never over an existing file or through a symlink
    for bad_name in ("../x.json", "/etc/x.json", "a/b.json", "..", ".hidden.json", ""):
        try:
            _out_name(bad_name)
            checks.append((f"--out refuses {bad_name!r}", False, "accepted"))
        except SystemExit:
            checks.append((f"--out refuses {bad_name!r}", True, ""))
    d = tempfile.mkdtemp(prefix="panel-split-selftest-out-", dir=_STORE)
    victim = os.path.join(_STORE, "victim.txt")
    Path(victim).write_text("untouched")
    os.symlink(victim, os.path.join(d, "planted.json"))
    try:
        _write_new(d, "planted.json", "overwritten")
        checks.append(("write refuses a planted symlink", False, "followed it"))
    except OSError:
        checks.append(("write refuses a planted symlink", Path(victim).read_text() == "untouched", ""))
    Path(d, "exists.json").write_text("old")
    try:
        _write_new(d, "exists.json", "new")
        checks.append(("write refuses an existing file", False, "overwrote it"))
    except OSError:
        checks.append(("write refuses an existing file", Path(d, "exists.json").read_text() == "old", ""))
    # lab directories: fixed root, TMPDIR ignored, git work trees and the live checkout refused
    saved_env, saved_td = os.environ.get("TMPDIR"), tempfile.tempdir
    try:
        os.environ["TMPDIR"] = str(REPO)
        tempfile.tempdir = None
        d2 = _managed_dir("panel-split-selftest-tmpdir-")
        checks.append(("managed dir ignores TMPDIR pointed at the repo",
                       os.path.dirname(os.path.realpath(d2)) == os.path.realpath(SAFE_ROOT), d2))
        shutil.rmtree(d2, ignore_errors=True)
    finally:
        if saved_env is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = saved_env
        tempfile.tempdir = saved_td
    for label, path in (("this repository", str(REPO)), ("the live checkout", _LIVE_CHECKOUT),
                        ("a subdir of this repository", str(HERE))):
        checks.append((f"unsafe-dir check refuses {label}", bool(_unsafe_reason(path)), str(_unsafe_reason(path))))
    checks.append(("unsafe-dir check accepts the store dir", _unsafe_reason(_STORE) is None, ""))
    # the outer exit assertion: every path through _run_guarded ends in the isolation check
    n_trips = len(assert_no_store_constructed())
    checks.append(("end-of-run check reports every deliberate trip", n_trips >= 10, f"reported {n_trips}"))

    def _trips_then_raises():
        try:
            _trip("selftest: deliberate trip inside a failing run")
        except GuardTrip:
            pass                                         # a swallowed trip, then an unrelated crash
        raise RuntimeError("selftest: deliberate crash")

    TRIPS.clear()
    checks.append(("exit assertion fails a run that swallowed a trip and crashed", _run_guarded(_trips_then_raises) == 4, ""))
    TRIPS.clear()
    checks.append(("exit assertion fails a run that raised GuardTrip",
                   _run_guarded(lambda: _trip("selftest: deliberate uncaught trip")) == 4, ""))
    TRIPS.clear()
    checks.append(("exit assertion keeps a clean refusal's code", _run_guarded(lambda: (_ for _ in ()).throw(SystemExit(3))) == 3, ""))
    checks.append(("exit assertion passes a clean run", _run_guarded(lambda: 0) == 0, ""))
    # The deliberate trips above are now RESET; main() runs the self-test through _run_guarded, so its final
    # isolation line asserts that nothing tripped AFTER this point.
    TRIPS.clear()
    print(f"selftest: deliberate trips reset; the isolation line below covers everything after the reset")
    for name, ok, note in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {note}")
    return 0 if all(ok for _n, ok, _x in checks) else 1


_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def _out_name(name: str | None) -> str:
    """A bare file name, or SystemExit. None = a generated name; an EMPTY name is refused. No separators, no `..`, no
    leading dot, nothing but [A-Za-z0-9._-]."""
    n = f"panel-split-{int(time.time())}.json" if name is None else name
    if (os.sep in n or (os.altsep and os.altsep in n) or ".." in n or not _NAME_RE.fullmatch(n)):
        raise SystemExit(f"REFUSED: --out takes a file NAME only (got {n!r})")
    return n


def _write_new(directory: str, name: str, text: str) -> str:
    """Create ``directory/name`` exclusively: refuses an existing file and never follows a symlink."""
    path = os.path.join(directory, name)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return path


def _run_guarded(body) -> int:
    """Run ``body`` and ALWAYS end with the isolation assertion, whatever happened: a normal return, a refusal
    (SystemExit), a GuardTrip, or any other exception. A tripped guard or a constructed store makes the exit code 4."""
    import traceback
    try:
        rc = body()
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, str):
            print(code)
            rc = 1
        else:
            rc = int(code or 0)
    except GuardTrip as exc:
        print(f"GUARD TRIPPED: {exc}")
        rc = 4
    except BaseException:  # noqa: BLE001
        traceback.print_exc()
        rc = 1
    bad = assert_no_store_constructed()
    print(f"exit-isolation={'clean' if not bad else 'VIOLATED ' + str(bad)} rc={4 if bad else rc}")
    return 4 if bad else rc


def main() -> int:
    return _run_guarded(_main)


def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="store_true", help="also call the local brain (see module docstring)")
    ap.add_argument("--variants", default="asr", help="comma list of asr,lower")
    ap.add_argument("--only", default="", help="comma list of session ids")
    ap.add_argument("--out", default=None, help="output file NAME (written into a new managed dir under /tmp; never a path)")
    ap.add_argument("--rescore", nargs="?", const="", default=None,
                    help="re-derive scores (no model); default source: the committed stored_run.py")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    out_name = _out_name(a.out)
    if a.rescore is not None:
        res = rescore(a.rescore or None)
        print(f"source={res['source']}")
        for x in res["sessions"]:
            print(f"{x['id']:3} {x['group']:8} {x['variant']:5} items={x['items']} "
                  f"raw={[f['raw'] for f in x['per_fact']]} gated={[f['gated'] for f in x['per_fact']]} "
                  f"ingest={[f['ingest'] for f in x['per_fact']]} [{x['ingest_source']}] wrong_owner={x['wrong_owner']}")
        for g, c in res["classes_asr"].items():
            print(f"class {g:8} sessions={c['sessions']} with_items={c['with_items']} expected_facts={c['facts']} "
                  f"raw_hit={c['raw']} survive_gates={c['gated']} to_ingest={c['ingest']} wrong_owner={c['wrong_owner']}")
        print(f"ingest_count_mismatch={res['ingest_count_mismatch']}")
    else:
        mode = "model" if a.model else "gates"
        variants = [v for v in a.variants.split(",") if v]
        only = [x for x in a.only.split(",") if x] or None
        lock_fd = None
        if mode == "model":
            why = preflight()
            if why:
                print(f"REFUSED: {why}")
                return 3
            lock_fd = open(LOCK, "a")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)           # wait for any voice harness to finish
            os.nice(10)
        try:
            res = asyncio.run(run(mode, variants, only))
        finally:
            if lock_fd:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
        for r in res["results"]:
            line = (f"{r['id']:3} {r['group']:8} {r['variant']:5} rows={r['rows']} user={r['user_turns_in']} "
                    f"owner={'ok' if r['gate_owner'] else 'NONE'} own_words_changed={len(r['own_words_changed'])} "
                    f"reached={r['reached_model']} chars={r['model_sees_chars']} expect_words_reach={r['expected_words_reach_model']}")
            if mode == "model":
                line += f" | items={r['model_items']} ingest={r['would_ingest']} t={r['latency_s']}s"
            if r["error"]:
                line += f" ERROR={r['error']}"
            print(line)
        print(f"model_calls={res['model_calls']} brain_connects={res['brain_connects']}")
    bad = assert_no_store_constructed()
    res["isolation"] = {"trips": bad, "clean": not bad}
    out_dir = _managed_dir("panel-split-out-")   # new, under /tmp, kept for the reader; never in a work tree
    out = _write_new(out_dir, out_name, json.dumps(res, indent=1, default=str))
    print(f"isolation={'clean' if not bad else 'VIOLATED ' + str(bad)} wrote={out}")
    return 4 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
