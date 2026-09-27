---
type: Runbook
title: Music path yt-dlp JavaScript runtime
description: How the live Music Assistant container solves YouTube's nsig/sig JS challenge (MA bakes deno; we only pin the image), the sh -lc PATH artifact that produced a false "no JS runtime" diagnosis, the read-only probe (JS solve + PO-token plugin/server major match), the 2026-09-25 bgutil mismatch outage and the recreate rule, the MA 2.10 setup-flow API break that blocks the 2.10.3 pin, and the apply/rollback procedure with the YouTube Music re-auth risk.
tags: [music, music-assistant, yt-dlp, youtube, deno, bgutil, po-token, docker, operations]
timestamp: 2026-09-27T00:00:00Z
---

# Music path yt-dlp JavaScript runtime

How the **live** music path resolves YouTube stream URLs, why it needs a
JavaScript engine, and why the fix is a digest pin rather than a container
change. Related: [Runtime topology](runtime-topology.md),
[Production incident runbook](incident-runbook.md).

**Bottom line: the live path already has a working JS engine.** Music Assistant
supplies it. Nothing needs adding. What was missing is *observability* — the
regression would be silent — and *control over upstream changes*.

## The mechanism

YouTube signs stream URLs with an obfuscated `n` / `sig` parameter that can only
be recovered by **executing YouTube's own player JavaScript**. yt-dlp does this
with the `yt_dlp_ejs` solver scripts, which need a real JS runtime.

The live chain, verified end to end on 2026-08-04:

| piece | where it comes from | durability |
|---|---|---|
| `deno` (2.7.4 on MA 2.8.7, 2.9.5 on 2.10.3) | **baked into the MA image** at `/app/venv/bin/deno`; declared in MA's ytmusic provider manifest | survives container recreate — it is in the image layers |
| `yt_dlp_ejs` 0.8.0 solver scripts | pulled in with yt-dlp | as below |
| `yt-dlp` 2026.07.04 | **installed dynamically** by MA at ytmusic provider load (`uv pip install yt-dlp[default] bgutil-ytdlp-pot-provider`) into the container's writable layer | **lost on container recreate**, reinstalled from PyPI on next provider load — needs network |
| `bgutil-ytdlp-pot-provider` (the PO-token **client plugin**) | installed by MA in the same `uv pip install`, unpinned | same as yt-dlp — and **only on a fresh container** (see the mismatch section) |
| PO tokens (the **server**) | `zoe-ytmusic-potoken` (bgutil) on `127.0.0.1:4416`, image-pinned in compose | separate container, `restart: unless-stopped` |

MA installs yt-dlp dynamically on purpose — its own code comment: *"Google breaks
things quite often which requires us to update some packages very frequently.
Installing them dynamically prevents us from having to update MA."* So **the
engine is the stable part and yt-dlp is the moving part** — the opposite of what
you would guess.

`/app/venv/bin` is first on the container's real `PATH`, and yt-dlp enables deno
by default (`js_runtimes` defaults to `{'deno': {}}`), resolving a bare `deno`
through `PATH`. No yt-dlp option, no MA setting, no `PATH` edit is required.

**"Dynamically installed" does NOT mean "kept current".** MA's `install_package`
is `uv pip install --no-cache <pkg>` with no `--upgrade`, so once a version is in
the container's writable layer every later provider load is a no-op. A **restart**
keeps the old packages; only a **re-create** (fresh writable layer) pulls the
current yt-dlp and plugin from PyPI.

## TRAP — `docker exec ... sh -lc` hides the runtime

This produced a **false diagnosis** in issue #1607 and is the single most
important thing on this page.

`sh -lc` is a **login** shell. Debian's `/etc/profile` **resets `PATH`** to a
default that does **not** include `/app/venv/bin`. Under `sh -lc`, `deno` looks
absent even though it is present and on the PATH Music Assistant actually runs
with:

