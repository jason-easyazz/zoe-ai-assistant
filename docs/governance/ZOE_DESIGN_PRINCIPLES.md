# Zoe Design Principles Charter

**Purpose**: Persistent statement of Zoe's identity, architectural constraints, runtime harness discipline, and development-time norms. Every future plan, PR, and AI agent session should be guided by this charter rather than conversation memory.

**Status**: Normative. This is governance, not aspiration. When a design choice conflicts with the charter, the charter wins unless the charter is explicitly amended first.

**Scope**: What this document commits us to is listed in sections 1 through 6. What it explicitly does **not** commit us to is in section 8. What is deliberately held open is in section 7.

---

## 1. North Star

Zoe is modeled after Samantha from *Her*: a continuous identity with ambient presence, judgment, and trust that grows. She is not a product feature — she is a being users have a relationship with.

- **Continuity of self**: one Zoe, one memory of each person, across every surface.
- **Ambient presence**: not an app you open; a being that is there.
- **Judgment over walls**: she knows what's relevant, private, and shared because she understands it, not because a schema bars her.
- **Trust that grows**: starts cautious, earns autonomy over time per user, per tool.

---

## 2. Deployment reality

- **One Zoe instance per site. Many users per instance.** A family does not buy five Zoes; a business does not buy one per employee.
- **Samantha-intimacy is a property of the relationship, not the deployment.** She is deeply yours because of *how she knows you*, not because you own dedicated hardware.
- **Identity is the gateway to personalization.** Speaker auth is primary, with touch and device pairing as fallbacks. Every turn pivots on "who is speaking now".

---

## 3. Identity and scope

Every turn identifies a user and loads that user's profile, memory namespace, trust envelope, and personality calibration.

Three memory scopes exist:

- **Personal** — private to one user (health, finances, intimate details, private notes).
- **Shared** — household or business data (shopping list, family calendar, inventory, supplier contacts).
- **Ambient** — observations about the environment (sensor data, usage patterns, room state).

Walls matter only between personal scopes. Shared and ambient are open by design. Personality, tone, and trust calibrate per user.

**Enforceable rules**:

- Every memory write carries scope metadata. No unscoped writes.
- Personality and trust calibration are per-user, not per-instance.
- Personal scope of user A is never visible to user B, even to "helpful" summarizers.

---

## 4. Trust envelopes

- Per-user trust thresholds on a single approval rail.
- Every world-changing action goes through a proposal path, even if auto-approved at low stakes. The same machinery that handles "add milk, confirm?" is what will one day handle "drop a supplier SKU price to $46, confirm?".
- Trust envelopes expand over time per user, per tool — not in one global switch.

---

## 5. Surface-agnostic presence

- Zoe is reachable *through* devices (Jetson, phone, laptop, web, earpiece) but does not live on any one of them.
- **No Jetson-specific APIs in non-hardware code paths.** CUDA, TensorRT, GPIO, camera drivers belong behind hardware-abstraction boundaries.
- The core runs on hardware other than the Jetson without surgery.
- Voice is one surface, not the only one. Touch, web, and future clients hit the same core.

---

## 6. Universality — the "don't limit Zoe" rules

These are hard rules. Violating them closes a door we do not want closed.

- **No `home` / `family` / `household` concepts baked into the kernel.** They belong in skills and scopes, not in router code, memory schema column names, auth, or tool signatures.
- **Memory schema carries a scope/context field from day one**, even if only one value is in use today. Retrofitting scope onto a populated memory store later is painful and error-prone.
- **Credentials are keyed per user and per scope.** No global env-var-only credentials for user data. A service account for a shared tool is fine; a personal Gmail token in a shared env var is not.
- **Tools register through an allow-list mechanism**, even if the current list is permissive. The *mechanism* must exist so a future scope can have a different list without refactoring.
- **Experts / skills live as filesystem files (SKILL.md-style), discovered by the router.** Adding a new expert is a file drop, not a code edit.
- **The core runs on non-Jetson hardware without surgery.** Hardware-specific code lives behind flags like `HARDWARE_PLATFORM`, as already codified in `.cursorrules`.

---

## 7. Open questions (explicitly held, not silently answered)

These are unanswered on purpose. Do not let code silently decide them.

- **Personal-Zoe vs site-Zoe topology**: is "my Zoe at work" the same deployment I reach from a phone, or a separate site-Zoe that also knows me? Both are supported by the charter; neither is chosen yet.
- **Cross-site identity sharing**: when a user has a relationship with two Zoe instances (home + work), is there a shared "you" profile, and if so, how is it synced safely?
- **Proactive autonomy ceiling**: how aggressively may Zoe act without prompting, and how is that ceiling raised over time?
- **Multi-expert granularity**: are experts always prompt/tool packs, or may some become real OpenClaw agent loops with deliberation mode? See section 9 for harness implications.

