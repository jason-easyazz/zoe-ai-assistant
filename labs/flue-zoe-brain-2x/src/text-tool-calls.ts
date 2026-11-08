/**
 * A tool call the model PRINTED instead of making (flag `ZOE_BRAIN_TEXT_TOOLCALL_RECOVERY`, default ON; `0|false|no|off` is
 * the kill switch and restores the provider stream exactly).
 *
 * THE MISS (QA round 3b, 2026-11-07, demo user). The 4B brain answered "chuck swim goggles for asha on my to-do list" - in a
 * FRESH session, with the lists group disclosed - with the reply TEXT `add_to_list(item="chuck swim goggles for asha",
 * list_type="tasks/to-do")`. llama-server returned it as content, not as a tool call, so pi-ai saw no tool call, nothing was
 * written, and the user was shown code. Every brain-path list write in that round failed this way or worse.
 *
 * WHAT THIS DOES. The provider stream is wrapped. The reply's LEADING text is held only while it could still be the start of a
 * call (an identifier, optionally behind a backtick or a code fence; a normal reply is released on its first non-identifier
 * character, so prose is delayed by about one token). When it does open `name(`, the rest of the reply is held to the end and
 * parsed. It becomes a REAL tool call - the agent loop runs the tool with every gate it always has (writes switch, replay
 * isolation, untrusted turn) and the model answers from the result - only when it is unambiguous:
 *   - exactly one call and nothing else (a fence / backticks / a trailing `;` aside);
 *   - `name` is a tool DISCLOSED to this round (in `context.tools` after the policies) - never a hidden or unknown one;
 *   - keyword arguments only (`k=v` or `k: v`, or one JSON object), literal values decoded faithfully (an escape we cannot
 *     decode refuses), every key a SCALAR schema property the value fits (type, integer, enum, length, range; no null), every
 *     required property present.
 * Anything else is passed through untouched; zoe-data's `tool_text_guard.py` then makes sure call syntax is never shown or
 * spoken. A real tool call or thinking-only round is never touched.
 *
 * Part of the live Zoe brain (flue-zoe-brain-2x.service, :3579).
 */
import { createAssistantMessageEventStream } from '@earendil-works/pi-ai';
import type { AssistantMessage, AssistantMessageEvent, AssistantMessageEventStream, Tool, ToolCall } from '@earendil-works/pi-ai';

/** `ZOE_BRAIN_TEXT_TOOLCALL_RECOVERY`: default ON; read per call. */
export function textToolCallRecoveryEnabled(): boolean {
  const v = (process.env.ZOE_BRAIN_TEXT_TOOLCALL_RECOVERY ?? '').trim().toLowerCase();
  return !['0', 'false', 'no', 'off'].includes(v);
}

const FENCE_OPEN = '(?:```[A-Za-z]*\\s*|`)?';
// The lead can still become a call: empty, a partial fence / backtick, or an identifier behind one.
const LEAD_UNDECIDED_RE = new RegExp('^\\s*(?:`{1,3}[A-Za-z]*\\s*)?(?:[A-Za-z_][\\w.]*)?$');
const LEAD_CALL_RE = new RegExp('^\\s*' + FENCE_OPEN + '[A-Za-z_][\\w.]*\\(');

/** 'undecided' (keep holding), 'call' (opens `name(`) or 'prose' (release). Pure. */
export function classifyLead(text: string): 'undecided' | 'call' | 'prose' {
  if (LEAD_CALL_RE.test(text)) return 'call';
  if (LEAD_UNDECIDED_RE.test(text)) return 'undecided';
  return 'prose';
}

type Scalar = string | number | boolean | null;

