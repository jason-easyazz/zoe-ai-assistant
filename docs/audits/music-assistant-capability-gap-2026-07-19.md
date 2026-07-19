# Music Assistant capability audit — what Zoe never surfaces

**Date:** 2026-07-19
**MA server:** version **2.8.7**, schema 29, `server_id afe4d96f005447d59966d26e943244eb`, base_url `http://192.168.1.218:8095`, status `running`
**Source cross-checked against:** `~/.opensrc/repos/github.com/music-assistant/server/2.8.7` (exact running version)
**Method:** live read-only probes through the repo's own client (`music_service._ma`), plus `@api_command` extraction from the pinned source. No playback was started, stopped or altered.

---

## 0. The single most important finding: the library is empty

Every "browse your library" feature idea dies on this measurement. Live counts:

| media type | `music/<type>/count` | favourites (`favorite_only=True`) |
|---|---|---|
| artists | **8** | **0** |
| albums | **0** | 0 |
| tracks | **0** | 0 |
| playlists | **13** | **0** |
| radios | **0** | 0 |
| genres | 59 | — |
| podcasts / audiobooks | 0 | 0 |

`music/recently_added_tracks` → `[]`. `music/in_progress_items` → `[]`.

The 8 artists are `Ashton Orion, DJs From Mars, Halsey, Loren Allred, Olivia Rodrigo, DJ Raphi, Rita Ora, Scott Bradlee's Postmodern Jukebox` — all `provider_mappings → ytmusic`, all `favorite: false`.

**Consequence:** an "Albums" or "Tracks" library tab would render a *blank screen*. Browsing on this house must go through the **ytmusic provider tree** (`music/browse`) or **`music/search`**, never `music/<type>/library_items`. The 59 genres are likewise useless — genre browse filters library tracks, and there are zero.

> **Instrument warning for whoever implements this.** `music/<type>/count` takes **`favorite_only`**, but `music/<type>/library_items` takes **`favorite`**. They are different parameter names for the same concept, and MA silently swallows the wrong one into `**kwargs`. Passing `favorite=True` to `count` returns the *unfiltered total* and looks like "8 favourites". I hit this and briefly recorded a false finding; the negative control (`library_items(favorite=True)` → `[]`) is what caught it.

---

## 1. Command-surface diff

MA 2.8.7 registers **145** static `@api_command`s plus per-media-type commands registered dynamically in `controllers/media/base.py:122-133` (`music/{artists,albums,tracks,playlists,radios,podcasts,audiobooks,genres}/{count,library_items,get,update,remove}`) and extras per controller (`artist_albums`, `artist_tracks`, `album_tracks`, `similar_tracks`, `track_albums`, `preview`, …).

**Zoe calls 26 of them** (`services/zoe-data/music_service.py`, `services/zoe-data/routers/music.py`) — 25 literal plus the `_QUEUE_CMDS` / `_PLAYER_CMDS` dispatch dicts at `music_service.py:293-306` (play, resume, pause, play_pause, stop, next, previous, volume_up, volume_down, volume_mute).

### Gap grouped by theme

#### A. Multi-room / grouping — **entirely unexposed, and this house is built for it**
- `players/cmd/set_members(target_player, player_ids_to_add, player_ids_to_remove)` — `controller.py:1126`
- `players/cmd/group(player_id, target_player)` — `:1180`
- `players/cmd/ungroup(player_id)` — `:1211`
- `players/cmd/group_many`, `players/cmd/ungroup_many`
- `players/cmd/group_volume`, `group_volume_up/down/mute`
- `players/create_group_player`, `players/remove_group_player`

