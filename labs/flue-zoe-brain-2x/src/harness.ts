/**
 * The HARNESS OVERLAY seam (flag-dark `ZOE_BRAIN_HARNESS_DIR`): Phase 0, item S-M, of the
 * nightly self-evolution loop (docs/architecture/self-evolution-nightly-loop.md 3.1).
 *
 * WHAT IT IS. The words around the Gemma rock - the soul, the doctrines, the English manner block,
 * the tool guidance and the recall tool's shape - as FILES the loop may edit, instead of TypeScript
 * constants that need a rebuild. The loop edits an overlay directory; it never edits code.
 *
 * DEFAULT = TODAY, BYTE FOR BYTE. With `ZOE_BRAIN_HARNESS_DIR` unset (the shipped default) nothing
 * is read, nothing is logged, `/health` is unchanged, and `activeHarness()` is `COMPILED_HARNESS`,
 * whose instructions ARE `ZOE_INSTRUCTIONS` (the same string). With it set, every file the
 * overlay does NOT carry falls back to the compiled-in text, so an empty overlay is today's brain.
 *
 * THE OVERLAY. A directory (normally `<root>/current`, a symlink to a versioned directory - see
 * scripts/harness-overlay.ts for switch / revert / the ledger) holding only these entries:
 *
 *   soul.md                 the soul. MUST open with the code-held persona + identity paragraphs
 *                           (`ZOE_PERSONA_FIXED`) byte for byte and keep the recall floor paragraph
 *                           (`SOUL_RECALL_FLOOR`) verbatim; the overlay may change the rest.
 *   doctrines/<slot>.md     one per `OVERLAY_DOCTRINE_SLOTS` entry (voice-delivery, activator, ...).
 *                           `activator.md` must still carry the code-derived group catalogue.
 *   manner.md               replaces zoe-data's ENGLISH manner block, and only when zoe-data already
 *                           sent it (flag on, adult member): who gets a manner block stays in code.
 *   tool-guidance.md        `## <tool_name>` sections replacing tool DESCRIPTIONS only, for the
 *                           tools in `GUIDANCE_TOOLS`. Schemas, groups, permissions: code.
 *   recall-shape.json       {"version":1,"recall_tool":{"limit":24,"query_max_chars":500}}
 *
 * FAIL CLOSED. Anything else - an unknown file or directory, a symlink inside the overlay, a file
 * over its size cap, invalid UTF-8, a control character, a chat-template or untrusted-fence marker,
 * a soul without the code-held opening, an unknown tool section, a malformed or out-of-range
 * recall-shape.json, an unknown key - REFUSES THE WHOLE OVERLAY: the compiled-in harness is served
 * and the refusal is logged loudly (console.error) and shown on `/health`. Nothing is ever half
 * applied.
 *
 * THE HARD BOUNDARY (owner, 2026-10-19). Restraint, memory authority, forgetting, guest policy,
 * identity, the untrusted-content fence (#2133) and tool permissions stay in CODE. Concretely: the
 * identity and prompt-confidentiality doctrines are appended by `composeInstructions` from code and
 * have no slot; the persona/identity opening of the soul is verified byte for byte; `web_search`
 * (the untrusted-content channel) and `memory_retire` (the forget judge) have no guidance section;
 * restraint, authority, forget and guest policy live in zoe-data, which this file cannot reach. The
 * schema is the single `OVERLAY_SCHEMA` object, and test/harness_boundary.test.ts fails if it ever
 * gains a key touching any of those.
 *
 * WHAT THE BOUNDARY IS, AND IS NOT. It is STRUCTURAL: no overlay key reaches the code that ENFORCES
 * those floors (write gates, the fence's tool taint, identity binding, zoe-data's restraint /
 * authority / forget / guest code), and the code-held prompt text (identity, confidentiality, the
 * soul's persona opening and recall floor) cannot be removed or moved. It is NOT a semantic filter
 * on the words inside an allowed doctrine: a doctrine can still SAY anything, and prompt text is
 * steering, never enforcement. Judging what an edit says is the nightly loop's gate (held-out cells,
 * control arms, the voice replay), not this loader.
 *
 * PER TURN. `activeHarness()` is a module variable read: the composed instructions, the tool
 * guidance map and the per-turn log suffix are all computed once, at load. Each loaded file's
 * sha256 is on `/health` (`harness`) and its first 16 hex chars on the per-turn FLUE_EARLY_TEXT line.
 *
 * HOT RELOAD. Flue re-renders the agent function (agents/zoe.ts `Zoe()`) before every model turn
 * and the provider reads `activeHarness()` on every model round, so swapping the module variable is
 * a complete reload: `SIGHUP` -> `reloadHarness()`, no restart. The handler is installed only when
 * the flag is set (Node's default SIGHUP action is to exit; with the flag unset that is unchanged).
 * A reload that lands between two tool rounds of one turn gives the later round the new prompt (one
 * prefix re-prefill); the nightly loop applies at 04:28, when nobody is talking to the panel.
 *
 * Part of the live Zoe brain (flue-zoe-brain-2x.service, :3579).
 */
