#!/usr/bin/env bash
set -euo pipefail

ROOT="${ZOE_ASSISTANT_ROOT:-/home/zoe/assistant}"
MIN_AGE_DAYS="${ZOE_WORKTREE_MIN_AGE_DAYS:-7}"
EXECUTE=0

log() {
  printf '[prune-worktrees] %s\n' "$*"
}

usage() {
  cat <<'EOF'
Prune stale git worktrees whose branches are merged into origin/main.

Dry-run by default. Pass --execute to remove candidates.

Safety guards (all must pass):
  - not the live checkout
  - worktree not locked
  - worktree has no uncommitted changes
  - branch merged into origin/main, or detached HEAD is ancestor of origin/main
  - no activity for MIN_AGE_DAYS (default 7) by worktree mtime

Environment:
  ZOE_ASSISTANT_ROOT       repo root (default: /home/zoe/assistant)
  ZOE_WORKTREE_MIN_AGE_DAYS  minimum idle days (default: 7)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute) EXECUTE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) log "unknown argument: $1"; usage; exit 1 ;;
  esac
done

cd "$ROOT"
git fetch --quiet origin main 2>/dev/null || log "warning: git fetch origin main failed; using local origin/main ref"

MAIN_REF="$(git rev-parse origin/main)"
MIN_AGE_SECONDS=$((MIN_AGE_DAYS * 86400))
NOW="$(date +%s)"
LIVE_ROOT="$(git rev-parse --show-toplevel)"

candidates=()
skipped=()

is_merged_ref() {
  local ref="$1"
  git merge-base --is-ancestor "$ref" "$MAIN_REF"
}

worktree_age_seconds() {
  local path="$1"
  local mtime
  mtime="$(stat -c %Y "$path" 2>/dev/null || echo 0)"
  echo $((NOW - mtime))
}

while IFS= read -r line; do
  case "$line" in
    worktree\ *)
      wt_path="${line#worktree }"
      wt_branch=""
      wt_locked=0
      ;;
    branch\ refs/heads/*)
      wt_branch="${line#branch refs/heads/}"
      ;;
    branch\ *)
      wt_branch=""
      ;;
    locked*)
      wt_locked=1
      ;;
    *)
      ;;
  esac

  if [[ "$line" == "" && -n "${wt_path:-}" ]]; then
    reason=""
    if [[ "$wt_path" == "$LIVE_ROOT" ]]; then
      reason="live checkout"
    elif [[ "$wt_locked" == 1 ]]; then
      reason="locked"
    elif [[ -n "$(git -C "$wt_path" status --porcelain 2>/dev/null)" ]]; then
      reason="dirty"
    else
      age="$(worktree_age_seconds "$wt_path")"
      if [[ "$age" -lt "$MIN_AGE_SECONDS" ]]; then
        reason="too recent (${age}s < ${MIN_AGE_SECONDS}s)"
      elif [[ -n "$wt_branch" ]]; then
        if git show-ref --verify --quiet "refs/heads/$wt_branch"; then
          branch_tip="$(git rev-parse "$wt_branch")"
          if is_merged_ref "$branch_tip"; then
            candidates+=("$wt_path|$wt_branch|merged-branch")
          else
            reason="branch $wt_branch not merged into origin/main"
          fi
        else
          reason="branch ref missing for $wt_branch"
        fi
      else
        detached_tip="$(git -C "$wt_path" rev-parse HEAD 2>/dev/null || echo "")"
        if [[ -z "$detached_tip" ]]; then
          reason="cannot resolve detached HEAD"
        elif is_merged_ref "$detached_tip"; then
          candidates+=("$wt_path||detached-merged")
        else
          reason="detached HEAD not ancestor of origin/main"
        fi
      fi
    fi

    if [[ -n "$reason" ]]; then
      skipped+=("$wt_path: $reason")
    fi

    wt_path=""
    wt_branch=""
    wt_locked=0
  fi
done < <(git worktree list --porcelain; echo)

log "candidates: ${#candidates[@]}; skipped: ${#skipped[@]} (min age ${MIN_AGE_DAYS}d)"

if [[ "${#candidates[@]}" -eq 0 ]]; then
  log "nothing to prune"
  exit 0
fi

for entry in "${candidates[@]}"; do
  IFS='|' read -r wt_path wt_branch wt_kind <<<"$entry"
  if [[ "$EXECUTE" == 1 ]]; then
    log "removing $wt_path ($wt_kind${wt_branch:+, branch $wt_branch})"
    if ! git worktree remove "$wt_path"; then
      log "warning: could not remove $wt_path; skipping"
      continue
    fi
    if [[ -n "$wt_branch" ]] && git show-ref --verify --quiet "refs/heads/$wt_branch"; then
      git branch -d "$wt_branch" 2>/dev/null || log "warning: could not delete local branch $wt_branch"
    fi
  else
    log "would remove $wt_path ($wt_kind${wt_branch:+, branch $wt_branch})"
  fi
done

if [[ "$EXECUTE" == 1 ]]; then
  git worktree prune
  log "prune complete"
else
  log "dry-run only; pass --execute to remove"
fi
