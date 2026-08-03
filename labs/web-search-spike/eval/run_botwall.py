#!/usr/bin/env python3
"""Score the extraction tiers on pages that REFUSE a plain client.

`run_eval.py` compares tier COMBINATIONS on general-knowledge queries. It
landed on Wikipedia, nothing blocked, and its own report says so: CloakBrowser
was scored purely as an extractor and its stealth value was *unmeasured*. This
harness is the missing experiment — one fixed corpus of deliberately hostile
URLs, every tier attempted against every URL, and the answer recorded per tier.

WHAT MAKES A RESULT TRUSTWORTHY HERE
------------------------------------
1. **Every tier is attempted on every URL, independently.** Not the fallback
   chain — the chain SHORT-CIRCUITS by design, so running it would leave the
   later tiers unmeasured on exactly the pages where the earlier ones worked.
   The chain is verified separately (`--chain`), on the same corpus.
2. **A refusal is recorded as a refusal**, with its status/marker, never folded
   into "no content". Same rule as `run_eval.py`.
3. **"Got content" is not the same as "got the page".** A bot wall renders
   perfectly and extracts to clean prose; length alone would score it a win.
   `meaningful()` therefore checks length AND the absence of wall markers AND
   the presence of page-shaped text — and it is reported alongside the raw
   length so a reader can disagree with the heuristic.

MEMORY DISCIPLINE — NOT OPTIONAL ON THIS BOX
--------------------------------------------
The Jetson runs the live voice brain (7.9 GB, mlocked) and Kokoro (2.2 GB).
Measured free memory during this run: 100-560 MB. CloakBrowser is ~553 MB per
launch. So:

  - the cloak pass is **strictly serial** — one Chromium at a time, always;
  - `free -m` is checked **before every launch** and the pass ABORTS rather
    than launching into less than `--min-free-mb`;
  - the whole harness is expected to run under
    `systemd-run --user --scope -p MemoryMax=1536M`, and warns when it is not.

Usage:
    python3 eval/run_botwall.py --tier httpx           # cheap, no browser
    python3 eval/run_botwall.py --tier jina
    systemd-run --user --scope -p MemoryMax=1536M -- \
        python3 eval/run_botwall.py --tier cloakbrowser
    python3 eval/run_botwall.py --report               # merge + render table
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from websearch.chain import BLOCKED, ERROR, OK, THIN, fetch_url  # noqa: E402
from websearch.direct import direct_fetch  # noqa: E402
from websearch.engines import BLOCK_MARKERS  # noqa: E402
from websearch.extract import jina_reader  # noqa: E402

CORPUS = HERE / "botwall-corpus.json"
RESULTS = HERE / "results"

#: A page that clears this is long enough to plausibly BE the page. Lower than
#: the chain's 600-char floor on purpose: this harness wants to SEE the thin
#: results and label them, not hide them behind the chain's policy.
MEANINGFUL_MIN_CHARS = 400

#: Phrases that mean "we rendered something, but it is not the page". Distinct
#: from `BLOCK_MARKERS` (which identify a challenge in raw HTML) because these
#: survive INTO extracted text.
NON_CONTENT_MARKERS = (
    "enable javascript",
    "javascript is disabled",
    "turn on javascript",
    "your browser is out of date",
    "unsupported browser",
    "are you a robot",
    "verify you are human",
    "checking your browser",
)


def meaningful(text: str) -> tuple[bool, str]:
    """(is_real_content, why). The heuristic, stated so it can be argued with."""
    stripped = text.strip()
    if len(stripped) < MEANINGFUL_MIN_CHARS:
        return False, f"only {len(stripped)} chars"
    low = stripped.lower()
    for marker in NON_CONTENT_MARKERS:
        if marker in low:
            return False, f"non-content marker: {marker!r}"
    for marker in BLOCK_MARKERS:
        if marker in low:
            return False, f"wall marker survived into text: {marker!r}"
    # A real page has sentences. A wall, a nav dump and a cookie banner are
    # mostly fragments, so require some sentence-ending punctuation OR a price.
    sentences = len(re.findall(r"[.!?](?:\s|$)", stripped))
    prices = len(re.findall(r"\$\s?\d", stripped))
    if sentences < 3 and prices == 0:
        return False, f"no prose and no price ({sentences} sentence marks)"
    return True, f"{len(stripped)} chars, {sentences} sentence marks, {prices} price tokens"


def target_hit(text: str, target: str | None) -> tuple[bool, str]:
    """Did we get THE PAGE, or merely A page?

    `meaningful()` is a generic heuristic and it OVERSTATES — measured on this
    corpus, Jina's read of the BWS product page scored 20,000 chars and 278
    price tokens while containing no product and no price, because the entire
    extract was navigation chrome and filter facets. Length plus dollar signs
    is not evidence of the answer.

    So each corpus entry names the string that must appear for the read to have
    achieved anything, and this is the column the verdict should be read from.
    """
    if not target:
        return True, "no target defined (SPA/control entry)"
    low = " ".join(text.split()).lower()
    if target.lower() not in low:
        return False, f"target {target!r} ABSENT — nav chrome, not the page"
    idx = low.find(target.lower())
    window = low[max(0, idx - 200): idx + 400]
    near = re.findall(r"\$\s?\d{1,4}(?:[.,]\d{2})?", window)
    if not near:
        return False, f"target {target!r} present but NO price within 600 chars of it"
    return True, f"target {target!r} found with prices nearby: {', '.join(near[:6])}"


def prices_in(text: str) -> list[str]:
    """Every `$nn.nn`-shaped token, deduped in order of appearance."""
    seen, out = set(), []
    for m in re.findall(r"\$\s?\d{1,4}(?:[.,]\d{2})?", text):
        norm = m.replace(" ", "")
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def free_mb() -> int:
    """Available memory, in MB. The launch gate."""
    try:
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemFree:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return -1


def in_memory_scope() -> bool:
    try:
        cg = pathlib.Path("/proc/self/cgroup").read_text()
    except OSError:
        return False
    return ".scope" in cg


def load_corpus() -> list[dict]:
    return json.loads(CORPUS.read_text())["urls"]


# --- per-tier attempts ------------------------------------------------------

def attempt_httpx(url: str, target: str | None) -> dict:
    try:
        page = direct_fetch(url)
    except Exception as exc:  # noqa: BLE001
        return _refusal(exc)
    return _served(page, target)


def attempt_jina(url: str, target: str | None) -> dict:
    try:
        page = jina_reader(url)
    except Exception as exc:  # noqa: BLE001
        return _refusal(exc)
    return _served(page, target)


def attempt_cloak(url: str, target: str | None, *, settle: bool = True) -> dict:
    from websearch.cloak import cloak_fetch

    try:
        page = cloak_fetch(url) if settle else cloak_fetch(url, settle=None)
    except Exception as exc:  # noqa: BLE001
        return _refusal(exc)
    return _served(page, target)


#: Full extracted text is written here, not into the store — a 20 kB body per
#: URL per tier would make the JSON unreviewable, and the price mining in
#: Part C needs the WHOLE text, not a 260-char sample.
TEXT_DIR = RESULTS / "botwall-text"


def _served(page, target: str | None) -> dict:
    ok, why = meaningful(page.text)
    hit, hit_why = target_hit(page.text, target)
    return {
        "verdict": OK if ok else THIN,
        "chars": len(page.text.strip()),
        "meaningful": ok,
        "target_hit": hit,
        "target_why": hit_why,
        "why": why,
        "elapsed_s": round(page.elapsed_s, 2),
        "detail": page.detail,
        "title": (page.title or "")[:90],
        "prices": prices_in(page.text)[:12],
        "sample": " ".join(page.text.split())[:260],
        "_text": page.text,
    }


def _refusal(exc: Exception) -> dict:
    from websearch.chain import _verdict_for

    verdict, detail = _verdict_for(exc)
    return {
        "verdict": verdict,
        "chars": 0,
        "meaningful": False,
        "target_hit": False,
        "target_why": "tier refused — nothing to search",
        "why": detail,
        "elapsed_s": 0.0,
        "detail": detail,
        "title": "",
        "prices": [],
        "sample": "",
    }


# --- passes -----------------------------------------------------------------

def run_tier(tier: str, *, min_free_mb: int, settle: bool = True) -> dict:
    corpus = load_corpus()
    if tier == "cloakbrowser" and not in_memory_scope():
        print(
            "WARNING: not running inside a systemd scope. Re-run under\n"
            "  systemd-run --user --scope -p MemoryMax=1536M -- ...\n",
            file=sys.stderr,
        )
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict] = {}
    for entry in corpus:
        url, uid = entry["url"], entry["id"]
        target = entry.get("target")
        if tier == "cloakbrowser":
            avail = free_mb()
            if avail >= 0 and avail < min_free_mb:
                print(f"ABORT before {uid}: only {avail} MB free (< {min_free_mb})", file=sys.stderr)
                out[uid] = {
                    "verdict": ERROR,
                    "chars": 0, "meaningful": False, "target_hit": False,
                    "target_why": "not attempted",
                    "why": f"not attempted — {avail} MB free, below the {min_free_mb} MB launch gate",
                    "elapsed_s": 0.0, "detail": "", "title": "", "prices": [], "sample": "",
                }
                continue
            print(f"  [{uid}] free={avail}MB launching...", file=sys.stderr, flush=True)
            result = attempt_cloak(url, target, settle=settle)
        elif tier == "jina":
            result = attempt_jina(url, target)
        else:
            result = attempt_httpx(url, target)
        result["url"] = url
        body = result.pop("_text", "")
        if body:
            (TEXT_DIR / f"{uid}.{tier}{'-nosettle' if not settle else ''}.txt").write_text(
                body, encoding="utf-8"
            )
        out[uid] = result
        print(
            f"  [{uid}] {tier}: {result['verdict']:8s} {result['chars']:6d} chars"
            f"  target={'HIT' if result['target_hit'] else 'miss'}  {result['target_why'][:60]}",
            file=sys.stderr, flush=True,
        )
    return out


def run_chain(*, min_free_mb: int, use_accept: bool = True) -> dict:
    """The real fallback chain over the same corpus — end-to-end verification.

    `use_accept=False` reproduces the FLOOR-ONLY behaviour, which is the
    control that showed the floor is insufficient: with it off,
    `cellarbrations-geraldton` is served by httpx at 759 chars containing no
    price, and the chain never reaches the tier that had one.
    """
    out: dict[str, dict] = {}
    for entry in load_corpus():
        if free_mb() < min_free_mb:
            print(f"ABORT chain before {entry['id']}: low memory", file=sys.stderr)
            break
        target = entry.get("target")
        accept = None
        if use_accept and target:
            accept = lambda t, _tgt=target: _tgt.lower() in " ".join(t.split()).lower()
        res = fetch_url(entry["url"], use_jina=False, accept=accept)
        out[entry["id"]] = {
            **res.to_dict(),
            "trail": res.trail(),
            "prices": prices_in(res.text)[:12],
        }
        print(f"  [{entry['id']}] chain -> {res.tier_served or 'NONE'} | {res.trail()[:120]}",
              file=sys.stderr, flush=True)
    return out


# --- report -----------------------------------------------------------------

_MARK = {OK: "PASS", THIN: "thin", BLOCKED: "BLOCKED", ERROR: "error"}


def _cell(r: dict | None) -> str:
    if r is None:
        return "—"
    mark = _MARK.get(r["verdict"], r["verdict"])
    if r["verdict"] in (BLOCKED, ERROR):
        return f"**{mark}**<br>{r['why'][:40]}"
    tick = "TARGET" if r.get("target_hit") else "no-target"
    return f"{mark} {r['chars']}c<br>{tick}"


def render(store: dict) -> str:
    corpus = {e["id"]: e for e in load_corpus()}
    tiers = [t for t in ("httpx", "jina", "cloakbrowser", "cloakbrowser-nosettle") if t in store]
    lines = [
        "# Bot-wall corpus — CloakBrowser scored at its actual job",
        "",
        f"- Run: `{store.get('run_id', '?')}`  ·  corpus v{store.get('corpus_version', 1)}"
        f"  ·  {len(corpus)} URLs  ·  {len(tiers)} tiers, each attempted INDEPENDENTLY on every URL",
        "",
        "## How to read this",
        "",
        "**Two columns, and the second is the one that matters.**",
        "",
        "- `PASS/thin/BLOCKED/error` is the tier's own outcome. `BLOCKED` means it",
        "  was REFUSED (status or challenge body); `thin` means something came back",
        "  but it is not the page; `error` is a transport/render failure.",
        "- `TARGET` / `no-target` is whether the extracted text actually contained",
        "  the thing we went for — the product name with a price beside it.",
        "",
        "> **`PASS` without `TARGET` is the interesting failure, and it is common.**",
        "> Measured here: Jina read the BWS product page at 20,000 characters with",
        "> 278 dollar-signs and still did not contain the product's price — the whole",
        "> extract was navigation chrome and price-range filter facets. A length-based",
        "> score calls that a win. It is not one. Any harness that reports only size",
        "> will systematically overstate the weakest tier on the hardest pages.",
        "",
        "| id | site | expect | " + " | ".join(tiers) + " | verdict |",
        "|---|---|---|" + "---|" * (len(tiers) + 1),
    ]
    for uid, entry in corpus.items():
        cells = [_cell(store.get(t, {}).get(uid)) for t in tiers]
        cheap = store.get("httpx", {}).get(uid) or {}
        jina = store.get("jina", {}).get(uid) or {}
        cloak = store.get("cloakbrowser", {}).get(uid) or {}
        cheap_won = cheap.get("target_hit") and cheap.get("verdict") == OK
        jina_won = jina.get("target_hit") and jina.get("verdict") == OK
        cloak_won = cloak.get("target_hit") and cloak.get("verdict") == OK
        if cheap_won:
            verdict = "cheap tier sufficed — no browser needed"
        elif cloak_won and not jina_won:
            verdict = "**CloakBrowser ONLY**"
        elif cloak_won and jina_won:
            verdict = "cloak + jina both got it"
        elif jina_won:
            verdict = "jina only"
        elif cloak.get("verdict") == OK:
            verdict = "rendered, but no price on the page"
        else:
            verdict = "**nothing got it**"
        lines.append(
            f"| `{uid}` | {entry['chain']} | {entry['expect']} | " + " | ".join(cells) + f" | {verdict} |"
        )

    if "cloakbrowser-nosettle" in store:
        lines += [
            "", "## Settle-wait A/B — is the post-load wait doing anything?", "",
            "Identical corpus, identical tier, the ONLY variable being the",
            "`SettlePolicy`. This is the negative control for PR #1626's settle:",
            "if the two columns match everywhere, the wait is pure cost.", "",
            "| id | with settle | without settle | delta |", "|---|---|---|---|",
        ]
        for uid in corpus:
            a = store.get("cloakbrowser", {}).get(uid) or {}
            b = store.get("cloakbrowser-nosettle", {}).get(uid) or {}
            da, db = a.get("chars", 0), b.get("chars", 0)
            if a.get("verdict") != b.get("verdict"):
                delta = f"**{b.get('verdict')} -> {a.get('verdict')}**"
            elif da and db and abs(da - db) / max(da, db) > 0.15:
                delta = f"**{da - db:+d} chars ({(da - db) / max(db, 1):+.0%})**"
            else:
                delta = "no material difference"
            lines.append(
                f"| `{uid}` | {a.get('verdict', '—')} {da}c | {b.get('verdict', '—')} {db}c | {delta} |"
            )

    lines += ["", "## Per-URL detail", ""]
    for uid, entry in corpus.items():
        lines.append(f"### `{uid}` — {entry['chain']}")
        lines.append("")
        lines.append(f"`{entry['url']}`")
        lines.append("")
        lines.append(f"> {entry['note']}")
        lines.append("")
        for tier in tiers:
            r = store.get(tier, {}).get(uid)
            if r is None:
                continue
            lines.append(
                f"- **{tier}** — `{r['verdict']}`, {r['chars']} chars, {r['elapsed_s']}s"
            )
            lines.append(f"    - heuristic: {r['why']}")
            lines.append(f"    - target: {r.get('target_why', 'n/a')}")
            if r.get("detail"):
                lines.append(f"    - tier detail: `{r['detail'][:150]}`")
            if r.get("sample"):
                lines.append(f"    - sample: `{r['sample'][:200]}`")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["httpx", "jina", "cloakbrowser"])
    ap.add_argument("--no-settle", action="store_true",
                    help="cloakbrowser without the post-load settle (the A/B control)")
    ap.add_argument("--chain", action="store_true", help="run the real fallback chain")
    ap.add_argument("--no-accept", action="store_true",
                    help="chain with the length floor ONLY — the control showing it is insufficient")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--min-free-mb", type=int, default=380)
    ap.add_argument("--store", default=str(RESULTS / "botwall-store.json"))
    args = ap.parse_args()

    store_path = pathlib.Path(args.store)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = json.loads(store_path.read_text()) if store_path.is_file() else {}
    store.setdefault("run_id", dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    store["corpus_version"] = json.loads(CORPUS.read_text())["version"]

    if args.tier:
        key = "cloakbrowser-nosettle" if (args.tier == "cloakbrowser" and args.no_settle) else args.tier
        store[key] = run_tier(args.tier, min_free_mb=args.min_free_mb, settle=not args.no_settle)
        store_path.write_text(json.dumps(store, indent=2))
    if args.chain:
        key = "chain-flooronly" if args.no_accept else "chain"
        store[key] = run_chain(min_free_mb=args.min_free_mb, use_accept=not args.no_accept)
        store_path.write_text(json.dumps(store, indent=2))
    if args.report:
        out = RESULTS / f"botwall-{store['run_id']}.md"
        out.write_text(render(store))
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