import { createHash } from 'node:crypto';
import { lstatSync, readdirSync, readFileSync, realpathSync } from 'node:fs';
import { basename, join } from 'node:path';
import {
  COMPILED_DOCTRINES,
  OVERLAY_DOCTRINE_SLOTS,
  ZOE_INSTRUCTIONS,
  composeInstructions,
  type OverlayDoctrineSlot,
} from './doctrines.ts';
import { SOUL_RECALL_FLOOR, ZOE_PERSONA_FIXED, ZOE_SOUL } from './soul.ts';
import { ACTIVATOR_TOOL_NAME, CORE_TOOL_NAMES, GROUP_SUMMARY, TOOL_GROUPS } from './tools/tool-groups.ts';

export const HARNESS_DIR_ENV = 'ZOE_BRAIN_HARNESS_DIR';

// ─── the compiled-in defaults the overlay can replace ─────────────────────────

/** recall_memory's explicit-recall packet size (zoe-data's for-prompt default is 12; see zoe-tools.ts). */
export const COMPILED_RECALL_TOOL_LIMIT = 24;
/** recall_memory's query hint ceiling, in characters. */
export const COMPILED_RECALL_QUERY_MAX_CHARS = 500;

/**
 * zoe-data's ENGLISH manner block, byte for byte (`services/zoe-data/lexicons_data/en.json`,
 * `manner.block`; pinned by test/harness_overlay.test.ts). The sidecar owns no manner text: this
 * constant exists only so an overlay `manner.md` can recognise the English block zoe-data sent and
 * replace it. Any other block (Spanish, or a changed English block) passes through untouched.
 */
export const COMPILED_MANNER_EN =
  'Talk like a friend, not a counsellor. Never open with "It sounds like", "That sounds like" or "I hear you", and never restate what they just said. Start with something of your own: a fact, a real opinion, a related thought, a next step. If they are upset, one short clause is enough ("Ugh, rough."), then carry on. Ask at most one question, only if the answer changes what you say next; otherwise end on a statement. If they criticise you, take it as feedback about you: name the exact thing, say what you will do. Never repeat your last answer. Never flatter.';

// ─── the schema: THE ONE PLACE that says what an overlay may touch ────────────

/**
 * Tools whose DESCRIPTION an overlay may replace. Every registered tool except:
 *   - `web_search`: the untrusted-content channel (the #2133 fence family);
 *   - `memory_retire`: the forget judge (forgetting stays in code).
 * Derived from the canonical tool-group map, so a new tool is NOT overlayable until it is added
 * here on purpose (test/harness_overlay.test.ts pins this set against the registered tools).
 */
export const GUIDANCE_TOOLS: readonly string[] = Object.freeze(
  [...CORE_TOOL_NAMES, ...Object.values(TOOL_GROUPS).flat()].filter(
    (name) => name !== 'memory_retire' && name !== 'web_search',
  ),
);