```
$ docker exec zoe-music-assistant sh -lc 'echo $PATH; command -v deno'
/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin      <- /app/venv/bin GONE
(no output — "deno NOT FOUND")

$ docker exec zoe-music-assistant printenv PATH
/app/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

$ docker exec zoe-music-assistant deno --version
deno 2.7.4 (stable, release, aarch64-unknown-linux-gnu)
```

The PID-1 environment (`tr '\0' '\n' < /proc/1/environ`) confirms the long PATH is
what MA runs with. **Always exec the interpreter or binary directly; never via
`sh -lc`, when checking what a service can see.**

## TRAP — `player_client=web` is not a JS test any more

The other half of the false diagnosis. `--extractor-args "youtube:player_client=web"`
returns `ERROR: No video formats found!` on a container with a perfectly working
JS engine, because YouTube now forces **SABR** streaming for the whole `web`
family. The `-v` trace names it:

```
[debug] [youtube] ... Some web client https formats have been skipped as they are
        missing a URL. YouTube is forcing SABR streaming for this client.
        See https://github.com/yt-dlp/yt-dlp/issues/12482
```

`web_safari` behaves identically. In the same run the JS engine and PO tokens
were both demonstrably fine — `JS runtimes: deno-2.7.4`, and
`Retrieved a gvs PO Token for web client` from the bgutil container. "No video
formats found" is a generic error; read the `-v` trace, never infer a cause.

**Use a challenged client instead** (was `player_client=tv` (TVHTML5); see the 2026-09-27 update below) — it returns nsig/sig-challenged
URLs, so it actually exercises the solver:

```
[youtube] [jsc:deno] Solving JS challenges using deno
[debug] [youtube] [jsc:deno] Using challenge solver lib script v0.8.0 (source: python package, variant: minified)
[debug] [youtube] [jsc:deno] Running deno: /app/venv/bin/deno run --ext=js --no-code-cache ... --cached-only -
https://rr2---sn-...googlevideo.com/videoplayback?...&n=yQmjd_mfxnWcPQ&...&sig=AE0s2JY...
```

Note `source: python package` and `--no-remote` — the solver runs **offline**
from the vendored `yt_dlp_ejs` scripts. No network fetch of solver code.

Some videos return `This video is DRM protected` on the `tv` client. That is a
property of the video, and it happens *after* a successful solve — not a JS
failure.

**Update 2026-09-27 — `tv` is now broken too.** Every video returns `ERROR: The
page needs to be reloaded.` on the `tv` client, on yt-dlp 2026.07.04 *and*
2026.08.19, so the probe could only exit 2. It now forces **`web_embedded`**:
challenged URLs (`n=` + `sig=`), solved by deno, and **no PO token needed**, so it
is independent of the bgutil container as well as of the YouTube login. Measured
that day on a fresh 2.10.3 container: `tv_simply`, `mweb`, `web_embedded` and MA's
own `web_music` (on a real YT Music track) all solved; `tv` and `android_vr`
(needs a GVS PO token) did not.

## Why the silent-regression risk is real

With **no** JS runtime, yt-dlp does not error. It quietly falls back to player
clients that hand out **pre-signed** URLs (`c=ANDROID_VR` today) and everything
looks healthy — which is exactly what the first #1607 verification observed and
correctly flagged as *"a fallback, not a fix"*. When YouTube withdraws that
client, playback dies with "no formats" and **no dependency error to point at**,
months after the change that caused it.

The image is where the engine comes from, so a floating `:stable` tag meant an
upstream image could drop deno with **no diff in this repo to review**. Hence
the digest pin plus a probe that fails loudly.

## The probe

`scripts/maintenance/music_jsruntime_probe.sh [container]` — **read-only**
(`--simulate`, resolves a URL, downloads nothing, writes nothing, restarts
nothing, never touches `/data` or auth state). Safe against production any time.

It is **auth-independent** by design: it uses a public Creative Commons video, so
a failure means the engine, never an expired YouTube login.

Exit codes: `0` healthy, `1` unhealthy, `2` could not check (container down, or
yt-dlp not yet installed because the ytmusic provider has not loaded).

