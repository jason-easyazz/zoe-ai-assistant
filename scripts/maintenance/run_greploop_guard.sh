#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO="${ZOE_GITHUB_REPO:-jason-easyazz/zoe-ai-assistant}"
CALLER_ZOE_GITHUB_REPO="${ZOE_GITHUB_REPO:-}"

for env_file in "${ROOT}/services/zoe-data/.env" "${ROOT}/.env" "${HOME}/.hermes/.env"; do
    if [[ -f "${env_file}" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "${env_file}"
        set +a
    fi
done

REPO="${CALLER_ZOE_GITHUB_REPO:-${ZOE_GITHUB_REPO:-${REPO}}}"

pr_number=""
repair_mode=0
previous=""
for arg in "$@"; do
    if [[ "${previous}" == "--pr" ]]; then
        pr_number="${arg}"
        previous=""
        continue
    fi
    case "${arg}" in
        --pr)
            previous="--pr"
            ;;
        --pr=*)
            pr_number="${arg#--pr=}"
            ;;
        --once|--packet-only)
            repair_mode=1
            ;;
    esac
done

if [[ "${repair_mode}" == "1" && -n "${pr_number}" && "${ZOE_GREPLOOP_SKIP_WORKTREE_SWITCH:-0}" != "1" ]]; then
    head_branch="$(gh pr view "${pr_number}" --repo "${REPO}" --json headRefName --jq .headRefName 2>/dev/null || true)"
    if [[ -z "${head_branch}" ]]; then
        cat >&2 <<EOF
Greploop repair mode could not determine the PR head branch.
PR #${pr_number}
Repository: ${REPO}
Set ZOE_GREPLOOP_SKIP_WORKTREE_SWITCH=1 only for read-only debugging.
EOF
        exit 2
    fi
    current_branch="$(git -C "${ROOT}" branch --show-current 2>/dev/null || true)"
    if [[ "${current_branch}" != "${head_branch}" ]]; then
        worktree_path="$(git -C "${ROOT}" worktree list --porcelain 2>/dev/null | awk -v branch="branch refs/heads/${head_branch}" '
            /^worktree / { path = substr($0, 10) }
            $0 == branch && found == "" { found = path }
            END { if (found != "") print found }
        ')"
        if [[ -n "${worktree_path}" && -x "${worktree_path}/scripts/maintenance/run_greploop_guard.sh" ]]; then
            exec "${worktree_path}/scripts/maintenance/run_greploop_guard.sh" "$@"
        fi
        cat >&2 <<EOF
Greploop repair mode must run from the PR branch worktree.
PR #${pr_number} head branch: ${head_branch}
Current root: ${ROOT}
Current branch: ${current_branch:-<detached>}
No matching worktree was found. Create/check out the PR worktree, then rerun this command.
Set ZOE_GREPLOOP_SKIP_WORKTREE_SWITCH=1 only for read-only debugging.
EOF
        exit 2
    fi
fi

USER_SITE="$(python3 -c 'import site; print(site.getusersitepackages())')"
export PYTHONPATH="${USER_SITE}:${ROOT}/services/zoe-data${PYTHONPATH:+:${PYTHONPATH}}"

exec python3 "${ROOT}/scripts/maintenance/greploop_guard.py" "$@"
