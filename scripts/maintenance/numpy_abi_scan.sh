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
#   5 — the walk itself failed part-way (unreadable subtree): the matches found
#       so far are still printed, but the scan is INCOMPLETE and says so
#
# Exit 5 is the half that a "check the root is a directory" guard cannot cover:
# a readable root containing an unreadable subtree passes that guard, yields a
# real-looking partial report, and would otherwise exit 0 — a before/after
# comparison against it silently under-counts. `find`'s own diagnostics are
# never discarded, so stderr names the path that could not be read.
#
# The scanned-extension count goes to stderr so an empty report is attributable.
# Matches on stdout only, one per line.
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
while IFS= read -r f; do
  [ -n "$f" ] || continue
  scanned=$((scanned + 1))
  sig=$(strings "$f" 2>/dev/null | grep -oE 'numpy\._?core\.multiarray failed to import' | sort -u | tr '\n' ',')
  if [ -n "$sig" ]; then
    matched=$((matched + 1))
    printf '%s\t%s\n' "$sig" "${f#"$ROOT"/}"
  fi
done <<< "$so_paths"
echo "scanned ${scanned} compiled extensions under ${ROOT}; ${matched} embed a numpy ABI signature" >&2
if [ "$find_status" -ne 0 ]; then
  echo "ERROR: the walk of ${ROOT} failed (find exit ${find_status}) — see the diagnostics above; this report is INCOMPLETE" >&2
  exit 5
fi
if [ "$scanned" -eq 0 ]; then
  echo "ERROR: found no compiled extensions at all — wrong root?" >&2
  exit 4
fi
