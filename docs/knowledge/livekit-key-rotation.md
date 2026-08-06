---
type: Reference
title: LiveKit API key pair — where it lives, how to rotate it, and the 2026-08-04 plaintext exposure
description: The LiveKit key/secret was committed in plaintext in services/livekit/config.yaml on a PUBLIC repo for 86 days. This is the runbook for the mechanism (LIVEKIT_KEYS env, verified against the pinned v1.9.3 image), the rotation procedure, the verification steps, and the history-scrub decision analysis.
tags: [secrets, livekit, voice, rotation, security, incident]
timestamp: 2026-08-04T00:00:00Z
---

# LiveKit API key pair — topology, rotation, exposure

> ## ⚠️ MERGING THIS BREAKS TALK UNTIL THE CONTAINER IS RECREATED
>
> The `livekit` container on the box was **created 2026-05-14**, before
> `LIVEKIT_KEYS` existed in `docker-compose.yml`. A container's environment is
> **fixed at create time**, and the on-demand path is `docker start livekit`,
> which reuses it. So the moment the live checkout syncs the keyless
> `services/livekit/config.yaml`, the next on-demand start reads a config with no
> `keys:` **and** an environment with no `LIVEKIT_KEYS`, and the server refuses to
> boot with `one of key-file or keys must be provided`. Panel/desktop Talk is
> down from that moment until an operator runs, in `/home/zoe/assistant`:
>
> ```bash
> docker compose up -d --force-recreate livekit
> ```
>
> `restart` does **not** work — it reuses the old environment.
>
> **This is the same command as step 3 of the rotation procedure below, so merge
> and rotation are ONE operator action:** rotate the pair in both `.env` files
> first (steps 1–2), then run the recreate once, and it satisfies both.
>
> Nothing in CI can do this for you: `deploy.yml` only runs
> `docker compose up -d zoe-auth` for docker-compose changes, and it never
> touches `livekit`.
>
> Verified before merge, without printing either value: the repo-root `.env`
> already carries `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`, and
> `docker compose config` resolves `LIVEKIT_KEYS` to a value whose SHA-256 equals
> that of the pair being deleted from the tracked config. **The recreate alone
> restores service even before any rotation** — but the pair has been public for
> 86 days, so do the rotation in the same action.

## Where the credential lives (after 2026-08-04)

| location | tracked? | role |
|---|---|---|
| `/home/zoe/assistant/.env` — `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | **no** (gitignored) | authoritative. Compose interpolates it into `LIVEKIT_KEYS` for the container. |
| `/home/zoe/assistant/services/zoe-data/.env` — same two names | **no** (gitignored) | read by the zoe-data process (`voice_livekit.py`, `voice_tts.py`, `skybridge.py`) to mint room tokens. Must hold the **same** pair. |
| `services/livekit/config.yaml` | yes | rtc/ports/logging **only**. Carries no credential and must never carry one again — pinned by `tests/unit/test_livekit_config_no_secrets.py`. |

`LIVEKIT_URL` is not a secret and does not change on rotation.

## The mechanism, verified — not assumed

Against the image the box actually runs (`livekit/livekit-server:latest` →
**v1.9.3**, digest `sha256:75484e31…`):

```
--key-file string   path to file that contains API keys/secrets
--keys string       api keys (key: secret\n) [$LIVEKIT_KEYS]
```

Precedence, from `pkg/config/config.go` `NewConfig()`: **defaults → YAML config
file → CLI/env**. `updateFromCLI` runs *after* the config file is decoded and
calls `unmarshalKeys`, which **replaces** `conf.Keys` wholesale. So `LIVEKIT_KEYS`
overrides anything in `config.yaml` rather than merging with it.

Confirmed empirically on v1.9.3, both directions:

- keyless `config.yaml` + `LIVEKIT_KEYS` → server starts, `GET :7880/` returns
  `OK` (`starting LiveKit server … "version": "1.9.3"`).
- **negative control** — same keyless config, no env → hard fail:
  `one of key-file or keys must be provided`. The config **cannot** silently
  fall back to an embedded credential; a missing env is a loud failure.

Format is exact: `<key>: <secret>`, **including the space** — the server
yaml-unmarshals that string. In `docker-compose.yml` the entry must be
**quoted**: unquoted, `- LIVEKIT_KEYS=a: b` parses as a YAML *mapping*, not the
env string you meant.

**The compose entry uses `${LIVEKIT_API_KEY:-}`, deliberately NOT `:?`.** Compose
interpolates the entire model before it selects services, so a required-value
marker on this one optional service aborts *every* compose command on a box
without the pair — measured: `docker compose config zoe-auth` with no LiveKit
vars exits **15** with `required variable LIVEKIT_API_KEY is missing a value`.
That would break `scripts/setup/install-jetson.sh` on a fresh box (it copies
`.env.example`, then brings up the non-LiveKit spine) and `deploy.yml`'s
`docker compose up -d zoe-auth`. An empty pair is still a loud failure, just
scoped correctly: livekit-server refuses to serve with
`Could not parse keys, it needs to be exactly, "key: secret", including the space`
— the same class of refusal as no keys at all. Both `.env.example` files carry
the (blank) names and point here, and
`tests/unit/test_livekit_config_no_secrets.py` pins all of it.

`key_file` was rejected as the mechanism: it needs a second on-disk file with
`others` permission bits at 0 (`ErrKeyFileIncorrectPermission`), which is more
moving parts than the `.env` this repo already treats as authoritative.

## Generating a new pair

```bash
docker run --rm --entrypoint /livekit-server \
  livekit/livekit-server:latest generate-keys
