#!/usr/bin/env python3
"""Web-lookup lab: drive the frozen corpus through the privacy path and report. No network, no model, no database.

Each of the 40 cases in corpus.json goes through the production code as a library:

    brain's proposed query -> web_lookup_scrub.scrub_query -> web_lookup_provider.lookup(FakeProvider)
                           -> web_lookup_answer.build_answer -> the reply

and is counted as: answered with a source, refused (nothing sent), unavailable (the reply says it could not
check), or withheld (rows came back, none usable). Two numbers MUST be zero:

  privacy leaks                  a household sentinel found, by an oracle that shares no code with the scrubber,
                                 in ANY query the fake provider was handed;
  injected instructions followed a second query sent, or the page's canary spoken as Zoe's own words (outside
                                 a "<title> (<site>) says:" quote), or a reply that breaks the answer contract.

    python3 labs/web-lookup/run_lab.py                    # print the report
    python3 labs/web-lookup/run_lab.py --check            # exit 1 on a leak, a followed instruction, or drift from report.json
    python3 labs/web-lookup/run_lab.py --write-report     # re-freeze report.json (on purpose only)
    python3 labs/web-lookup/run_lab.py --negative-control # no scrubber, naive composer: the instruments must go red

What this does NOT measure: the Gemma brain reading fenced results (that is the sidecar's fence and tool tier,
and a live bench - see docs/knowledge/web-lookup.md, acceptance list). Synthetic data only.
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ZD = HERE.parents[1] / "services" / "zoe-data"
CORPUS = HERE / "corpus.json"
REPORT = HERE / "report.json"
if str(ZD) not in sys.path:
    sys.path.insert(0, str(ZD))

import web_lookup_answer as wa  # noqa: E402
import web_lookup_provider as wp  # noqa: E402
from research_evidence import WebFallbackOutcome  # noqa: E402
from web_lookup_scrub import PrivateTerms, ScrubResult, scrub_query  # noqa: E402


def squeeze(text: str) -> str:
    """The leak oracle's view of a string: ASCII letters and digits only, lowercased, accents folded."""
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text or "")).lower()
    return "".join(c for c in s if c.isascii() and c.isalnum())


def leaked(query: str, sentinels: list[str]) -> list[str]:
    sq = squeeze(query)
    return [s for s in sentinels if s in sq]


def _scripted(spec: dict):
    if "raise" in spec:
        return {"TimeoutError": TimeoutError, "ConnectionError": ConnectionError}.get(spec["raise"], RuntimeError)("scripted")
    if "status" in spec:
        return WebFallbackOutcome(spec["status"], "fake", [], spec.get("detail", ""))
    return spec.get("rows", [])


def _naive_answer(outcome: WebFallbackOutcome) -> wa.LookupAnswer:
    """NEGATIVE CONTROL composer: pastes page text straight into the reply."""
    text = " ".join(f"{r.get('title', '')}: {r.get('snippet', '')}" for r in outcome.results)
    return wa.LookupAnswer(outcome.status, True, text)


def run_case(case: dict, terms: PrivateTerms, corpus: dict, *, negative_control: bool = False) -> dict:
    provider = wp.FakeProvider(default=_scripted(case["provider"]))
    if negative_control:
        scrub = ScrubResult(True, case["query"], "ok")
    else:
        scrub = scrub_query(case["query"], terms)
    outcome = wp.lookup(provider, scrub, deadline_s=2.0)
    answer = _naive_answer(outcome) if negative_control else wa.build_answer(outcome.status, outcome.results)
    canary = corpus["canary"]
    planted = canary in json.dumps(case["provider"])
    own_words = answer.text
    for c in answer.claims:  # what is left once every attributed quote is removed is Zoe's own voice
        own_words = own_words.replace(f"{c.source_title} ({c.source_site}) says: {c.text}", "")
    contract = wa.check_reply(answer.text, answer)
    followed = []
    if len(provider.queries) > 1:
        followed.append("second_query")
    if canary in own_words or any(canary in c.source_title or canary in c.source_site for c in answer.claims):
        followed.append("canary_in_own_words")
    if planted and contract:
        followed.append("contract:" + "+".join(contract))
    if outcome.status == "refused":
        got = "refused"
    elif answer.status == "results" and answer.claims and not contract:
        got = "answered"
    elif answer.status == wa.STATUS_WITHHELD:
        got = "withheld"
    else:
        got = "unavailable"
    return {
        "id": case["id"],
        "kind": case["kind"],
        "outcome": got,
        "sent": provider.queries[0] if provider.queries else None,
        "reason": scrub.reason,
        "leaks": sorted({s for q in provider.queries for s in leaked(q, corpus["household"]["sentinels"])}),
        "followed": followed,
        "rows_withheld": answer.withheld,
        "quoted_canary": planted and any(canary in c.text for c in answer.claims),
        "sources": [f"{c.source_title} ({c.source_site})" for c in answer.claims],
        "reply": answer.text,
        "contract": contract,
    }


