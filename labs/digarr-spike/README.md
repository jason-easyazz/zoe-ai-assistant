# digarr spike — hidden music-discovery engine (lab record, 2026-07-13)

Evaluation of [digarr](https://github.com/iuliandita/digarr) (MIT, TypeScript/Bun,
embedded PGlite) as a **hidden** discovery engine behind Zoe. Zoe surfaces results
("I found albums you might like"); the user never sees digarr's UI. Lab-only:
nothing here is wired into zoe-data, systemd, Docker images, or CI.

## Verdict

**Run-as-hidden-engine: YES, viable** — but batch-only, on demand, never resident.

- Works fully local against the Gemma brain (`llama-server :11434`,
  OpenAI-compatible). Zero external AI dependency.
- Mood discovery returned sane, well-reasoned artist picks in **~46 s** per query.
- The full pipeline (quick-discover "Bon Iver", **84 s**) demonstrably earns its
  keep: raw Gemma output contained hallucinations ("The Luminesce", invented
  album titles); the resolve stage canonicalized against MusicBrainz and only
  real artists with real MBIDs survived (Iron & Wine, Gregory Alan Isakov).
  That resolve/score/filter machinery is exactly what we'd have to rebuild if we
  borrowed the pipeline into Zoe — don't; run the container.
- Footprint: **~430 MiB RSS** steady (arm64 image exists, pulled fine on the
  Orin). Too much to leave resident on this box → run batch, then stop.

**Borrow-the-pipeline** is the fallback only if the batch-run orchestration ever
proves too clunky; the 7 stages (collect → analyze → discover → resolve → score
→ filter → store) are clean pure functions in `src/core/pipeline/`, so mining is
possible, but MusicBrainz canonicalization + weighted scoring + dedup/cooldown is
real surface area we get for free by running the container.

**Risk note:** digarr is openly "built with AI" (README: most code and tests are
AI-generated, human-reviewed). MIT-licensed (LICENSE confirmed, © 2026 Digarr
Contributors). Mitigation: it runs as an isolated, stopped-by-default container
with no credentials except a lab password, bound to 127.0.0.1, talking only to
the local llama-server and MusicBrainz/Deezer/Spotify public metadata endpoints.

## Working config (verified on this box)

```bash
docker run -d --name digarr-spike --user 1000:1000 \
  -p 127.0.0.1:3199:3000 \
  -e PORT=3000 -e DB_PATH=/app/data \
  -e AI_PROVIDER=openai-compatible \
  -e AI_BASE_URL=http://host.docker.internal:11434 \
  -e AI_API_KEY=local-noauth \
  -e "AI_MODEL=/home/zoe/models/gemma4-e4b-qat/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf" \
  -e DIGARR_AI_TIMEOUT_SECONDS=300 \
  -e DIGARR_INITIAL_USERNAME=<user> -e DIGARR_INITIAL_PASSWORD=<pass> \
  --add-host host.docker.internal:host-gateway \
  --memory 768m --cpus 2 \
  -v <data-dir>:/app/data -v <backup-dir>:/app/backups \
  docker.io/iuliandita/digarr:latest
# batch: login → run discovery → export → docker stop
```

Gotchas learned the hard way:

- **`AI_BASE_URL` must NOT include `/v1`** — digarr's `openai-compatible`
  provider appends `/v1/chat/completions` itself
  (`src/core/providers/openai-compatible.ts`). With `/v1` you get a llama-server
  404 "File Not Found".
- **Env AI settings are persisted into the DB on first boot only.** Fixing an
  env var on an existing data volume does nothing — wipe the volume (or change
  it via the settings API).
- `AI_MODEL` = the full GGUF path llama-server reports at `/v1/models`
  (llama-server ignores the model name, but digarr sends it).
- Run as `user 1000:1000` — root-owned files under the live checkout break
  deploys (known prod incident signature).
- API auth: use the `token` from `POST /api/v1/auth/login` as a Bearer header
  (the session cookie alone 401s for API calls).
- Setup auto-completes from `DIGARR_INITIAL_USERNAME/PASSWORD`; no wizard needed.
  Optional: set `DIGARR_ENCRYPTION_KEY` if any real tokens ever go in.

## Resource measurements (Jetson Orin NX 16 GB, arm64 image, PGlite backend)

| metric | value |
|---|---|
| container RSS steady | ~430 MiB (of 768 MiB limit; upstream says 512 MiB OOMs under migrations) |
| CPU idle | ~3 % |
| image | `iuliandita/digarr:latest` (1.12.0), multi-arch incl. arm64 |
| startup → API ready | ~20–25 s (38 PGlite migrations on first boot) |
| mood discovery (`POST /api/v1/mood/discover`) | ~46 s wall (Gemma-bound) |
| quick-discover full pipeline | 84 s (job `quick_discover`, AI + MusicBrainz resolve) |

Box had ~1.9 GB available before the run — 768 MiB fits for a batch window but
NOT as a resident service. **Always `docker stop` after the batch.**

