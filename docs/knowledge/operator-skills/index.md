---
type: index
title: Operator Hermes + OpenClaw skills — retirement archive (NOT installed)
description: Version-controlled archive of every operator skill under ~/.hermes/skills and ~/.openclaw that is not recoverable from a public source. Preservation only — nothing here is read by any runtime, and restoring is a deliberate manual step.
---

# Operator Hermes + OpenClaw skills — retirement archive

## Why this bundle exists

`~/.hermes/skills` is **not a git repository and has no remote**. Neither is
`~/.openclaw/workspace/skills` (a git repo, but with no remote). Anything living
only in those directories exists in exactly one place: that box.

The operator decision to retire the OpenClaw/Hermes skills is conditional on
those skills **remaining referenceable afterwards**. This bundle is what makes
that condition true. Its rule is one line:

> Archive what cannot be re-obtained. Record where the rest comes from.

"Re-obtainable" is not assumed, it is **measured** — by diffing the live install
against the actual upstream source and recording the result. Every exclusion
below names the source it is recoverable from.

## This is a backup, not an installation

`services/zoe-data/skill_discovery.py` parses exactly two directories —
`~/.openclaw/workspace/skills/` and `~/.hermes/skills/`. This tree is read by
**nothing**. Copying a skill here does not install it, enable it, or change what
Zoe can do.

Worth knowing before you trust that module's name: **`skill_discovery.py` is a
dead-end catalogue.** Its only two outputs are `GET /api/agent/peers/{name}/card`
(zero callers — verified across Python and JS including `dist/`) and
`~/.openclaw/workspace/FEDERATION_SKILLS.md` (zero readers). It dispatches
nothing. Four separate implementations parse these same directories, and the most
sophisticated one is the only one feeding nothing.

This bundle also sits outside `skills/` on purpose: `skills/AGENTS.md` states
that operator-level Hermes skills "must not be mixed in here". That contract is
respected — these are records under `docs/knowledge/`, not skill definitions
under `skills/`.

Every file here is byte-identical to its source (verified with `cmp`). One file
was deliberately **not** copied: a stray
`zoe-engineering/SKILL.md.bak.20260615-200903` in the source directory, excluded
per the root `AGENTS.md` rule against hoarding backup copies in the repo. It
still exists at `~/.hermes/skills/zoe-engineering/` and is worth deleting there.

## Restoring

Only restore if a skill is found **missing** from `~/.hermes/skills`. Restoring
is manual and deliberate:

```bash
cp -r docs/knowledge/operator-skills/<skill-name> ~/.hermes/skills/
```

`skills_watcher.py` watches `~/.hermes/skills` and invalidates the discovery
cache on change, so a restored skill is picked up without a zoe-data restart.

## Zoe-authored Hermes skills

Nine live skills — six of which `AGENTS.md` names as binding process. These carry
`author: Zoe` in their frontmatter, exist nowhere upstream, and a `hermes`
reinstall would not bring them back:

| Skill | Notes |
|---|---|
| `zoe-engineering` | Default agent contract for Zoe engineering work |
| `github-greptile-loop` | Named in `AGENTS.md`; wraps `scripts/maintenance/greploop_guard.py` |
| `source-code-context` | opensrc-first third-party source lookup |
| `code-structure-cleanup` | Cleanup-pass contract |
| `agentic-engineering-workflow` | Micky-style workflow pack entrypoint |
| `grep-loop-review-workflow` | Micky-style workflow pack entrypoint |
| `zoe-board` | Multica board repair |
| `zoe-cloakbrowser` | Browser work via the CloakBrowser broker |
| `zoe-status-refresh` | Generated knowledge refresh |

Two stale, retained only so the retirement decision is recorded rather than
silently lost:

| Skill | Status |
|---|---|
| `zoe-graphify` | **Stale.** graphify is retired (`CLAUDE.md`); do not restore. |
| `zoe-engineering` | Description still claims "Graphify-first codebase navigation" — inaccurate; navigation is codebase-memory + Serena. Fix on restore. |

## Hermes third-party skills

