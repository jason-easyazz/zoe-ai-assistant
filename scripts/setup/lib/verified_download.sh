#!/usr/bin/env bash
# Verified model download: pinned sha256 + size, free-space check, resume, .part -> rename, .ready marker.
# Source it (needs common.sh for ok/warn/err). Pins come from the ONE manifest, models.lock (repo root), by file name.
# Every failure is a single plain sentence on stderr and a non-zero return; an existing good file is never deleted.

# lock_field <lock> <file-name> <sha256|bytes> -> prints the pinned value, nothing if no entry
lock_field() {
  awk -v want="/$2\"" -v field="$3" '
    /"path":/ { hit = (index($0, want) > 0); next }
    hit && $0 ~ "\"" field "\":" { v = $0; sub(/^[^:]*:[ \t]*/, "", v); gsub(/[",[:space:]]/, "", v); print v; exit }'  "$1"
}

_vd_free_bytes() { df -Pk "$1" | awk 'NR==2 {print $4 * 1024}'; }
_vd_size() { stat -c %s "$1"; }
_vd_hash() { sha256sum "$1" | awk '{print $1}'; }
_vd_human() { awk -v b="$1" 'BEGIN { printf "%.1f GB", b / 1073741824 }'; }

# verified_download <dir> <file-name> <url> <lock>   (returns 0 = file present and good, 1 = refused/failed)
verified_download() {
  local dir="$1" name="$2" url="$3" lock="$4"
  local final="$dir/$name" part="$dir/$name.part" ready="$dir/$name.ready"
  local sha bytes got need free have
  sha="$(lock_field "$lock" "$name" sha256)"; bytes="$(lock_field "$lock" "$name" bytes)"
  [[ "$sha" =~ ^[0-9a-f]{64}$ ]] || sha=""
  [[ "$bytes" =~ ^[0-9]+$ ]] || bytes=""
  mkdir -p "$dir" || { err "I could not create the folder $dir, so $name was not downloaded."; return 1; }

  if [[ -z "$sha" ]]; then
    warn "$name has no pinned checksum yet, so I cannot prove it is the right file; it is used as it is."
    [[ -s "$final" ]] && { ok "$name already present, skipping"; return 0; }
  elif [[ -s "$final" ]]; then
    if [[ -f "$ready" && "$(cat "$ready" 2>/dev/null)" == "$sha  ${bytes:-$(_vd_size "$final")}" && ( -z "$bytes" || "$(_vd_size "$final")" == "$bytes" ) ]]; then
      ok "$name already verified, skipping"; return 0
    fi
    if [[ -z "$bytes" || "$(_vd_size "$final")" == "$bytes" ]] && [[ "$(_vd_hash "$final")" == "$sha" ]]; then
      printf '%s  %s\n' "$sha" "$(_vd_size "$final")" > "$ready"; ok "$name already present and verified, skipping"; return 0
    fi
    warn "The existing $name does not match its checksum, so I will download a fresh copy and keep the old one until it is proven good."
  fi

  rm -f "$ready"
  if [[ -n "$bytes" ]]; then
    got=0; [[ -f "$part" ]] && got="$(_vd_size "$part")"
    (( got > bytes )) && { rm -f "$part"; got=0; }
    need=$(( bytes - got )); have="$(_vd_free_bytes "$dir")"
    if (( have < need )); then
      err "Not enough disk space for $name: it needs $(_vd_human "$need") more and only $(_vd_human "$have") is free in $dir. Free up space and run this again."
      return 1
    fi
  fi

  if [[ -z "$bytes" || ! -f "$part" || "$(_vd_size "$part")" != "$bytes" ]]; then
    local quiet=(-s); [[ -t 2 ]] && quiet=(--progress-bar)
    if ! curl -fL "${quiet[@]}" --retry 3 -C - -o "$part" "$url"; then
      err "The download of $name stopped before it finished (network or server problem). Run this again and it will carry on from where it stopped."
      return 1
    fi
  fi

  if [[ -n "$sha" ]]; then
    if [[ -n "$bytes" && "$(_vd_size "$part")" != "$bytes" ]] || [[ "$(_vd_hash "$part")" != "$sha" ]]; then
      rm -f "$part"
      err "The downloaded $name is damaged or is not the expected file, so I discarded it and kept any earlier copy. Run this again, and if it repeats check the network."
      return 1
    fi
  fi
  mv -f "$part" "$final" || { err "I could not move the finished $name into place in $dir."; return 1; }
  [[ -n "$sha" ]] && printf '%s  %s\n' "$sha" "$(_vd_size "$final")" > "$ready"
  ok "$name downloaded${sha:+ and verified}"
  return 0
}
