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
  const issueRaw = req('TARGET_ISSUE');
  const targetIssue = Number.parseInt(issueRaw, 10);
  if (!Number.isInteger(targetIssue) || targetIssue <= 0) {
    throw new Error(`TARGET_ISSUE must be a positive integer, got "${issueRaw}"`);
  }

  return {
    // Voice brain — live local llama.cpp; the harness agents are NOT bound here.
    llmBaseUrl: req('LLM_BASE_URL'),
    llmModel: opt('LLM_MODEL', 'local'),
    llmApiKey: opt('LLM_API_KEY', 'not-needed'),
    // Harness model — separate endpoint (default cloud/dev); swap to local later
    // by changing only HARNESS_LLM_BASE_URL / HARNESS_LLM_MODEL, no code change.
    harnessLlmProvider: opt('HARNESS_LLM_PROVIDER', 'openai'),
    harnessLlmBaseUrl: req('HARNESS_LLM_BASE_URL'),
    harnessLlmModel: req('HARNESS_LLM_MODEL'),
    harnessLlmApiKey: opt('HARNESS_LLM_API_KEY', ''),
    // Token may be empty if the box relies on an authenticated gh CLI.
    githubToken: opt('GITHUB_TOKEN', ''),
    githubRepo: req('GITHUB_REPO'),
    targetIssue,
    zoeCheckout: req('ZOE_CHECKOUT'),
    verifyCmd: opt('VERIFY_CMD', 'echo "verify placeholder OK"'),
  };
}
