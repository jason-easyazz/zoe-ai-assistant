# ADR: Contacts / people-memory — production hardening

## Status

Proposed (2026-07-09). Extends [ADR-contacts-from-known-people.md](ADR-contacts-from-known-people.md)
with an internet-researched, best-practice review and the remaining work to make the
people-memory system production-grade. Supersedes the earlier assumption that "propose-on-mention
doesn't fire on flue" (that was a false negative — see §3).

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
| Explicit contact create (`people_create` tool) | `intent_router._execute_people_create_direct` | ✅ **fixed** (#1200 FK, #1203 private-by-default) |
| Passive capture: likes, LLM person-extraction, **propose-on-mention** | `_persist_memory_candidates` (chat.py) + `voice_tts.py:2284` | ✅ fires on flue (proven: a live "niece" mention created a `person_create` proposal) |
| Dossier render of contacts | `zoe_memory_compose` | ✅ live |
| Backfill known people → proposals + delivery list | `contact_backfill.py`, `/pending-contacts` | ✅ merged |

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
- **P2 — Confidence-gate LLM person extraction.** Add a verbalized-confidence field to the
  extraction prompt; **≥0.8 → auto**, **<0.4 → discard**, **else → `person_create` proposal**.
  Per-attribute. Flag-gated, replay-gated.
- **P3 — Back-off / nag contract.** Track surfaced-count so an un-actioned offer stops after N
  turns; a declined proposal is not re-offered.
- **P4 — person_create UI confirm card** for the touch panel (`ui_components_for_suggestions`),
  matching the voice/for-prompt surface.

Guardrails (unchanged): every auto-create stays user-confirmed unless high-confidence; flag-gated +
demo-user-lab-proved; hot-path changes replay-gated; private-by-default visibility.

## Acceptance criteria

- A person mentioned in conversation surfaces as a confirmable "add contact?" on **voice and chat**,
  via the flue brain, without a delivery gap.
- Auto-created contacts only when confidence is high; everything else is confirm-gated; nothing is
  silently wrong or family-shared.
- No nagging: an un-actioned or declined offer backs off.
