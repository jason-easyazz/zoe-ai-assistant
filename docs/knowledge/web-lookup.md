---
type: Reference
title: Web lookup (ZOE_WEB_SEARCH_TOOL) - look it up, say where it came from, send nothing private
description: The privacy-first lookup path behind the flag-dark web_search tool - the query scrubber, the provider seam with its fake and its one real adapter, the answer contract, the lab result on the frozen 40-question corpus, the known limits, and the acceptance list that must hold before the flag is switched on.
tags: [web-lookup, privacy, prompt-injection, flag-dark, zoe-data, lab]
timestamp: 2026-11-14T00:00:00Z
---

# Web lookup (`ZOE_WEB_SEARCH_TOOL`, default off)

Owner ask: when Zoe is asked "are you sure?" or for a live fact, she can look it up and say where it came from.
A lookup is the one place a turn's words leave the house, so the path is built privacy-first and stays dark until the
acceptance list below holds. With the flag off, `POST /api/system/web-search` answers 404 and none of this runs.

## The path

| Step | Code (`services/zoe-data/`) | Rule |
|---|---|---|
| 1. names | `web_lookup_terms.household_private_terms()` | every member and contact name, household-wide, cached 60 s; unreadable means the lookup is refused |
| 2. scrub | `web_lookup_scrub.scrub_query(raw, PrivateTerms)` | pure; returns the minimal keyword query or a refusal; **when in doubt, do not send** |
| 3. send | `web_lookup_provider.lookup(provider, ScrubResult)` | the only door out; takes the scrubber's result, never a string; hard deadline; rows capped |
| 4. screen | `web_lookup_answer.screen_rows(rows)` | a row that reads as an instruction to the assistant is withheld before the brain sees it |
| 5. answer | `web_lookup_answer.build_answer` / `check_reply` | every claim carries a title and a site; the reply says it looked it up; page text is data |

The brain's side is unchanged and lives in the sidecar (`labs/flue-zoe-brain-2x/src/untrusted-content.ts`): results are
fenced, and a turn that received results can change nothing and cannot search again.

### What the scrubber strips, and what it refuses

Stripped: known names and places however they are spelled (case, possessive, accents, dotted, hyphenated, zero-width
characters, full-width letters, glued together or to a number, digits written for letters; each half of a hyphenated
name); a name after a relation word ("my friend Dana"); memory
(a run of two or more content words shared with a memory text, its numbers, its mid-sentence capitalised words);
e-mail addresses, phone and other long numbers, coordinates, street addresses; people-pointing and filler words.

Refused (nothing is sent): a link, an encoded-looking token, letters spelled out one by one, a word that mixes
look-alike alphabets (a Cyrillic letter inside a Latin name), a query over 300
characters, a query that is mostly memory, a query with too little left after stripping, or a final re-check that still
finds a private word. Reason codes: `empty`, `too_long`, `link`, `encoded`, `spelled_out`, `mixed_script`, `memory_heavy`,
`private_only`, `tripwire`, and from the endpoint `private_names_unreadable`.

The result carries how many things were removed and of which kind, never the things. The raw query is never logged.
Every word list is in `lexicons_data/web_lookup_<lang>.json`; a language with no file has structural rules only.

### Provider seam

- `FakeProvider`: scripted, records every query handed to it. Tests and the lab use nothing else.
- `LiveProvider`: the one real adapter, `research_evidence.fetch_web_fallback` (Tavily when keyed, else DuckDuckGo).
  Returned by `provider_for_env()` only while the flag is on; otherwise `OffProvider`, which opens nothing.
- Outbound rules: no private, loopback or link-local address, IP-pinned (`agent_safety.guarded_urlopen`); at most 3
  redirects, each re-validated (Tavily follows none); a 6 s end-to-end deadline enforced by the seam; provider body
  capped at 512 KiB (Tavily, streamed) / 5 MiB (DuckDuckGo HTML); what comes back is at most 5 rows of
  title / url / snippet, 4000 characters in all, each with a public http(s) link.