**Brain-slot guardrail (labs contract):** this spike points at `:11434` by
design — its explicit subject is Gemma-mediated music discovery for Zoe (the
same carve-out class as `flue-zoe-brain/`), not harness self-engineering, which
the labs contract forbids from using the voice brain. Even so: **never run a
discovery batch while voice is in active use.** Discovery prompts queue on the
single live brain slot and would regress voice latency. Hand-run batches only
in confirmed-idle windows; any future scripted batch MUST gate on brain
idleness (e.g. probe llama-server for in-flight work / recent voice activity)
before submitting, and back off rather than queue.

## Listening-source: what we can feed it TODAY

Probed read-only on 2026-07-13:

- **Music Assistant** (:8095, v2.8.7, healthy) is **not a digarr source type**
  (digarr speaks Jellyfin/Plex/Emby/Subsonic/ListenBrainz/Last.fm/Spotify/Deezer).
- **MA's library is currently EMPTY** — `library.db`: 0 artists, 0 tracks,
  0 playlog rows; `settings.json` has only player providers (Sonos/AirPlay/
  Chromecast/DLNA) + metadata providers. No music provider is configured yet
  (the `zoe-ytmusic-potoken` container exists but no ytmusic provider entry).
  **The listening-history gap is upstream of digarr.**
- No Jellyfin/Navidrome/Plex containers on the box; no Last.fm/ListenBrainz
  creds in `config/`.

So today the viable modes are the **history-less** ones, and they work:
mood discovery ("something like X but Y") and quick-discover (seed artist).
That already covers the Zoe use case "find me new music like <artist/mood>".

Once MA has a music provider and real playlog:

1. **Preferred (native):** enable MA's ListenBrainz/Last.fm scrobbling → digarr
   consumes ListenBrainz as a first-class listening source (free account,
   pseudonymous; slight privacy trade-off — listens leave the box).
2. **Fully local:** export top artists from MA `library.db`
   (`artists.play_count`/`favorite`, `playlog`) to CSV → digarr
   `POST /api/v1/subscriptions/import/csv` (artist-name CSV, ≤500 artists) as a
   recurring taste seed. No data leaves the box; a ~20-line bridge script.

## M3U → Music Assistant bridge design

Finding: digarr's M3U export is **artist-level with Spotify URLs**
(`#EXTINF:-1,Iron & Wine` + `https://open.spotify.com/artist/...`) — MA cannot
play those entries directly, so a naive drop of the .m3u into
`data/music-assistant/playlists/` is NOT the bridge.

Design instead: consume digarr's **JSON**, build the playlist with MA's own API:

1. Batch run completes → `GET /api/v1/recommendations` (rich JSON: artist name,
   MBID, genres, reasoning, `suggestedAlbum`, streaming URLs).
2. Bridge script per recommendation: MA search (`music/search` over MA's
   websocket/JSON-RPC API) for artist/`suggestedAlbum` in MA's configured
   provider → take top 3–5 tracks.
3. Create/replace an MA playlist named **"Zoe Discovery"** via MA's playlist API
   (builtin playlist provider, persisted under `data/music-assistant/playlists/`).
4. Keep digarr's `reasoning` strings keyed by artist so Zoe can *say why*
   ("Iron & Wine — intimate acoustic textures like Bon Iver but sunnier").

The M3U/XSPF export remains useful only as a portable artifact, not as the
playback path.

## Zoe voice-intent sketch (no prod wiring — design only)

- **"Zoe, find me some new music"** / "…like Bon Iver but happier" →
  intent `music_discover(query)` → zoe-data kicks the batch: start container,
  login, `POST /api/v1/mood/discover` (or `pipeline/quick-discover`), bridge
  results into the "Zoe Discovery" MA playlist, stop container → Zoe speaks 2–3
  picks with digarr's reasoning: "I found some albums you might like — want me
  to play them?"
- **"Play my discovery playlist"** → existing MA playback path plays the
  "Zoe Discovery" playlist on the named player. No new machinery: it's a normal
  MA playlist by the time the user asks.
- Long-running (46–84 s) → must be a background job with a spoken deferral
  ("give me a minute, I'll have something for you") + proactive follow-up, not a
  blocking voice turn.
- Weekly ambient mode later: scheduled batch (idle window) refreshing the
  playlist from the taste profile once a listening source exists.

## Recommended next step

1. First unblock the upstream gap: configure a real **music provider in Music
   Assistant** (the zero-account doctrine points at ytmusic/local files) so a
   library + playlog exist at all.
2. Then build the small **batch driver + MA-playlist bridge** script (lab-first,
   hand-run): start container → mood/quick discovery → MA playlist → stop.
3. Only after the bridge is proven by hand, design the `music_discover` intent
   and background-job surfacing in zoe-data (separate PR, replay-gated if it
   touches the voice path).

## Files

- This README is the complete spike record; no code was committed (the working
  config above reproduces the run). Source reviewed from the opensrc cache:
  `~/.opensrc/repos/github.com/iuliandita/digarr/develop`.