Verified in both directions on 2026-08-04 — green against live, and **red**
against a throwaway candidate container (same image, no volumes) with
`/app/venv/bin/deno` moved aside. A probe that has never gone red proves nothing.

The last stage (added 2026-09-27) compares the **PO-token plugin** version inside
MA with the **server** version from `/ping` — fetched from *inside* the MA
container, so it also proves `127.0.0.1:4416` is reachable from MA's network
namespace — and fails on a **major** mismatch. It runs after the JS verdict and
says the engine is fine, so the two failure classes are never confused.

```
$ scripts/maintenance/music_jsruntime_probe.sh          # live, 2026-09-27
OK: JS runtime present -- deno 2.7.4 (stable, release, aarch64-unknown-linux-gnu)
OK: yt-dlp 2026.07.04
OK: yt-dlp registers a runtime -- JS runtimes: deno-2.7.4
OK: EJS solver executed -- [jsc:deno] Solving JS challenges using deno
OK: resolved a challenged (nsig-signed) stream URL
UNHEALTHY: PO-token plugin 1.3.1 (inside zoe-music-assistant) and server 2.0.0
  (zoe-ytmusic-potoken) differ in MAJOR version -- ...
```

and green on a fresh 2.10.3 container (yt-dlp 2026.08.19, plugin 2.0.0):
`OK: PO-token plugin 2.0.0 matches server 2.0.0 (major 2)` /
`HEALTHY: ... (web_embedded client).`

Static counterpart in CI: `tests/unit/test_music_assistant_image_pin.py` asserts
the digest pin and that the bump procedure still points at the probe. It needs no
Docker or network; the live behaviour is the probe's job.

## Incident 2026-09-25 — PO-token plugin/server major mismatch

YouTube Music went down at 2026-09-25 18:16 and stayed down: MA logged
`Error loading provider … ytmusic: No stream formats found` ~29×/h, preceded by
`[pot:bgutil:http] Plugin and HTTP server major versions are mismatched`. The
`zoe-ytmusic-potoken` server had been bumped to **2.0.0** (GHSA-qpv9-8xfj-xx9m),
while the plugin inside MA was still **1.3.1**, installed when the container was
created on 2026-08-03. The compose comment then claimed the plugin "floats to
2.x" — it does not (see *"Dynamically installed" does NOT mean "kept current"*).
yt-dlp 2026.07.04 was stale as well. The logs also showed `The provided YouTube
account cookies are no longer valid`, so **a re-auth is needed whatever is fixed**.

**The rule:** moving the bgutil **server** across a major means **re-creating
`zoe-music-assistant`** (or aligning the plugin by hand) in the same change, then
running the FULL probe, which now checks the major match.

**Fix without the MA bump** (keeps the 2.8.7 image and the panel reconnect path;
verified on a throwaway 2.8.7 container 2026-09-27: `web_music` solve + PO token +
stream URL, plugin 2.0.0 matched):

```bash
docker exec zoe-music-assistant uv pip install --no-cache \
    'yt-dlp[default]==2026.8.19' 'bgutil-ytdlp-pot-provider==2.0.0'
docker restart zoe-music-assistant      # yt-dlp is imported once per process
scripts/maintenance/music_jsruntime_probe.sh   # must be HEALTHY incl. the PO line
# then re-auth on the panel (next section): the cookies have rotated
```

It lives in the writable layer, so a later re-create replaces it with whatever
PyPI has then — which is the behaviour you want, as long as the server major still
matches. Undo: the same command with `yt-dlp[default]==2026.7.4`
`bgutil-ytdlp-pot-provider==1.3.1`.

## MA 2.10.x — pinned, but BLOCKED on zoe-data (read before re-creating)