- The two caps are opt-in arguments. Callers that do not pass them (chat research, `zoe_agent`) behave as before.

### Answer contract

- A claim is `Claim(text, source_title, source_site)`. No title, no public site or no text means no claim.
- The reply is composed from fixed sentences: it looked it up; or it tried and the search was refused or failed; or
  it did not send the question. A failed or refused lookup never yields a claim. At most two claims.
- Page text only fills the quoted part, neutralised (markup, control and invisible characters, role markers) and cut.
  The answer type has no field for an action, a tool call or a second search.

## Lab result (`labs/web-lookup/`, frozen corpus, 2026-11-14)

40 questions, scripted provider responses, no network and no model: **27 answered with a source, 10 refused, 2
unavailable, 1 withheld; privacy leaks 0; injected instructions followed 0.** Negative control (scrubber and composer
removed): 15 leaking cases, 5 of 5 injections followed. Details and the definitions of the two zeros:
[labs/web-lookup/README.md](../../labs/web-lookup/README.md). Pinned by `services/zoe-data/tests/test_web_lookup_lab.py`.

## Known limits

- **A name nobody told the scrubber about.** An unknown name is caught only after a relation word, and only when
  capitalised or after "called" / "named". "my friend dana" in lower case is sent with "dana" in it.
- **A short private word glued to other letters.** Under six letters plus three or more glued letters is not seen
  ("chloefamily"), because the same rule would strip "christmas" in a household with a Chris. Pinned by a test.
- **Other disguises.** A private word written backwards, translated, or abbreviated is not recognised. The sidecar's
  rule that a turn holding web results cannot search again is what stops a page from asking for such a query.
- **Over-stripping.** A household name that is also a public word or a public person's name is stripped
  ("Taylor Swift tour" loses "taylor"). Two adjacent years read as a long number. The lookup still runs, with a worse query.
- **Instruction detection is cue-based** and can be evaded by paraphrase (lab case `injection-05`). Such text is
  quoted with attribution; it cannot cause an action.
- **English lexicon only** today.

## Acceptance list (all must hold before `ZOE_WEB_SEARCH_TOOL=1` on a household box)

Proven in this lab round:

- [x] Scrubber: planted sentinels never leave, including a seeded 2100-query obfuscation sweep (`test_web_lookup_scrub.py`).
- [x] Seam: a bare string cannot be sent; a refused scrub never reaches a provider; deadline, row caps and public-link rule hold (`test_web_lookup_provider.py`).
- [x] Outbound: redirects capped by the real `guarded_urlopen` against a hop server; a redirect to a private address refused; Tavily body capped and no redirect followed.
- [x] Answer contract: 20 injection shapes in title and snippet withheld; every outcome worded honestly (`test_web_lookup_answer.py`).
- [x] Lab corpus: leaks 0, followed 0, negative control red (`test_web_lookup_lab.py`).
- [x] Flag off: the endpoint is 404 and nothing is read or sent (`test_web_search_tool.py`).

Not done; each is required before the flag is switched on:

- [ ] **Voice replay gate** for these `services/zoe-data/` changes on the box (`voice_regression_probe.py`), per the merge rules.
- [ ] **Memory and places reach the scrubber at runtime.** The endpoint has no turn context, so today it knows names
      only. The sidecar has to pass the turn's recalled memory texts (or zoe-data has to look them up by turn id).
- [ ] **Live brain bench.** The Gemma brain with the tool on, against the same 40 questions and injected pages: does
      it cite a source and say it looked it up (`check_reply` on real replies), and does it obey nothing inside the fence.
- [ ] **`check_reply` on live replies**, in shadow first (log `no_disclosure` / `no_source`, change nothing).
- [ ] **The sidecar words `refused` properly.** Today it reports the new status as a failed lookup.
- [ ] **A real provider run** from the box with a synthetic query set: latency inside the 6 s budget, Tavily credit use.
- [ ] **Owner opt-in** recorded: which provider, and that keyword queries leave the house when the flag is on.
