"""The re-pass's CONTENDER EVALUATION: ROVER fusion, the per-chunk hypothesis store, the contender ledger and the verdicts.

Owner policy (2026-11-09): run every strong contender for a while, until we can safely say one is not needed. So each night the re-pass runs Qwen3-ASR + Parakeet in production
(their chunk texts feed a FUSION step), and Whisper large-v3 + Granite on a rotating SAMPLE of the day's speech (evaluation only: never in the stored text). Every model's per-chunk
hypothesis is kept for a week (private, forget-reached), and a small ledger records, per model per night, what it contributed. A plain verdict line says when a model looks droppable.
NOTHING here prunes a model: the owner decides.

FUSION. ROVER (word voting, Qwen3 as the pivot and tie-break; design measured in the lab: ``fusion/rover.py``) over at least THREE voters; fewer than three and the pivot's text stands.
Contested slots (no strict majority) and low-confidence chunks are stored with every model's alternative and the vote counts, and exposed through ``check_queue`` for the owner's
morning transcript check. ``FUSERS`` keeps the seam open (``primary`` = no voting).

LEDGER (``ledger.jsonl``, COUNTS ONLY, no word): one line per model per night - chunks run, speech seconds, agreement with the fused output, ``sole`` (fused words only this model
heard), ``invented`` (words only this model produced that the fused output rejected), and the owner-correction hits / misses once a day review lets the owner fix a line.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

HYPS_KEEP_DAYS = 7
_TOK = re.compile(r"[a-z0-9']+")


def toks(text: str) -> list[str]:
    return _TOK.findall((text or "").lower())


def agreement(a: str, b: str, cap: int = 2500) -> float:
    x, y = toks(a)[:cap], toks(b)[:cap]
    if not x and not y:
        return 1.0
    if not x or not y:
        return 0.0
    return round(difflib.SequenceMatcher(None, x, y, autojunk=False).ratio(), 4)


# ── fusion: ROVER (word voting), Qwen3 as the pivot ─────────────────────────────────────────────────────────────────────────────────────────

PIVOT = "qwen3-asr"
EPS = 0.01                  # the pivot's tie-break weight
LOW_CONF_DIST = 0.5         # every other engine more than this fraction of words away from the pivot => "[low confidence]", keep the pivot text
FILL = {"mm", "mmm", "um", "uh", "hmm", "ah", "oh", "yeah", "hm", "mhm", "uh-huh"}
_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _n2w(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + _n2w(n % 100))
    return str(n)


def norm_keep(s: str) -> list[str]:
    """Normalised words that KEEP fillers (the voting and the fused text stay verbatim): lower case, numbers < 1000 spelt, punctuation removed."""
    s = unicodedata.normalize("NFC", s or "").lower().replace("-", " ").replace("\u2014", " ").replace("\u2019", "'")
    s = re.sub(r"\b(\d+)\b", lambda m: _n2w(int(m.group(1))) if int(m.group(1)) < 1000 else m.group(1), s)
    return "".join(c if (c.isalnum() or c == "'" or unicodedata.category(c)[0] == "M") else " " for c in s).split()       # digits survive only for numbers >= 1000 (smaller ones were spelt above): a year or an amount is kept


def _rtoks(text: str) -> list[tuple]:
    """[(word, surface)]: a single-word token keeps its surface (case, punctuation); a split token's surface is its words."""
    out: list[tuple] = []
    for raw in (text or "").split():
        ws = norm_keep(raw)
        if len(ws) == 1:
            out.append((ws[0], raw))
        else:
            out += [(w, w) for w in ws]
    return out