/** Registered tools that deliberately have NO overlay guidance section (the hard boundary). */
export const CODE_HELD_TOOLS: readonly string[] = Object.freeze(['memory_retire', 'web_search']);

export const RECALL_SHAPE_BOUNDS = Object.freeze({
  limit: Object.freeze({ min: 1, max: 48 }),
  query_max_chars: Object.freeze({ min: 50, max: 2000 }),
});

/**
 * Every file an overlay may hold, every JSON key, every doctrine slot and every tool section.
 * Anything not named here is refused. Pinned by test/harness_boundary.test.ts.
 */
export const OVERLAY_SCHEMA = Object.freeze({
  files: Object.freeze([
    'soul.md',
    'manner.md',
    'tool-guidance.md',
    'recall-shape.json',
    ...OVERLAY_DOCTRINE_SLOTS.map((slot) => `doctrines/${slot}.md`),
  ]),
  doctrineSlots: OVERLAY_DOCTRINE_SLOTS,
  recallShapeKeys: Object.freeze(['version', 'recall_tool', 'recall_tool.limit', 'recall_tool.query_max_chars']),
  guidanceTools: GUIDANCE_TOOLS,
});

const TOP_FILES = new Set(['soul.md', 'manner.md', 'tool-guidance.md', 'recall-shape.json']);
const DOCTRINES_DIR = 'doctrines';
const SLOT_FILES = new Map<string, OverlayDoctrineSlot>(OVERLAY_DOCTRINE_SLOTS.map((s) => [`${s}.md`, s]));

// Caps. The compiled instructions are 9,326 chars; the cap allows ~670 tokens of growth (the 8k
// llama slot is windowed, but a bigger prompt is slower to prefill on every cache miss).
export const MAX_FILE_BYTES = 16 * 1024;
export const MAX_TEXT_CHARS = 6000;
export const MAX_INSTRUCTIONS_CHARS = 12_000;
export const MAX_TOOL_DESCRIPTION_CHARS = 1000;
/** zoe-data's wire ceiling for a manner block (manner.ts MANNER_MAX_CHARS). */
export const MAX_MANNER_CHARS = 800;

// Control characters except newline (tab and CR are refused too: the compiled text has none).
const CONTROL_RE = /[\u0000-\u0009\u000b-\u001f\u007f]/;
/** Markers an overlay may never carry: chat-template tokens and the untrusted-content fences. */
export const FORBIDDEN_MARKERS: readonly string[] = Object.freeze([
  '<<<',
  '>>>',
  '<start_of_turn',
  '<end_of_turn',
  '<|',
  '|>',
]);

// ─── the loaded harness ───────────────────────────────────────────────────────

export interface RecallShape {
  limit: number;
  queryMaxChars: number;
}

export interface HarnessComponent {
  /** overlay-relative path, e.g. `soul.md`, `doctrines/activator.md` */
  file: string;
  sha256: string;
}

export interface Harness {
  /** ZOE_BRAIN_HARNESS_DIR is set */
  enabled: boolean;
  /** compiled = flag unset; overlay = loaded; refused = flag set but the overlay was refused */
  status: 'compiled' | 'overlay' | 'refused';
  /** the configured directory (as set) and what it resolved to at load */
  dir: string | null;
  resolved: string | null;
  /** basename of the resolved directory: the overlay version */
  label: string | null;
  reason: string | null;
  instructions: string;
  /** sha256 of `instructions` (the soul + every doctrine actually in force) */
  instructionsSha256: string;
  /** overlay manner.md, or null = pass zoe-data's block through */
  manner: string | null;
  /** tool name -> overlay description (only the tools the overlay carries) */
  toolGuidance: ReadonlyMap<string, string>;
  recallShape: RecallShape;
  /** the files loaded from the overlay, sorted by path */
  components: readonly HarnessComponent[];
  /** sha256 over the sorted (path, sha256) list: one id for the whole overlay version */
  overlaySha256: string | null;
  /** appended to the per-turn FLUE_EARLY_TEXT line; '' when the flag is unset */
  logSuffix: string;
  loadedAt: string;
  loadMs: number;
}