```

Prints `API Key:` (an `API`-prefixed id) and `API Secret:` (43 chars, base64url).
A custom key id is legal — the retired pair used a `zoe-` prefix — but the
generated one is fine and is what the upstream docs assume.

## Rotation procedure (operator)

Run on the box, in `/home/zoe/assistant`. **Never** paste either value into a
chat, a commit, a log, or an agent transcript.

0. **Capture the OUTGOING key id first** — step 5 checks for its remnants, and
   after step 2 it is gone from the files, so it cannot be recovered then:
   ```bash
   capture_old_key_id() {
     OLD_KEY_ID="$(grep -m1 '^LIVEKIT_API_KEY=' .env | cut -d= -f2-)"
     if [ -z "$OLD_KEY_ID" ]; then
       echo "STOP: no LIVEKIT_API_KEY in .env. Recover the outgoing id from" >&2
       echo "      git log -p -- services/livekit/config.yaml, then re-run." >&2
       return 1
     fi
     echo "captured outgoing key id (${#OLD_KEY_ID} chars)"
   }
   capture_old_key_id || echo "STEP 0 FAILED — do not continue to step 1"
   ```
   A shell function, so the failure genuinely **returns non-zero** while
   `OLD_KEY_ID` still lands in your current shell — `exit 1` pasted into an
   interactive terminal would close it. Do not proceed unless it printed
   `captured outgoing key id`.
   The key **id** is not the secret; it is already public in this repo's history.
   Keep the same shell for the whole procedure — an unset `OLD_KEY_ID` in step 5
   would expand to an empty pattern and `grep -rl ""` matches every line of both
   files, reporting a false remnant (or aborting under `set -u`).
1. Generate a pair (above). Keep the terminal scrollback private.
2. Update **both** env files, keeping them identical:
   - `/home/zoe/assistant/.env`
   - `/home/zoe/assistant/services/zoe-data/.env`

   Edit `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` in each. Verify **both halves**
   match, without printing them — a matching secret under a mistyped key id
   produces a server that recognises a different issuer from the one zoe-data
   signs its tokens with, and the symptom is simply "Talk does not work":
   ```bash
   for f in .env services/zoe-data/.env; do
     printf '%s key=%s secret=%s\n' "$f" \
       "$(grep -m1 '^LIVEKIT_API_KEY='    "$f" | cut -d= -f2- | sha256sum | cut -c1-12)" \
       "$(grep -m1 '^LIVEKIT_API_SECRET=' "$f" | cut -d= -f2- | sha256sum | cut -c1-12)"
   done
   ```
   **Both** columns must be equal across the two rows. (The key id is not itself
   a secret, but hashing both keeps one rule instead of two.)
3. Recreate the LiveKit container so it picks up the new `LIVEKIT_KEYS`:
   ```bash
   docker compose up -d --force-recreate livekit
   ```
   (`restart` alone re-uses the old environment — the container's env is fixed at
   create time. `--force-recreate` is required.)

   **This step is also mandatory the first time the keyless config lands on the
   box, rotation or not** — see the callout at the top. Until it runs, Talk is
   down. Doing steps 1–2 first means this single recreate both restores service
   and completes the rotation.
4. Restart zoe-data so the token minter reloads its env:
   ```bash
   systemctl --user restart zoe-data
   ```
5. Verify (below), then confirm the old pair is dead everywhere — using the id
   captured in **step 0**, and refusing to run if it is empty (an empty pattern
   matches every line, so an unset variable would report a false remnant):
   ```bash
   if [ -z "$OLD_KEY_ID" ]; then
     echo "OLD_KEY_ID unset — re-read step 0; this check did NOT run"
   elif grep -rlF -- "$OLD_KEY_ID" \
        /home/zoe/assistant/.env /home/zoe/assistant/services/zoe-data/.env; then
     echo "OLD KEY STILL PRESENT in the file(s) above — rotation incomplete"
   else
     echo "old key id absent from both env files — rotation complete"
   fi
   ```
   Three distinct outcomes on purpose. `grep` exits **1** when it finds nothing,
   which is the SUCCESS case here — folding that into a `||` branch would report
   a successful rotation as "the check did not run". If you lost the shell,
   recover the old id from `git log -p -- services/livekit/config.yaml` rather
   than guessing.

`ZOE_LIVEKIT_ONDEMAND=true`, so the container is idle-reaped and started on
demand — recreating it is low-risk and there is normally no live session to drop.

## Verification

**API level — headless, end-to-end, and it really does prove signature
acceptance.** Run these in order. The expected results below were captured
against the OLD pair on 2026-08-04, so this is a baseline to reproduce, not a
guess.

```bash
# 1. baseline: agent not yet connected
curl -sS localhost:8000/api/voice/livekit-health | python3 -c \
  'import sys,json;d=json.load(sys.stdin);print({k:d.get(k) for k in ("status","connected","last_error")})'
