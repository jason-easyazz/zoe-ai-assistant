#!/usr/bin/env bash
# Read-only ABI signature scan: which numpy headers was each compiled extension built against?
# numpy 1.x headers embed "numpy.core.multiarray failed to import"
# numpy 2.x headers embed "numpy._core.multiarray failed to import"
#
# FAIL-CLOSED: this is the migration's before/after verification instrument, so
# "I scanned nothing" must never look like "I found nothing". A missing root
# exits 2; an empty scan exits 4; the scanned-extension count goes to stderr so
# an empty report is attributable. Matches on stdout only, one per line.
ROOT="${1:?usage: numpy_abi_scan.sh <site-packages-dir>}"
if [ ! -d "$ROOT" ]; then
  echo "ERROR: not a directory: $ROOT" >&2
  exit 2
fi
scanned=0
matched=0
while IFS= read -r f; do
  scanned=$((scanned + 1))
  sig=$(strings "$f" 2>/dev/null | grep -oE 'numpy\._?core\.multiarray failed to import' | sort -u | tr '\n' ',')
  if [ -n "$sig" ]; then
    matched=$((matched + 1))
    printf '%s\t%s\n' "$sig" "${f#"$ROOT"/}"
  fi
done < <(find "$ROOT" -name "*.so")
echo "scanned ${scanned} compiled extensions under ${ROOT}; ${matched} embed a numpy ABI signature" >&2
if [ "$scanned" -eq 0 ]; then
  echo "ERROR: found no compiled extensions at all — wrong root?" >&2
  exit 4
fi
