#!/usr/bin/env bash
# Read-only ABI signature scan: which numpy headers was each compiled extension built against?
# numpy 1.x headers embed "numpy.core.multiarray failed to import"
# numpy 2.x headers embed "numpy._core.multiarray failed to import"
ROOT="${1:?usage: numpy_abi_scan.sh <site-packages-dir>}"
find "$ROOT" -name "*.so" 2>/dev/null | while read -r f; do
  sig=$(strings "$f" 2>/dev/null | grep -oE 'numpy\._?core\.multiarray failed to import' | sort -u | tr '\n' ',')
  [ -n "$sig" ] && echo "${sig}	${f#$ROOT/}"
done