The compose pin is **2.10.3** (`sha256:885872224fa5…`, released 2026-09-11, ≥14 d
old; 2.10.4 clears the 14-day rule on 2026-10-02). It closes the three advisories
against 2.8.7 — GHSA-5fch-fp25-3g2p (OAuth-callback XSS + bearer-token theft,
fixed 2.9.9), GHSA-m6c2-h3pf-84q7, GHSA-j369-4c4w-7qmq — which matter because
`network_mode: host` puts :8095 on the LAN. Stage 1 (`--engine-only`) is green
(deno 2.9.5). **It must not be adopted until zoe-data is updated**, because 2.10
changed the provider-config API that zoe-data's `music_service` drives (all read
from the 2.10.3 image source, diffed command-by-command against 2.8.7):

| zoe-data call | 2.8.7 | 2.10.3 | effect |
|---|---|---|---|
| `config/providers/save` with `instance_id` (panel **Reconnect**) | writes `cookie` into `values` | `ProviderConfig.update` skips keys that are not declared entries; ytmusic's `cookie`/`username`/`po_token_server_url` are now setup-flow-owned and read via `get_setup_value`, which prefers `setup_data` | **silent no-op**: reports success, the stale cookie stays |
| `config/providers/save` without `instance_id` (first connect) | creates the instance | `ValueError: Adding a provider is only possible through the setup flow` | first connect fails |
| `config/providers/get_entries(provider_domain=…)` | ok | now `get_entries(instance_id)` only | error → zoe-data falls back to `[]` |
| `music/recommendations` | ok | removed | "for you" shelves empty (best-effort path) |
| `player_queues/play_media` | has `username` | `username` removed | none — zoe-data does not pass it |

The 2.10 equivalents are `config/providers/setup(provider_domain)` /
`config/providers/reconfigure(instance_id)` → `config/flows/submit(flow_id, values)`.
Also on first start 2.10 **migrates `settings.json` one way**
(`migrate_provider_setup_data` moves those keys into encrypted `setup_data`), so
**rolling back to 2.8.7 needs the store restored from a backup**, not just the
old digest.

Other 2.10 surfaces to check on the day (not yet exercised live): Sendspin moves
aiosendspin 4.4.0 → 9.1.1 (PIN pairing changed at 9.0.0 — expect re-pairing);
the AirPlay provider swapped libraop for a new backend with explicit streaming
modes (`auto` / `ap2_ptp` / `ap2_ntp` / `ap2_compat` / `raop`) — if the "Zoe Panel"
AirPlay-2 output (shairport-sync 5.1) goes silent, pin its streaming mode;
music-assistant-models 1.1.115 → 1.1.205.

**Order:** (1) zoe-data PR moving `save_provider` / the ytmusic sign-in / the
OAuth path to the setup/reconfigure flow API, deployed; (2) stop-and-back-up the
store; (3) re-create on this digest; (4) FULL probe; (5) panel re-auth, then the
Sendspin / AirPlay checks above.

## ⚠ Re-auth risk — read before restarting Music Assistant

**Restarting or recreating `zoe-music-assistant` can require a YouTube Music
re-authentication (QR + phone sign-in).** MA's YouTube Music provider re-runs its
Premium check on load and, when it fails, the provider comes back
`needs_attention` and music search returns nothing. This box has been in exactly
that state: `Error loading provider(instance) ytmusic--...: User does not have
Youtube Music Premium (will be retried later)`.

Credentials themselves live in `auth.db` inside the bind mount
(`/home/zoe/.zoe/music-assistant` → `/data`), so a restart does **not** delete
them — the risk is the provider failing its check and needing a fresh sign-in.

**Reconnect procedure** (in place; preserves the instance and its settings):

Panel **Music** → **Browse** → **Sources** tab → the YouTube Music row shows amber
**Reconnect** with MA's `last_error` as the subtitle → tap it → the panel mints a
one-time token + QR (`POST /api/music/setup/start`) → **scan with your phone** →
sign in to Google on the phone (the password only goes to Google) → Zoe harvests
the cookie and calls `save_provider` **with the existing `provider_instance_id`**,
so it refreshes the current instance instead of minting a duplicate. A manual
cookie-paste fallback is on the same phone page.