Live: **14 players, 9 mutually groupable.** Real `can_group_with` payload from `Bedroom` (Sonos, `RINCON_347E5C9BEC8F01400`, currently `playing`):
```
['up286412cf6eb7', '07af8dad-cc27-a42f-dffb-b9025e92344b', 'ap40cbc0db9fb8',
 'apf2e069e22442', 'ap9c207b93ae6d', 'f2f19f55-f07f-4604-d698-cbb4d3da43bc',
 '60580bba-90c8-ae48-f22f-6b441b3d2a4e', 'RINCON_38420B45B65001400',
 'b72b454a-9e06-e7a2-61fd-ba158dd2c831']
```
`set_members` is in `supported_features` for both Sonos zones (Bedroom, Living Room), all four Chromecasts (Kitchen Display, Bathroom speaker, Bedroom 2 TV, Bedroom 3 TV), all three AirPlay endpoints and the MacBook universal player. There is also an existing Chromecast group player `House` (`e0951e90-…`) with members Bedroom 3 TV + Bathroom + Kitchen Display, currently `powered: false`.

Zoe's speaker picker is **single-select only** — it either transfers playback (`/transfer`) or sets a default (`/preferred-player`). There is no way to play in two rooms at once.

#### B. Radio mode / "don't stop the music" — plumbing exists on both sides, no UI
- `player_queues/play_media(..., radio_mode: bool = False, start_item=None)` — `player_queues.py:494-503`. Zoe calls this command but **never passes `radio_mode`**.
- `player_queues/dont_stop_the_music(queue_id, dont_stop_the_music_enabled)` — `:416`
- `music/tracks/similar_tracks(item_id, provider_instance_id_or_domain, limit)`

Gate verified: `set_dont_stop_the_music` raises `UnsupportedFeaturedException` unless some provider has `ProviderFeature.SIMILAR_TRACKS`. Live provider features for **ytmusic** (`instance ytmusic--HemJN6vc`):
```
['artist_toptracks', 'recommendations', 'artist_albums', 'library_podcasts',
 'library_playlists', 'library_albums', 'browse', 'similar_tracks', 'search',
 'library_artists', 'library_tracks']
```
`similar_tracks` is present → **both features are genuinely usable on this setup.**

Live `similar_tracks` off `good 4 u` returned real results:
`good 4 u (Olivia Rodrigo)`, `Billie Eilish x Olivia Rodrigo mashup (Carneyval)`, `Sobbing (Forrest Rose)`, `Wicked Looks Good On Me (Ash Queen)`, `Lemonade (Anna Graceman)`, `Houdini (Dua Lipa)`, `Situationship (Bodine Monet)`.

Live queue object confirms the field is exposed and currently **off**:
```json
{"queue_id":"RINCON_347E5C9BEC8F01400","display_name":"Bedroom","items":19,
 "shuffle_enabled":false,"repeat_mode":"off","dont_stop_the_music_enabled":false,
 "current_index":4,"state":"playing",
 "current_item":{"name":"Thomas Newman - Peanut Butter Man","duration":100}}
```

#### C. Provider browse tree — the only viable "browse" on this house
`music/browse(path=...)` works and is never called by Zoe. Live root:
```
builtin://          Music Assistant
radiobrowser://     RadioBrowser
ytmusic--HemJN6vc://  YouTube Music
```
Into ytmusic (note: nodes carry `translation_key`, **`name` is empty** — render from `translation_key`):
```
artists  -> ytmusic--HemJN6vc://artists
albums   -> ytmusic--HemJN6vc://albums
tracks   -> ytmusic--HemJN6vc://tracks
playlists-> ytmusic--HemJN6vc://playlists
podcasts -> ytmusic--HemJN6vc://podcasts
recommendations -> ytmusic--HemJN6vc://recommendations
```
RadioBrowser (a configured music provider, `features: ['browse','search']`):
```
radiobrowser_by_popularity -> radiobrowser://popularity
radiobrowser_by_category   -> radiobrowser://category
```
**Internet radio is fully available and completely absent from Zoe.**

#### D. Artist discography — works, but only for 8 artists
`music/artists/artist_albums` on `Halsey` (`library://artist/3`) returned:
`The Great Impersonator, Badlands, If I Can't Have Love I Want Power (×2), Manic, hopeless fountain kingdom (Plus), hopeless fountain kingdom, BADLANDS`
`music/artists/artist_tracks`: `Him & I, Without Me, Bad At Love, Eastside, Colors, Gasoline, Control, Could Have Been Me`