export class HarnessRefusal extends Error {}

const sha256 = (data: string | Uint8Array): string => createHash('sha256').update(data).digest('hex');

function compiledHarness(): Harness {
  return Object.freeze({
    enabled: false,
    status: 'compiled',
    dir: null,
    resolved: null,
    label: null,
    reason: null,
    instructions: ZOE_INSTRUCTIONS,
    instructionsSha256: sha256(ZOE_INSTRUCTIONS),
    manner: null,
    toolGuidance: new Map(),
    recallShape: Object.freeze({ limit: COMPILED_RECALL_TOOL_LIMIT, queryMaxChars: COMPILED_RECALL_QUERY_MAX_CHARS }),
    components: Object.freeze([]),
    overlaySha256: null,
    logSuffix: '',
    loadedAt: new Date().toISOString(),
    loadMs: 0,
  }) as Harness;
}

/** The compiled-in harness: today's brain, byte for byte. */
export const COMPILED_HARNESS: Harness = compiledHarness();

// ─── validation ───────────────────────────────────────────────────────────────

const refuse = (why: string): never => {
  throw new HarnessRefusal(why);
};

/** Decode strictly as UTF-8 and drop exactly ONE trailing newline (an editor's, or the renderer's). */
function decodeText(file: string, bytes: Uint8Array): string {
  let text: string;
  try {
    text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch {
    return refuse(`${file}: not valid UTF-8`);
  }
  if (text.charCodeAt(0) === 0xfeff) refuse(`${file}: byte-order mark`);
  return text.endsWith('\n') ? text.slice(0, -1) : text;
}

/** The shared rules for every text an overlay supplies. Returns the text unchanged. */
export function checkText(file: string, text: string, maxChars: number): string {
  if (!text.trim()) refuse(`${file}: empty (delete the file to use the compiled-in text)`);
  if (text !== text.trim()) refuse(`${file}: leading or trailing whitespace`);
  if (text.length > maxChars) refuse(`${file}: ${text.length} chars, over the ${maxChars} cap`);
  if (CONTROL_RE.test(text)) refuse(`${file}: control character`);
  for (const marker of FORBIDDEN_MARKERS) {
    if (text.includes(marker)) refuse(`${file}: forbidden marker ${JSON.stringify(marker)}`);
  }
  return text;
}

function checkSoul(text: string): string {
  checkText('soul.md', text, MAX_TEXT_CHARS);
  const opening = ZOE_PERSONA_FIXED + '\n\n';
  if (!text.startsWith(opening) || !text.slice(opening.length).trim()) {
    refuse('soul.md: must open with the code-held persona and identity paragraphs, byte for byte, then add to them');
  }
  if (!text.split('\n\n').includes(SOUL_RECALL_FLOOR)) {
    refuse('soul.md: must keep the code-held recall floor paragraph verbatim (never answer about the person from your own head)');
  }
  return text;
}

function checkDoctrine(slot: OverlayDoctrineSlot, text: string): string {
  const file = `doctrines/${slot}.md`;
  checkText(file, text, MAX_TEXT_CHARS);
  if (slot === 'activator' && !text.includes(GROUP_SUMMARY)) {
    refuse(`${file}: must carry the code-derived ability-group catalogue verbatim`);
  }
  return text;
}

const HEADING_RE = /^## ([a-z][a-z0-9_]*)$/;

/** Parse tool-guidance.md: `## <tool>` sections, each body one description. */
export function parseToolGuidance(text: string): Map<string, string> {
  const file = 'tool-guidance.md';
  checkText(file, text, MAX_FILE_BYTES);
  const out = new Map<string, string>();
  let current: string | null = null;
  let body: string[] = [];
  const flush = () => {
    if (current === null) return;
    const description = body.join('\n').replace(/^\n+/, '').replace(/\n+$/, '');
    checkText(`${file} ## ${current}`, description, MAX_TOOL_DESCRIPTION_CHARS);
    if (current === ACTIVATOR_TOOL_NAME && !description.includes(GROUP_SUMMARY)) {
      refuse(`${file} ## ${current}: must carry the code-derived ability-group catalogue verbatim`);
    }
    out.set(current, description);
  };
  for (const line of text.split('\n')) {
    if (line.startsWith('#')) {
      const m = line.match(HEADING_RE);
      if (!m) refuse(`${file}: malformed heading ${JSON.stringify(line.slice(0, 60))}`);
      flush();
      const name = m![1];
      if (CODE_HELD_TOOLS.includes(name)) refuse(`${file}: ${name} guidance is code-held`);
      if (!GUIDANCE_TOOLS.includes(name)) refuse(`${file}: unknown tool ${name}`);
      if (out.has(name)) refuse(`${file}: duplicate section ${name}`);
      current = name;
      body = [];
      continue;
    }
    if (current === null) {
      if (line.trim()) refuse(`${file}: text before the first ## heading`);
      continue;
    }
    body.push(line);
  }
  flush();
  if (out.size === 0) refuse(`${file}: no sections`);
  return out;
}

function exactKeys(file: string, where: string, value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) refuse(`${file}: ${where} is not an object`);
  const obj = value as Record<string, unknown>;
  for (const k of Object.keys(obj)) if (!keys.includes(k)) refuse(`${file}: unknown key ${where === '(root)' ? k : `${where}.${k}`}`);
  for (const k of keys) if (!(k in obj)) refuse(`${file}: missing key ${where === '(root)' ? k : `${where}.${k}`}`);
  return obj;
}

