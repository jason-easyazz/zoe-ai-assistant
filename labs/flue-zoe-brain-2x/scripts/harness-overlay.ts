/**
 * harness-overlay: render, validate, switch and revert the live brain's HARNESS OVERLAY
 * (src/harness.ts, flag-dark ZOE_BRAIN_HARNESS_DIR). Phase 0 item S-M of
 * docs/architecture/self-evolution-nightly-loop.md.
 *
 * LAYOUT (the overlay ROOT, e.g. ~/.zoe/brain-harness):
 *
 *   <root>/current        symlink -> <version>   (ZOE_BRAIN_HARNESS_DIR points HERE)
 *   <root>/<version>/     one overlay version (soul.md, doctrines/, manner.md, ...)
 *   <root>/ledger.jsonl   every switch: who, when, from/to version and overlay sha256, why
 *
 * USAGE (from labs/flue-zoe-brain-2x; `npm run harness -- <command> ...`):
 *
 *   render   <dir>                                   write the compiled-in harness as an overlay
 *   validate <dir>                                   load it exactly as the brain would; exit 1 if refused
 *   init     --root R --who W                        render the compiled harness as R/<ts>-compiled, point current at it
 *   status   --root R [--health-url U]               current version, its sha, the ledger tail, the brain's view
 *   switch   --root R --to VERSION --who W [--reason TEXT] [--reload unit:NAME|pid:N|none] [--health-url U]
 *   revert   --root R --who W [--reason TEXT] [--reload ...] [--health-url U]
 *
 * EXIT 0 only when the brain confirmed, or the switch is on disk only BY DESIGN (--reload none, or
 * the brain shows no harness on /health). EXIT 3 otherwise: timeout, refused, or a brain that was
 * not signalled (its overlay root is another directory, or no main pid).
 *
 * SWITCH = validate the target (a refused overlay is never switched to), re-point `current`
 * atomically (symlink + rename), append the ledger line, then HOT-RELOAD the brain: SIGHUP to the
 * unit's main pid, then poll `/health` until it reports the new overlay sha256 (10 s budget). The
 * signal is only sent when `/health` shows the brain was started WITH ZOE_BRAIN_HARNESS_DIR (Node's
 * default SIGHUP action is to exit, and a brain without the flag has no handler). Otherwise the
 * switch is on disk only and takes effect at the next start. REVERT = switch back to the `from` of
 * the latest ledger line, refusing when the ledger and `current` disagree.
 *
 * The default reload target is the live unit, flue-zoe-brain-2x.service on :3579: an operator or
 * the nightly loop's apply step runs this. Hand tests use --reload pid:N and a scratch port.
 */
import { execFileSync } from 'node:child_process';
import {
  closeSync,
  existsSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  openSync,
  readdirSync,
  readFileSync,
  readlinkSync,
  renameSync,
  rmSync,
  statSync,
  symlinkSync,
  unlinkSync,
  writeFileSync,
  writeSync,
} from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { COMPILED_DOCTRINES, OVERLAY_DOCTRINE_SLOTS } from '../src/doctrines.ts';
import {
  COMPILED_MANNER_EN,
  COMPILED_RECALL_QUERY_MAX_CHARS,
  COMPILED_RECALL_TOOL_LIMIT,
  GUIDANCE_TOOLS,
  HarnessRefusal,
  loadOverlay,
  type Harness,
} from '../src/harness.ts';
import { ZOE_SOUL } from '../src/soul.ts';
import { zoeTools } from '../src/tools/zoe-tools.ts';

export const CURRENT = 'current';
export const LEDGER = 'ledger.jsonl';
const LOCK = '.harness-overlay.lock';
const VERSION_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
export const RELOAD_BUDGET_MS = 10_000;

// ─── render ───────────────────────────────────────────────────────────────────

/** The compiled-in harness as overlay files (relative path -> exact bytes). */
export function renderCompiledFiles(): Map<string, string> {
  const files = new Map<string, string>();
  files.set('soul.md', ZOE_SOUL + '\n');
  for (const slot of OVERLAY_DOCTRINE_SLOTS) files.set(`doctrines/${slot}.md`, COMPILED_DOCTRINES[slot] + '\n');
  files.set('manner.md', COMPILED_MANNER_EN + '\n');
  const byName = new Map(zoeTools.map((t) => [t.name, t.description]));
  const sections = GUIDANCE_TOOLS.map((name) => {
    const description = byName.get(name);
    if (description === undefined) throw new Error(`guidance tool ${name} is not registered`);
    return `## ${name}\n\n${description}\n`;
  });
  files.set('tool-guidance.md', sections.join('\n'));
  files.set(
    'recall-shape.json',
    JSON.stringify(
      { version: 1, recall_tool: { limit: COMPILED_RECALL_TOOL_LIMIT, query_max_chars: COMPILED_RECALL_QUERY_MAX_CHARS } },
      null,
      2,
    ) + '\n',
  );
  return files;
}

