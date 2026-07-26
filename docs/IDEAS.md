---
type: ideas-board
audience: human-first (Jason) + all agents
---
# Zoe Ideas Board 🧭

**The pin-it-so-we-don't-lose-it board.** Capture an idea after a brief chat;
tackle it at the right time — not all at once. Core idea first, in plain English.
Detail/AI notes go under each entry. Move the status as it progresses.

**Status key:** 💡 pinned · 🔬 exploring · 🔨 building · ✅ done · 🗄️ parked

> **For agents:** when Jason says *"pin this" / "put a pin in it" / "remember this idea"*,
> add a one-line entry here (core idea + any source link + status 💡). **Never lose it.**
> When an idea graduates into real work, create/point to a plan in [PLANS.md](PLANS.md).

---

## 🎥 Inspiration (videos / articles that shaped direction)

### Flue as the harness substrate — 🔨 building
- **Core:** [youtube.com/watch?v=PzVV4X37ihg](https://www.youtube.com/watch?v=PzVV4X37ihg) — changed how Jason works; led to the decision to build Zoe's autonomous harness on **Flue**.
- Plan: memory `project-zoe-harness-on-flue` → [PLANS.md](PLANS.md).

### Self-evolution harness — 🔬 exploring
- **Core:** [youtube.com/watch?v=n5cYS6KuyK8](https://www.youtube.com/watch?v=n5cYS6KuyK8) — laid out the idea of a **harness that helps Zoe self-evolve** (edit her own code, write-gated through the PR harness).
- Plan: memory `project-zoe-self-evolution-toolchain`.

### (Jason: annotate) — 💡 pinned
- **Core:** [youtube.com/watch?v=vy7o1g2iHY8](https://www.youtube.com/watch?v=vy7o1g2iHY8) — *Jason to add the one line: what this sparked.*

---

## 💡 Ideas

### A richer "Pinterest-style" ideas surface inside Zoe — 💡 pinned
- **Core:** this file is v1. The real idea is a **visual board in Zoe** to save links/videos/ideas, browse for inspiration, and let agents read/append it. Originally planned a Zoe section for exactly this.
- **Next:** research whether an existing open-source project already does this well (visual idea/link board, local, agent-accessible) before building. Source: Jason, 2026-06-23.

### Wire the relationship graph into recall (GBrain lesson) — 💡 pinned
- **Core:** feed Zoe's existing `people_relate` graph into the memory recall ranking, so asking about one person also surfaces facts about the people/things connected to them. Source: Jason shared [github.com/garrytan/gbrain](https://github.com/garrytan/gbrain) 2026-07-09.
- **Why it's the one gap:** GBrain benchmarks **+31.4 P@5 from a knowledge graph over vector-only RAG.** Audit (2026-07-09) found Zoe already has the *rest* of GBrain's playbook, mostly LIVE — multi-signal hybrid recall (`memory_service._semantic_search._blend`: vector + keyword/BM25 + salience/access_count + confidence + time-decay + recency, `ZOE_HYBRID_RETRIEVAL_ENABLED=1`), an idle enrichment "dream cycle" (`MEMORY_DIGEST_ENABLED`), and recall injection (`ZOE_SEAM_RECALL_INJECT`) — and it's local/private where GBrain defaults to hosted embeddings. The single missing signal: `_semantic_search` never calls the graph. `relationship_graph.neighbors()` + `GET /people/{id}/graph` exist (ADR #1022) but stay dark.
- **Next:** add a graph-adjacency boost to `_blend` (pure Postgres recursive-CTE, **no new models, ~zero RAM**). Gated behind `ZOE_RELATIONSHIP_GRAPH_ENABLED`, whose go-live is **RAM-blocked on the replay gate** — so this lands AFTER the memory-pressure / RAM-reclamation workstream (W0), not before. GBrain's +31 P@5 is the justification to make it the *first* memory increment once RAM clears. **Reject from GBrain:** hosted embeddings (breaks the local/private rock) and a resident local reranker like Qwen3-Reranker (breaks the RAM budget — see the 2026-07-08 25 GB-swap incident).

### Forget tombstones — a forget blocks in-flight/late memory writes for that name — ✅ IMPLEMENTED (#1304 + #1307, 2026-07-13)
- **Core:** memory extraction runs seconds behind the conversation, so a fact mentioned just before "forget everything about X" can finish saving just AFTER the forget — one straggler row resurrects the forgotten person (seen live 2026-07-13 during the F14 verification: a digest row "User has a friend named Delia" landed post-forget; a repeat forget swept it). Idea: `memory_forget_entity` writes a short-lived per-user tombstone for the name (e.g. a row in Postgres or an in-process TTL map, ~2–5 min), and every writer's ingest path (memory_extractor / digest / person_extractor / expert_dispatch — they already share `reconcile_for_ingest`) checks it and drops name-anchored candidates for tombstoned entities.
- **Shipped:** in-process 5-min TTL map (`memory_tombstones.py` — in-process is *correct*, not just cheap: zoe-data is one process, so the in-flight writes a tombstone must block die with it on restart); the drop lives at the `MemoryService.ingest` chokepoint so no writer can miss it; explicit teach HANDLERS (memory_store / store_fact) bypass at ingest and clear only after their store succeeds, while an explicit "remember (that) X …" UTTERANCE clears up front in the extraction hooks — deliberately pre-mining, because the mined ingests are otherwise shadow-dropped (shared `is_explicit_teach`, structural verb detection). Live-proven: seed → immediate forget → zero stragglers; re-teach within the TTL stores. Source: Jason, 2026-07-13 (from the live forget test, PR #1295's known race).

### The orb on the music card could react to what's playing — or be how you ask about it — 💡 pinned
- **Core:** the Zoe orb just sits there on the music card. It could react to what's playing, or be how you ask Zoe about the current track — rather than just being present. Parked for later exploration, not scoped. Source: Jason, 2026-07-16.

### Thin Tauri desktop shell — Zoe summonable anywhere on your computer — 🗄️ parked
- **Core:** a *thin* native wrapper (Tauri, not Electron) around the existing served web UI that adds only what the browser can't: **global hotkey summon** (push-to-talk/quick-ask from any app), **tray/menu-bar residence + autostart**, and later the **"Zoe can see/act on this computer"** computer-use companion. UI stays single-source — the shell never grows its own screens (no second UI codebase). Source: Jason, 2026-07-19 (desktop UI deep-dive chat).
- **Sequencing:** the installable-desktop path starts as **PWA polish inside the desktop-ui overhaul** (manifest/SW already ~80% there); the native shell is a separate later arc because per-OS packaging/signing (macOS needs a Mac; Jetson is ARM Linux) is a permanent maintenance tax. Detail will live in the desktop-ui overhaul plan (in progress on `claude/desktop-ui-overhaul-a512f5`).

<!-- New ideas: copy an entry above. One line of plain English first. -->