`~/.hermes/skills` was reorganised upstream into **category bundles** — a
`DESCRIPTION.md` plus nested sub-skills — so the 33 top-level directories there
now hold far more than 33 skills. The nine Zoe-authored ones above are only part
of the picture; the rest are third-party (`Hermes Agent`, `community`,
`Orchestra Research`, `Nous Research`, and assorted MIT-licensed ports).

Third-party does **not** automatically mean re-obtainable. Provenance was
established by diffing the live install against two sources:

- the upstream checkout at `~/.hermes/hermes-agent` (`NousResearch/hermes-agent`), and
- the live upstream tree via `gh api repos/NousResearch/hermes-agent/contents/skills/<path>`.

That diff split the third-party content three ways.

**Archived here** — present locally, absent from upstream, no public homepage.
These are registry installs with no permanence guarantee; if the box is wiped
they are gone:

| Path | Author | Why archived |
|---|---|---|
| [`autonomous-ai-agents/kanban-codex-lane/`](autonomous-ai-agents/kanban-codex-lane/SKILL.md) | Hermes Agent | Hermes+Codex dual-lane Kanban convention; 404 upstream |
| [`creative/creative-ideation/`](creative/creative-ideation/SKILL.md) | SHL0MS | 404 upstream, no homepage in frontmatter |
| [`creative/pixel-art/`](creative/pixel-art/SKILL.md) | dodo-reach | 404 upstream, no homepage in frontmatter |
| [`diagramming/`](diagramming/DESCRIPTION.md), [`domain/`](domain/DESCRIPTION.md), [`feeds/`](feeds/DESCRIPTION.md), [`gaming/`](gaming/DESCRIPTION.md), [`gifs/`](gifs/DESCRIPTION.md), [`inference-sh/`](inference-sh/DESCRIPTION.md) | — | Category stubs that exist in no upstream tree |
| [`leisure/find-nearby/`](leisure/find-nearby/SKILL.md) | — | Whole category absent upstream |
| [`mcp/`](mcp/DESCRIPTION.md) (`mcporter`, `native-mcp`) | community, Hermes Agent | Whole category absent upstream |
| [`media/spotify/`](media/spotify/SKILL.md) | Hermes Agent | 404 upstream |
| [`mlops/research/`](mlops/research/DESCRIPTION.md), [`mlops/training/`](mlops/training/DESCRIPTION.md), [`mlops/vector-databases/`](mlops/vector-databases/DESCRIPTION.md) | Orchestra Research | 404 upstream |
| [`software-development/hermes-s6-container-supervision/`](software-development/hermes-s6-container-supervision/SKILL.md) | Hermes Agent | 404 upstream |

**Not archived — recoverable from `NousResearch/hermes-agent`.** Every file the
local install shares with that repo is byte-identical (`diff -rq` reports zero
differing files across all 23 third-party categories — only presence/absence).
`apple`, `autonomous-ai-agents` (minus the row above), `creative` (minus the rows
above), `data-science`, `email`, `github`, `media` (minus `spotify`), `mlops`
(minus the rows above), `note-taking`, `productivity`, `research`, `smart-home`,
`social-media`, `software-development` (minus the row above). The local install
is a strict *subset* of upstream in most of these — upstream has more, not less.

**Not archived — recoverable from a named public repo:**

| Path | Recover from |
|---|---|
| `creative/baoyu-comic` (v1.56.1) | `JimLiu/baoyu-skills` — tagged releases, homepage declared in its own frontmatter |
| `creative/baoyu-article-illustrator` (v1.57.0) | same |

Those two are 70 of the ~102 candidate files and are pure image-prompt libraries
with no Zoe coupling; vendoring them would have tripled the archive for content
that has a 20k-star public home.

**Not archived — already archived once.** `~/.hermes/skills/touch-panel` is
byte-identical to [`openclaw/touch-panel/`](openclaw/touch-panel/SKILL.md)
(verified with `diff -rq`). The same skill was installed into both agents; it is
preserved once, not twice.

### Excluded on a security scan — `productivity/linear`

