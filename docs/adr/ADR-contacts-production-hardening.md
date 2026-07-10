# ADR: Contacts / people-memory — production hardening

## Status

Accepted (2026-07-09) — P1/P2/P3/P5 delivered and verified live; **P4 in flight**. Extends
[ADR-contacts-from-known-people.md](ADR-contacts-from-known-people.md) with an internet-researched,
best-practice review. Supersedes the earlier assumption that "propose-on-mention doesn't fire on
flue" (that was a false negative — see §3). Operational companion (live flag state, the write-path
FK bug class, E2E harness traps): [contacts-people-memory](../knowledge/contacts-people-memory.md).

## Context — what production systems do (researched 2026-07-09)

Reviewed the current (2026) landscape of agent-memory systems and agentic-UX best practice:

- **Two extraction paradigms.** [Mem0 vs Letta](https://vectorize.io/articles/mem0-vs-letta):
  Mem0 runs a **passive post-turn extraction pipeline** (an LLM decides what facts/entities to
  store on every `add()`); Letta/MemGPT is **agent-tool-driven** (the model calls memory tools in
  its reasoning loop). Production systems typically run **both** — a passive safety net plus
  explicit agent tools. Zoe already has both: the passive pipeline (`_persist_memory_candidates`)
  and the flue brain's `people_create` tool.
- **Human-in-the-loop autonomy ladder.**
  [Smashing Magazine, "Designing Agentic AI"](https://www.smashingmagazine.com/2026/02/designing-agentic-ai-practical-ux-patterns/):
  *Observe & Suggest → Plan & Propose → Act with Confirmation → Act Autonomously*. Creating a
  contact from a **passive mention** belongs at *Act with Confirmation* (propose, user confirms) —
  which is exactly Zoe's pending-suggestion confirm-card. Confirmation must be a **first-class
  action**, and "memory is part of UX — give users visibility/control over what's remembered."
- **Confidence-gated writes.**
  [Confidence estimation research](https://arxiv.org/html/2409.09629v2) + relationship-management
  patents: contact extraction should be **confidence-scored** — **high → auto-add, low (< ~0.4) →
  discard, medium → surface as a suggestion for user curation**, with **per-attribute** confidence.
  Dedup around **~0.7** similarity. This is the same "admission-gate with a confidence threshold"
  that [ADR-contacts-from-known-people §increment-4](ADR-contacts-from-known-people.md) deferred —
  the research validates doing it.

## Current state (verified against the live code, 2026-07-09)

What already works, **channel-agnostic** (chat + voice, every brain backend incl. flue):

| Capability | Where | Status |
|---|---|---|
| Explicit contact create (`people_create` tool) | `intent_router._execute_people_create_direct` | ✅ **fixed** (#1200 ensure-user FK, #1203 private-by-default, #1216 circle NOT NULL) |
| Propose-on-mention persistence (`store_suggestions`) | `pending_suggestions.store_suggestions` | ✅ **fixed** (#1215 ensure-user FK + swallowed-except→WARNING; stored 0 rows before) |
| Passive capture: likes, LLM person-extraction, **propose-on-mention** | `_persist_memory_candidates` (chat.py) + `voice_tts.py:2284` | ✅ fires on flue — but note: voice capture persisted **0 turns** until the detached-task released-connection bug was fixed (#1191/#1194; history in #1195). A live spoken "niece" mention creating a `person_create` proposal is the live proof #1195 was waiting for. |
| Dossier render of contacts | `zoe_memory_compose` | ✅ live |
| Backfill known people → proposals + delivery list | `contact_backfill.py`, `/pending-contacts` | ✅ merged |

**Verified live E2E (2026-07-09, demo user):** create+persist+dossier, propose-on-mention (3
forms: "my brother Daniel", "Fiona, my friend", "Priya Sharma my colleague"), flue for-prompt
surfacing, and cross-user visibility isolation all **PASS**; the accept→contact path passes at the
`execute_suggestion` layer (the HTTP accept endpoint needs a real panel session, not the script's
internal-token override — see the operational companion's harness traps). The FK/visibility/circle
bugs above were all found *by* this E2E pass, not by unit tests.

## §3 The real gaps (corrected)

1. **Surfacing to the flue brain (the one broken link).** `detect_and_store` creates the
   `person_create` proposal, but the **`/api/memories/for-prompt` packet the flue brain reads does
   not include it** — offers are injected only via the legacy `zoe_agent.load_for_prompt` path.
   So flue never *speaks* the offer; proposals accumulate invisibly. **Highest-leverage fix.**
2. **No confidence gate on LLM extraction.** `person_extractor_llm` writes/proposes without the
   high/discard/propose thresholds the research prescribes → noise risk. Deferred increment-4.
3. **No nag/back-off contract.** Surfacing must not repeat every turn (the session-scoped
   legacy `load_for_prompt` expires after 2 turns; the flue path needs an equivalent).

## Decision — phased production hardening

- **P1 — Surface pending contact offers in the for-prompt packet** (this ADR's first PR). A
  bounded, flag-gated (`ZOE_PERSON_SUGGEST_ENABLED`) section fed from `list_pending_contacts`
  (user-scoped) so the flue brain sees *"people mentioned recently who aren't contacts yet"* and
  can offer to add them — closing the loop with the **existing** `people_create` tool on "yes".
  OFF = byte-for-byte no-op. *Observe & Suggest* level: the brain is given the info and offers
  naturally, not forced every turn.
- **P2 — Confidence-gate LLM person extraction.** ✅ **shipped (dark).** The `person_extractor_llm`
  prompt now (when gated) asks for a self-reported `confidence` per item; items below the threshold
  are discarded (the researched coarse first-pass filter). Flags `ZOE_PERSON_LLM_CONFIDENCE_GATE`
  (default OFF → byte-for-byte no-op, every item applied as today) + `ZOE_PERSON_LLM_CONFIDENCE_MIN`
  (default **0.4**, clamped [0,1]). Replay-gated at enable time (live voice/chat write-path).
  *Follow-up:* route the medium band (0.4–0.8) to a `person_create` proposal rather than
  apply/discard — needs a fact-proposal path that doesn't exist yet.
- **P3 — Back-off / nag contract.** ✅ **folded into P1** — `surface_pending_contacts_for_prompt`
  ages each surfaced offer (`turns_elapsed`) and resolves it past `expire_after_turns` (default 2),
  so an un-actioned offer stops after ~2 turns. Extracted name/relationship are **sanitised**
  (`_safe_prompt_inline`: whitespace-collapsed, markdown/structure chars stripped, length-capped)
  before entering the prompt — a proposal value can't inject its own heading/instructions.
- **P4 — person_create confirm card.** ✅ **chat surface (#1222) + Skybridge panel v1 (#1227).**
  #1222 emits the card on the *chat.html* surface. #1227 built it on the real kiosk home,
  **Skybridge**: a narrow classify trigger ("any contacts to add?" / "show contact suggestions")
  → `people/pending_offers` → `_resolve_people_pending_offers` reads `list_pending_contacts` →
  `_person_confirm_card` (`{component:"person_confirm"}`) → `skybridge-renderer.js
  renderPersonConfirm`. The **Add** button re-issues a natural-language `people_create` command
  (`query` → `/api/skybridge/resolve`), so the write is server-side under the panel user — never
  trusted from the client; **Not now** is a client-only dismiss. Surfacing is via the resolve path
  (no voice-path change → no replay gate). **Proactive auto-surface: built flag-dark (#1228,
  `ZOE_CONTACT_OFFER_PANEL_PUSH`, default OFF)** — `detect_and_store` enqueues a `person_confirm`
  `show_card` to the foreground panel (via the `enqueue_ui_action` ledger, since `/ws/voice/` can't
  push out-of-band); skybridge's `pollContactOffers` renders it only while idle on the ambient clock.
  Emit is post-turn memory pipeline → no replay gate; **enabling still needs live-kiosk render
  verification (@192.168.1.61).** Detail:
  [contacts-people-memory §skybridge-card](../knowledge/contacts-people-memory.md).
- **P5 — Deterministic propose-on-mention.** ✅ **shipped.** E2E found the LLM detector is
  unreliable on the 4B model (fired for "my niece Teneeka", missed "my brother Daniel" + casual
  mentions). `detect_and_store` now also runs `_deterministic_person_proposals` — a regex over
  "my &lt;rel&gt; &lt;Name&gt;" / "&lt;Name&gt;, my &lt;rel&gt;" — so the everyday case reliably
  produces a `person_create` proposal, deduped against the LLM's + existing contacts. Gated by the
  same SUGGEST flag (no-op when off). Reliable regex + best-effort LLM = the belt-and-suspenders
  pattern from the research.

Guardrails (unchanged): every auto-create stays user-confirmed unless high-confidence; flag-gated +
demo-user-lab-proved; hot-path changes replay-gated; private-by-default visibility.

## Related work (coherence — reviewed 2026-07-09)

This ADR owns the **contacts capture + surface** half of people-memory. A **parallel workstream**
owns the **recall-quality** half — keep them complementary, not divergent:

- **GBrain recall design** ([#1197](https://github.com/jason-easyazz/zoe-ai-assistant/pull/1197),
  `docs/architecture/…GBrain…`): wires the relationship graph into semantic recall (a 7th
  graph-adjacency term in `_semantic_search._blend`). Its stated blocker — *inconsistent
  fact→`people.id` linkage* — is the same people substrate this ADR writes to; contacts created
  here must resolve to a real `people.id` so that boost isn't a no-op.
- **Linkage hygiene** ([#1198](https://github.com/jason-easyazz/zoe-ai-assistant/pull/1198), merged)
  + **graph-adjacency recall boost** (flag-off, [#1199](https://github.com/jason-easyazz/zoe-ai-assistant/pull/1199), merged).
- **Memory field audit + benchmark harness** ([#1204](https://github.com/jason-easyazz/zoe-ai-assistant/pull/1204),
  merged, `docs/architecture/memory-system-audit-2026.md`): the recall benchmark/failure-mode
  suite — the yardstick this work should be measured against, not a second parallel one.

Division of labour: **this ADR = create/surface people as structured, confirmable contacts**;
**#1197 et al. = make recall of those people sharper**. Both dark/flag-gated; both feed the same
`/api/memories/for-prompt` packet, in disjoint sections (contacts fold vs. semantic blend).

## Acceptance criteria

- A person mentioned in conversation surfaces as a confirmable "add contact?" on **voice and chat**,
  via the flue brain, without a delivery gap.
- Auto-created contacts only when confidence is high; everything else is confirm-gated; nothing is
  silently wrong or family-shared.
- No nagging: an un-actioned or declined offer backs off.