Also unused: `music/artists/artist_toptracks` (in ytmusic's feature list), `music/albums/album_tracks`, `music/tracks/track_albums`, `music/tracks/preview`.

#### E. Lyrics — **works, returns time-synced LRC**, with a latency trap
`metadata/get_track_lyrics(track: Track)` — takes a **full serialized track object**, not a URI (`metadata.py:554-558`). Returns `[plain, lrc]`.

Real payload for `Halsey — Without Me`:
```
[00:14.46] Found you when your heart was broke
[00:17.97] I filled your cup until it overflowed
[00:21.07] Took it so far...
```
`good 4 u` returned a 3063-byte LRC with `[ti:]/[ar:]/[al:]` headers and per-line timestamps.

**Measured latency trap:** cold lookups (LRCLIB network fetch) exceeded **30s** and returned nothing to the client; the *same* tracks then returned in **0.07–0.1s** afterwards, because the server-side lookup completed and cached even though the client had given up. Zoe's `_TIMEOUT_S = 5.0` (`music_service.py:25`) would fail essentially every cold lyric fetch. An implementer must use a longer timeout, treat a cold miss as "not yet" rather than "none", and re-ask. `Thomas Newman — Peanut Butter Man` legitimately returned `[None, None]` (instrumental score cue) — absence is a real state, not only a timeout.

#### F. Queue & transport niceties
- `player_queues/move_item_end` — "move to end" (Zoe has `move_item` only)
- `player_queues/set_playback_speed`
- `player_queues/get`, `player_queues/get_active_queue`
- `players/cmd/seek` (player-level; Zoe uses queue-level `player_queues/seek`)
- `players/cmd/power`, `players/cmd/select_source`, `players/cmd/select_sound_mode`
- `players/cmd/play_announcement` — supported by both Sonos zones
- `players/add_currently_playing_to_favorites` — **a single-call "heart what's playing"**, exactly what the `.mfav` button is trying to be
- `QueueOption` on `play_media` — Zoe's panel only ever sends `replace` and `add`; MA supports play-next/enqueue modes on the same call

#### G. Library write / favourites
- `music/favorites/remove_item(media_type, library_item_id)` — **no un-favourite in Zoe**
- `music/library/add_item`, `music/library/remove_item`
- `music/mark_played`, `music/mark_unplayed`, `music/sync`, `music/refresh_item`
- `music/track_by_name`, `music/get_library_item`, `music/item`

#### H. Deliberately out of scope (listed for completeness)
`auth/*` (19 commands), `config/core/*`, `config/players/*`, `config/dsp_presets/*`, `tasks/*` (10), `providers/manifests*`, `logging/get`, `metadata/set_preferred_language`, `music/match_providers`, `music/add_provider_mapping`, `players/plugin_source(s)`, `players/player_control(s)`.

---

## 2. Ranked shortlist — worth building for THIS house on THIS panel

Ranked by (value on a finger-operated 1280×720 glance surface) × (cheapness given existing plumbing).

### 1. "Don't stop the music" toggle — *highest value per unit of work*
The backend endpoint **already exists and has zero callers**: `POST /api/music/dont-stop` (`routers/music.py:474`) → `player_queues/dont_stop_the_music`. ytmusic satisfies the `similar_tracks` gate. The queue object already returns `dont_stop_the_music_enabled` for read-back, so the toggle can render true state on load — no new state to invent.
**Work:** one toggle button beside the existing shuffle/repeat pair, which are already wired for both write *and* read-back. Essentially a copy of `#mShuf`.
**Why it matters:** the failure mode this fixes is silence. The queue is 19 items; when it ends the house goes quiet and someone has to walk to the panel. This is the single most "ambient home" feature MA offers and it is one button.

### 2. Seek — the scrub bar is already drawn and already dead
`POST /api/music/seek` exists (`routers/music.py:283`). The panel renders `.mscrub > .mtrack > #mFill` and updates the fill, but has **no pointer handler at all**. Users will already be trying to drag it.
**Work:** one pointer handler on an element that already exists, hitting an endpoint that already exists. Nothing new server-side.
**Why:** a progress bar that looks draggable and isn't is worse than no bar. This is a correctness fix as much as a feature.

### 3. Speaker grouping / multi-room
Nine mutually-groupable players, two Sonos zones, an existing `House` group. `players/cmd/set_members` is one call with a list.
**Work:** new `/api/music/group` endpoint + turn the existing speaker picker from single-select into multi-select (checkboxes on rows that already render). Moderate but contained.
**Why:** this is the feature people actually buy Sonos for, and the panel currently makes it *impossible* — it only ever moves playback, never spreads it. Highest ceiling of anything here; the reason it's third and not first is that it's the only item on this list needing genuinely new backend surface and careful UI (multi-select on a finger target).

### 4. Volume on the music card
`control` with `volume_set` is already implemented and already used — but **only on the Rooms screen** (`#rmVol`). The music card has no volume at all.
**Work:** move/duplicate an existing wired slider. Near-free.
**Why:** volume is the most-reached-for control on any music surface, and it currently lives on a different screen.

### 5. Play-next
`player_queues/play_media` already takes `option`; the panel only ever sends `replace` and `add` (`_wireBrowseRow`, home.html:1478 — tap = replace, `＋` badge = add).
**Work:** a third gesture (long-press on the `＋`, or a third badge) passing the play-next enqueue option. One parameter on a call that's already made.
**Why:** cheap, and "play this next" is the natural third verb once you have "play now" and "add to end".

### 6. Fix the heart — un-favourite + read-back (see §4; this is a bug fix)
`music/favorites/remove_item` and `players/add_currently_playing_to_favorites` both exist. Today the heart is one-way and lies.

### 7. Internet radio via RadioBrowser
`music/browse('radiobrowser://popularity' | '://category')` — a configured, working provider, zero exposure.
**Why 7th:** genuinely useful and genuinely absent, but it needs a new browse UI, and it competes with ytmusic for the same screen real estate. Worth doing *after* a browse surface exists for something else.

### 8. Lyrics
Works, synced LRC, and a 1280×720 panel is an excellent lyrics surface (this is a real "wow" feature on a wall panel).
**Why 8th despite the appeal:** the cold-fetch latency (>30s vs the 5s client default) means a naive implementation shows an empty pane most of the time. Needs a longer timeout, a caching/retry strategy, and a genuine "no lyrics" state. Not hard, but not free — and it's the one item here where shipping it badly is worse than not shipping it.

### 9. Artist discography drill-down
`artist_albums` / `artist_tracks` / `artist_toptracks` all work and return good data — but there are **8 artists**. Build it when the library grows, or build it on top of *search* results rather than the library.

---

## 3. What I'd skip, and why

- **Library browse tabs by album / track / genre.** 0 albums, 0 tracks, 59 genres attached to nothing. These tabs would be empty screens. This is the most obvious-looking feature in MA and it is the wrong one for this house.
- **DSP / EQ (`config/players/dsp/*`, `config/dsp_presets/*`).** Per-player filter chains with input/output gain. This is a settings-app concern, not a finger-operated glance surface, and the live DSP block on the Sonos shows `"state": "disabled"` anyway.
- **Podcasts / audiobooks** (`music/podcasts/*`, `music/audiobooks/*`, `in_progress_items`). All zero, no provider configured for them. Nothing to show.
- **`auth/*` (19 commands), `tasks/*` (10), `config/core/*`, `providers/manifests`.** Server administration. Zoe already reads `config/providers` for the provider list, which is all the panel needs.
- **`players/cmd/play_announcement`.** Supported on both Sonos zones and tempting, but it belongs to Zoe's TTS/voice path (which is a locked rock with its own replay gate), not to the music card. Wiring an announcement button here would create a second, ungated audio path.
- **`set_playback_speed`.** Meaningful for audiobooks/podcasts; he has none.
- **`players/cmd/power`, `select_source`, `select_sound_mode`.** Device-management verbs that duplicate what the Sonos/TV remotes already do, and `select_source` is Sonos-only here.
- **`music/sync`, `refresh_item`, `match_providers`.** Maintenance operations with no glanceable result and real potential to confuse (a sync takes minutes and shows nothing).

---

## 4. Broken or half-wired things found

These are bugs, not feature gaps.

**a) The `.mfav` heart is one-way and reports state it does not know.**
`home.html:1902-1904`:
```js
var fav=document.getElementById('mFav');
if(fav)fav.onclick=function(){if(!_music.np||!_music.np.uri){toast('Nothing playing');return;}
  _qpost('favorite',{uri:_music.np.uri}).then(function(r){if(r&&r.ok){fav.classList.add('on');toast('Added to favourites');}else toast('Couldn’t favourite');}).catch(function(){toast('Couldn’t favourite');});};
```
Three distinct defects:
1. **No un-favourite.** `classList.add('on')` with no corresponding `remove` anywhere in the file. MA has `music/favorites/remove_item`; Zoe's backend is add-only (`music_service.py:530-534`).
2. **No read-back.** Nothing reads favourite state from MA, so the heart **always starts empty on load** regardless of truth, and once lit it stays lit across subsequent track changes for the rest of the session.
3. **It acts on the wrong item.** It uses `_music.np.uri` — the *playing* track — while `cfPaintMeta` (home.html:1678) shows the *focused* Cover Flow track's title/artist directly above it. Flick the Cover Flow to another track and the heart favourites something other than what the label says.

