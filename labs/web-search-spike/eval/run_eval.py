#!/usr/bin/env python3
"""Free-tier COMBINATION comparison harness — the instrument, not the answer.

Operator-triggered, never CI. It runs the fixed corpus against each available
FREE combination and records what actually happened, so the later free-vs-paid
decision is made on data rather than on paper reasoning.

    python3 eval/run_eval.py --list                     # combos + tier health
    python3 eval/run_eval.py --combo scrapers           # one combo, full corpus
    python3 eval/run_eval.py --all --limit 5            # smoke run, 5 queries
    python3 eval/run_eval.py --all                      # the real run
    python3 eval/run_eval.py --report results/<stamp>.json

DESIGN RULES (these are the point of the harness):

* **A blocked tier must report BLOCKED.** Never a silently shorter result list.
  Every query records `status` in {ok, empty, blocked, unconfigured, error} and
  the runner refuses to average a blocked run into a score.
* **The automatic score is a smoke signal, not a verdict.** Keyword hit-rate
  and cross-combination overlap are cheap proxies; they catch "this combo
  returned garbage", not "this combo is better". The operator's manual sheet
  (`--report` output) is where the real judgement is recorded.
* **Live calls are modest and budgeted.** Tavily's free tier is 33/day, so a
  full 25-query corpus run costs most of a day's allowance — the runner refuses
  to start a Tavily combo it cannot afford, and says so.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import statistics
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from websearch import tavily
from websearch.engines import EnginesBlocked
from websearch.engines import search as ddgs_search
from websearch.extract import ExtractUnavailable, cloakbrowser_available, jina_reader
from websearch.merge import consensus_merge
from websearch.packet import estimate_tokens, format_packet
from websearch.scrapers import scrape

HERE = pathlib.Path(__file__).parent
CORPUS = HERE / "corpus.json"
RESULTS = HERE / "results"

# Wikipedia's own search, used as the "scrapers" combo's discovery step: the
# structured tier needs a URL, and this is the free, never-blocked way to get
# one without an engine.
WIKI_SEARCH = "https://en.wikipedia.org/w/api.php"


# --- combinations ----------------------------------------------------------

def _wiki_search_urls(query: str, limit: int = 3) -> list[str]:
    import httpx

    from websearch.engines import UA_POLITE

    resp = httpx.get(
        WIKI_SEARCH,
        params={"action": "query", "format": "json", "list": "search", "srsearch": query, "srlimit": limit},
        headers={"User-Agent": UA_POLITE},
        timeout=10.0,
        trust_env=False,
    )
    resp.raise_for_status()
    hits = resp.json().get("query", {}).get("search", [])
    return [f"https://en.wikipedia.org/wiki/{h['title'].replace(' ', '_')}" for h in hits]


def combo_scrapers(query: str) -> dict:
    """Tier 0 alone: Wikipedia search -> structured extract. No engine."""
    urls = _wiki_search_urls(query)
    if not urls:
        return {"status": "empty", "results": [], "packet": ""}
    extract = scrape(urls[0])
    if extract is None:
        return {"status": "empty", "results": [], "packet": ""}
    packet = format_packet([], extract=extract)
    return {"status": "ok", "results": [{"title": extract.title, "url": extract.url}], "packet": packet}


def combo_ddgs(query: str) -> dict:
    results = ddgs_search(query, limit=6)
    if not results:
        return {"status": "empty", "results": [], "packet": ""}
    return {
        "status": "ok",
        "results": [{"title": r.title, "url": r.url} for r in results],
        "packet": format_packet(results),
    }


def combo_tavily(query: str) -> dict:
    results = tavily.search(query, limit=6)
    if not results:
        return {"status": "empty", "results": [], "packet": ""}
    return {
        "status": "ok",
        "results": [{"title": r.title, "url": r.url} for r in results],
        "packet": format_packet(results),
    }


def combo_ddgs_plus_scrapers(query: str) -> dict:
    """The realistic free default today: metasearch + structured enrichment."""
    results = ddgs_search(query, limit=6)
    if not results:
        return {"status": "empty", "results": [], "packet": ""}
    extract = scrape(results[0].url)
    return {
        "status": "ok",
        "results": [{"title": r.title, "url": r.url} for r in results],
        "packet": format_packet(results, extract=extract),
        "enriched": extract is not None,
    }


def combo_tavily_plus_scrapers(query: str) -> dict:
    results = tavily.search(query, limit=6)
    if not results:
        return {"status": "empty", "results": [], "packet": ""}
    extract = scrape(results[0].url)
    return {
        "status": "ok",
        "results": [{"title": r.title, "url": r.url} for r in results],
        "packet": format_packet(results, extract=extract),
        "enriched": extract is not None,
    }


def combo_all_free(query: str) -> dict:
    """Every configured free tier, consensus-merged across tiers."""
    batches = []
    notes = []
    try:
        batches.append(ddgs_search(query, limit=6))
    except EnginesBlocked as exc:
        notes.append(f"ddgs blocked: {exc}")
    if tavily.configured():
        try:
            batches.append(tavily.search(query, limit=6))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"tavily: {type(exc).__name__}: {exc}")
    else:
        notes.append("tavily unconfigured")
    batches = [b for b in batches if b]
    if not batches:
        return {"status": "blocked" if notes else "empty", "results": [], "packet": "", "notes": notes}
    merged = consensus_merge(batches, limit=6)
    extract = scrape(merged[0].url)
    return {
        "status": "ok",
        "results": [{"title": r.title, "url": r.url, "tiers": r.engines} for r in merged],
        "packet": format_packet(merged, extract=extract),
        "notes": notes,
    }


def combo_jina_extract(query: str) -> dict:
    """Page-extract tier, measured on a URL the free engines found."""
    try:
        results = ddgs_search(query, limit=3)
    except EnginesBlocked as exc:
        raise EnginesBlocked(f"jina combo needs a URL and ddgs is blocked: {exc}") from exc
    if not results:
        return {"status": "empty", "results": [], "packet": ""}
    page = jina_reader(results[0].url)
    return {
        "status": "ok",
        "results": [{"title": results[0].title, "url": results[0].url}],
        "packet": page.text[:1200],
        "extract_chars": len(page.text),
        "truncated": page.truncated,
    }


COMBOS = {
    "scrapers": (combo_scrapers, "Tier 0 alone — Wikipedia search + structured extract"),
    "ddgs": (combo_ddgs, "ddgs metasearch (18 engines) alone"),
    "tavily-free": (combo_tavily, "Tavily free tier alone (33/day cap)"),
    "ddgs+scrapers": (combo_ddgs_plus_scrapers, "ddgs + structured enrichment"),
    "tavily+scrapers": (combo_tavily_plus_scrapers, "Tavily free + structured enrichment"),
    "all-free": (combo_all_free, "Every configured free tier, consensus-merged"),
    "jina-extract": (combo_jina_extract, "Jina Reader page extraction (enrichment tier)"),
}

NEEDS_TAVILY = {"tavily-free", "tavily+scrapers"}


# --- scoring ---------------------------------------------------------------

def keyword_score(payload: dict, expect: list[str]) -> float:
    """Crude: fraction of expected terms appearing anywhere in the output.

    A SMOKE SIGNAL. It cannot tell a good answer from a keyword-stuffed one;
    it reliably catches an empty or off-topic combination, which is its job.
    """
    if not expect:
        return 0.0
    blob = json.dumps(payload.get("results", [])).lower() + " " + str(payload.get("packet", "")).lower()
    return sum(1 for term in expect if term.lower() in blob) / len(expect)


def run_combo(name: str, queries: list[dict], delay_s: float) -> dict:
    fn, description = COMBOS[name]
    rows = []
    for i, item in enumerate(queries):
        started = time.monotonic()
        row = {"id": item["id"], "type": item["type"], "q": item["q"]}
        try:
            payload = fn(item["q"])
            row.update(payload)
            row["score"] = keyword_score(payload, item.get("expect", []))
        except EnginesBlocked as exc:
            row.update({"status": "blocked", "error": str(exc)[:200], "score": None})
        except tavily.TavilyUnconfigured as exc:
            row.update({"status": "unconfigured", "error": str(exc)[:200], "score": None})
        except tavily.TavilyBudgetExhausted as exc:
            row.update({"status": "budget-exhausted", "error": str(exc)[:200], "score": None})
        except ExtractUnavailable as exc:
            row.update({"status": "blocked", "error": str(exc)[:200], "score": None})
        except Exception as exc:  # noqa: BLE001
            row.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"[:200], "score": None})
        row["elapsed_s"] = round(time.monotonic() - started, 3)
        # Keep the packet in the record but never let it dominate the file.
        if isinstance(row.get("packet"), str):
            row["packet"] = row["packet"][:1500]
        rows.append(row)
        print(f"  [{name}] {item['id']:<10} {row['status']:<16} {row['elapsed_s']:>6.2f}s "
              f"score={row['score'] if row['score'] is not None else '--'}")
        if delay_s and i < len(queries) - 1:
            time.sleep(delay_s)

    scored = [r["score"] for r in rows if r["score"] is not None]
    statuses = [r["status"] for r in rows]
    return {
        "combo": name,
        "description": description,
        "n": len(rows),
        "ok": statuses.count("ok"),
        "blocked": statuses.count("blocked"),
        "empty": statuses.count("empty"),
        "errors": statuses.count("error"),
        "unconfigured": statuses.count("unconfigured"),
        # Deliberately None rather than 0.0 when nothing succeeded — a blocked
        # combo has NO score, and averaging it to zero would silently rank it
        # as "bad" rather than "unmeasured".
        "mean_score": round(statistics.mean(scored), 3) if scored else None,
        "median_latency_s": round(statistics.median([r["elapsed_s"] for r in rows]), 3) if rows else None,
        "rows": rows,
    }


def cross_combo_overlap(report: dict) -> dict:
    """Per-query URL agreement between combinations — a consensus proxy."""
    by_query: dict[str, dict[str, set]] = {}
    for combo in report["combos"]:
        for row in combo["rows"]:
            if row["status"] != "ok":
                continue
            urls = {r.get("url", "") for r in row.get("results", []) if r.get("url")}
            by_query.setdefault(row["id"], {})[combo["combo"]] = urls
    out = {}
    for qid, per_combo in by_query.items():
        names = sorted(per_combo)
        if len(names) < 2:
            continue
        pairs = {}
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                union = per_combo[a] | per_combo[b]
                pairs[f"{a}|{b}"] = round(len(per_combo[a] & per_combo[b]) / len(union), 3) if union else 0.0
        out[qid] = pairs
    return out


# --- reporting -------------------------------------------------------------

def write_manual_sheet(report: dict, path: pathlib.Path) -> None:
    """The markdown the OPERATOR fills in. This is the real scoring surface."""
    lines = [
        "# Free-tier combination comparison — manual review sheet",
        "",
        f"- Run: `{report['stamp']}`  ·  corpus v{report['corpus_version']}  ·  {report['n_queries']} queries",
        f"- Tier health at run time: `{json.dumps(report['tier_status'])}`",
        "",
        "**How to use this.** The automatic `score` is a keyword smoke signal only —",
        "it catches empty/off-topic output, it does not rank answer quality. Fill in",
        "the Verdict column yourself; that is the data the free-vs-paid decision uses.",
        "",
        "## Automatic summary",
        "",
        "| combo | ok | blocked | empty | err | mean score | median latency |",
        "|---|---|---|---|---|---|---|",
    ]
    for combo in report["combos"]:
        score = combo["mean_score"]
        lines.append(
            f"| `{combo['combo']}` | {combo['ok']} | {combo['blocked']} | {combo['empty']} | "
            f"{combo['errors']} | {score if score is not None else '**unmeasured**'} | "
            f"{combo['median_latency_s']}s |"
        )

    lines += ["", "## Per-query verdicts (operator fills Verdict + Notes)", ""]
    for combo in report["combos"]:
        lines += [f"### `{combo['combo']}` — {combo['description']}", "",
                  "| id | type | query | status | score | latency | Verdict (good/ok/bad) | Notes |",
                  "|---|---|---|---|---|---|---|---|"]
        for row in combo["rows"]:
            score = row["score"] if row["score"] is not None else "--"
            lines.append(
                f"| {row['id']} | {row['type']} | {row['q'][:40]} | {row['status']} | {score} | "
                f"{row['elapsed_s']}s |  |  |"
            )
        lines.append("")

    lines += ["## Decision", "",
              "- Best free combination for OPEN LOOKUP: ", "- Best free combination for CLAIM CHECK: ",
              "- Is any paid tier worth revisiting, and on what evidence: ", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--combo", action="append", help="combination to run (repeatable)")
    parser.add_argument("--all", action="store_true", help="run every available combination")
    parser.add_argument("--list", action="store_true", help="list combinations + tier health")
    parser.add_argument("--limit", type=int, help="only the first N corpus queries (smoke run)")
    parser.add_argument("--type", help="only queries of this type")
    parser.add_argument("--delay", type=float, default=2.0, help="seconds between queries (rate-limit courtesy)")
    parser.add_argument("--report", help="re-render the manual sheet from an existing results JSON")
    args = parser.parse_args()

    if args.report:
        report = json.loads(pathlib.Path(args.report).read_text())
        out = pathlib.Path(args.report).with_suffix(".md")
        write_manual_sheet(report, out)
        print(f"wrote {out}")
        return 0

    import websearch as ws

    status = ws.tier_status()
    if args.list:
        print("Tier health:")
        for tier, state in status.items():
            print(f"  {tier:<14} {state}")
        print(f"  cloakbrowser   {'installed' if cloakbrowser_available() else 'absent'}"
              "  (broker returns SCREENSHOTS only — no page text; see extract.py)")
        print("\nCombinations:")
        for name, (_fn, desc) in COMBOS.items():
            blocked = " [needs TAVILY_API_KEY]" if name in NEEDS_TAVILY and not tavily.configured() else ""
            print(f"  {name:<18} {desc}{blocked}")
        return 0

    corpus = json.loads(CORPUS.read_text())
    queries = corpus["queries"]
    if args.type:
        queries = [q for q in queries if q["type"] == args.type]
    if args.limit:
        queries = queries[: args.limit]

    names = list(COMBOS) if args.all else (args.combo or [])
    if not names:
        parser.error("pass --combo NAME (repeatable), --all, or --list")

    runnable = []
    for name in names:
        if name not in COMBOS:
            parser.error(f"unknown combo {name!r}; see --list")
        if name in NEEDS_TAVILY and not tavily.configured():
            print(f"SKIP {name}: TAVILY_API_KEY unset (recorded as unconfigured, NOT as a zero score)")
            continue
        if name in NEEDS_TAVILY:
            budget = tavily.budget_state()
            if budget.remaining < len(queries):
                print(f"SKIP {name}: needs {len(queries)} searches, {budget.remaining}/{budget.limit} left today")
                continue
        runnable.append(name)

    if not runnable:
        print("nothing runnable — see the skip reasons above")
        return 1

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "stamp": stamp,
        "corpus_version": corpus["version"],
        "n_queries": len(queries),
        "tier_status": status,
        "combos": [],
    }
    for name in runnable:
        print(f"\n=== {name} ({len(queries)} queries) ===")
        report["combos"].append(run_combo(name, queries, args.delay))

    report["cross_combo_overlap"] = cross_combo_overlap(report)

    RESULTS.mkdir(parents=True, exist_ok=True)
    out_json = RESULTS / f"{stamp}.json"
    out_json.write_text(json.dumps(report, indent=1), encoding="utf-8")
    write_manual_sheet(report, out_json.with_suffix(".md"))

    print(f"\nwrote {out_json}")
    print(f"wrote {out_json.with_suffix('.md')}  <- fill in the Verdict column")
    for combo in report["combos"]:
        score = combo["mean_score"]
        print(f"  {combo['combo']:<18} ok={combo['ok']}/{combo['n']} blocked={combo['blocked']} "
              f"score={score if score is not None else 'UNMEASURED'} median={combo['median_latency_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