Contracts, not restated here: `services/zoe-data/routers/AGENTS.md` (the re-auth
in-place rule) and `services/zoe-data/AGENTS.md` (PO-token wiring, `save_provider`
instance_id rule). Preconditions: `zoe-ytmusic-potoken` must be running — if it is
down, ytmusic login fails and the panel says *"The YouTube Music helper isn't
running yet."* And per the reconnect feature's own note: **reconnecting a
non-Premium account will not restore search** — MA requires Premium.

## Apply

There is **no urgency** — the live path is green. Apply at a convenient moment.

> **2026-09-27:** the pin is now 2.10.3 and the box still runs 2.8.7, so the
> "pin changes nothing live" premise below no longer holds — a re-create **adopts
> 2.10.3**. Read *MA 2.10.x — pinned, but BLOCKED on zoe-data* first.

**Original procedure (2026-08-04, when the pin equalled the running image).** The
pinned digest *was* the image already running, so the pin changed nothing live.
The pin's job is to govern the **next pull**, not to change what is running now.

```bash
# 0. Confirm the pin matches what is actually RUNNING.
#    Compare IMAGE IDs, not a tag against a digest: the live container was
#    created from the old `:stable` compose entry, so `.Config.Image` reports
#    that original TAG string while RepoDigests reports `repo@sha256:...`. Those
#    two can never be equal, so the old form could not pass (cross-review,
#    #1635). The image ID is the thing both sides genuinely share.
docker inspect zoe-music-assistant --format '{{.Image}}'          # running image ID
docker image inspect ghcr.io/music-assistant/server:stable \
  --format '{{.Id}}'                                              # must be IDENTICAL

# ...and confirm the digest in docker-compose.modules.yml is that image's:
docker image inspect ghcr.io/music-assistant/server:stable \
  --format '{{index .RepoDigests 0}}'

# 1. Baseline BEFORE anything (must be green already)
scripts/maintenance/music_jsruntime_probe.sh

# 2. Sync the live checkout (never stash, never checkout -b there)
cd /home/zoe/assistant && git merge --ff-only origin/main

# 3. Adopt WITHOUT recreating the container
docker compose -f docker-compose.modules.yml up -d --no-recreate music-assistant
```

**If a recreate happens** (deliberately, or because compose decides the service
changed), the ⚠ re-auth risk above applies, and yt-dlp is reinstalled from PyPI on
the next provider load — so the box needs working network. Verify afterwards:

```bash
# a. container healthy
docker ps --filter name=zoe-music-assistant --format '{{.Names}}\t{{.Status}}'

# b. provider actually CONNECTED, not needs_attention
docker logs --since 10m zoe-music-assistant 2>&1 | grep -i "ytmusic\|provider"
#    ...and on the panel: Music -> Browse -> Sources shows "Connected", not amber
#    "Reconnect". If amber, run the reconnect procedure above.

# c. one real search returns results (the user-visible check)
#    Panel: Music -> search a known track. Empty results with a connected
#    provider = suspect the YT Music provider, see reference_music_provider_reconnect.

# d. the JS engine survived the new image
scripts/maintenance/music_jsruntime_probe.sh
```

## Bump the pin

```bash
docker pull ghcr.io/music-assistant/server:<version>     # explicit tag, >=14 d old
docker image inspect ghcr.io/music-assistant/server:<version> \
  --format '{{index .RepoDigests 0}}'     # <- new digest into docker-compose.modules.yml
```

Then prove the new image, in **two stages**. One stage cannot cover both, and
running the wrong one on the candidate is why this used to be unsatisfiable:

**Stage 1 — the candidate, BEFORE merging. `--engine-only`.**

```bash
docker run -d --name ma-bump-candidate --entrypoint sleep \
  ghcr.io/music-assistant/server@sha256:<NEW> infinity
scripts/maintenance/music_jsruntime_probe.sh --engine-only ma-bump-candidate
docker rm -f ma-bump-candidate
```