function boundedInt(file: string, key: string, v: unknown, bounds: { min: number; max: number }): number {
  if (typeof v !== 'number' || !Number.isInteger(v) || v < bounds.min || v > bounds.max) {
    refuse(`${file}: ${key} must be an integer ${bounds.min}..${bounds.max}`);
  }
  return v as number;
}

/** Parse recall-shape.json: exact keys, integers in range, nothing else. */
export function parseRecallShape(text: string): RecallShape {
  const file = 'recall-shape.json';
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return refuse(`${file}: malformed JSON`);
  }
  const root = exactKeys(file, '(root)', parsed, ['version', 'recall_tool']);
  if (root.version !== 1) refuse(`${file}: version must be 1`);
  const tool = exactKeys(file, 'recall_tool', root.recall_tool, ['limit', 'query_max_chars']);
  return {
    limit: boundedInt(file, 'recall_tool.limit', tool.limit, RECALL_SHAPE_BOUNDS.limit),
    queryMaxChars: boundedInt(file, 'recall_tool.query_max_chars', tool.query_max_chars, RECALL_SHAPE_BOUNDS.query_max_chars),
  };
}

function checkManner(text: string): string {
  return checkText('manner.md', text, MAX_MANNER_CHARS);
}

// ─── loading ──────────────────────────────────────────────────────────────────

function readRegular(path: string, rel: string): Uint8Array {
  const st = lstatSync(path);
  if (st.isSymbolicLink()) refuse(`${rel}: symlinks are not allowed inside an overlay`);
  if (!st.isFile()) refuse(`${rel}: not a regular file`);
  if (st.size > MAX_FILE_BYTES) refuse(`${rel}: ${st.size} bytes, over the ${MAX_FILE_BYTES} cap`);
  return readFileSync(path);
}