/** Write the compiled-in harness into `dir` (which must not exist, or be empty). */
export function renderOverlay(dir: string): void {
  if (existsSync(dir) && readdirSync(dir).length > 0) throw new Error(`${dir} exists and is not empty`);
  mkdirSync(join(dir, 'doctrines'), { recursive: true });
  for (const [rel, text] of renderCompiledFiles()) writeFileSync(join(dir, rel), text);
}

// ─── ledger, lock, symlink ────────────────────────────────────────────────────

export interface LedgerEntry {
  ts: string;
  action: 'init' | 'switch' | 'revert' | 'reload';
  who: string;
  from?: string | null;
  to: string;
  from_sha256?: string | null;
  to_sha256?: string | null;
  reason?: string;
  outcome?: string;
  ms?: number;
}

function appendLedger(root: string, entry: LedgerEntry): void {
  const fd = openSync(join(root, LEDGER), 'a', 0o600);
  try {
    writeSync(fd, JSON.stringify(entry) + '\n');
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
}

export function readLedger(root: string): LedgerEntry[] {
  const path = join(root, LEDGER);
  if (!existsSync(path)) return [];
  return readFileSync(path, 'utf8')
    .split('\n')
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l) as LedgerEntry);
}

async function withLock<T>(root: string, fn: () => T | Promise<T>): Promise<T> {
  const path = join(root, LOCK);
  try {
    const st = statSync(path);
    if (Date.now() - st.mtimeMs > 120_000) unlinkSync(path); // a crashed run's lock
  } catch {
    /* no lock */
  }
  let fd: number;
  try {
    fd = openSync(path, 'wx', 0o600);
  } catch {
    throw new Error(`another harness-overlay run holds ${path}`);
  }
  try {
    writeSync(fd, String(process.pid));
    return await fn();
  } finally {
    closeSync(fd);
    rmSync(path, { force: true });
  }
}

/** The version `current` points at, or null when there is no `current` yet. */
export function currentVersion(root: string): string | null {
  const link = join(root, CURRENT);
  let st;
  try {
    st = lstatSync(link);
  } catch {
    return null;
  }
  if (!st.isSymbolicLink()) throw new Error(`${link} exists and is not a symlink`);
  const target = readlinkSync(link);
  if (!VERSION_RE.test(target)) throw new Error(`${link} -> ${target} is not a version in ${root}`);
  return target;
}

function checkVersion(root: string, version: string): string {
  if (!VERSION_RE.test(version) || version === CURRENT) throw new Error(`bad version name ${JSON.stringify(version)}`);
  const dir = join(root, version);
  const st = lstatSync(dir, { throwIfNoEntry: false });
  if (!st || st.isSymbolicLink() || !st.isDirectory()) throw new Error(`${dir} is not a version directory`);
  return dir;
}

/** Load a version exactly as the brain would; throws with the refusal reason. */
export function validateVersion(root: string, version: string): Harness {
  return loadOverlay(checkVersion(root, version));
}

function shaOrNull(root: string, version: string | null): string | null {
  if (!version) return null;
  try {
    return validateVersion(root, version).overlaySha256;
  } catch {
    return null;
  }
}

/** Atomically re-point `current` (a fresh symlink renamed over the old one). */
function pointCurrent(root: string, version: string): void {
  const tmp = join(root, `.${CURRENT}.${process.pid}.tmp`);
  rmSync(tmp, { force: true });
  symlinkSync(version, tmp);
  renameSync(tmp, join(root, CURRENT));
}

// ─── reload ───────────────────────────────────────────────────────────────────

export interface ReloadOptions {
  /** 'unit:<name>' (default the live unit), 'pid:<n>', or 'none' */
  reload?: string;
  healthUrl?: string;
  budgetMs?: number;
}

export interface ReloadResult {
  /**
   * `skipped` = on disk only BY DESIGN (--reload none, or no harness on the brain's /health).
   * `not_signalled` = a brain that should have switched was NOT signalled (its /health overlay root
   * is another directory, or there is no main pid): the switch did not take effect.
   */
  outcome: 'confirmed' | 'refused' | 'timeout' | 'skipped' | 'not_signalled';
  ms: number;
  detail: string;
}