Same new image, separate name, **no volumes from live** — so nothing can touch
MA's `/data` or auth state. That isolation is exactly why the FULL probe cannot
be used here: with no volumes there is no configured ytmusic provider, so MA
never runs its dynamic `uv pip install yt-dlp[default]`, the yt-dlp import
fails, and the probe correctly exits **2 CANNOT CHECK** — never the green this
step asks for (cross-review, #1635).

`--engine-only` is also the *right scope* for this stage: the digest pin
protects the **image-baked deno**, and yt-dlp is installed dynamically and is
explicitly not what the pin controls. Green here means the new image still ships
the engine, which is the whole claim the pin makes.

**Stage 2 — the live container, AFTER recreating it on the new digest.** Back
up the store first, with MA stopped (some files are root-owned and 0600, so tar
from a container, and a stopped MA gives consistent SQLite files). The live
container belongs to compose project `assistant` (working dir
`/home/zoe/assistant`); pass `-p assistant` or compose creates a duplicate.

```bash
docker stop zoe-music-assistant
docker run --rm -v /home/zoe/.zoe:/z --entrypoint sh <image> -c \
  'tar -C /z -czf /z/music-assistant.backup-$(date +%Y%m%d-%H%M).tgz music-assistant'
docker compose -p assistant --project-directory /home/zoe/assistant \
  --env-file /home/zoe/assistant/.env -f docker-compose.modules.yml \
  up -d --force-recreate music-assistant
scripts/maintenance/music_jsruntime_probe.sh          # FULL probe, must be green
```

Only the live container has the configured provider, hence yt-dlp, hence a real
nsig solve to observe. The probe is auth-independent, so a failure here means the
engine, never an expired YouTube login.

## Rollback

Revert the compose line to the previous digest and re-apply:

```bash
git revert <commit>          # or restore the previous digest by hand
cd /home/zoe/assistant && git merge --ff-only origin/main
docker compose -f docker-compose.modules.yml up -d music-assistant
scripts/maintenance/music_jsruntime_probe.sh
```

**Same ⚠ re-auth caveat** — a rollback that changes the image *does* recreate the
container, so budget for the reconnect procedure and verify the provider is
connected afterwards. **Rolling back from 2.10.x to 2.8.7 also needs the store
restored** from the pre-bump backup (2.10 migrates `settings.json` one way — see
the 2.10 section); stop MA, move the store aside, untar the backup, then re-create.

## What was NOT done, and why

Three mechanisms were weighed for getting a JS engine to the live path:

- **Overlay Dockerfile** (`FROM` pinned upstream `+ deno`) — rejected. It would
  duplicate a working upstream mechanism, add an image we must rebuild on every MA
  release, and fight MA's own dynamic-install design.
- **Bind-mounting a static deno + PATH via compose** — rejected for the same
  reason. (It *would* have worked: on Linux yt-dlp's `_find_exe` returns the bare
  basename and resolves it through `PATH`, and `/usr/local/bin` is already on the
  container's PATH. Recorded because it is the right shape **if** upstream ever
  drops deno.)
- **Rely on MA's own supported mechanism + pin the image + probe it** — chosen.
  MA already declares and ships deno; the correct production-care action on a
  healthy container is to leave it alone and make its guarantee observable.

Also settled, and the only reason it appears in a JS-runtime runbook at all: the
similar names caused the #1607 misdiagnosis. `modules/zoe-music/` was a **first-party
FastAPI bridge on :8100** and is a *different thing* from `zoe-music-assistant`, the
upstream Music Assistant container on :8095 that this document is about. **The live
music path does not go through it**, so nothing here — the digest pin, the JS engine,
the probe — concerns it either way.

> **Scope note.** Whether that module is deleted is **not decided by this document**,
> and this PR neither removes it nor authorises removing it. `docs/CANONICAL.md` is the
> locked-in truth for what is live; read it, not this runbook, before touching the
> module. Its actual removal is a separate operator decision tracked on #1607 and
> carried by its own PR (#1653) — which updates CANONICAL in the same change, as the
> lock-in mechanism requires.