/** Every allowed file present in `resolved`, as raw bytes keyed by overlay-relative path. */
function readOverlayFiles(resolved: string): Map<string, Uint8Array> {
  const files = new Map<string, Uint8Array>();
  for (const entry of readdirSync(resolved, { withFileTypes: true })) {
    const name = entry.name;
    if (name === DOCTRINES_DIR) {
      const path = join(resolved, name);
      const st = lstatSync(path);
      if (st.isSymbolicLink() || !st.isDirectory()) refuse(`${name}: must be a plain directory`);
      for (const d of readdirSync(path, { withFileTypes: true })) {
        const rel = `${DOCTRINES_DIR}/${d.name}`;
        if (!SLOT_FILES.has(d.name)) refuse(`${rel}: unknown doctrine (slots: ${OVERLAY_DOCTRINE_SLOTS.join(', ')})`);
        files.set(rel, readRegular(join(path, d.name), rel));
      }
      continue;
    }
    if (!TOP_FILES.has(name)) refuse(`${name}: unknown entry (allowed: ${OVERLAY_SCHEMA.files.join(', ')})`);
    files.set(name, readRegular(join(resolved, name), name));
  }
  return files;
}

/**
 * Load and validate the overlay at `dir`. Throws HarnessRefusal (or an fs error) on anything the
 * schema does not allow. Pure apart from the reads: never touches the active harness.
 */
export function loadOverlay(dir: string): Harness {
  const started = performance.now();
  let resolved: string;
  try {
    resolved = realpathSync(dir);
  } catch {
    return refuse(`${dir}: does not exist`);
  }
  if (!lstatSync(resolved).isDirectory()) refuse(`${dir}: not a directory`);
  const raw = readOverlayFiles(resolved);

  const text = (rel: string): string | null => {
    const bytes = raw.get(rel);
    return bytes ? decodeText(rel, bytes) : null;
  };
  const soulText = text('soul.md');
  const soul = soulText === null ? ZOE_SOUL : checkSoul(soulText);
  const doctrines = { ...COMPILED_DOCTRINES } as Record<OverlayDoctrineSlot, string>;
  for (const slot of OVERLAY_DOCTRINE_SLOTS) {
    const t = text(`${DOCTRINES_DIR}/${slot}.md`);
    if (t !== null) doctrines[slot] = checkDoctrine(slot, t);
  }
  const instructions = composeInstructions(soul, doctrines);
  if (instructions.length > MAX_INSTRUCTIONS_CHARS) {
    refuse(`instructions: ${instructions.length} chars, over the ${MAX_INSTRUCTIONS_CHARS} cap`);
  }
  const mannerText = text('manner.md');
  const guidanceText = text('tool-guidance.md');
  const shapeText = text('recall-shape.json');
  const components = [...raw.entries()]
    .map(([file, bytes]) => ({ file, sha256: sha256(bytes) }))
    .sort((a, b) => (a.file < b.file ? -1 : a.file > b.file ? 1 : 0));
  const overlaySha256 = sha256(components.map((c) => `${c.file}\u0000${c.sha256}\n`).join(''));
  const label = basename(resolved);
  const instructionsSha256 = sha256(instructions);
  const logSuffix =
    ` harness=${label}@${overlaySha256.slice(0, 16)} instructions=${instructionsSha256.slice(0, 16)}` +
    components.map((c) => ` ${c.file}=${c.sha256.slice(0, 16)}`).join('');
  return {
    enabled: true,
    status: 'overlay',
    dir,
    resolved,
    label,
    reason: null,
    instructions,
    instructionsSha256,
    manner: mannerText === null ? null : checkManner(mannerText),
    toolGuidance: guidanceText === null ? new Map() : parseToolGuidance(guidanceText),
    recallShape:
      shapeText === null
        ? { limit: COMPILED_RECALL_TOOL_LIMIT, queryMaxChars: COMPILED_RECALL_QUERY_MAX_CHARS }
        : parseRecallShape(shapeText),
    components,
    overlaySha256,
    logSuffix,
    loadedAt: new Date().toISOString(),
    loadMs: Math.round((performance.now() - started) * 1000) / 1000,
  };
}