async function fetchHealth(url: string): Promise<Record<string, unknown> | null> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(1500) });
    if (!res.ok) return null;
    return (await res.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function mainPid(reload: string): number {
  if (reload.startsWith('pid:')) return Number(reload.slice(4));
  const unit = reload.slice('unit:'.length);
  const out = execFileSync('systemctl', ['--user', 'show', '-p', 'MainPID', '--value', unit], { encoding: 'utf8' });
  return Number(out.trim());
}

/** SIGHUP the brain (only if it runs with the flag) and wait for /health to report `expectSha`. */
export async function reloadBrain(root: string, expectSha: string | null, opts: ReloadOptions): Promise<ReloadResult> {
  const reload = opts.reload ?? 'unit:flue-zoe-brain-2x.service';
  const healthUrl = opts.healthUrl ?? 'http://127.0.0.1:3579/health';
  const budget = opts.budgetMs ?? RELOAD_BUDGET_MS;
  if (reload === 'none') return { outcome: 'skipped', ms: 0, detail: 'reload disabled (--reload none)' };
  const before = await fetchHealth(healthUrl);
  const view = before?.harness as Record<string, unknown> | undefined;
  if (!view) {
    return {
      outcome: 'skipped',
      ms: 0,
      detail: `${healthUrl} shows no harness (brain down, or started without ZOE_BRAIN_HARNESS_DIR): switched on disk only, takes effect at the next start`,
    };
  }
  if (resolve(String(view.dir ?? '')) !== resolve(join(root, CURRENT))) {
    return { outcome: 'not_signalled', ms: 0, detail: `the brain's overlay dir is ${String(view.dir)}, not ${join(root, CURRENT)}: not signalled` };
  }
  const pid = mainPid(reload);
  if (!Number.isInteger(pid) || pid <= 1) return { outcome: 'not_signalled', ms: 0, detail: `no main pid for ${reload}` };
  const started = performance.now();
  process.kill(pid, 'SIGHUP');
  for (;;) {
    const h = (await fetchHealth(healthUrl))?.harness as Record<string, unknown> | undefined;
    // Signal -> the brain's /health reporting the new overlay: the measured reload latency.
    const ms = Math.round((performance.now() - started) * 10) / 10;
    const fresh = h !== undefined && String(h.loaded_at) > String(view.loaded_at);
    if (fresh && h.overlay_sha256 === expectSha && h.status === 'overlay') return { outcome: 'confirmed', ms, detail: `pid ${pid}` };
    if (fresh && h.status === 'refused') {
      return { outcome: 'refused', ms, detail: `brain refused the overlay: ${String(h.reason)}` };
    }
    if (ms > budget) return { outcome: 'timeout', ms, detail: `no confirmation within ${budget} ms` };
    await new Promise((r) => setTimeout(r, 50));
  }
}

// ─── commands ─────────────────────────────────────────────────────────────────

export interface SwitchResult {
  from: string | null;
  to: string;
  fromSha: string | null;
  toSha: string | null;
  reload: ReloadResult;
}

async function doSwitch(
  root: string,
  target: string | (() => string),
  who: string,
  action: 'init' | 'switch' | 'revert',
  reason: string | undefined,
  opts: ReloadOptions,
): Promise<SwitchResult> {
  if (!who.trim()) throw new Error('--who is required');
  // The lock is held through the reload and its confirmation, so a concurrent switch can never
  // re-point `current` or signal while this one is still being confirmed.
  return withLock(root, async () => {
    // A revert resolves its target HERE, under the lock, from the ledger as it stands now.
    const to = typeof target === 'function' ? target() : target;
    const harness = validateVersion(root, to); // a refused overlay is never switched to
    const from = currentVersion(root);
    const fromSha = shaOrNull(root, from);
    pointCurrent(root, to);
    appendLedger(root, {
      ts: new Date().toISOString(),
      action,
      who,
      from,
      to,
      from_sha256: fromSha,
      to_sha256: harness.overlaySha256,
      ...(reason ? { reason } : {}),
    });
    const toSha = harness.overlaySha256;
    const reload = await reloadBrain(root, toSha, opts);
    appendLedger(root, { ts: new Date().toISOString(), action: 'reload', who, to, outcome: reload.outcome, ms: reload.ms });
    return { from, to, fromSha, toSha, reload };
  });
}

export async function switchTo(root: string, to: string, who: string, reason?: string, opts: ReloadOptions = {}): Promise<SwitchResult> {
  return doSwitch(root, to, who, 'switch', reason, opts);
}

/** Restore the overlay that was current before the latest switch. */
export async function revert(root: string, who: string, reason?: string, opts: ReloadOptions = {}): Promise<SwitchResult> {
  return doSwitch(
    root,
    () => {
      const last = readLedger(root).filter((e) => e.action !== 'reload').at(-1);
      if (!last) throw new Error(`${join(root, LEDGER)} has no switch to revert`);
      const current = currentVersion(root);
      if (last.to !== current) {
        throw new Error(`ledger says current is ${last.to} but ${join(root, CURRENT)} -> ${current}: fix by hand, then switch`);
      }
      if (!last.from) throw new Error(`the latest switch (${last.action} to ${last.to}) has no previous overlay to restore`);
      return last.from;
    },
    who,
    'revert',
    reason,
    opts,
  );
}

/**
 * CLI exit status for a switch: 0 ONLY when the brain confirmed it or it was on disk only by design
 * (`skipped`: no flag on the brain, or --reload none); 3 for everything else - a signalled brain
 * that did not confirm (timeout), refused the overlay, or a brain that was never signalled
 * (`not_signalled`: another overlay root, no main pid). The caller (the nightly loop's apply step)
 * must treat nonzero as "the switch did not take effect". Allow-listed, so a new outcome fails closed.
 */
export function switchExitCode(r: SwitchResult): number {
  return r.reload.outcome === 'confirmed' || r.reload.outcome === 'skipped' ? 0 : 3;
}

/** Render the compiled-in harness as a version and point `current` at it (a fresh root). */
export async function init(root: string, who: string, opts: ReloadOptions = {}): Promise<SwitchResult> {
  mkdirSync(root, { recursive: true });
  const version = `${new Date().toISOString().replace(/[:.]/g, '-')}-compiled`;
  renderOverlay(join(root, version));
  return doSwitch(root, version, who, 'init', 'compiled-in harness (parity baseline)', opts);
}

// ─── CLI ──────────────────────────────────────────────────────────────────────

function parseArgs(argv: string[]): { cmd: string; pos: string[]; flags: Map<string, string> } {
  const [cmd = 'help', ...rest] = argv;
  const pos: string[] = [];
  const flags = new Map<string, string>();
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i];
    if (a.startsWith('--')) {
      const v = rest[i + 1];
      if (v === undefined || v.startsWith('--')) throw new Error(`${a} needs a value`);
      flags.set(a.slice(2), v);
      i++;
    } else pos.push(a);
  }
  return { cmd, pos, flags };
}