The write path itself is **well-formed** — `favorite_add` sends `music/favorites/add_item` with `item=<uri>`, matching MA's `add_item_to_favorites(item: str | MediaItemType | ItemMapping)` at `controllers/music.py:868-871`.
**Unverified — would require mutating state:** whether that call actually persists for a *ytmusic* URI. MA resolves the URI, forces the item into the library, then sets the favourite flag. Live favourite counts are **0 across every media type**, which is consistent either with "never tapped" or with "silently not persisting". Confirming needs a real write, which is out of scope for this audit. **This is the one thing here worth a deliberate 30-second manual test** (tap the heart once, then re-run `music/tracks/count(favorite_only=True)`).

**b) `POST /api/music/dont-stop` is dead code.** Implemented at `routers/music.py:474`, wired through to MA, **zero front-end callers**. See shortlist #1 — the backend half is already built.

**c) The scrub bar is decorative while `/api/music/seek` exists.** See shortlist #2.

**d) `_TIMEOUT_S = 5.0` is too low for metadata commands.** Fine for players/queues (all sub-second), but guarantees failure on cold `metadata/get_track_lyrics`. Any lyrics work needs a per-call override — `_ma_response` already accepts `timeout_s`, so the mechanism exists.

**e) Other implemented-but-uncalled endpoints** (`routers/music.py`, no `home.html` caller): `/search`, `/recommendations`, `/playlists/tracks`, `/playlists/add`, `/status`, `/providers`, `/available-providers`, `/play`, `GET /preferred-player`. Notably **`/search` is unreachable from the estate panel** — search is offloaded to a phone via the jukebox QR code. That may well be deliberate (typing on a wall panel is unpleasant), but it means the panel cannot start anything not already in Recent or Playlists.

**f) Orphaned legacy page.** `services/zoe-ui/dist/touch/music.html` (634 lines) is a full standalone music page with media-type tabs including radio, unreachable from `home.html` — only the old `dashboard.html` / `lists.html` / `calendar.html` / `memories.html` and `js/touch-menu.js` link to it. Either a retirement candidate or a source of markup to lift.

---

## 5. Reproducing these probes

```bash
cd /home/zoe/assistant/services/zoe-data
export MUSIC_ASSISTANT_TOKEN=$(sed -n 's/^MUSIC_ASSISTANT_TOKEN=//p' .env | head -1)
export MUSIC_ASSISTANT_URL=$(sed -n 's/^MUSIC_ASSISTANT_URL=//p' .env | head -1)
python3 -c "
import sys,asyncio; sys.path.insert(0,'.')
import music_service as m
print(asyncio.run(m._ma('info')))"
```
`_ma` nests arguments under `\"args\"`; flat args are silently dropped. A bare `curl` gets 401.
