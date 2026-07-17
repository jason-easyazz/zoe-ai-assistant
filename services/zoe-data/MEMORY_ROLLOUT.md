# People + Memory Rollout Flags

This rollout uses environment flags so the new memory stack can be enabled gradually.

## Flags

- `PEOPLE_CUSTOM_FIELDS=true`
  - Enables API/UI use of dynamic custom field schema.
- `MEMORY_AUTO_INGEST=false`
  - When false, OpenClaw-extracted memories go to review queue.
  - When true, high-confidence preference memories may auto-approve.

## Hindsight offline flags

Hindsight remains a bake-off sidecar, not a production chat hot path.
Defaults are intentionally local/offline and non-retaining:

- `HINDSIGHT_ENABLED=false`
  - Keeps the Hindsight client disabled until a controlled bake-off or rollout
    explicitly enables it.
- `HINDSIGHT_BASE_URL=http://127.0.0.1:8888`
  - Must stay on localhost or a private-network address while
    `HINDSIGHT_OFFLINE_ONLY=true`.
- `HINDSIGHT_OFFLINE_ONLY=true`
  - Rejects public/cloud memory endpoints, public LLM providers, and public
    embedding endpoints unless an OpenAI-compatible provider is pointed at a
    local/private base URL.
- `HINDSIGHT_AUTO_RETAIN=false`
  - Prevents blind durable writes. Retain candidates must stay pending until
    memory admission, evidence, approval, and verification gates allow
    promotion.
- `HINDSIGHT_ASYNC_RETAIN=true`
  - Keeps approved retain execution off the chat response path.
- `HINDSIGHT_BANK_PREFIX=zoe`
  - Prefixes isolated per-user and per-scope Hindsight banks.
- `HINDSIGHT_TIMEOUT_SECONDS=6.0`
  - Caps sidecar calls during bake-off and controlled recall tests.

Local model/provider settings:

- `HINDSIGHT_API_LLM_PROVIDER=llamacpp|ollama|lmstudio|vllm|openai`
  - `openai` is allowed only when `HINDSIGHT_API_LLM_BASE_URL` points to a
    localhost/private OpenAI-compatible endpoint.
- `HINDSIGHT_API_LLM_BASE_URL=http://127.0.0.1:<port>/v1`
  - Required for OpenAI-compatible local runtimes.
- `HINDSIGHT_API_EMBEDDINGS_PROVIDER=local|onnx|sentence_transformers|tei|openai|custom`
  - Public/cloud embedding providers are rejected by the offline validator.
- `HINDSIGHT_API_EMBEDDINGS_TEI_URL=http://127.0.0.1:<port>`
  - Required when using a local TEI embedding service.
- `HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL=http://127.0.0.1:<port>/v1`
  - Required when using an OpenAI-compatible local/private embedding endpoint.
- `HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL`,
  `HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_PATH`,
  `HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_ID`, or
  `HINDSIGHT_API_EMBEDDINGS_OPENAI_MODEL`
  - Identifies the local embedding model for the selected provider.

Do not disable offline-only mode or enable Hindsight auto-retain in production.
The offline-memory validator treats those settings as rollout blockers.

## Suggested rollout stages

1. Enable `PEOPLE_CUSTOM_FIELDS=true` only.
2. Keep `MEMORY_AUTO_INGEST=false` until review queue quality is validated.
3. Enable `MEMORY_AUTO_INGEST=true` for a small cohort.
4. Monitor `memory_items.status` distribution and rejection rate.

## Rollback

- Set feature-enabling flags to false and restart `zoe-data`.
- When backing out Hindsight bake-off traffic, set `HINDSIGHT_ENABLED=false`
  and `HINDSIGHT_AUTO_RETAIN=false`, and keep
  `HINDSIGHT_OFFLINE_ONLY=true`.
- Existing `memory_items` records are preserved; only behavior changes.
- UI continues to function with local SQL memory list/search.
