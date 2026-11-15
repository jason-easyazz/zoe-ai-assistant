/**
 * The lab app's ONLY tools: one `defineTool` per schema the kernel bridge derived. The runner writes
 * those schemas (BrainBridge.tools(), OpenAI function shape) to OSLAB_TOOLS_FILE before this
 * process starts; nothing is hand-written here. `run()` forwards the model's arguments to the bridge
 * over localhost and returns the ONE sentence the kernel's receipt produced. There is no member,
 * store, token or credential in this file: who the turn is for is set by the runner on the bridge.
 */
import { readFileSync } from 'node:fs';
import { defineTool } from '@flue/runtime';
import * as v from 'valibot';

type Spec = { type: 'string' | 'integer' | 'number' };
type Fn = { name: string; description: string; parameters: { required: string[]; properties: Record<string, Spec & { description?: string }> } };

// Types only: the kernel (and the adapter's check) own range and format, so a bad value reaches them and
// is DIAGNOSED rather than bounced by this layer. The description IS sent to the model (valibot metadata ->
// JSON schema), which is where the kernel's format hint ("24-hour HH:MM") reaches it.
function field(spec: Spec & { description?: string }) {
  const d = v.description(spec.description ?? '');
  if (spec.type === 'string') return v.pipe(v.string(), v.trim(), v.minLength(1), d);
  if (spec.type === 'integer') return v.pipe(v.number(), v.integer(), d);
  return v.pipe(v.number(), d);
}

export function bridgeUrl(): string {
  return process.env.OSLAB_BRIDGE_URL ?? '';
}

async function forward(tool: string, args: unknown): Promise<string> {
  try {
    const r = await fetch(`${bridgeUrl()}/propose`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ tool, arguments: args }),
      signal: AbortSignal.timeout(15_000),
    });
    const j = (await r.json()) as { say?: unknown };
    return typeof j.say === 'string' ? j.say : 'I could not do that. Nothing changed.';
  } catch {
    return 'I could not do that: I cannot reach my action service. Nothing changed.';
  }
}

export function labTools() {
  const file = process.env.OSLAB_TOOLS_FILE;
  if (!file) return [];
  const fns = (JSON.parse(readFileSync(file, 'utf8')) as { function: Fn }[]).map((t) => t.function);
  return fns.map((fn) => {
    const entries: Record<string, ReturnType<typeof field> | ReturnType<typeof v.optional>> = {};
    for (const [name, spec] of Object.entries(fn.parameters.properties)) {
      entries[name] = fn.parameters.required.includes(name) ? field(spec) : v.optional(field(spec));
    }
    return defineTool({
      name: fn.name,
      description: fn.description,
      input: v.object(entries),
      run: async ({ data }) => forward(fn.name, data),
    });
  });
}
