/**
 * Bounded connectivity check (run via `npm run check:llm`).
 *
 * Sends ONE small, non-streaming, low-max_tokens request to LLM_BASE_URL to
 * prove the provider target is reachable and OpenAI-compatible. Intentionally
 * tiny so it doesn't load whatever model is serving.
 *
 * LAB ONLY.
 */
import { loadConfig } from './config.ts';

async function main(): Promise<void> {
  const cfg = loadConfig();
  const url = `${cfg.llmBaseUrl.replace(/\/$/, '')}/chat/completions`;
  const started = Date.now();

  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${cfg.llmApiKey}`,
    },
    body: JSON.stringify({
      model: cfg.llmModel,
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