`skillspector scan --no-llm ~/.hermes/skills/productivity/linear` returns
**100/100 CRITICAL — DO NOT INSTALL** (90 findings). It is therefore **not
imported here**, per the root `AGENTS.md` skill-safety rule.

The headline finding is `TT3` — a tainted flow from `os.environ.get` into a
network request in `scripts/linear_api.py:89`. On inspection that is
`Authorization: $LINEAR_API_KEY` on a `POST https://api.linear.app/graphql`,
i.e. **ordinary API authentication**, which is exactly the "legitimately powerful
code scores HIGH/CRITICAL" false positive the root contract warns about. No real
credential is present — the only literals in the file are example Linear document
UUIDs.

This is a **flagged gap, not a settled exclusion**: `productivity/linear` is
currently recoverable from nowhere. Before the live install is deleted, an
operator must either waive the finding (re-scan with the LLM stage against a
local/NV provider — do not egress it to an external one) and import it, or
accept losing it. Until then, **do not delete
`~/.hermes/skills/productivity/linear`.**

Everything that *was* imported scanned clean or low:
`kanban-codex-lane`, `creative-ideation`, `leisure`, `media/spotify`,
`mlops/research`, `mlops/training`, `mlops/vector-databases`, and the six
category stubs → **LOW / SAFE**. `mcp` → **10/100 LOW / SAFE** (one MEDIUM `E1`
on an `https://api.example.com` doc example). `creative/pixel-art` →
**MEDIUM / CAUTION** (two `subprocess` calls in `pixel_art_video.py` that shell
out to ffmpeg, plus no declared `permissions` block).
`software-development/hermes-s6-container-supervision` → **25/100 MEDIUM /
CAUTION** (`PE3` on a diagram of container init paths — no credential present).
Nothing here is installed or executed by archiving it.

All 35 imported files were grepped for tokens, keys, passwords, webhook URLs,
private IPs, home paths and emails before commit. The only hits were the
placeholder strings `ghp_xxxxxxxxxxxxxxxxxxxx` and `Bearer sk-xxxxxxxxxxxxxxxxxxxx`
in `mcp/native-mcp/SKILL.md` — upstream documentation examples, too short to
match any real token format, left byte-identical rather than edited.

## OpenClaw skills — [`openclaw/`](openclaw/)

27 skills from `~/.openclaw/workspace/skills/` (a git repo with **no remote**).
Unlike Hermes, these carry no `author:` metadata, so provenance was established by
diffing all 34 against the stock `skills/` shipped with `openclaw@2026.5.12`:

| Class | Count | Backed up? |
|---|---|---|
| Zoe-only (absent from stock) | 20 | yes |
| Stock but locally modified | 11 | yes, minus the 4 symlinks below |
| Stock, untouched | 3 | no — re-pullable from the npm package |

**31 of 34 OpenClaw skills are ours** —
`briefing`, `family-data`, `grocery-meal`, `ha-patterns`, `home-assistant`,
`journal`, `memory-consolidation`, `proactive`, `touch-panel`, `transactions`,
`weather`, `zoe-ui`, `dynamic-widgets`. So this is not a vendor pack.

**But none of them has ever run.** Verified 2026-07-20 by two independent
methods: session-corpus forensics (18,433 tool calls, 17,042 `read`s, **17,040 of
them `HEARTBEAT.md`, zero of any skill path**), and executing OpenClaw's own
shipped loader against the live config — it builds a 14-skill catalog containing
**no `zoe-*` entries at all**. Corroborated behaviourally: `journal_entries`,
`transactions`, `open_loops`, `background_tasks` and `dashboard_layouts` all hold
**0 rows**.

The cause is a **config bug, not a design gap.** The loader *does* scan
`workspaceDir/skills`, merged last at highest precedence — but `workspaceDir`
resolves from `agents.list[0].workspace` (which beats `agents.defaults.workspace`)
to `~/.openclaw/agents/main`, and `~/.openclaw/agents/main/skills` does not exist.
Repoint it and 12 load immediately. `agents/main` is also where `HEARTBEAT.md`
lives, which is why the corpus is 17,040 heartbeat reads.

