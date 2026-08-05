#!/usr/bin/env bash
# Verify the LIVE music path's yt-dlp has a working JavaScript engine.
#
# WHY THIS EXISTS
# ---------------
# YouTube signs stream URLs with an obfuscated `n`/`sig` parameter that can only
# be recovered by executing YouTube's own player JavaScript. yt-dlp does that
# with the yt_dlp_ejs solver scripts, which need a real JS runtime (deno).
# Without one, yt-dlp silently falls back to player clients that hand out
# PRE-SIGNED URLs (ANDROID_VR today). That fallback works right up until
# YouTube withdraws it, and then playback breaks with no dependency error --
# just "no formats". This probe makes the difference observable BEFORE that.
#
# Music Assistant supplies the engine itself: its ytmusic provider manifest
# declares `deno==2.7.4`, and the upstream image bakes the binary into
# /app/venv/bin/deno. We add nothing. This probe guards that upstream property
# so a future MA image that drops it fails loudly here instead of silently in
# playback months later. See docs/knowledge/music-ytdlp-js-runtime.md.
#
# READ-ONLY. Runs `--simulate` (resolves a URL, downloads no media), never
# writes to the container, never touches MA's /data volume or auth state, and
# never restarts anything. Safe to run against production at any time.
#
# TRAP -- do NOT "simplify" the docker exec calls to `sh -lc`.
# `sh -lc` is a LOGIN shell; Debian's /etc/profile RESETS PATH to a default that
# does NOT include /app/venv/bin. Under `sh -lc` deno looks absent even though
# it is present and on the PATH that Music Assistant actually runs with. That
# artifact is what produced the false "no JS runtime" diagnosis in issue #1607.
# Always exec the interpreter directly, as below.
#
# Usage:  music_jsruntime_probe.sh [container]     (default zoe-music-assistant)
# Exit:   0 healthy, 1 unhealthy, 2 could not run the check at all.
set -uo pipefail

CONTAINER="${1:-zoe-music-assistant}"
PY=/app/venv/bin/python
# A Creative Commons video (Big Buck Bunny) -- public, no auth, not DRM-gated.
# The check is deliberately AUTH-INDEPENDENT: it must pass without MA's YouTube
# session, so a failure means the JS engine, never an expired login.
PROBE_URL="https://www.youtube.com/watch?v=aqz-KE-bpKQ"
# `tv` (TVHTML5) is the point of the probe: it returns nsig/sig-CHALLENGED URLs,
# so a green result proves the solver actually ran. The default client chain
# (ANDROID_VR) returns pre-signed URLs and would pass with NO JS engine at all,
# which is precisely the blind spot this exists to close.
PROBE_CLIENT="tv"

fail()  { echo "UNHEALTHY: $*" >&2; exit 1; }
skip()  { echo "CANNOT CHECK: $*" >&2; exit 2; }
ok()    { echo "OK: $*"; }

command -v docker >/dev/null 2>&1 || skip "docker not available on this host"

if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
    skip "container '$CONTAINER' is not running (nothing live to probe)"
fi

# 1. Is a JS runtime binary present and executable at all?
if ! deno_version=$(docker exec "$CONTAINER" deno --version 2>&1 | head -1); then
    fail "no working 'deno' on PATH inside $CONTAINER.
  Music Assistant is expected to supply it (ytmusic manifest: deno==2.7.4,
  baked at /app/venv/bin/deno). If an MA image update dropped it, yt-dlp has
  no way to solve YouTube's nsig challenge and playback depends entirely on
  YouTube continuing to serve pre-signed URLs.
  Runbook: docs/knowledge/music-ytdlp-js-runtime.md"
fi
ok "JS runtime present -- $deno_version"

# 2. Is yt-dlp importable? MA installs it DYNAMICALLY at ytmusic provider setup
#    (uv pip install yt-dlp[default]) into the container's writable layer -- it
#    is NOT in the image. Absent here usually means the provider never loaded
#    (e.g. the Premium check failed), not that the JS engine is broken.
if ! ytdlp_version=$(docker exec "$CONTAINER" "$PY" -c 'import yt_dlp; print(yt_dlp.version.__version__)' 2>&1); then
    skip "yt-dlp is not installed in $CONTAINER -- MA installs it when the
  ytmusic provider loads. Check the provider is connected, then re-run.
  (raw: $ytdlp_version)"
fi
ok "yt-dlp $ytdlp_version"

# 3. Does yt-dlp REGISTER the runtime? Presence on disk is not the same as
#    yt-dlp finding it -- a version below its minimum is reported unsupported.
runtimes=$(docker exec "$CONTAINER" "$PY" -m yt_dlp --simulate -v --skip-download "$PROBE_URL" 2>&1 \
    | grep -m1 'JS runtimes:' || true)
case "$runtimes" in
    *deno*|*node*|*bun*|*quickjs*) ok "yt-dlp registers a runtime -- ${runtimes#*] }" ;;
    "") fail "yt-dlp reported no 'JS runtimes:' line at all (unexpected build?)" ;;
    *)  fail "yt-dlp registers NO usable JS runtime: ${runtimes#*] }" ;;
esac

# 4. THE REAL TEST -- force a challenged client and require the solver to run
#    AND a playable URL to come back. Both halves matter: the solver line alone
#    would still pass if the solve then failed.
out=$(docker exec "$CONTAINER" "$PY" -m yt_dlp \
        -f "bestaudio/best" --simulate -v \
        --extractor-args "youtube:player_client=${PROBE_CLIENT}" \
        -g "$PROBE_URL" 2>&1)

if ! grep -q 'Solving JS challenges using' <<<"$out"; then
    fail "the EJS solver never ran for the '${PROBE_CLIENT}' client.
  yt-dlp had a runtime registered but did not use it, so the nsig path is
  unproven. Re-run with -v by hand and read which stage bailed.
  Runbook: docs/knowledge/music-ytdlp-js-runtime.md"
fi
solver=$(grep -m1 'Solving JS challenges using' <<<"$out")
ok "EJS solver executed -- ${solver#*] }"

# The URL must carry the SIGNATURE PARAMETERS, not merely be a googlevideo URL.
# `n=` is the nsig challenge output and `sig=`/`signature=` the cipher output —
# they are the actual product of the JS solve, so requiring them makes this a
# check on the ENGINE rather than on YouTube having returned some URL
# (cross-review, #1635). The solver-ran assertion above and the forced `tv`
# client already made this operationally sound; this makes it say what it means.
if ! grep -qE '^https://[^ ]*googlevideo\.com/videoplayback[^ ]*[?&]n=' <<<"$out" \
   || ! grep -qE '^https://[^ ]*googlevideo\.com/videoplayback[^ ]*[?&](sig|signature)=' <<<"$out"; then
    # Distinguish "YouTube changed" from "our engine broke" -- SABR enforcement
    # withholds URLs from a whole client family and is NOT a JS fault.
    if grep -q 'forcing SABR streaming' <<<"$out"; then
        fail "YouTube is now forcing SABR for the '${PROBE_CLIENT}' client, so this
  probe can no longer resolve a URL through it. The JS engine is FINE (the
  solver ran above) -- the probe needs a different PROBE_CLIENT.
  Pick another challenged client and update this script."
    fi
    fail "solver ran but no playable URL came back. Tail of the run:
$(tail -5 <<<"$out")"
fi
ok "resolved a challenged (nsig-signed) stream URL"

echo "HEALTHY: ${CONTAINER} can solve YouTube JS challenges (${PROBE_CLIENT} client)."
