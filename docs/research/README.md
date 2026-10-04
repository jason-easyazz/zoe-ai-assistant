# Memory & Hallucination Reduction Research - Documentation Index

**Date:** November 18, 2025
**Status:** Ready for Implementation
**Timeline:** 2 weeks (P0), 1 week (P1)

> **Later reports (2026-09-29):** [samantha-context-engineering-2026-09-29.md](./samantha-context-engineering-2026-09-29.md) (context manager vs training) and [zoe-context-audit-2026-09-29.md](./zoe-context-audit-2026-09-29.md) (code-level audit of Zoe's context assembly against it).
>
> **2026-10-03 audits:** [companion-field-vs-samantha-2026-10-03.md](./companion-field-vs-samantha-2026-10-03.md) (field scan vs the Samantha spec, pieces to borrow), [flue-and-agent-runtimes-2026-10-03.md](./flue-and-agent-runtimes-2026-10-03.md) (Flue feature matrix, upgrade risk, runtime field comparison), [inference-speech-stack-2026-10-03.md](./inference-speech-stack-2026-10-03.md) (brain/router/STT/TTS/Pi config vs the field, ranked actions), [infra-data-config-2026-10-03.md](./infra-data-config-2026-10-03.md) (host, uvicorn, Postgres, Chroma, docker, naming/TLS, backups — ranked actions). The memory profile lives in `docs/knowledge/memory-pressure-profile-2026-10-03.md`.
>
> **2026-10-04 follow-ups (deep research per idea, each with a go/no-go):** [barge-in-duck-decide-resume-2026-10-04.md](./barge-in-duck-decide-resume-2026-10-04.md) (P2 deep dive: field barge-in/backchannel/truncation practice vs Zoe's live barge-in path, flag-dark duck → decide → resume design, heard-text truncation hook, measurement plan, go/no-go), [personality-identity-layer-2026-10-04.md](./personality-identity-layer-2026-10-04.md) (Nomi.ai + the companion field's persona systems vs Zoe's soul doctrine; a structured persona config + per-member relationship modes as a stable system-prompt tail, P5 drift band as the measurement, Samantha bar as the gate, go/no-go), [speaker-gate-rebuild-2026-10-04.md](./speaker-gate-rebuild-2026-10-04.md) (P3 deep-research record: Omi's speaker-ID postmortem, CPU/ONNX embedders, Zoe's live voice-ID path traced, flag-dark Pi-side design, enrolment + measurement plan, go/no-go), [telegram-calls-voice-photos-2026-10-04.md](./telegram-calls-voice-photos-2026-10-04.md) (Telegram calls / voice notes / photos: Bot API has no calls, voice notes in/out via OGG-Opus + ffmpeg, Gemma 4 vision RAM honesty, staged design + go/no-go), [self-building-skills-2026-10-04.md](./self-building-skills-2026-10-04.md) (W7 / B8 deep-research record: the open Agent Skills / SKILL.md standard and its adopters, OpenClaw / Hermes / Letta self-extension and the ClawHub poisoning numbers, HA blueprints + HACS + OVOS / Mycroft store precedents, Alexa FROST / Hunches and Dialogflow intent suggestions for "when to offer", sandboxing / permission-ladder / skill-scanner literature; Zoe's halves traced with file:line (21k-row intent-miss log, the dead chat→board card, proposal contract + admission, Omnigent / Multica / Flue executor state and flags, cross-review, deploy gate, compose catalog, discoverability, skillspector); the end-to-end detect → Review item → ticket-from-template → cloud worktree build → draft PR + cross-vendor review → risk-tiered merge → deploy → inbox report → complaint-to-fix loop; seven draft contracts (Skill Contract, Panel Card Guidelines, Permission Ladder, Testing & Gates, Cost & Accounts, When Zoe Asks, Progress & Fixes); Jetson vs Pi 5 vs laptop for the builder; tides-then-stocks proof skill with negative controls; go/no-go; PR order)., [agent-sessions-off-box-2026-10-04.md](./agent-sessions-off-box-2026-10-04.md) (Q4: measured dev-tooling RAM on the Jetson, laptop/cloud sessions vs the panel Pi — Pi verdict NO; capped builder slice keeps self-evolution), [arousal-detection-licence-scope-2026-10-04.md](./arousal-detection-licence-scope-2026-10-04.md) (Q18: no permissive sub-100 MB arousal model exists; Wav2Small is a recipe with a half non-commercial teacher; scope split act-vs-record; NO-GO on downloadable candidates, GO on a measured bake-off), [presence-without-sensors-2026-10-04.md](./presence-without-sensors-2026-10-04.md) (Q19: camera is enrolled but never called; USB power is the prerequisite; phone BLE needs the IRK; mmWave sensor comparison; fail-safe fusion design), [failure-fed-proposals-2026-10-04.md](./failure-fed-proposals-2026-10-04.md) (Q19: nine failure signals in seven unjoined places; failure ledger + fingerprinted classes + SRE-shaped triggers → evidence-packed proposals; operator approves)
> **2026-10-03 audits:** [companion-field-vs-samantha-2026-10-03.md](./companion-field-vs-samantha-2026-10-03.md) (field scan vs the Samantha spec, pieces to borrow), [flue-and-agent-runtimes-2026-10-03.md](./flue-and-agent-runtimes-2026-10-03.md) (Flue feature matrix, upgrade risk, runtime field comparison), [inference-speech-stack-2026-10-03.md](./inference-speech-stack-2026-10-03.md) (brain/router/STT/TTS/Pi config vs the field, ranked actions), [infra-data-config-2026-10-03.md](./infra-data-config-2026-10-03.md) (host, uvicorn, Postgres, Chroma, docker, naming/TLS, backups — ranked actions), [incomplete-turn-marker-2026-10-04.md](./incomplete-turn-marker-2026-10-04.md) (P7 deep-research record: turn-completeness models and the LLM first-token ●/◐/○ marker in the field, Zoe's live endpointing → turn_stream → Kokoro path traced with the three traps a naïve port hits, flag-dark hold + re-engage design, pause-thoughts rung, corpus measurement with negative controls, go/no-go). The memory profile lives in `docs/knowledge/memory-pressure-profile-2026-10-03.md`.
> **2026-10-03 audits:** [companion-field-vs-samantha-2026-10-03.md](./companion-field-vs-samantha-2026-10-03.md) (field scan vs the Samantha spec, pieces to borrow), [flue-and-agent-runtimes-2026-10-03.md](./flue-and-agent-runtimes-2026-10-03.md) (Flue feature matrix, upgrade risk, runtime field comparison), [inference-speech-stack-2026-10-03.md](./inference-speech-stack-2026-10-03.md) (brain/router/STT/TTS/Pi config vs the field, ranked actions), [infra-data-config-2026-10-03.md](./infra-data-config-2026-10-03.md) (host, uvicorn, Postgres, Chroma, docker, naming/TLS, backups — ranked actions), [pull-not-push-inbox-2026-10-04.md](./pull-not-push-inbox-2026-10-04.md) (P1 + P10 deep-research record: Nomi's cadence and one-unanswered-then-wait, the notification / interruptibility literature with numbers, Alexa / Google / OVOS / HA pending-item surfaces, Zoe's live proactive chain traced with file:line, a flag-dark orb-state + "what's up?" inbox design with one delivery mechanism, the learned should-I-speak head's features / labels / 200-row threshold, measurement with negative controls, go/no-go). The memory profile lives in `docs/knowledge/memory-pressure-profile-2026-10-03.md`.

---

## 📚 Documentation Overview

This research package analyzes 40+ production LLM systems (2025) and provides specific recommendations for enhancing Zoe's memory and reducing hallucinations while building on your existing solid architecture.

---

## 🎯 Quick Start (5 Minutes)

**If you're ready to start immediately:**

1. Read: [`EXECUTIVE_SUMMARY.md`](./EXECUTIVE_SUMMARY.md) (5 min)
2. Start: `IMPLEMENTATION_GUIDE_P0.md` → P0-2 Section (Monday morning)
3. Reference: `ARCHITECTURE_DIAGRAM.md` for visual guidance

**Key Takeaway:** Your architecture is 80% there. You need 3 strategic additions (P0), not a rewrite.

---

## 📄 Document Guide

### 1. EXECUTIVE_SUMMARY.md (⭐ Start Here)
**Purpose:** High-level overview and quick decision-making
**Length:** 15 minutes
**Audience:** Project leads, decision makers

**Contents:**
- TL;DR: What you need to know
- What you've built (better than you think)
- What's missing (strategic gaps)
- The 3 high-impact recommendations (P0)
- Expected outcomes
- Implementation timeline
- Quick reference card

**When to read:** Before starting implementation to understand strategy

---

### 2. MEMORY_HALLUCINATION_ANALYSIS.md (📚 Comprehensive Reference)
**Purpose:** Deep analysis of research findings vs. current architecture
**Length:** 2-3 hours (30,000 words)
**Audience:** Architects, senior developers

**Contents:**
- Current state assessment (what works, what's missing)
- Research findings (Zep, Mem0, Letta, GraphRAG benchmarks)
- Gap analysis vs production systems
- 6 prioritized recommendations (P0, P1, P2)
- Answers to all 42 specific questions
- Architecture decision records
- Success metrics and monitoring

**When to read:** For detailed understanding of "why" behind recommendations

**Key Sections:**
- Section 1: Memory System Enhancements (L1 behavioral memory)
- Section 2: Hallucination Reduction (pre-response validation, confidence)
- Section 3: Platform Optimization (Jetson vs Pi context budgets)
- Section 6: Answers to Your 42 Questions

---

### 3. IMPLEMENTATION_GUIDE_P0.md (🛠️ Step-by-Step Code)
**Purpose:** Practical implementation guide with code examples
**Length:** 1-2 hours (15,000 words)
**Audience:** Developers implementing the changes

**Contents:**
- **P0-2:** Context Validation (Days 1-2)
  - Files to create: `context_validator.py`
  - Files to update: `chat.py`
  - Testing procedures

- **P0-3:** Confidence Expression (Day 3)
  - Files to create: `response_formatter.py`
  - Integration points
  - Example outputs

- **P0-1:** Behavioral Memory (Days 4-6)
  - Files to create: `behavioral_memory.py`, cron job
  - Database schema
  - Nightly extraction pipeline
  - Integration with context assembly

**When to read:** While implementing, as a coding reference

**Features:**
- Complete file contents (copy-paste ready)
- Testing procedures
- Troubleshooting guide
- Verification steps

---

### 4. ARCHITECTURE_DIAGRAM.md (🎨 Visual Guide)
**Purpose:** Visual representation of architecture changes
**Length:** 20 minutes (visual + annotations)
**Audience:** Visual learners, team presentations

**Contents:**
- Current architecture (before P0)
- Enhanced architecture (after each P0 feature)
- Memory layer visualization (L0, L1, L2)
- Decision flow diagrams
- Context budget comparison (Jetson vs Pi)
- Confidence level formatting examples
- Implementation sequence flowchart

**When to read:** When explaining architecture to others, or for quick visual reference

---

## 🎯 Reading Path by Role

### For Project Lead / Decision Maker
**Goal:** Understand ROI and make go/no-go decision

1. **EXECUTIVE_SUMMARY.md** (15 min)
   - Focus on: TL;DR, Expected Outcomes, Timeline
2. **ARCHITECTURE_DIAGRAM.md** (10 min)
   - Focus on: Expected Outcomes Dashboard
3. **Decision:** Approve P0 implementation?

**Time Investment:** 25 minutes
**Expected Outcome:** Go/no-go decision with confidence

---

### For Architect / Tech Lead
**Goal:** Understand technical approach and integration

1. **EXECUTIVE_SUMMARY.md** (15 min)
2. **MEMORY_HALLUCINATION_ANALYSIS.md** - Sections 1-3 (1 hour)
   - Focus on: Current State Assessment, Integration Strategy, Architecture Decisions
3. **ARCHITECTURE_DIAGRAM.md** (20 min)
   - Focus on: Three-Layer Memory Architecture, Decision Flows

**Time Investment:** 1.5 hours
**Expected Outcome:** Confident in architectural soundness

---

### For Developer Implementing
**Goal:** Write code and integrate features

1. **EXECUTIVE_SUMMARY.md** - Quick Reference Card (5 min)
2. **IMPLEMENTATION_GUIDE_P0.md** - Relevant P0 section (30 min per feature)
3. **ARCHITECTURE_DIAGRAM.md** - As needed for context (10 min)
4. **MEMORY_HALLUCINATION_ANALYSIS.md** - Section 9 (Troubleshooting) as needed

**Time Investment:** 45 minutes per feature
**Expected Outcome:** Feature implemented correctly with tests

---

### For Curious Team Member
**Goal:** Understand overall direction

1. **EXECUTIVE_SUMMARY.md** (15 min)
2. **ARCHITECTURE_DIAGRAM.md** (20 min)
3. **MEMORY_HALLUCINATION_ANALYSIS.md** - Section 1 & 2 (30 min)

**Time Investment:** 1 hour
**Expected Outcome:** Solid understanding of enhancements

---

## 🚀 Implementation Checklist

### Pre-Implementation (30 minutes)
- [ ] Read `EXECUTIVE_SUMMARY.md` (entire team)
- [ ] Review `ARCHITECTURE_DIAGRAM.md` (developers)
- [ ] Set up test environment
- [ ] Create implementation branch: `git checkout -b memory-hallucination-p0`
- [ ] Back up current database: `cp data/memory.db data/memory.db.backup`

### Week 1: P0 Implementation

#### Monday-Tuesday: P0-2 Context Validation
- [ ] Read `IMPLEMENTATION_GUIDE_P0.md` → P0-2 section
- [ ] Create `intent_system/validation/context_validator.py`
- [ ] Update `chat.py` with validation logic
- [ ] Test deterministic intents (< 10ms target)
- [ ] Verify context skipping in logs
- [ ] **Success Criteria:** Tier 0 intents execute without context fetching

#### Wednesday: P0-3 Confidence Expression
- [ ] Read `IMPLEMENTATION_GUIDE_P0.md` → P0-3 section
- [ ] Create `intent_system/formatters/response_formatter.py`
- [ ] Update `chat.py` response formatting
- [ ] Test all confidence thresholds (high/medium/low/very-low)
- [ ] A/B test uncertainty language
- [ ] **Success Criteria:** Uncertainty expressed appropriately

#### Thursday-Saturday: P0-1 Behavioral Memory
- [ ] Read `IMPLEMENTATION_GUIDE_P0.md` → P0-1 section
- [ ] Create `behavioral_memory.py`
- [ ] Design and create SQL schema
- [ ] Implement LLM-based pattern extraction
- [ ] Create cron job script
- [ ] Add cron service to docker-compose.yml
- [ ] Test manual extraction with test user
- [ ] **Success Criteria:** 5-10 patterns extracted per user

### Week 2: Testing & Validation
- [ ] Measure hallucination rate (100 test queries)
- [ ] Gather user feedback on uncertainty expression
- [ ] Monitor behavioral pattern quality
- [ ] Check nightly cron job logs
- [ ] Iterate on confidence thresholds if needed
- [ ] Document any issues encountered
- [ ] **Go/No-Go Decision:** Proceed to P1?

### Week 3-4 (Optional): P1 Implementation
- [ ] Evaluate P0 success metrics
- [ ] Read `MEMORY_HALLUCINATION_ANALYSIS.md` → Section 3 (P1)
- [ ] Implement P1-1: Temporal-aware similarity (if approved)
- [ ] Implement P1-3: Platform context budgets (if approved)
- [ ] Implement P1-2: Response grounding (if approved)

---

## 📊 Success Metrics

### Baseline (Before P0)
- Hallucination rate: ~15-20%
- Context retention: ~70%
- Tier 0 latency: 200-500ms (context overhead)
- User satisfaction: Baseline
- Personalization: Generic

### Target (After P0)
- Hallucination rate: < 5% ✅
- Context retention: > 90% ✅
- Tier 0 latency: < 10ms ✅
- User satisfaction: +20% ✅
- Personalization: "Remembers me" ✅

### Measurement Methods
1. **Hallucination Rate:** Manual review of 100 responses with known ground truth
2. **Context Retention:** Ask same question 7 days apart, verify memory
3. **Latency:** Check execution_time_ms in logs
4. **User Satisfaction:** Feedback form (1-10 scale)
5. **Personalization:** Count behavioral patterns in use per response

---

## 🆘 Troubleshooting Quick Reference

### Issue: Context still being fetched for Tier 0 intents
**Solution:** See `IMPLEMENTATION_GUIDE_P0.md` → Troubleshooting → Issue 1

### Issue: Confidence always shows as 0.5
**Solution:** See `IMPLEMENTATION_GUIDE_P0.md` → Troubleshooting → Issue 2

### Issue: Behavioral extraction returns 0 patterns
**Solution:** See `IMPLEMENTATION_GUIDE_P0.md` → Troubleshooting → Issue 3

### Issue: Patterns not showing in system prompt
**Solution:** See `IMPLEMENTATION_GUIDE_P0.md` → Troubleshooting → Issue 4

### General Questions
**Reference:** `MEMORY_HALLUCINATION_ANALYSIS.md` → Section 6 (Answers to 42 Questions)

---

## 📞 Support & Questions

### During Implementation
1. Check logs: `/app/data/logs/zoe-core.log`
2. Verify database: `sqlite3 /app/data/memory.db ".schema"`
3. Review relevant section in `IMPLEMENTATION_GUIDE_P0.md`
4. Search for question in `MEMORY_HALLUCINATION_ANALYSIS.md` Section 6

### For Architectural Questions
- Reference: `MEMORY_HALLUCINATION_ANALYSIS.md` → Architecture Decision Records
- Visual aid: `ARCHITECTURE_DIAGRAM.md` → Decision Flows

### For "Why" Questions
- Reference: `MEMORY_HALLUCINATION_ANALYSIS.md` → Detailed Analysis sections
- Example: "Why behavioral memory?" → Section 1, Research Finding #1

---

## 🎓 Key Research Findings

### What the Research Showed
1. **Temporal knowledge graphs** (Zep) outperform pure vector search by 9% (74% vs 65%)
2. **Natural language memory** (L1) bridges gap between vectors and personalization
3. **Pre-response validation** reduces hallucinations 40-60%
4. **Confidence expression** builds user trust without accuracy loss
5. **Platform-aware context** maximizes resource utilization

### What We Decided
1. **Extend Light RAG** with temporal weighting (don't replace with Zep)
2. **Implement L1 behavioral memory** before fine-tuning (80% benefit, 20% complexity)
3. **Add validation layer** to intent system (high impact, minimal changes)
4. **Surface confidence transparently** to users (trust building)
5. **Defer GraphRAG/DPO** to P2 (validate need first)

---

## 📈 Timeline & Milestones

```
Week 1 (Nov 18-22): P0 Implementation
  ├─ Day 1-2: P0-2 Context Validation
  ├─ Day 3:   P0-3 Confidence Expression
  └─ Day 4-6: P0-1 Behavioral Memory

Week 2 (Nov 25-29): Testing & Validation
  ├─ Measure hallucination rate
  ├─ Gather user feedback
  ├─ Monitor behavioral patterns
  └─ Go/No-Go decision for P1

Week 3-4 (Dec 2-13): P1 Implementation (Optional)
  ├─ P1-1: Temporal-aware similarity
  ├─ P1-3: Platform context budgets
  └─ P1-2: Response grounding

Month 2+ (2026+): P2 Future Considerations
  ├─ Chain-of-thought prompting
  ├─ GraphRAG integration (if needed)
  └─ DPO fine-tuning (after 6+ months data)
```

---

## 🎯 Critical Success Factors

### ✅ What Will Make This Succeed
1. **Start with P0-2** (quickest win, proves approach)
2. **Measure everything** (baseline → target metrics)
3. **Incremental validation** (don't proceed until P0 stable)
4. **Build on foundation** (extend, don't rewrite)

### ❌ What Will Make This Fail
1. **Rewriting working systems** (intent system is solid)
2. **Premature optimization** (GraphRAG/DPO before validating need)
3. **Ignoring constraints** (Pi 5 has 4K limit)
4. **Adding complexity** for its own sake

---

## 🚦 Next Actions

### Immediate (This Week)
1. **Monday 9am:** Read `EXECUTIVE_SUMMARY.md` (entire team, 15 min)
2. **Monday 10am:** Start P0-2 implementation (developer assigned)
3. **Monday 5pm:** P0-2 tested and working
4. **Tuesday:** Complete P0-2, start P0-3
5. **Wednesday:** Complete P0-3, start P0-1
6. **Thursday-Saturday:** Complete P0-1

### Next Week
1. **Monday:** Measure P0 success metrics
2. **Tuesday-Thursday:** Iterate based on feedback
3. **Friday:** Go/No-Go decision for P1

---

## 📚 Additional Resources

### Research References
- LoCoMo benchmark (10K conversations, 26K tokens avg)
- Second Me architecture (96% accuracy with personalization)
- Zep temporal knowledge graphs (74% accuracy)
- Production LLM systems (40+ analyzed)

### Code Examples
- All in `IMPLEMENTATION_GUIDE_P0.md` (copy-paste ready)
- Complete file contents provided
- Testing procedures included

### Visual Aids
- `ARCHITECTURE_DIAGRAM.md` for all visual representations
- Before/after comparisons
- Flow diagrams
- Context budget visualizations

---

## 📝 Document Maintenance

**Last Updated:** November 18, 2025
**Version:** 1.0
**Status:** Ready for Implementation

**Change Log:**
- 2025-11-18: Initial research analysis and documentation complete
- Future: Update after P0 implementation with lessons learned

---

## 🎉 Final Words

**Your research is excellent. Your architecture is solid. You're closer than you think.**

The three P0 recommendations will transform Zoe from "smart assistant" to "remembers like Samantha" in just 2 weeks.

**Start Monday. Start with P0-2. Prove it works. Build from there.**

Good luck! 🚀

---

**Quick Links:**
- [Executive Summary](./EXECUTIVE_SUMMARY.md) - Start here (15 min)
- [Implementation Guide](./IMPLEMENTATION_GUIDE_P0.md) - Code examples
- [Architecture Diagrams](./ARCHITECTURE_DIAGRAM.md) - Visual guide
- [Comprehensive Analysis](./MEMORY_HALLUCINATION_ANALYSIS.md) - Full details (30,000 words)