def _align(a: list, b: list) -> list[tuple]:
    """Levenshtein alignment: [(i | None, j | None)]."""
    n, m = len(a), len(b)
    D = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        D[i][0] = i
    for j in range(m + 1):
        D[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i][j] = min(D[i - 1][j - 1] + (a[i - 1] != b[j - 1]), D[i - 1][j] + 1, D[i][j - 1] + 1)
    i, j, out = n, m, []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and D[i][j] == D[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            out.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif i > 0 and D[i][j] == D[i - 1][j] + 1:
            out.append((i - 1, None))
            i -= 1
        else:
            out.append((None, j - 1))
            j -= 1
    return out[::-1]


def _edit(a: list, b: list) -> int:
    return sum(1 for x, y in _align(a, b) if x is None or y is None or a[x] != b[y])


def rover(hyps: dict, pivot: str = PIVOT, lexicon: Iterable[str] = ()) -> dict:
    """ROVER over one chunk's hypotheses ``{model: text}`` (measured on Sunday's audio: 5.0% WER against the pivot alone at 7.3%; LLM free-generation fusion did worse, LLM slot
    arbitration only tied it - so there is NO LLM stage). Words are aligned to the PIVOT; the majority wins, the pivot breaks ties (weight 1 + EPS); an EMPTY hypothesis abstains; an
    INSERTED word needs a strict majority of the voters; Granite's "zoe" is ignored unless the pivot also says "zoe" (its keyword bias). With fewer than three voters there is
    nothing to outvote the pivot: its text stands. If EVERY other engine is more than 50 % away from the pivot the chunk is flagged ``low_conf`` and the pivot text kept.
    Returns {"text", "words", "sources" (per output word: the models that voted for it), "slots" (contested: no strict majority), "voters", "low_conf"}."""
    P = _rtoks(hyps.get(pivot, ""))
    pw = [w for w, _ in P]
    n = len(P)
    base = {"text": " ".join(s for _, s in P), "words": pw, "sources": [{pivot}] * n, "slots": [], "voters": [pivot], "low_conf": False}
    others = [m for m in sorted(hyps) if m != pivot and (hyps[m] or "").strip()]
    if not P and len(others) >= 2:
        # The pivot heard NOTHING but two+ other engines recognised speech: never drop it silently. Keep the voters' medoid text (closest to the rest) and flag the chunk
        # ``low_conf`` so it reaches the transcript-check queue (a whole-chunk candidate). One lone engine (a likely hallucination on silence) still yields empty.
        toks = {m: [w for w, _ in _rtoks(hyps[m])] for m in others}
        best = min(others, key=lambda m: (sum(_edit(toks[m], toks[o]) for o in others if o != m), m))
        surf = " ".join(s for _, s in _rtoks(hyps[best]))
        return {**base, "text": surf, "words": toks[best], "sources": [{best}] * len(toks[best]), "voters": [pivot] + others, "low_conf": True}
    if not P or len(others) < 2:
        base["voters"] = [pivot] + others
        return base
    base["voters"] = [pivot] + others
    # low confidence: every other engine is far from the pivot
    dists = [_edit(pw, [w for w, _ in _rtoks(hyps[m])]) / max(1, n) for m in others]
    if all(d > LOW_CONF_DIST for d in dists):
        return {**base, "low_conf": True, "text": base["text"]}
    votes = [{} for _ in range(n)]            # slot -> word | None -> weight
    who = [{} for _ in range(n)]              # slot -> word | None -> models
    surf = [{} for _ in range(n)]
    gaps = [{} for _ in range(n + 1)]         # gap -> tuple(words) -> weight
    gwho = [{} for _ in range(n + 1)]
    gsurf = [{} for _ in range(n + 1)]
    for i, (w, sf) in enumerate(P):
        votes[i][w] = 1 + EPS
        who[i][w] = {pivot}
        surf[i][w] = sf
    N = 1
    for m in others:
        S = _rtoks(hyps[m])
        N += 1
        sw = [w for w, _ in S]
        last = -1
        ins: dict = {}
        for a, b in _align(pw, sw):
            if a is not None and b is not None:
                if "granite" in m and sw[b] == "zoe" and pw[a] != "zoe":
                    continue                                       # Granite's keyword bias: abstain on this slot
                votes[a][sw[b]] = votes[a].get(sw[b], 0) + 1
                who[a].setdefault(sw[b], set()).add(m)
                surf[a].setdefault(sw[b], S[b][1])
                last = a
            elif a is not None:
                votes[a][None] = votes[a].get(None, 0) + 1
                who[a].setdefault(None, set()).add(m)
                last = a
            else:
                if "granite" in m and sw[b] == "zoe":
                    continue
                ins.setdefault(last + 1, []).append(b)
        for g, bs in ins.items():
            key = tuple(sw[b] for b in bs)
            gaps[g][key] = gaps[g].get(key, 0) + 1
            gwho[g].setdefault(key, set()).add(m)
            gsurf[g].setdefault(key, [S[b][1] for b in bs])
    out_t, out_w, out_s, slots = [], [], [], []
    for i in range(n + 1):
        for key, wgt in sorted(gaps[i].items(), key=lambda x: -x[1]):
            if wgt > N / 2:                                        # an inserted word needs a strict majority
                for w, sf in zip(key, gsurf[i][key]):
                    out_t.append(sf)
                    out_w.append(w)
                    out_s.append(gwho[i][key])
        if i < n:
            best = max(votes[i].items(), key=lambda x: x[1])[0]
            raw = {c: int(round(v)) for c, v in votes[i].items()}
            if raw[best] <= N / 2 and len(raw) > 1:
                slots.append({"pos": len(out_w), "pick": best, "votes": {("" if c is None else c): v for c, v in raw.items()},
                              "who": {("" if c is None else c): sorted(who[i].get(c, ())) for c in raw}, "n": N})
            if best is not None:
                out_t.append(surf[i][best])
                out_w.append(best)
                out_s.append(who[i][best])
    return {"text": " ".join(out_t), "words": out_w, "sources": out_s, "slots": slots, "voters": [pivot] + others, "low_conf": False}


def fuse_primary(hyps: dict, primary: str = PIVOT, lexicon: Iterable[str] = ()) -> dict:
    t = hyps.get(primary) or next((x for x in hyps.values() if x), "")
    w = norm_keep(t)
    return {"text": t, "words": w, "sources": [{primary}] * len(w), "slots": [], "voters": [m for m in hyps if hyps[m]], "low_conf": False}


FUSERS: dict[str, Callable[..., dict]] = {"primary": fuse_primary, "rover": rover}


def fuse(hyps: dict, primary: str = PIVOT, how: str = "rover", lexicon: Iterable[str] = ()) -> dict:
    return FUSERS.get(how, rover)(hyps, primary, lexicon)


LOW_CONF_TAG = "[low confidence]"


def is_contested(row: dict) -> bool:
    """A stored row the owner's morning transcript check should look at: a "who said this?" turn (``kind`` who_said), or a chunk flagged low confidence / with a ROVER slot without a strict majority."""
    return row.get("kind") == "who_said" or bool(row.get("low_conf") or row.get("slots"))


def check_queue(home: Path, user_id: str, rid: Optional[str] = None, kind: Optional[str] = None) -> list[dict]:
    """THE INPUT for the owner's morning transcript-check feature (feature/transcript-check): per contested chunk of the member's recordings - ``rid``, ``i``, ``t0``/``t1``, each
    model's alternative (``hyps``), the ROVER pick (``rover``), the contested slots with their vote counts (``slots``: pick, votes {word: n}, who {word: [models]}) and ``low_conf``.
    A SECOND item kind, ``who_said``, carries the turns the speaker step would not decide (``mark`` = "lean" for "X?", "unknown" for "?"): ``rid``, ``seg_id`` (the stored segment: the owner's tap
    calls ``omi_repass.confirm_who`` with the person, which adds that turn's embedding to the person's pendant print), ``t0``/``t1`` and ``cands`` = [[person id | "you", score]] best first.
    ``kind`` filters ("transcript" | "who_said"). PRIVATE (it holds words and voice-match scores): the caller is the member's own surface. Newest night first."""
    d = hyps_dir(home)
    out: list[dict] = []
    for p in (sorted(d.glob("hyps-*.jsonl"), reverse=True) if d.exists() else []):
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            k = r.get("kind") or "transcript"
            if r.get("u") == user_id and (rid is None or r.get("rid") == rid) and (kind is None or k == kind) and is_contested(r):
                out.append({"kind": k, **{x: r.get(x) for x in ("rid", "i", "t0", "t1", "hyps", "rover", "slots", "low_conf", "voters", "seg_id", "mark", "cands")}})
    return out


# ── what each model contributed ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def contributions(chunks: list[dict], primary_models: Iterable[str]) -> dict:
    """``chunks``: [{"hyps": {model: text}, "fused": text, "speech_s": float, "sources": [set of models per fused word] (ROVER) | None}]. Per model: chunks run, speech seconds, agreement
    with the fused text, ``sole`` fused words whose ONLY voter was this model (the vote chose it), ``invented`` words only this model produced that the fused text does not contain.
    ``sole`` / ``invented`` only count on chunks where at least two models ran."""
    out: dict[str, dict] = {}
    for c in chunks:
        hy = {m: t for m, t in c["hyps"].items() if t is not None}
        fused = set(toks(c["fused"]))
        for m, t in hy.items():
            o = out.setdefault(m, {"chunks": 0, "speech_s": 0.0, "agree_sum": 0.0, "agree_n": 0, "words": 0, "sole": 0, "invented": 0, "cmp_chunks": 0})
            o["chunks"] += 1
            o["speech_s"] += float(c.get("speech_s") or 0.0)
            o["words"] += len(toks(t))
            o["agree_sum"] += agreement(c["fused"], t)
            o["agree_n"] += 1
            rest = {x for mm, tt in hy.items() if mm != m for x in toks(tt)}
            if len(hy) >= 2:
                mine = set(toks(t))
                o["cmp_chunks"] += 1
                # a production voter is judged on the fused text itself; an EVALUATION contender never votes in the stored text, so it is judged on what ROVER would have produced
                # with it added as one more voter (``with``): its sole words there, and the words that vote would have changed
                alt = (c.get("with") or {}).get(m)
                if alt is not None:
                    o["sole"] += sum(1 for src in alt["sources"] if set(src) == {m})
                    o["changed"] = o.get("changed", 0) + _edit(toks(c["fused"]), toks(alt["text"]))
                    reject = set(toks(alt["text"]))
                elif c.get("sources") is not None:
                    o["sole"] += sum(1 for src in c["sources"] if set(src) == {m})
                    reject = fused
                else:
                    o["sole"] += len([w for w in toks(c["fused"]) if w in mine and w not in rest])
                    reject = fused
                o["invented"] += len([w for w in toks(t) if w not in rest and w not in reject])
    return out


def pick_sample(chunks: list[tuple], speech_budget_s: float, ordinal: int) -> list[tuple]:
    """A rotating sample spread across the day. ``chunks``: [(rid, idx, t0, t1)] in day order. Take every k-th chunk (k = total speech / budget, rounded up) starting at a
    per-night offset (``ordinal`` = the night's day number), so consecutive nights see different chunks, until ``speech_budget_s`` is used."""
    total = sum(c[3] - c[2] for c in chunks)
    if total <= 0 or speech_budget_s <= 0:
        return []
    k = max(1, int(-(-total // speech_budget_s)))
    picked, used = [], 0.0
    for i in range(ordinal % k, len(chunks), k):
        d = chunks[i][3] - chunks[i][2]
        if used + d > speech_budget_s and picked:
            break
        picked.append(chunks[i])
        used += d
    return picked


# ── ledger + verdicts ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def ledger_path(home: Path) -> Path:
    return home / "ledger.jsonl"


def append_ledger(home: Path, night: str, per_model: dict, eval_models: Iterable[str] = ()) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = ledger_path(home)
    ev = set(eval_models)
    with open(p, "a", encoding="utf-8") as fh:
        for m, o in sorted(per_model.items()):
            fh.write(json.dumps({"night": night, "model": m, "eval": m in ev, **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in o.items()}}, sort_keys=True) + "\n")
    os.chmod(p, 0o600)


def read_ledger(home: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in ledger_path(home).read_text().splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def append_owner(home: Path, night: str, model: str, hit: int, miss: int) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = ledger_path(home)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"night": night, "model": model, "owner_hit": hit, "owner_miss": miss, "kind": "owner"}, sort_keys=True) + "\n")
    os.chmod(p, 0o600)


def summarise(rows: list[dict]) -> dict:
    s: dict[str, dict] = {}
    for r in rows:
        o = s.setdefault(r["model"], {"nights": set(), "speech_s": 0.0, "words": 0, "sole": 0, "invented": 0, "changed": 0, "cmp_words": 0, "agree_sum": 0.0, "agree_n": 0, "owner_hit": 0, "owner_miss": 0, "eval": False})
        if r.get("kind") == "owner":
            o["owner_hit"] += int(r.get("owner_hit") or 0)
            o["owner_miss"] += int(r.get("owner_miss") or 0)
            continue
        o["nights"].add(r["night"])
        o["speech_s"] += float(r.get("speech_s") or 0.0)
        o["words"] += int(r.get("words") or 0)
        o["sole"] += int(r.get("sole") or 0)
        o["invented"] += int(r.get("invented") or 0)
        o["changed"] += int(r.get("changed") or 0)
        o["agree_sum"] += float(r.get("agree_sum") or 0.0)
        o["agree_n"] += int(r.get("agree_n") or 0)
        o["eval"] = bool(r.get("eval"))
    return s


def verdicts(rows: list[dict], *, min_nights: int = 14, min_hours: float = 3.0, sole_bar: float = 0.01, invented_bar: float = 0.01) -> list[str]:
    """One plain line per model. A model is only called a DROP CANDIDATE after ``min_nights`` nights AND ``min_hours`` of sampled speech, when its sole-contributor rate is under
    ``sole_bar`` AND it adds invented words (>= ``invented_bar`` of its words). Never an action: the owner decides."""
    out = []
    for m, o in sorted(summarise(rows).items()):
        n, h = len(o["nights"]), o["speech_s"] / 3600.0
        sole = o["sole"] / o["words"] if o["words"] else 0.0
        inv = o["invented"] / o["words"] if o["words"] else 0.0
        agree = o["agree_sum"] / o["agree_n"] if o["agree_n"] else 0.0
        own = (f"; owner fixes {o['owner_hit']} hit / {o['owner_miss']} missed" if (o["owner_hit"] or o["owner_miss"]) else "")
        head = f"{m}: {sole * 100:.1f}% of its words were the only source of a final word, {inv * 100:.1f}% invented, agrees {agree * 100:.0f}% over {n} nights / {h:.1f} h{own}"
        if n < min_nights or h < min_hours:
            out.append(head + f" - not enough data yet (need {min_nights} nights and {min_hours:g} h)")
        elif sole < sole_bar and inv >= invented_bar:
            out.append(head + f" - below the {sole_bar * 100:g}% bar and it adds invented words: CANDIDATE TO DROP (owner decides)")
        elif sole < sole_bar:
            out.append(head + f" - below the {sole_bar * 100:g}% bar but adds no invented words: harmless, drop only to save time")
        else:
            out.append(head + " - earns its place: keep")
    return out


def contender_line(rows: list[dict], **kw: Any) -> str:
    """The weekly morning-report line (one NIGHT_JOB line, trimmed to the report's 300 characters)."""
    parts = verdicts(rows, **kw)
    return " | ".join(p.replace(" of its words were the only source of a final word", " sole").replace(" invented", " inv") for p in parts)[:900]


# ── per-chunk hypotheses (PRIVATE: they hold words) ──────────────────────────────────────────────────────────────────────────────────────

def hyps_dir(home: Path) -> Path:
    return home / "hyps"


def store_hyps(home: Path, night: str, rows: list[dict]) -> None:
    """rows: [{"u": user, "rid": id, "i": chunk index, "t0", "t1", "hyps": {model: text}}]. 0600, kept ``HYPS_KEEP_DAYS`` days (the audio's own retention)."""
    d = hyps_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    p = d / f"hyps-{night}.jsonl"
    with open(p, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    os.chmod(p, 0o600)


def prune_hyps(home: Path, today: Optional[date] = None, keep_days: int = HYPS_KEEP_DAYS) -> int:
    n = 0
    cut = (today or datetime.now(timezone.utc).date()) - timedelta(days=keep_days)
    d = hyps_dir(home)
    for p in (d.glob("hyps-*.jsonl") if d.exists() else []):
        try:
            if date.fromisoformat(p.stem.removeprefix("hyps-")) <= cut:
                p.unlink()
                n += 1
        except ValueError:
            continue
    sweep_work(home)
    return n


WORK_STALE_S = 6 * 3600          # a worker's manifest / output older than this belongs to a run that died (a night is ~2 h); live runs delete their own files


def sweep_work(home: Path, older_than_s: float = WORK_STALE_S, now: Optional[float] = None) -> int:
    """Delete the abandoned per-run work files (``work/<id>.manifest.json`` + ``.out.jsonl``: audio paths, raw transcripts, speaker embeddings) a SIGKILL / reboot left behind."""
    import time
    n, t = 0, (now if now is not None else time.time())
    d = home / "work"
    for p in (list(d.iterdir()) if d.is_dir() else []):
        try:
            if p.is_file() and (older_than_s <= 0 or t - p.stat().st_mtime >= older_than_s):
                p.unlink()
                n += 1
        except OSError:
            continue
    return n


def _rewrite(home: Path, fn: Callable[[dict], Optional[dict]]) -> int:
    n = 0
    d = hyps_dir(home)
    for p in (sorted(d.glob("hyps-*.jsonl")) if d.exists() else []):
        keep = []
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            new = fn(r)
            n += int(new is not r)
            if new is not None:
                keep.append(json.dumps(new))
        tmp = p.with_suffix(".tmp")
        tmp.write_text("\n".join(keep) + ("\n" if keep else ""))
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    sweep_work(home, 0)       # an erasure clears EVERY work file whatever its age: they hold the same words + voice embeddings and name no member, so none can be proven clean (scratch; a run that loses one retries)
    return n


def forget_user(home: Path, user_id: str) -> int:
    """The member is erased: every hypothesis line of theirs goes."""
    return _rewrite(home, lambda r: None if r.get("u") == user_id else r)


def _scrub(obj: Any, rx: "re.Pattern[str]", mark: str) -> Any:
    """Redact DECODED strings and dict keys (never the JSON text: ``json.dumps`` escapes non-ASCII, so a name such as an accented one would not match)."""
    if isinstance(obj, str):
        return rx.sub(mark, unicodedata.normalize("NFC", obj))       # composed and decomposed spellings of an accented name are the same name
    if isinstance(obj, dict):
        out: dict = {}
        for k, v in obj.items():
            nk = rx.sub(mark, k) if isinstance(k, str) else k
            if nk in out and isinstance(out[nk], (int, float)) and isinstance(v, (int, float)):
                out[nk] += v                                   # two forgotten spellings collapse onto the mark: keep the vote count
            else:
                out[nk] = _scrub(v, rx, mark)
        return out
    if isinstance(obj, list):
        return [_scrub(v, rx, mark) for v in obj]
    return obj


def erase_entity(home: Path, user_id: str, rx: "re.Pattern[str]", mark: str = "[forgotten]") -> int:
    """A forgotten name: it is replaced wherever a hypothesis of that member holds it (hypotheses, the ROVER pick AND the contested-slot picks / vote keys / who keys)."""
    rxn = re.compile(unicodedata.normalize("NFC", rx.pattern), rx.flags) if isinstance(getattr(rx, "pattern", None), str) else rx

    def fn(r: dict) -> Optional[dict]:
        if r.get("u") != user_id:
            return r
        before = {k: r.get(k) for k in ("hyps", "rover", "slots")}
        clean = _scrub(before, rxn, mark)
        if clean == before:
            return r
        return {**r, **clean}
    return _rewrite(home, fn)


def forget_recording(home: Path, user_id: str, rid: str) -> int:
    """A recording is deleted: every hypothesis / who-said line of THAT recording goes."""
    return _rewrite(home, lambda r: None if (r.get("u") == user_id and r.get("rid") == rid) else r)


def score_correction(fixed: str, hyps: dict, bar: float = 0.95) -> dict:
    """The owner fixed a line: which model's hypothesis already said it (hit: agreement with the fix >= ``bar``) and which did not (miss). The fix is GROUND TRUTH for the ledger."""
    return {m: ("hit" if agreement(fixed, t) >= bar else "miss") for m, t in hyps.items() if t is not None}


def note_owner_correction(home: Path, user_id: str, rid: str, start_ms: int, end_ms: int, fixed: str, night: Optional[str] = None) -> dict:
    """The seam for the day review's "fix this line": scores every model's stored hypothesis for the chunks that overlap the corrected span and appends the hits / misses to the ledger.
    Counts only reach the ledger; the corrected words are not stored here."""
    tally: dict[str, list] = {}
    d = hyps_dir(home)
    for p in (sorted(d.glob("hyps-*.jsonl")) if d.exists() else []):
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if (r.get("kind") or "transcript") != "transcript":
                continue                                    # a who_said / speaker-check row shares the store but holds no hypotheses: nothing to score
            if r.get("u") != user_id or r.get("rid") != rid or r["t1"] * 1000 <= start_ms or r["t0"] * 1000 >= end_ms:
                continue
            for m, v in score_correction(fixed, r.get("hyps") or {}).items():
                t = tally.setdefault(m, [0, 0])
                t[0 if v == "hit" else 1] += 1
    night = night or datetime.now(timezone.utc).date().isoformat()
    for m, (h, x) in tally.items():
        append_owner(home, night, m, h, x)
    return {m: {"hit": h, "miss": x} for m, (h, x) in tally.items()}
