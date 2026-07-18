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
  - branch NOT checked out by another worktree (removing one would pull the
    shared branch out from under the other — possibly a live agent session)
  - branch merged into origin/main, or detached HEAD is ancestor of origin/main
  - no activity for MIN_AGE_DAYS (default 7) by latest commit timestamp

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
worktree_count=0

# Branches checked out by MORE THAN ONE worktree.
#
# Removing one of those worktrees deletes the shared branch out from under the
# other — which may be a LIVE agent session. Seen for real: an agent worktree and
# the session driving it both sat on `claude/memory-hardening-w0-7ff9c7`, and the
# branch was merged, so every other guard passed and the tool offered to remove
# it. Git itself refuses a double-checkout for this reason; the merged-ness of a
# branch says nothing about whether someone is still standing on it.
#
# Built once up front rather than per-worktree so the scan stays O(worktrees).
shared_branches=""
while IFS= read -r br; do
  [[ -n "$br" ]] && shared_branches+="${br}"$'\n'
done < <(
  git worktree list --porcelain 2>/dev/null \
    | sed -n 's|^branch refs/heads/||p' \
    | sort | uniq -d
)

branch_is_shared() {
  local branch="$1"
  [[ -n "$branch" ]] || return 1
  grep -qxF -- "$branch" <<<"$shared_branches"
}

is_merged_ref() {
  local ref="$1"
  git merge-base --is-ancestor "$ref" "$MAIN_REF"
}

worktree_age_seconds() {
  local path="$1"
  local activity_ts
  activity_ts="$(git -C "$path" log -1 --format=%ct HEAD 2>/dev/null || echo 0)"
  if [[ "$activity_ts" == 0 ]]; then
    activity_ts="$(stat -c %Y "$path" 2>/dev/null || echo 0)"
  fi
  echo $((NOW - activity_ts))
}

while IFS= read -r line; do
  case "$line" in
    worktree\ *)
      wt_path="${line#worktree }"
      wt_branch=""
      wt_locked=0
      worktree_count=$((worktree_count + 1))
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
      elif branch_is_shared "$wt_branch"; then
        # Another worktree (possibly a live agent session) is standing on this
        # same branch. Merged-ness is irrelevant here — removing this worktree
        # would pull the branch out from under the other one.
        reason="branch $wt_branch checked out by another worktree"
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

if [[ "$worktree_count" -eq 0 ]]; then
  log "error: git worktree list returned no worktrees; refusing to continue"
  exit 1
fi

log "candidates: ${#candidates[@]}; skipped: ${#skipped[@]} (min age ${MIN_AGE_DAYS}d)"

# Print WHY each worktree was skipped. Previously these were collected and
# thrown away, so an operator saw "skipped: 79" with no way to tell a boring
# age-guard skip from a load-bearing safety skip (dirty tree, shared branch)
# without re-deriving it by hand. Set ZOE_WORKTREE_QUIET=1 to suppress.
if [[ "${ZOE_WORKTREE_QUIET:-0}" != 1 && "${#skipped[@]}" -gt 0 ]]; then
  for entry in "${skipped[@]}"; do
    log "  skip: $entry"
  done
fi

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
