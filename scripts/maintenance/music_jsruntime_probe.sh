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
# Usage:  music_jsruntime_probe.sh [--engine-only] [container]
#           default container: zoe-music-assistant
# Exit:   0 healthy, 1 unhealthy, 2 could not run the check at all.
#
# --engine-only stops after the deno check. It exists for the DIGEST-BUMP
# candidate, which is deliberately started with NO volumes from live and
# therefore has no configured ytmusic provider -- so MA never runs its dynamic
# `uv pip install yt-dlp[default]` and the full probe correctly exits 2 CANNOT
# CHECK, which can never be the green the bump procedure asks for (cross-review,
# #1635). Engine-only is the RIGHT scope for that stage anyway: the digest pin
# protects the image-baked deno, and yt-dlp is explicitly not ours to control.
# Run the full probe against the live container after recreating it.
set -uo pipefail

ENGINE_ONLY=0
if [ "${1:-}" = "--engine-only" ]; then ENGINE_ONLY=1; shift; fi
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

if [ "$ENGINE_ONLY" -eq 1 ]; then
    echo "HEALTHY (engine-only): ${CONTAINER} ships a working JS runtime."
    echo "  NOT checked: the nsig solve itself. Run the FULL probe against the"
    echo "  live container once it is recreated on this image."
    exit 0
fi

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
# Capture the TRANSPORT status separately from the grep. The pipe made $? the
# grep's, so a container that stopped between the `docker ps` check above and
# this exec -- or a network fault -- produced an empty $runtimes and the "no JS
# runtimes line at all" branch declared the build broken. Same
# transport-vs-engine confusion as the solver stage below, and equally a false
# alarm against a digest bump that is fine (cross-review, #1635).
reg_out=$(docker exec "$CONTAINER" "$PY" -m yt_dlp --simulate -v --skip-download "$PROBE_URL" 2>&1)
reg_rc=$?
runtimes=$(grep -m1 'JS runtimes:' <<<"$reg_out" || true)
if [ "$reg_rc" -ne 0 ] && [ -z "$runtimes" ]; then
    skip "yt-dlp exited ${reg_rc} without reporting its runtimes -- transport or
  container fault, NOT a missing JS engine. Re-run when stable. Tail:
$(tail -5 <<<"$reg_out")"
fi
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
rc=$?

# A TRANSPORT failure is CANNOT CHECK, not UNHEALTHY. A DNS blip, a YouTube
# hiccup, or a container that stopped mid-`docker exec` all make yt-dlp exit
# nonzero WITHOUT the solver ever running -- and the missing-solver check below
# would then declare the JS engine broken. That is a false alarm on the one
# signal this probe exists to make trustworthy, and it would be raised against
# a digest bump that is actually fine (cross-review, #1635). Exit 2 says "ask
# again", exit 1 says "the engine is gone"; they must not be confused.
if [ "$rc" -ne 0 ] && ! grep -q 'Solving JS challenges using' <<<"$out"; then
    case "$out" in
        *"Solving JS challenges"*) : ;;
        *)
            skip "yt-dlp exited ${rc} before the solver ran -- this looks like a
  transport/network failure or a container that went away, NOT a JS-engine
  fault. Re-run when the network and container are stable. Tail:
$(tail -5 <<<"$out")" ;;
    esac
fi

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