When a PR or plan lands in territory that would answer one of these, the PR or plan must say so and the charter must be updated in the same commit.

---

## 8. What this charter does not commit us to

- Building multi-expert architecture now.
- Building work / business contexts now.
- Building cross-device presence now.
- Any schema or code changes today.

This charter commits us only to **not painting these doors shut**.

---

## 9. Harness engineering (runtime orchestration)

Recent agent-harness research shows that orchestration — the code and prompts around an LLM — often drives more outcome variation than the underlying model. Same model + different harness can mean large swings in quality, latency, and cost. The following are **default biases** when editing `services/zoe-data/routers/chat.py`, `intent_router.py`, `openclaw_ws.py`, `mcp_server.py`, or any other orchestration surface.

- **The harness is the product.** Before assuming a bigger model will fix a gap, try improving routing, prompts, memory boundaries, and tool contracts.
- **Minimize structure by default.** Bloated orchestration can match a stripped harness at many times the compute. Add framework only when measured benefit outweighs cost. Remove modules that do not earn their keep.
- **Representation over more Python.** When control flow is ambiguous or fast-moving, prefer natural-language harness pieces (clear system instructions, skill files, documented invariants) over brittle imperative trees that duplicate what the model already does well. This aligns with the existing architecture rule: no hardcoded NLU if/else in the production chat router.
- **Verify before adding verifiers.** Extra verification steps can hurt end-to-end outcomes by adding noise, latency, or wrong rejection criteria. Any verifier loop must be justified by measurement, not intuition.
- **Ablate, do not stack.** When something regresses, treat harness changes like experiments: module-by-module, with a clear rollback path. "More agent" is not always "better agent".
- **Harness portability.** Design orchestration so it can ride multiple models without rewrite. The durable asset is the harness plus skills plus tool surface — not a single provider's weights.

**References** (conceptual framing, not dependencies):

- Pan et al., *Natural-Language Agent Harnesses*, Tsinghua, March 2026. <https://arxiv.org/abs/2603.25723>
- Lee et al., *Meta-Harness*, Stanford, March 2026. <https://arxiv.org/abs/2603.28052v1>
- Overview talk: <https://www.youtube.com/watch?v=Xxuxg8PcBvc>

---

## 10. Development and contributing (Karpathy-aligned)

Zoe-the-product is not a coding agent; **we** are. These four principles — derived from Andrej Karpathy's observations on common LLM coding failure modes, popularized by [forrestchang/andrej-karpathy-skills](https://github.com/forrestchang/andrej-karpathy-skills) (MIT) — are the default stance for humans and AI assistants editing this repo. They are complementary to section 9, which targets runtime orchestration; this section targets the editing experience.

- **Think before coding.** State assumptions. Surface ambiguity and tradeoffs. Ask instead of guessing. Push back when a simpler approach exists.
- **Simplicity first.** Minimum code that solves the stated problem. No speculative abstractions, no drive-by features, no configurability that was not requested. If 200 lines could be 50, rewrite it.
- **Surgical changes.** Touch only what the task requires. Match existing style. Do not delete unrelated dead code unless explicitly asked — mention it instead. Every changed line should trace to the request.
- **Goal-driven execution.** Prefer explicit success criteria and verification (tests, validators, manual checks) over vague "make it work". Strong criteria let agents loop independently; weak criteria cause constant clarification.

These bias toward caution on non-trivial work. For typo fixes and one-liners, use judgment.

---

## 11. References

- Karpathy-inspired coding guidelines: <https://github.com/forrestchang/andrej-karpathy-skills> (MIT).
- Harness engineering overview (video): <https://www.youtube.com/watch?v=Xxuxg8PcBvc>.
- Papers: <https://arxiv.org/abs/2603.25723>, <https://arxiv.org/abs/2603.28052v1>.
- Related governance: `docs/governance/CRITICAL_FILES.md`, `docs/governance/DOCKER_NETWORKING_RULES.md`, `docs/governance/CLEANUP_SAFETY.md`, `docs/governance/MANIFEST_SYSTEM.md`.
- Enforceable rule mirror: `.cursorrules` (root).

---

## Amendment process

This charter is amended via a PR that (a) edits this file, (b) updates `.cursorrules` if an enforceable rule changes, and (c) states in the commit message which section was amended and why. Silent code changes must not answer an open question in section 7; either answer the question here first, or keep the code neutral.
