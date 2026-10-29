#!/bin/bash
# threads.sh [--count] <pr>... : print the UNRESOLVED review threads of each PR; --count prints only the number (one PR).
# Pages through ALL threads (2026-10-28: first:50 hid unresolved threads on a PR with >50 and left it BLOCKED with
# everything green; a first:100 cap would do the same past 100). Exits non-zero if any page cannot be read, so a
# caller can never mistake "API failed" for "0 unresolved".
. "$(dirname "${BASH_SOURCE[0]}")/landing_lib.sh"
OWNER=${LANDING_GH_REPO%%/*}; NAME=${LANDING_GH_REPO##*/}
COUNT=0; [ "${1:-}" = --count ] && { COUNT=1; shift; }
Q='query($o:String!,$r:String!,$n:Int!,$a:String){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100,after:$a){pageInfo{hasNextPage endCursor} nodes{id isResolved path line comments(first:1){nodes{author{login} body}}}}}}}'
rc=0
for n in "$@"; do
  [ "$COUNT" = 1 ] || echo "===== #$n"
  after=""; total=0
  while :; do
    page=$(gh api graphql -F o="$OWNER" -F r="$NAME" -F n="$n" ${after:+-F a="$after"} -f query="$Q") || { echo "threads.sh: gh api failed for #$n" >&2; exit 1; }
    echo "$page" | jq -e '(has("errors") | not) and (.data.repository.pullRequest.reviewThreads.nodes != null)' >/dev/null 2>&1 || { echo "threads.sh: unreadable page for #$n" >&2; exit 1; }
    if [ "$COUNT" = 1 ]; then
      c=$(echo "$page" | jq -r '[.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved==false)] | length')
      total=$((total + c))
    else
      echo "$page" | jq -r '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved==false) | "--- \(.id) \(.comments.nodes[0].author.login) \(.path):\(.line)\n\(.comments.nodes[0].body[0:900])"'
    fi
    [ "$(echo "$page" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage')" = true ] || break
    after=$(echo "$page" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor')
  done
  [ "$COUNT" = 1 ] && echo "$total"
done
exit $rc