/** A tiny literal parser over `src` from `i`: a quoted string, number, boolean or null. */
function parseValue(src: string, i: number): { value: Scalar; end: number } | null {
  const q = src[i];
  if (q === '"' || q === "'") {
    const simple: Record<string, string> = { n: '\n', t: '\t', r: '\r', '\\': '\\', "'": "'", '"': '"' };
    let out = '';
    for (let j = i + 1; j < src.length; j++) {
      const c = src[j];
      if (c === '\\') {
        const n = src[j + 1];
        if (n !== undefined && n in simple) {
          out += simple[n];
          j++;
          continue;
        }
        const hex = n === 'u' ? /^[0-9a-fA-F]{4}/.exec(src.slice(j + 2)) : null;
        if (!hex) return null; // an escape we cannot decode faithfully: never guess a value
        out += String.fromCharCode(parseInt(hex[0], 16));
        j += 5;
        continue;
      }
      if (c === q) return { value: out, end: j + 1 };
      out += c;
    }
    return null;
  }
  const m = /^(-?\d+(?:\.\d+)?|true|false|True|False|null|None)/.exec(src.slice(i));
  if (!m) return null;
  const t = m[1];
  const value = t === 'true' || t === 'True' ? true : t === 'false' || t === 'False' ? false
    : t === 'null' || t === 'None' ? null : Number(t);
  return { value, end: i + t.length };
}

/** `{name, args}` when `text` is exactly ONE printed call with keyword (or one JSON-object) arguments; null otherwise. Pure. */
export function parseTextToolCall(text: string): { name: string; args: Record<string, Scalar> } | null {
  let s = text.trim();
  const fenced = /^```[A-Za-z]*\s*([\s\S]*?)\s*```$/.exec(s);
  if (fenced) s = fenced[1].trim();
  else if (s.startsWith('`') && s.endsWith('`') && s.length > 2) s = s.slice(1, -1).trim();
  s = s.replace(/;\s*$/, '');
  // `default_api.add_to_list(` - a namespace prefix is dropped; the tool is still looked up by its own name
  const head = /^(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)\(/.exec(s);
  if (!head || !s.endsWith(')')) return null;
  const name = head[1];
  const inner = s.slice(head[0].length, -1).trim();
  if (inner.startsWith('{')) {
    try {
      const obj = JSON.parse(inner) as unknown;
      if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return null;
      const args: Record<string, Scalar> = {};
      for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
        if (v !== null && !['string', 'number', 'boolean'].includes(typeof v)) return null;
        args[k] = v as Scalar;
      }
      return { name, args };
    } catch {
      return null;
    }
  }
  const args: Record<string, Scalar> = {};
  let i = 0;
  const ws = () => { while (i < inner.length && /\s/.test(inner[i])) i++; };
  ws();
  while (i < inner.length) {
    const key = /^([A-Za-z_]\w*)\s*[=:]\s*/.exec(inner.slice(i));
    if (!key) return null; // a positional argument: ambiguous, never guessed
    i += key[0].length;
    const val = parseValue(inner, i);
    if (!val) return null;
    if (key[1] in args) return null;
    args[key[1]] = val.value;
    i = val.end;
    ws();
    if (i >= inner.length) break;
    if (inner[i] !== ',') return null;
    i++;
    ws();
  }
  return { name, args };
}

type PropSchema = {
  type?: unknown;
  enum?: unknown;
  minLength?: unknown;
  maxLength?: unknown;
  minimum?: unknown;
  maximum?: unknown;
};
type JsonSchemaLike = { type?: unknown; properties?: Record<string, PropSchema>; required?: unknown };

const num = (x: unknown): number | undefined => (typeof x === 'number' && Number.isFinite(x) ? x : undefined);

/** True when the scalar fits this property's schema - type, enum, length and range. Anything we cannot check is a refusal. */
function fits(prop: PropSchema, v: Scalar): boolean {
  const want = prop.type;
  if (want === 'string') {
    if (typeof v !== 'string') return false;
    const min = num(prop.minLength);
    const max = num(prop.maxLength);
    if (min !== undefined && v.length < min) return false;
    if (max !== undefined && v.length > max) return false;
  } else if (want === 'number' || want === 'integer') {
    if (typeof v !== 'number') return false;
    if (want === 'integer' && !Number.isInteger(v)) return false;
    const lo = num(prop.minimum);
    const hi = num(prop.maximum);
    if (lo !== undefined && v < lo) return false;
    if (hi !== undefined && v > hi) return false;
  } else if (want === 'boolean') {
    if (typeof v !== 'boolean') return false;
  } else {
    return false; // an array / object / untyped property: a printed scalar is never guessed into it
  }
  if (Array.isArray(prop.enum) && !prop.enum.includes(v)) return false;
  return true;
}

