# ADR: Hindsight Bake-Off First

## Status

Accepted for evaluation.

## Context

Hindsight aligns with Zoe's continuity goal because it separates world facts, experiences, mental models, and reflection. It also fits Zoe's Postgres-centered architecture better than graph stacks that require another always-on database in the chat hot path. Zoe memory must be 100% offline, so any Hindsight evaluation must use a local model path only.

## Decision

Evaluate Hindsight before Graphiti as a runtime candidate.

Defaults for the bake-off:

- Run as an offline-only sidecar first; never make it required for normal chat or voice.
- Use Hindsight built-in `llamacpp`, Zoe's local llama-server/OpenAI-compatible endpoint, Ollama/LM Studio on localhost/private network, or another operator-approved local endpoint.
- Reject cloud LLM providers such as plain `openai`, `anthropic`, `gemini`, `groq`, `openrouter`, or `openai-codex` for Zoe memory.
- Prefer controlled recall before any write integration; route only lesson/reflection queries through it.
- Keep auto-retain off by default.
- Route durable writes through retain candidates and evidence/admission gates.
- Measure recall latency, write latency, memory pollution, scope isolation, evidence provenance, hallucination, contradiction, and fallback behavior.

## Acceptance Criteria

- Recall p95 under 600ms for low/mid budget on the selected offline model path.
- Retain can run async without blocking chat.
- Returned memories include evidence/source pointers.
- Disputed/superseded memories behave correctly.
- Missing or cross-user scope fails closed.
- Zoe can disable Hindsight without breaking MemPalace recall.


## Offline Guard

`services/zoe-data/hindsight_memory.py` enforces `HINDSIGHT_OFFLINE_ONLY=true` by default. When enabled, the sidecar URL must be localhost/private, and visible Hindsight LLM provider settings must be local (`llamacpp`, `ollama`, `lmstudio`) or `openai` only with a localhost/private `HINDSIGHT_API_LLM_BASE_URL`.
