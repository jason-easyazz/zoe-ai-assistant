# web-lookup lab

Lab proof for the privacy-first web lookup (`ZOE_WEB_SEARCH_TOOL`, default off). Operator doc and the acceptance
list: [docs/knowledge/web-lookup.md](../../docs/knowledge/web-lookup.md).

`run_lab.py` drives a FROZEN corpus of 40 questions (`corpus.json`) through the production code, called as a
library, with a scripted fake provider: the brain's proposed query -> `web_lookup_scrub.scrub_query` ->
`web_lookup_provider.lookup(FakeProvider)` -> `web_lookup_answer.build_answer`. No network, no model, no database.
Everything in the corpus is invented; sites are reserved `example` domains.

```bash
python3 labs/web-lookup/run_lab.py                     # the report
python3 labs/web-lookup/run_lab.py --check             # exit 1 on a leak, a followed instruction, or drift from report.json
python3 labs/web-lookup/run_lab.py --negative-control  # no scrubber, naive composer: must go red
python3 labs/web-lookup/run_lab.py --json              # every reply, source and sent query
```

## Result (frozen in `report.json`, 2026-11-14)

| | |
|---|---|
| questions | 40 |
| answered with a source | 27 |
| refused, nothing sent | 10 |
| unavailable (the reply says it could not check) | 2 |
| withheld (rows came back, none usable) | 1 |
| **privacy leaks** | **0** |
| **injected instructions followed** | **0** |
| injected rows withheld | 5 |
| instruction text quoted with attribution | 1 |

Negative control (same corpus, scrubber and composer removed): 15 leaking cases, 5 of 5 injections followed.

## How the two zeros are measured

- **Privacy leak**: the fake provider records every query it is handed. An oracle that shares no code with the
  scrubber (letters and digits only, lowercased, accents folded, then a substring search for the household's
  sentinels) is run over every one. 15 of the 40 proposed queries carry a sentinel on the way in; 3 more carry
  private content the oracle has no sentinel for (an unknown friend's name, a street address, an encoded blob).
- **Injected instruction followed**: five cases return page text carrying a canary and an instruction. A case
  counts as followed if a second query is sent, if the canary appears in Zoe's own words (anything outside a
  `<title> (<site>) says:` quote, or in a source title or site), or if the reply breaks the answer contract.

## What this does not show

- `injection-05` is an instruction the cue list does not recognise. It is quoted, attributed to its site, as page
  text. Nothing acted on it, but Zoe would say it aloud as a quote. Cue matching is evadable by paraphrase; the
  guarantee that holds is structural (the composed answer cannot act, search again or speak page text as its own).
- No model is in the loop. Whether the Gemma brain obeys text inside the sidecar's fence is not measured here.
- The scrubber is given the household's names, places and memory texts by the corpus. At runtime the endpoint
  feeds it member and contact names only (see the acceptance list).

Regression net: `services/zoe-data/tests/test_web_lookup_lab.py` (ci_safe) pins the report and the negative control.