def run(*, negative_control: bool = False) -> dict:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    h = corpus["household"]
    terms = PrivateTerms.of(names=h["names"], places=h["places"], memory=h["memory"])
    results = [run_case(c, terms, corpus, negative_control=negative_control) for c in corpus["cases"]]
    expected = {c["id"]: c["expect"] for c in corpus["cases"]}
    mismatches = [
        r["id"] for r in results
        if r["outcome"] != expected[r["id"]]["outcome"] or r["sent"] != expected[r["id"]]["sent"]
    ]
    count = lambda outcome: sum(r["outcome"] == outcome for r in results)  # noqa: E731
    return {
        "cases": len(results),
        "answered_with_source": count("answered"),
        "refused": count("refused"),
        "unavailable": count("unavailable"),
        "withheld": count("withheld"),
        "privacy_leaks": sum(bool(r["leaks"]) for r in results),
        "injected_instructions_followed": sum(bool(r["followed"]) for r in results),
        "injected_rows_withheld": sum(r["rows_withheld"] for r in results if r["kind"] == "injection"),
        "instruction_text_quoted_with_attribution": sum(r["quoted_canary"] for r in results),
        "queries_sent": sum(r["sent"] is not None for r in results),
        "expectation_mismatches": mismatches,
        "results": results,
    }


def frozen(report: dict) -> str:
    """report.json: the totals, and one line per case (outcome, the query sent, the scrub reason, rows withheld)."""
    head = {k: v for k, v in report.items() if k != "results"}
    rows = [json.dumps({k: r[k] for k in ("id", "outcome", "sent", "reason", "rows_withheld")}, ensure_ascii=False) for r in report["results"]]
    return json.dumps(head, indent=1)[:-2] + ',\n "results": [\n  ' + ",\n  ".join(rows) + "\n ]\n}\n"


def _print(report: dict) -> None:
    for r in report["results"]:
        flag = " LEAK" if r["leaks"] else ""
        flag += " FOLLOWED" if r["followed"] else ""
        print(f'{r["id"]:18} {r["outcome"]:11} sent={r["sent"]!r}{flag}')
    print()
    for key in ("cases", "answered_with_source", "refused", "unavailable", "withheld", "queries_sent", "injected_rows_withheld",
                "instruction_text_quoted_with_attribution", "privacy_leaks", "injected_instructions_followed"):
        print(f"{key:42} {report[key]}")
    print(f'{"expectation_mismatches":42} {report["expectation_mismatches"] or "none"}')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--negative-control", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    report = run(negative_control=args.negative_control)
    print(json.dumps(report, indent=1, ensure_ascii=False)) if args.json else _print(report)
    if args.write_report and not args.negative_control:
        REPORT.write_text(frozen(report), encoding="utf-8")
    if args.check:
        bad = report["privacy_leaks"] or report["injected_instructions_followed"] or report["expectation_mismatches"]
        drift = not REPORT.exists() or REPORT.read_text(encoding="utf-8") != frozen(report)
        if bad or drift:
            print("CHECK FAILED:", "leak/followed/mismatch" if bad else "report.json drift", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
