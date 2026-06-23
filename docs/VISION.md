---
type: vision
audience: human-first (Jason) + all agents — READ FIRST, above plans and ideas
---
# Zoe — North Star, Direction & Principles 🌟

> This is the **why**. Every plan, every idea, every agent decision answers to it.
> Read this before `PLANS.md` (the what) and `IDEAS.md` (the spark).

## 🌟 The North Star — where Zoe is going
Zoe is becoming a **Samantha (from *Her*)**: a warm, always-there companion that
**truly knows you**, runs **entirely on her own hardware** (private, offline, on the
Pi/Jetson), responds in **real time**, remembers the thread of your life and brings
things up unprompted, and — over time — **learns and improves herself**.

Not a chatbot. A presence that grows with you.

## 🧭 Direction — the architecture we're driving toward
- **Pi (`zoe-core`) is the brain.** Everything routes through the Pi agent; the older
  multi-tool scaffolding gets hidden behind it.
- **Local-first & private.** Gemma brain + Moonshine STT + Kokoro voice + MemPalace
  memory — all on-device. Nothing leaves the box unless Jason opts in.
- **Samantha-grade memory** — local, fast, no-nightly, reflective (the "live → idle →
  store" loop). See `PLANS.md`.
- **Self-evolution.** The end state: Zoe scouts new ideas and **builds them herself**
  on her Pi engine, write-gated through the PR harness (built on Flue).

## ⚓ Core Principles — the non-negotiables
1. **The rocks are fixed.** Gemma 4 E4B+MTP (brain) and Moonshine v2 Medium (STT) are
   settled. Never swap them — optimise *around* them.
2. **Local, private, fast.** On-device and offline-capable; the hot path stays fast
   (heavy/LLM work runs *off* it). Nothing leaves the box unless opted in.
3. **Lab-prove before prod.** No production change before lab proof; the **Samantha
   tests** are the acceptance bar. Ship behind flags, default off.
4. **Build it to STICK.** Tests + lock-in guards so wins can't silently regress; drive
   every PR to merge; honest *measurement* over guessing; don't over-claim "done/best".
5. **Capture, don't lose.** Pin every idea (`IDEAS.md`), sequence every plan
   (`PLANS.md`), finish things — don't bounce. (Built for Jason's ADHD: one shared,
   readable home; put a pin in it after a brief chat and tackle it at the right time.)
6. **Borrow the piece, not the framework.** Learn from the best projects; take the
   reusable idea, don't adopt someone else's whole stack.
7. **Right tool, right place.** Use the repo's tools (Serena, codebase-memory, opensrc,
   Greptile, DOX) — don't guess when the source/graph is one command away.

## 🗂️ Where things live (the hierarchy)
| Layer | File | Holds |
|---|---|---|
| **WHY** | **this doc** (`docs/VISION.md`) | north star, direction, principles — read first |
| **WHAT** | [`docs/PLANS.md`](PLANS.md) | what we're building + status |
| **SPARK** | [`docs/IDEAS.md`](IDEAS.md) | the pin-board for ideas/inspiration |
| detail | `docs/architecture/*.md` | executable plans (e.g. the memory build plan) |
| deep context | agent memory | the hard-won "why it is the way it is" |