/** The configured overlay directory, or null when the flag is unset. */
export function configuredHarnessDir(): string | null {
  const v = (process.env[HARNESS_DIR_ENV] ?? '').trim();
  return v ? v : null;
}

/**
 * The harness the environment asks for, never throwing: flag unset -> COMPILED_HARNESS; overlay
 * refused -> the compiled-in harness marked `refused` with the reason (FAIL CLOSED).
 */
export function harnessFromEnv(): Harness {
  const dir = configuredHarnessDir();
  if (dir === null) return COMPILED_HARNESS;
  const started = performance.now();
  try {
    return loadOverlay(dir);
  } catch (err) {
    const reason = err instanceof HarnessRefusal ? err.message : `unreadable: ${String((err as Error)?.message ?? err)}`;
    return {
      ...COMPILED_HARNESS,
      enabled: true,
      status: 'refused',
      dir,
      reason,
      logSuffix: ` harness=REFUSED instructions=${COMPILED_HARNESS.instructionsSha256.slice(0, 16)}`,
      loadedAt: new Date().toISOString(),
      loadMs: Math.round((performance.now() - started) * 1000) / 1000,
    };
  }
}

// ─── the active harness ───────────────────────────────────────────────────────

let active: Harness | null = null;
let hupInstalled = false;

/** The harness in force. Lazily loaded from the environment on first use; a plain read after. */
export function activeHarness(): Harness {
  if (active === null) active = harnessFromEnv();
  return active;
}

function logLoad(h: Harness, log: (line: string) => void, warn: (line: string) => void): void {
  if (!h.enabled) return; // flag unset: the logs stay exactly as they are today
  if (h.status === 'refused') {
    warn(`HARNESS overlay REFUSED (${h.reason}); serving the compiled-in harness dir=${h.dir}`);
    return;
  }
  log(
    `HARNESS overlay loaded label=${h.label} overlay_sha256=${h.overlaySha256} instructions_sha256=${h.instructionsSha256}` +
      ` files=${h.components.length} load_ms=${h.loadMs} dir=${h.dir}`,
  );
}

/** Re-read the environment's overlay and make it the active harness (startup and SIGHUP). */
export function reloadHarness(
  log: (line: string) => void = console.log,
  warn: (line: string) => void = console.error,
): Harness {
  const h = harnessFromEnv();
  active = h;
  logLoad(h, log, warn);
  return h;
}

/**
 * Startup: load + log once, and when the flag is set install the SIGHUP reload handler (once per
 * process). With the flag unset nothing is installed, so SIGHUP keeps Node's default.
 */
export function initHarness(): Harness {
  const h = reloadHarness();
  if (h.enabled && !hupInstalled) {
    hupInstalled = true;
    process.on('SIGHUP', () => {
      try {
        reloadHarness();
      } catch (err) {
        console.error(`HARNESS reload failed (${String(err)}); keeping the previous harness`);
      }
    });
  }
  return h;
}

// ─── one harness per turn ─────────────────────────────────────────────────────

const turnHarness = new WeakMap<AbortSignal, Harness>();

/**
 * The harnesses `Zoe()` rendered recently (newest last). `Zoe()` has no handle on the turn's
 * AbortSignal, so the render cannot bind the harness itself; it leaves the object here and the
 * turn's first model round claims it by matching the instructions in the system prompt it was
 * handed. A handful is plenty: only a reload landing between a render and its first round makes the
 * render and the active harness differ. Flag unset: nothing is ever stored.
 */
const RENDER_MEMORY = 4;
const rendered: Harness[] = [];

/**
 * The harness `Zoe()` renders the turn's system prompt from: the active one, remembered so the
 * turn's first round binds THIS object even when a SIGHUP lands between the render and that round
 * (otherwise the new overlay's manner, tool guidance and recall shape ride under the old render).
 */
export function harnessForRender(): Harness {
  const h = activeHarness();
  if (!h.enabled) return h;
  const at = rendered.indexOf(h);
  if (at !== -1) rendered.splice(at, 1);
  rendered.push(h);
  if (rendered.length > RENDER_MEMORY) rendered.shift();
  return h;
}