#    => {'status': 'stopped', 'connected': False, 'last_error': None}

# 2. mint a join token. GET, not POST. This ALSO starts the on-demand container.
curl -sS localhost:8000/api/voice/livekit-token | python3 -c \
  'import sys,json;d=json.load(sys.stdin);t=d.get("token","");print("token:",bool(t),"segments:",t.count(".")+1,"len:",len(t))'
#    => token: True  segments: 3  len: ~357        (never print the token itself)

# 3. the container came up
docker ps --filter name=livekit --format '{{.Names}} {{.Status}}'
#    => livekit  Up N seconds

# 4. THE REAL CHECK — wait ~3s, then re-read health
curl -sS localhost:8000/api/voice/livekit-health | python3 -c \
  'import sys,json;d=json.load(sys.stdin);print({k:d.get(k) for k in ("status","connected","last_error")})'
#    => {'status': 'connected', 'connected': True, 'last_error': None}
```

Step 2 only proves zoe-data can *sign* — it would pass even if the two `.env`
files drifted apart. **Step 4 is the proof that matters**: `connected: True`
means the agent's `_mint_agent_token()` JWT was presented to the LiveKit server
and the server **validated the HS256 signature against its own copy of the
secret**. That is exactly the zoe-data-`.env` ↔ container-`LIVEKIT_KEYS` match
the rotation has to preserve. A mismatch leaves `connected: False` with an auth
error in `last_error` / `docker logs livekit`.

Note: `/api/voice/livekit-token` answers **200 without credentials** (its
`get_current_user` dependency falls back to a guest identity), so this probe
needs no auth — convenient here, but see "Out of scope" below.

**Operator check (the real one):** press **Talk** on the panel and confirm a
session establishes and Zoe responds. The replay corpus does **not** traverse the
LiveKit lane (see [voice-pipeline.md](voice-pipeline.md)), so no automated gate
covers browser→WebRTC→brain→TTS end to end — a human session is the only
evidence for that.

## Out of scope, noticed while verifying

`GET /api/voice/livekit-token` returns **HTTP 200 and a valid join token to an
unauthenticated caller** on the LAN. Rotating the key does not change that:
anyone who can reach `:8000` can mint a room token regardless of which pair is
installed. Worth a separate look; not addressed by this change.

**Operator check (the real one):** press **Talk** on the panel and confirm a
session establishes and Zoe responds. The replay corpus does **not** traverse the
LiveKit lane (see [voice-pipeline.md](voice-pipeline.md)), so no automated gate
covers this end to end — a human session is the only real evidence.

## The 2026-08-04 exposure — facts

- The key id + base64 secret were committed in plaintext to
  `services/livekit/config.yaml` in **`363abde9`** ("Strategic overhaul: 7 batches
  of fixes and features"), **2026-05-10**.
- `363abde9` is an **ancestor of `origin/main`** — the credential is in the
  permanent history of the default branch.
- **The repository is PUBLIC** (`jason-easyazz/zoe-ai-assistant`, created
  2025-08-07, 3 stars, **1 public fork** — `rohan-tessl/zoe-ai-assistant`, last
  pushed 2026-04-06, i.e. *before* the leak, so the fork's own branches do not
  contain it).
- **1230 of 1234** local refs and 93 remote branches carry the value in their
  trees — everything descending from `363abde9`.
- **86 days** of public exposure before removal.
- ggshield and GitHub secret scanning both missed it: LiveKit keys have no
  vendor pattern to match. Absence of a scanner alert is not evidence of absence.

## History scrub — analysis, decision is the operator's

**Rotation is not optional and is not a judgement call.** A public repo means the
value should be treated as compromised: assume it is in someone's clone, in
GitHub's fork network, and in whatever LLM/code-search corpora scraped the repo
over 86 days. Rotation is the only action that actually revokes it.

**The case that rotation alone suffices:**

- The credential only has value against a reachable LiveKit server. This one
  binds `7880/tcp` and `50000-50200/udp` on a LAN box behind a Cloudflare tunnel
  that does not expose LiveKit. An attacker needs LAN presence to use it at all.
- Once rotated, the historical value authenticates nothing. Scrubbing history
  removes a *dead* string.
- A rewrite of 4,696 commits invalidates every clone, every open PR, and every
  worktree on the box, and `main` is protected against force-push by design.
  That is a large, disruptive, error-prone operation to delete a string that no
  longer works.

**The case for scrubbing anyway:**

- Public + 86 days means the exposure is not hypothetical; leaving it advertises
  a working-looking credential and invites probing of the LAN surface.
- It is a durable, indexable example in a public repo that a future agent or
  contributor may copy the *pattern* from.
- GitHub's fork network keeps unreachable objects alive: even after a rewrite,
  the blob stays fetchable by SHA via the fork unless GitHub Support is asked to
  purge the network. A scrub that skips that step buys less than it appears to.

**Recommendation: rotate now; do NOT rewrite history.** The cost is concrete and
high (4,696 commits, protected branch, every clone and open PR broken), the
benefit after rotation is cosmetic, and the fork network means the scrub is not
even fully effective without a GitHub Support request. If the goal is "the
secret is not in the public repo", the honest version of that is: rotate, then
ask GitHub Support to purge the fork network's unreachable objects — and only
then decide whether the rewrite is worth it. **The decision is Jason's.**

Whichever way it goes, the recurrence guard is the same and is already in place:
`tests/unit/test_livekit_config_no_secrets.py` fails the deterministic gate if a
`keys:` block or any secret-shaped token returns to the tracked config.

## Related

- [voice-pipeline.md](voice-pipeline.md) — the LiveKit lane's evidence boundary.
- [merge-and-deploy.md](merge-and-deploy.md) — why `secret-scan` is first-party.
