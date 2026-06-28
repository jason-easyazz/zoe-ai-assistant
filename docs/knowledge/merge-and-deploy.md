---
type: Reference
title: Merge & Deploy Discipline
description: How code actually ships in Zoe — that a merged PR is not a deploy, the protected-main merge gates, and the Greptile/greploop gotchas (large-PR skip, thread resolution, REST-not-GraphQL verification).
tags: [git, merge, deploy, greptile, ci, workflow]
timestamp: 2026-06-26T00:00:00Z
---

# Merge & Deploy Discipline

The non-obvious rules for getting a change reviewed, merged, and actually live. Binding workflow
prose lives in the root `AGENTS.md` (Greptile PR loop, Branching policy); this records *what is true*
so an agent doesn't relearn it the hard way. Runtime/deploy context: [runtime-topology.md](runtime-topology.md).

## Merged ≠ live

There is **no deploy pipeline.** Live `zoe-data` runs uvicorn from the `/home/zoe/assistant` checkout,
which is usually on a **feature branch** (e.g. `feat/people-card-7in-panel` as of 2026-06-26) — **not
`main`**. So **landing a PR on `main` does not deploy it.** To deploy: get the change into that
checkout, then `systemctl --user restart zoe-data.service`. Always run a focused local check / voice
replay before calling something live-verified — never treat a green PR as "shipped."

## Protected `main` — the merge gates (verified live 2026-06-26)

- `strict = true` — a PR must be **up to date with `main`** to merge (it can sit **BEHIND** if `main`
  races ahead; clear with update-branch / re-run).
- Required status checks: **`validate`**, **`GitGuardian Security Checks`**, **`Greptile Review`**.
- `required_conversation_resolution = true` — **every review thread must be resolved**, not just replied to.
- `0` required human approvals → green checks + resolved threads = mergeable; repo `allow_auto_merge = true`.
- **Never** `--admin` / `--force` (no bypassing protection) unless the operator explicitly asks for that
  emergency path. Merge with `gh pr merge <n> --squash --auto`.

## Greptile / greploop gotchas

- **Greptile silently SKIPS large PRs** (>~50 files) and ignores `docs/archive/**`. If the
  `Greptile Review` check never posts, the PR can't satisfy the gate → **keep PRs small** (use
  `/split-to-prs` when a branch grows). This is *the* reason big PRs stall.
- **Resolve threads via GraphQL `resolveReviewThread`** — replying to a Greptile comment does NOT
  satisfy `required_conversation_resolution`; the thread must be marked resolved.
- **Verify a merge via REST, not GraphQL.** On this host the GraphQL `pr view` is unreliable (phantom
  merged states / SHAs). Trust `gh api repos/:owner/:repo/pulls/N --jq .merged` and the commits on
  `main`.

## Worktree hygiene

- Work in a **dedicated git worktree** (`~/.worktrees/<slug>`), branch off fresh `origin/main`. Never
  switch the live checkout (`/home/zoe/assistant`) to a feature branch for agent work.
- **One checkout-driver at a time.** Never run a background PR-merge/greploop driver (which does
  `git checkout`/`reset`) against the same working tree you're editing — it has silently wiped
  uncommitted work. Commit first, or isolate in a separate worktree.
- Branches die at merge (`delete_branch_on_merge`); merged task worktrees are auto-reclaimed.
