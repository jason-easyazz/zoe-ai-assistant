/**
 * Bounded connectivity check (run via `npm run check:llm`).
 *
 * Sends ONE small, non-streaming, low-max_tokens request to the HARNESS model
 * endpoint (HARNESS_LLM_BASE_URL) — the model the agents actually run on — to
 * prove it is reachable and OpenAI-compatible before the full loop. It does NOT
 * touch the live voice brain on :11434. Intentionally tiny.
 *
 * LAB ONLY.
 */
import { loadConfig } from './config.ts';

async function main(): Promise<void> {
  const cfg = loadConfig();
  const url = `${cfg.harnessLlmBaseUrl.replace(/\/$/, '')}/chat/completions`;
  const started = Date.now();

  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${cfg.harnessLlmApiKey}`,
    },
    body: JSON.stringify({
      model: cfg.harnessLlmModel,
      messages: [{ role: 'user', content: 'reply with the single word: ok' }],
      max_tokens: 3,
      temperature: 0,
      stream: false,
    }),
    signal: AbortSignal.timeout(15_000),
  });

  const ms = Date.now() - started;
  if (!res.ok) {
    throw new Error(`LLM endpoint returned HTTP ${res.status} after ${ms}ms`);
  }
  const json: any = await res.json();
  const content = json?.choices?.[0]?.message?.content ?? '<no content>';
  console.log(`OK  HTTP ${res.status}  ${ms}ms  model=${json?.model ?? '?'}`);
  console.log(`reply: ${JSON.stringify(content)}`);
}

main().catch((err) => {
  console.error('LLM connectivity check FAILED:', err?.message ?? err);
  process.exit(1);
});