function need(flags: Map<string, string>, key: string): string {
  const v = flags.get(key);
  if (!v) throw new Error(`--${key} is required`);
  return v;
}

function describe(h: Harness): Record<string, unknown> {
  return {
    status: h.status,
    label: h.label,
    overlay_sha256: h.overlaySha256,
    instructions_sha256: h.instructionsSha256,
    instructions_chars: h.instructions.length,
    tool_guidance_sections: h.toolGuidance.size,
    manner: h.manner === null ? 'pass-through' : 'overlay',
    recall_shape: h.recallShape,
    components: h.components,
  };
}

export async function main(argv: string[]): Promise<number> {
  const { cmd, pos, flags } = parseArgs(argv);
  const reloadOpts: ReloadOptions = { reload: flags.get('reload'), healthUrl: flags.get('health-url') };
  const print = (v: unknown) => console.log(JSON.stringify(v, null, 2));
  switch (cmd) {
    case 'render': {
      if (!pos[0]) throw new Error('render <dir>');
      renderOverlay(pos[0]);
      print({ rendered: resolve(pos[0]), ...describe(loadOverlay(pos[0])) });
      return 0;
    }
    case 'validate': {
      if (!pos[0]) throw new Error('validate <dir>');
      try {
        print(describe(loadOverlay(pos[0])));
        return 0;
      } catch (err) {
        if (!(err instanceof HarnessRefusal)) throw err;
        print({ status: 'refused', reason: err.message });
        return 1;
      }
    }
    case 'init':
    case 'switch':
    case 'revert': {
      const root = need(flags, 'root');
      const who = need(flags, 'who');
      const r =
        cmd === 'init'
          ? await init(root, who, reloadOpts)
          : cmd === 'switch'
            ? await switchTo(root, need(flags, 'to'), who, flags.get('reason'), reloadOpts)
            : await revert(root, who, flags.get('reason'), reloadOpts);
      print(r);
      const code = switchExitCode(r);
      if (code) console.error(`harness-overlay: ${cmd} is on disk but the brain did NOT switch to it (${r.reload.outcome}: ${r.reload.detail})`);
      return code;
    }
    case 'status': {
      const root = need(flags, 'root');
      const current = currentVersion(root);
      const health = await fetchHealth(flags.get('health-url') ?? 'http://127.0.0.1:3579/health');
      print({
        root: resolve(root),
        current,
        current_sha256: shaOrNull(root, current),
        ledger_tail: readLedger(root).slice(-5),
        brain: health ? (health.harness ?? 'running without ZOE_BRAIN_HARNESS_DIR') : 'unreachable',
      });
      return 0;
    }
    default:
      console.log('usage: harness-overlay render|validate|init|status|switch|revert (see the header of scripts/harness-overlay.ts)');
      return cmd === 'help' ? 0 : 2;
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (err) => {
      console.error(`harness-overlay: ${err instanceof Error ? err.message : String(err)}`);
      process.exit(1);
    },
  );
}
