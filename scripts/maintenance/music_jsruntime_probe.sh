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
# declares `deno` (2.7.4 in MA 2.8.7, 2.9.5 in 2.10.3), and the upstream image bakes the binary into
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
# Exit:   0 healthy, 1 unhealthy (JS engine, or a PO-token plugin/server major
#         mismatch -- the message says which), 2 CANNOT CHECK (could not run a
#         check at all, INCLUDING an unreadable plugin or server version).
# Env:    ZOE_YTMUSIC_POTOKEN_URL -- PO-token server base URL, default
#         http://127.0.0.1:4416. Negative control without touching live:
#           ZOE_YTMUSIC_POTOKEN_URL=http://127.0.0.1:1 music_jsruntime_probe.sh   # -> exit 2
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
# The forced client is the point of the probe: it must return nsig/sig-CHALLENGED
# URLs, so a green result proves the solver actually ran. The default client
# chain (ANDROID_VR) returns pre-signed URLs and would pass with NO JS engine at
# all, which is precisely the blind spot this exists to close.
# `web_embedded`, not `tv`: by 2026-09-27 the `tv` (TVHTML5) client failed for
# every video with "The page needs to be reloaded" on current AND older yt-dlp,
# so the probe could only ever exit 2. web_embedded still returns challenged
# URLs (n= + sig=), solves them with deno, and needs NO PO token -- so it stays
# independent of the bgutil container as well as of MA's YouTube login. Verified
# 2026-09-27: green on the MA 2.8.7 and 2.10.3 images, red (exit 1) with deno
# moved aside. When YouTube breaks this client too, pick another challenged one
# (tv_simply and mweb also solved that day, but both mint a PO token).
PROBE_CLIENT="web_embedded"
# The PO-token server the plugin/server check pings, as seen from INSIDE the MA
# container (host network). Same override zoe-data uses; point it at a closed
# port to exercise the CANNOT CHECK branch without stopping anything live.
POTOKEN_URL="${ZOE_YTMUSIC_POTOKEN_URL:-http://127.0.0.1:4416}"
POTOKEN_URL="${POTOKEN_URL%/}"

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
  Music Assistant is expected to supply it (ytmusic manifest: deno==<pinned>,
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
# (cross-review, #1635). The solver-ran assertion above and the forced challenged
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

# 5. PO-token plugin <-> server MAJOR versions must match. Checked LAST, after
#    the JS verdict above, because it is not a JS fault -- but it is the same
#    class of silent YouTube outage: bgutil refuses a cross-major pairing and MA
#    only logs "No stream formats found" (2026-09-25 -> 09-27: plugin 1.3.1
#    inside MA, server 2.0.0). MA installs the plugin UNPINNED but only when the
#    container is CREATED (`uv pip install` without --upgrade is a no-op on a
#    restart), so bumping the server image alone strands the old plugin.
#    The ping runs from INSIDE the MA container, so it also proves the
#    127.0.0.1:4416 publish is reachable from MA's (host) network namespace.
#    Either version UNREADABLE is CANNOT CHECK (exit 2), never HEALTHY: a plugin
#    that is missing or a server that is down is exactly the unknown this stage
#    exists to rule out.
plugin_v=$(docker exec "$CONTAINER" "$PY" -c 'import importlib.metadata as m; print(m.version("bgutil-ytdlp-pot-provider"))' 2>/dev/null)
server_v=$(docker exec -e "PING_URL=${POTOKEN_URL}/ping" "$CONTAINER" "$PY" -c 'import json, os, urllib.request; print(json.load(urllib.request.urlopen(os.environ["PING_URL"], timeout=5))["version"])' 2>/dev/null)
if [ -z "$plugin_v" ]; then
    skip "the JS engine is FINE (see above), but the PO-token plugin version could not be
  read inside ${CONTAINER} (bgutil-ytdlp-pot-provider not installed?) -- the
  plugin/server pairing is unproven."
elif [ -z "$server_v" ]; then
    skip "the JS engine is FINE (see above), but the PO-token server version could not be
  read from ${POTOKEN_URL}/ping (inside ${CONTAINER}) -- is zoe-ytmusic-potoken up?
  The plugin/server pairing is unproven."
elif [ "${plugin_v%%.*}" != "${server_v%%.*}" ]; then
    fail "PO-token plugin ${plugin_v} (inside ${CONTAINER}) and server ${server_v}
  (zoe-ytmusic-potoken) differ in MAJOR version -- bgutil refuses the pairing and
  YouTube Music fails with 'No stream formats found'. The JS engine is FINE (see
  above). Re-create ${CONTAINER} so MA reinstalls the current plugin, or align
  the two versions by hand. Runbook: docs/knowledge/music-ytdlp-js-runtime.md"
else
    ok "PO-token plugin ${plugin_v} matches server ${server_v} (major ${server_v%%.*})"
fi

echo "HEALTHY: ${CONTAINER} can solve YouTube JS challenges (${PROBE_CLIENT} client)."
