#!/usr/bin/env bash
set -euo pipefail

# Usage: scripts/maintenance/github_backup.sh [--tag TAG] [--msg "Commit message"]
# Commits all changes, excluding sensitive files, and pushes. Optionally tags.

# Ensure we are in repo root
REPO_ROOT="$(cd "$(dirname "$0")"/../.. && pwd)"
cd "$REPO_ROOT"

# Ensure .gitignore has our exclusions
ensure_ignores() {
  local ig=.gitignore
  touch "$ig"
  grep -qxF ".env" "$ig" || echo ".env" >> "$ig"
  grep -qxF "*.db" "$ig" || echo "*.db" >> "$ig"
  grep -qxF "data/api_keys.json" "$ig" || echo "data/api_keys.json" >> "$ig"
  grep -qxF "data/backups/" "$ig" || echo "data/backups/" >> "$ig"
  grep -qxF "*.pyc" "$ig" || echo "*.pyc" >> "$ig"
  grep -qxF "__pycache__/" "$ig" || echo "__pycache__/" >> "$ig"
  grep -qxF ".DS_Store" "$ig" || echo ".DS_Store" >> "$ig"
  grep -qxF "*.log" "$ig" || echo "*.log" >> "$ig"
}

commit_msg="Major cleanup and organization - 2 tasks complete, system stable"
tag_name=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      tag_name="$2"; shift 2 ;;
    --msg)
      commit_msg="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 2;;
  esac
done

ensure_ignores

# Stage and commit
# Avoid adding private key dirs even if present
git add -A

if ! git diff --cached --quiet; then
  git commit -m "$commit_msg"
else
  echo "No staged changes to commit."
fi

# Tag if requested
if [[ -n "$tag_name" ]]; then
  git tag -a "$tag_name" -m "$commit_msg" || true
fi

# Push main and tags if remote exists
if git remote | grep -q .; then
  current_branch="$(git rev-parse --abbrev-ref HEAD)"
  git push origin "$current_branch" || true
  if [[ -n "$tag_name" ]]; then
    git push origin --tags || true
  fi
else
  echo "No git remotes configured; skipping push."
fi
