/**
 * Spike config — reads the .env knobs once and validates them.
 *
 * LAB ONLY. See ../README.md.
 */

export interface SpikeConfig {
  // --- Voice brain: the LIVE local llama.cpp (harness agents do NOT run here) ---
  /** OpenAI-compatible base URL of the live voice brain, e.g. http://127.0.0.1:11434/v1 */
  llmBaseUrl: string;
  /** Voice-brain model name (llama.cpp ignores it; any non-empty string is fine). */
  llmModel: string;
  /** Voice-brain API key; most local llama.cpp servers don't need a real one. */
  llmApiKey: string;

  // --- Harness model: SEPARATE endpoint the agents actually run on (per §2b) ---
  /** Provider style for the harness model (e.g. "openai"); used as the Flue provider id seed. */
  harnessLlmProvider: string;
  /** Harness OpenAI-compatible base URL; default a cloud/dev endpoint, swappable to local. */
  harnessLlmBaseUrl: string;
  /** Harness model name the agents are bound to. */
  harnessLlmModel: string;
  /** Harness API key (real key for a cloud/dev endpoint; may be empty for a local one). */
  harnessLlmApiKey: string;

  /** GitHub token (or empty to defer to an authenticated gh CLI). */
  githubToken: string;
  /** owner/repo, e.g. jason-easyazz/zoe-ai-assistant */
  githubRepo: string;
  /** Issue number the spike scouts + implements against. */
  targetIssue: number;
  /** Absolute path to a local clone of githubRepo to branch from. */
  zoeCheckout: string;
  /** Shell command run during the verify phase to produce evidence. */
  verifyCmd: string;
}

function req(name: string): string {
  const v = process.env[name];
  if (!v || v.trim() === '') {
    throw new Error(`Missing required env var ${name} (see .env.example)`);
  }
  return v.trim();
}

function opt(name: string, fallback: string): string {
  const v = process.env[name];
  return v && v.trim() !== '' ? v.trim() : fallback;
}

export function loadConfig(): SpikeConfig {
  // Optional: the issue can instead come from `flue run harness --input '{"issue":N}'`.
  // 0 means "unset" — the workflow resolves input.issue ?? cfg.targetIssue and
  // fails loudly there if neither is provided.
  const issueRaw = opt('TARGET_ISSUE', '0');
  const targetIssue = Number.parseInt(issueRaw, 10);
  if (issueRaw !== '0' && (!Number.isInteger(targetIssue) || targetIssue <= 0)) {
    throw new Error(`TARGET_ISSUE must be a positive integer, got "${issueRaw}"`);
  }

  return {
    // Voice brain — live local llama.cpp. The harness agents do NOT run here; it
    // is kept for reference only and is optional (the spike no longer pings it —
    // the connectivity check lives in FINDINGS).
    llmBaseUrl: opt('LLM_BASE_URL', 'http://127.0.0.1:11434/v1'),
    llmModel: opt('LLM_MODEL', 'local'),
    llmApiKey: opt('LLM_API_KEY', 'not-needed'),
    // Harness model — SEPARATE endpoint the agents run on (default OpenRouter, an
    // OpenAI-compatible cloud endpoint). The provider is registered in src/app.ts
    // as `openrouter`; model strings look like `openrouter/<model-id>`. Swap to a
    // local endpoint later by changing only these vars — no code change.
    harnessLlmProvider: opt('HARNESS_LLM_PROVIDER', 'openrouter'),
    harnessLlmBaseUrl: opt('HARNESS_LLM_BASE_URL', 'https://openrouter.ai/api/v1'),
    harnessLlmModel: opt('HARNESS_LLM_MODEL', 'openrouter/anthropic/claude-3.5-haiku'),
    // Key read from OPENROUTER_API_KEY (the box's existing key) or HARNESS_LLM_API_KEY.
    harnessLlmApiKey: opt('OPENROUTER_API_KEY', opt('HARNESS_LLM_API_KEY', '')),
    // Token may be empty if the box relies on an authenticated gh CLI.
    githubToken: opt('GITHUB_TOKEN', ''),
    githubRepo: req('GITHUB_REPO'),
    targetIssue,
    zoeCheckout: req('ZOE_CHECKOUT'),
    verifyCmd: opt('VERIFY_CMD', 'echo "verify placeholder OK"'),
  };
}