These skills are therefore **unproven intent, not lost capability**. They are
worth reading before rebuilding the same ideas on Flue — which supports this exact
`SKILL.md` format natively — and worth nothing as a restore target.

**Two traps that made this look wired-up:**

- **Silent shadowing.** 12 workspace skills collide by name with bundled ones
  (`github`, `healthcheck`, `taskflow`, `weather`, …). The agent loads the STOCK
  version, so the catalog shows the skill "present" while the customization is inert.
- **`list_openclaw_skills` reports them as installed.** `mcp_server.py` scans the
  workspace directory directly and returns `builder_skills_installed`, so Zoe
  claims the builders are installed while the agent cannot load one.

**Four are symlinks into the repo and are NOT duplicated here** —
`zoe-capability-extender`, `zoe-page-builder`, `zoe-widget-builder` point at
`skills/openclaw/*` (already version-controlled), and `zoe-verify` dangles. All
four are rejected at load time as `symlink-escape` regardless, because the config
has no `skills.load.allowSymlinkTargets`. See
[`../../../skills/AGENTS.md`](../../../skills/AGENTS.md). Also broken:
`memory-consolidation` has a lowercase `skill.md` and could never load.

**Three are stock and NOT archived** — `openai-whisper-api`, `session-logs` and
`video-frames` are byte-identical to the copies shipped in
`openclaw@2026.5.12` (`diff -rq` against
`$(npm root -g)/openclaw/skills/<name>`), so they are re-pullable from npm.

The archived copies here are byte-identical to the live workspace. The only
difference `diff -rq` reports is a `.clawhub` marker and `_meta.json` alongside
six of them (`gh-issues`, `github`, `healthcheck`, `mcporter`, `node-connect`,
`skill-creator`) — ClawHub *install registry* metadata, not skill content.

**`~/.openclaw/skills/` is a second, separate directory** holding one skill:
`graphify` (v0.7.11, 48 KB). Not archived. graphify is retired per `CLAUDE.md`
("do not resurrect it"), the doc is third-party tool documentation rather than
anything of ours, and the Hermes-side `zoe-graphify` wrapper is already preserved
above with its retirement recorded.

## Retiring the live installs

This bundle is the precondition, not the retirement. Once it is merged, the
live directories can be removed — **except `~/.hermes/skills/productivity/linear`**,
which is still unarchived pending the scan waiver described above.

Retirement is an operator step. It is irreversible for anything not covered here,
so verify coverage first:

```bash
# 1. Confirm every live skill is either archived or listed as an exclusion above
diff <(ls ~/.hermes/skills) <(ls docs/knowledge/operator-skills)
diff <(ls ~/.openclaw/workspace/skills) <(ls docs/knowledge/operator-skills/openclaw)

# 2. Rescue the one skill archived nowhere, BEFORE any rm -rf.
mkdir -p ~/.hermes-retired
mv ~/.hermes/skills/productivity/linear ~/.hermes-retired/linear

# 3. The rm is GUARDED — it cannot run unless linear is provably safe,
#    either rescued in step 2 or already imported into this bundle.
if [ -f ~/.hermes-retired/linear/SKILL.md ] \
   || [ -f docs/knowledge/operator-skills/productivity/linear/SKILL.md ]; then
  rm -rf ~/.hermes/skills ~/.openclaw/skills ~/.openclaw/workspace/skills
else
  echo "linear is unarchived and unrescued — NOTHING DELETED"
fi
```

The guard is structural, not advisory: `rm -rf ~/.hermes/skills` takes
`productivity/linear` with it, and nothing in this bundle can bring it back, so
the destructive step lives inside the `if` rather than after a warning.

## Related

- [../../architecture/zoe-flue-integration.md](../../architecture/zoe-flue-integration.md) §8.2 — the Hermes retirement inventory these skills belong to
- [../merge-and-deploy.md](../merge-and-deploy.md) — the Greptile loop `github-greptile-loop` drives