/**
 * The newest remembered render whose instructions sit in `systemPrompt`, else null. (Two harnesses
 * with byte-identical instructions are indistinguishable here; the newest wins, and the prompt is
 * the same either way.)
 */
function renderedInto(systemPrompt: string | undefined): Harness | null {
  if (!systemPrompt) return null;
  for (let i = rendered.length - 1; i >= 0; i--) {
    if (systemPrompt.includes(rendered[i].instructions)) return rendered[i];
  }
  return null;
}

/**
 * The harness a TURN runs under, bound to the turn's AbortSignal on its first model round (the same
 * keying as the acting identity, request-identity.ts). It is the harness the turn's system prompt
 * was RENDERED from when `systemPrompt` identifies it (see `harnessForRender`), else the active
 * one. A SIGHUP that lands mid-turn therefore never changes a turn's later tool rounds, nor mixes
 * two versions into the first: the reload takes effect from the next turn. Flag unset (or no
 * signal): the active harness, nothing stored.
 */
export function harnessForTurn(signal?: AbortSignal, systemPrompt?: string): Harness {
  const now = activeHarness();
  if (!now.enabled || !signal) return now;
  const bound = turnHarness.get(signal);
  if (bound) return bound;
  const turn = renderedInto(systemPrompt) ?? now;
  turnHarness.set(signal, turn);
  return turn;
}

/**
 * Keep a turn's system prompt on the turn's harness: when `Zoe()` rendered a newer harness's
 * instructions (a reload landed after the turn began), swap them back for the turn's own. The SAME
 * context when nothing differs (always, with the flag unset).
 */
export function withTurnInstructions<C extends { systemPrompt?: string }>(context: C, turn: Harness): C {
  const now = activeHarness();
  if (turn === now || turn.instructions === now.instructions) return context;
  const system = context.systemPrompt ?? '';
  if (!system.includes(now.instructions)) return context;
  return { ...context, systemPrompt: system.replace(now.instructions, () => turn.instructions) };
}

/** Test seam: forget the active harness so the next read reloads from the environment. */
export function resetHarnessForTests(): void {
  active = null;
  rendered.length = 0;
}

/** The `/health` body field; only called when the flag is set. */
export function harnessHealth(h: Harness = activeHarness()): Record<string, unknown> {
  return {
    status: h.status,
    dir: h.dir,
    resolved: h.resolved,
    label: h.label,
    ...(h.reason ? { reason: h.reason } : {}),
    overlay_sha256: h.overlaySha256,
    instructions_sha256: h.instructionsSha256,
    components: h.components,
    loaded_at: h.loadedAt,
    load_ms: h.loadMs,
  };
}

// ─── the per-round applications (provider, tools) ─────────────────────────────

/**
 * The manner block to place for this turn: the overlay's manner.md replaces zoe-data's block only
 * when zoe-data sent the ENGLISH block. '' (no block: flag off, child, guest) stays ''.
 */
export function harnessManner(forwarded: string, h: Harness = activeHarness()): string {
  if (h.manner === null || forwarded !== COMPILED_MANNER_EN) return forwarded;
  return h.manner;
}

/**
 * Replace tool descriptions the overlay carries. The SAME context object when there is nothing to
 * replace (the flag-off path allocates nothing). Name, parameters and everything else untouched.
 */
export function withToolGuidance<C extends { tools?: readonly { name: string; description: string }[] }>(
  context: C,
  h: Harness = activeHarness(),
): C {
  const guidance = h.toolGuidance;
  if (guidance.size === 0 || !context.tools?.length) return context;
  let changed = false;
  const tools = context.tools.map((tool) => {
    const description = guidance.get(tool.name);
    if (description === undefined || description === tool.description) return tool;
    changed = true;
    return { ...tool, description };
  });
  return changed ? { ...context, tools } : context;
}
