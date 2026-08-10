#!/usr/bin/env bash
# Read-only ABI signature scan: which numpy headers was each compiled extension built against?
# numpy 1.x headers embed "numpy.core.multiarray failed to import"
# numpy 2.x headers embed "numpy._core.multiarray failed to import"
#
# FAIL-CLOSED: this is the migration's before/after verification instrument, so
# "I scanned nothing" must never look like "I found nothing", and "I scanned
# PART of it" must never look like "I scanned all of it". Three distinct
# refusals, each with its own exit code so a caller can tell them apart:
#
#   2 — the root is not a directory (typo'd or stale path)
#   4 — the root held no compiled extensions at all (wrong root)
#   5 — the scan is INCOMPLETE: at least one extension that exists was never
#       examined. The matches found so far are still printed, but the report is
#       explicitly partial and says how many went unexamined.
#
# Exit 5 covers the whole CLASS "an unexamined binary reached the report", which
# has two instances at two different levels, and both must be closed or the
# instrument still lies:
#
#   walk level — `find` cannot descend an unreadable subtree. A "check the root
#     is a directory" guard passes here, so the walk's own status is captured.
#   FILE level — `find` lists a .so but `strings` cannot open it (mode 000, bad
#     ACL, dangling symlink). This one is nastier: nothing upstream fails, the
#     file is counted, and the report reads as clean. It was open until
#     2026-08-10 because `strings ... 2>/dev/null` discarded the diagnostic and
#     `tr` — the last stage of the pipeline — owned the exit status, so neither
#     the error nor the failure survived. Reproduced then: one readable .so plus
#     one `chmod 000` .so reported "scanned 2" and exited 0.
#
# Neither `find`'s nor `strings`' diagnostics are discarded, so stderr always
# names the path that could not be read.
#
# The scanned / unreadable counts go to stderr so an empty report is
# attributable. Matches on stdout only, one per line.
ROOT="${1:?usage: numpy_abi_scan.sh <site-packages-dir>}"
if [ ! -d "$ROOT" ]; then
  echo "ERROR: not a directory: $ROOT" >&2
  exit 2
fi
# Command substitution (not a pipe or process substitution) so `find`'s exit
# status survives to be tested — in `while ... done < <(find ...)` the status
# belongs to the loop and the walk's failure is lost.
so_paths=$(find "$ROOT" -name "*.so")
find_status=$?
scanned=0
matched=0
unreadable=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  # `strings`' stderr is deliberately NOT discarded — it is the only thing that
  # names the file that could not be opened. Its exit status is read from
  # PIPESTATUS[0] rather than `$?`, because `$?` here is `tr`'s: a pipeline's
  # status is its LAST stage, and `tr` succeeds happily on the empty input a
  # failed `strings` hands it. `exit "${PIPESTATUS[0]}"` inside the command
  # substitution re-exports the first stage's status as the substitution's own,
  # so the assignment's `$?` is the one that matters.
  sig=$(
    strings "$f" | grep -oE 'numpy\._?core\.multiarray failed to import' | sort -u | tr '\n' ','
    exit "${PIPESTATUS[0]}"
  )
  strings_status=$?
  if [ "$strings_status" -ne 0 ]; then
    unreadable=$((unreadable + 1))
    continue
  fi
  scanned=$((scanned + 1))
  if [ -n "$sig" ]; then
    matched=$((matched + 1))
    printf '%s\t%s\n' "$sig" "${f#"$ROOT"/}"
  fi
done <<< "$so_paths"
echo "scanned ${scanned} compiled extensions under ${ROOT}; ${matched} embed a numpy ABI signature" >&2
if [ "$unreadable" -ne 0 ]; then
  echo "ERROR: ${unreadable} compiled extension(s) under ${ROOT} could not be read and were NEVER EXAMINED — see the diagnostics above; this report is INCOMPLETE" >&2
fi
if [ "$find_status" -ne 0 ]; then
  echo "ERROR: the walk of ${ROOT} failed (find exit ${find_status}) — see the diagnostics above; this report is INCOMPLETE" >&2
fi
if [ "$find_status" -ne 0 ] || [ "$unreadable" -ne 0 ]; then
  exit 5
fi
if [ "$scanned" -eq 0 ]; then
  echo "ERROR: found no compiled extensions at all — wrong root?" >&2
  exit 4
fi
