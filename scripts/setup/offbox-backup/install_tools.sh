#!/usr/bin/env bash
# Install the pinned restic + age binaries the off-box backup needs (OWNER step; the lab also uses it with --dest).
# Downloads from the upstream GitHub releases and REFUSES anything whose sha256 is not the one pinned here.
# Pins are >= 14 days old at pin time: restic 0.19.1 (2026-07-05), age v1.3.2 (2026-08-29). restic's sums are from its
# SHA256SUMS release file; age's are the release-asset digests GitHub reports. Bump version + both sums together.
#
#   scripts/setup/offbox-backup/install_tools.sh                 # -> ~/.local/bin (restic, age, age-keygen)
#   scripts/setup/offbox-backup/install_tools.sh --dest DIR      # anywhere else (the lab)
set -euo pipefail

RESTIC_VERSION=0.19.1
AGE_VERSION=v1.3.2
declare -A RESTIC_SHA=(
  [arm64]=a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465
  [amd64]=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c
)
declare -A AGE_SHA=(
  [arm64]=6b8dc4333c53a5a57c9e5834e3a48f92605d7154014cd07269ff3327db5d37f4
  [amd64]=cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10
)

dest="$HOME/.local/bin"
[[ "${1:-}" == "--dest" ]] && dest="${2:?--dest needs a directory}"
case "$(uname -m)" in
  aarch64|arm64) arch=arm64 ;;
  x86_64|amd64) arch=amd64 ;;
  *) echo "unsupported architecture $(uname -m)" >&2; exit 2 ;;
esac

have() { [[ -x "$dest/$1" ]] && "$dest/$1" ${2:-version} 2>/dev/null | grep -q -- "$3"; }
if have restic version "$RESTIC_VERSION" && have age --version "$AGE_VERSION"; then
  echo "restic $RESTIC_VERSION and age $AGE_VERSION already in $dest"
  exit 0
fi

mkdir -p "$dest"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fetch() {  # url file sha
  curl -fsSL --retry 3 -o "$work/$2" "$1"
  echo "$3  $work/$2" | sha256sum -c --quiet - || { echo "CHECKSUM MISMATCH for $2 - refusing" >&2; exit 1; }
}

fetch "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_${arch}.bz2" \
      restic.bz2 "${RESTIC_SHA[$arch]}"
bunzip2 -c "$work/restic.bz2" > "$work/restic"
install -m 0755 "$work/restic" "$dest/restic"

fetch "https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/age-${AGE_VERSION}-linux-${arch}.tar.gz" \
      age.tgz "${AGE_SHA[$arch]}"
tar -xzf "$work/age.tgz" -C "$work"
install -m 0755 "$work/age/age" "$dest/age"
install -m 0755 "$work/age/age-keygen" "$dest/age-keygen"

"$dest/restic" version
"$dest/age" --version