/** The call as a ToolCall when `name` is a disclosed tool and the arguments fit its schema; null otherwise. Pure. */
export function toolCallFor(parsed: { name: string; args: Record<string, Scalar> }, tools: readonly Tool[] | undefined,
  id: string): ToolCall | null {
  const tool = (tools ?? []).find((t) => t.name === parsed.name);
  if (!tool) return null;
  const schema = (tool.parameters ?? {}) as JsonSchemaLike;
  const props = schema.properties ?? {};
  const required = Array.isArray(schema.required) ? (schema.required as string[]) : [];
  const args: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(parsed.args)) {
    const prop = props[k];
    if (!prop || v === null || !fits(prop, v)) return null; // a None/null is not guessed into "not given"
    args[k] = v;
  }
  for (const r of required) if (!(r in args)) return null;
  return { type: 'toolCall', id, name: parsed.name, arguments: args };
}

let seq = 0;

/** The finished message rewritten to one real tool call, or null when its text is not one unambiguous call. Pure but for the id. */
export function recoveredMessage(message: AssistantMessage, tools: readonly Tool[] | undefined): AssistantMessage | null {
  if (message.content.some((c) => c.type === 'toolCall')) return null;
  const text = message.content.filter((c) => c.type === 'text').map((c) => (c as { text: string }).text).join('');
  const parsed = parseTextToolCall(text);
  if (!parsed) return null;
  const call = toolCallFor(parsed, tools, `text-call-${Date.now().toString(36)}-${(seq++).toString(36)}`);
  if (!call) return null;
  const kept = message.content.filter((c) => c.type === 'thinking');
  return { ...message, content: [...kept, call], stopReason: 'toolUse' };
}

/**
 * Wrap a provider stream so a printed call is made for real (see the header). Same object back when the flag is off or no tool
 * is offered this round.
 */
export function recoverTextToolCalls(inner: AssistantMessageEventStream, tools: readonly Tool[] | undefined): AssistantMessageEventStream {
  if (!textToolCallRecoveryEnabled() || !tools || tools.length === 0) return inner;
  const out = createAssistantMessageEventStream();
  void (async () => {
    const held: AssistantMessageEvent[] = [];
    let mode: 'lead' | 'call' | 'pass' = 'lead';
    let lead = '';
    const flush = () => {
      for (const e of held) out.push(e);
      held.length = 0;
    };
    try {
      for await (const ev of inner) {
        if (mode === 'pass') {
          out.push(ev);
          continue;
        }
        if (ev.type === 'done') {
          const rewritten = mode === 'call' ? recoveredMessage(ev.message, tools) : null;
          if (rewritten) {
            held.length = 0; // the printed call is never shown
            const idx = rewritten.content.length - 1;
            const call = rewritten.content[idx] as ToolCall;
            console.warn(`TEXT_TOOLCALL_RECOVERED tool=${call.name}`);
            out.push({ type: 'toolcall_start', contentIndex: idx, partial: rewritten });
            out.push({ type: 'toolcall_end', contentIndex: idx, toolCall: call, partial: rewritten });
            out.push({ type: 'done', reason: 'toolUse', message: rewritten });
            continue;
          }
          flush();
          out.push(ev);
          continue;
        }
        if (ev.type === 'error' || ev.type.startsWith('toolcall_')) {
          mode = 'pass'; // a real tool call (or an error): nothing to recover
          flush();
          out.push(ev);
          continue;
        }
        if (ev.type === 'text_delta' && mode === 'lead') {
          lead += ev.delta;
          const verdict = classifyLead(lead);
          held.push(ev);
          if (verdict === 'call') mode = 'call';
          else if (verdict === 'prose') {
            mode = 'pass';
            flush();
          }
          continue;
        }
        if (held.length === 0 && mode === 'lead' && !ev.type.startsWith('text_')) {
          out.push(ev); // start / thinking ahead of any text: nothing to hold yet
          continue;
        }
        held.push(ev);
      }
      flush();
    } catch (err) {
      flush();
      throw err;
    } finally {
      out.end();
    }
  })().catch(() => {
    /* the inner stream reports its own errors as events; a throw here has already flushed and ended */
  });
  return out;
}
