#!/usr/bin/env bash
set -euo pipefail

# Prune stale git worktrees under ~/.worktrees (and other registered paths).
#
# A worktree is a removal candidate ONLY when ALL of the following hold:
#   - it is not the live checkout (the main working tree)
#   - it is not locked
#   - `git status --porcelain` is empty (no dirty or untracked tracked-path work)
#   - its work is on main: HEAD is an ancestor of origin/main, or its branch
#     matches a merged PR head (squash merges leave no ancestry)
#   - it has had no filesystem activity for at least AGE_DAYS (default 7)
#
# Dry-run by default; pass --execute to remove. Never uses --force.

ROOT="${ZOE_ASSISTANT_ROOT:-/home/zoe/assistant}"
AGE_DAYS="${PRUNE_AGE_DAYS:-7}"
MODE="${1:-}"

log() {
  printf '[prune-worktrees] %s\n' "$*"
}

cd "$ROOT"
git fetch --quiet origin main || log "warning: git fetch failed; using last-known origin/main"
git worktree prune

now="$(date +%s)"
age_limit=$((AGE_DAYS * 86400))
candidates=()

# One batched lookup: head branch names of merged PRs (squash merges leave
# no ancestry, so the ancestor check alone misses most merged branches).
merged_heads=""
if command -v gh >/dev/null 2>&1; then
  merged_heads="$(gh pr list --state merged --limit 1000 --json headRefName --jq '.[].headRefName' 2>/dev/null || true)"
fi

is_merged_pr_head() {
  [[ -n "$1" && -n "$merged_heads" ]] && grep -Fxq "$1" <<<"$merged_heads"
}

while IFS= read -r line; do
  case "$line" in
    "worktree "*) wt="${line#worktree }"; locked=0 ;;
    "locked"*) locked=1 ;;
    "")
      [[ -z "${wt:-}" ]] && continue
      reason=""
      if [[ "$wt" == "$ROOT" ]]; then
        reason="live checkout"
      elif [[ "$locked" == 1 ]]; then
        reason="locked"
      elif [[ ! -d "$wt" ]]; then
        reason="missing (git worktree prune handles it)"
      elif [[ -n "$(git -C "$wt" status --porcelain 2>/dev/null | head -1)" ]]; then
        reason="dirty"
      elif ! git -C "$wt" merge-base --is-ancestor HEAD origin/main 2>/dev/null \
          && ! is_merged_pr_head "$(git -C "$wt" branch --show-current 2>/dev/null)"; then
        reason="not merged into origin/main"
      else
        # Activity = newest of: root dir mtime, per-worktree git index mtime
        # (updated by any git operation), and last commit time. Root mtime
        # alone misses edits below the top level.
        last_active="$(stat -c %Y "$wt")"
        gitdir="$(git -C "$wt" rev-parse --absolute-git-dir 2>/dev/null || true)"
        if [[ -n "$gitdir" && -f "$gitdir/index" ]]; then
          idx_mtime="$(stat -c %Y "$gitdir/index")"
          (( idx_mtime > last_active )) && last_active="$idx_mtime"
        fi
        commit_time="$(git -C "$wt" log -1 --format=%ct 2>/dev/null || echo 0)"
        (( commit_time > last_active )) && last_active="$commit_time"
        if (( now - last_active < age_limit )); then
          reason="active within ${AGE_DAYS}d"
        fi
      fi
      if [[ -z "$reason" ]]; then
        candidates+=("$wt")
      else
        log "skip: $wt ($reason)"
      fi
      wt=""
      ;;
  esac
done < <(git worktree list --porcelain; echo)

if [[ ${#candidates[@]} -eq 0 ]]; then
  log "no prunable worktrees"
  exit 0
fi

log "${#candidates[@]} prunable worktree(s):"
printf '  %s\n' "${candidates[@]}"

if [[ "$MODE" != "--execute" ]]; then
  log "dry-run complete; re-run with --execute to remove"
  exit 0
fi

for wt in "${candidates[@]}"; do
  branch="$(git -C "$wt" branch --show-current 2>/dev/null || true)"
  log "removing $wt"
  if ! git worktree remove "$wt"; then
    log "failed to remove $wt (in-progress operation or new changes?); skipping"
    continue
  fi
  if [[ -n "$branch" ]]; then
    if git branch -d "$branch" 2>/dev/null; then
      log "deleted merged branch $branch"
    elif is_merged_pr_head "$branch"; then
      # squash-merged: content is on main but ancestry is not, -d refuses
      git branch -D "$branch" && log "deleted squash-merged branch $branch"
    else
      log "kept branch $branch (not fully merged)"
    fi
  fi
done
git worktree prune
log "done"
