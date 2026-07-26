# Dock pinned controls — design spec

**Status:** research / design. Not implemented. Read-only pass over production.
**Panel:** Raspberry Pi 5, 1280×720, kiosk Chromium.
**Verified against live HA:** `curl localhost:8000/api/ha/entities` — 2026-07-17.
**Mockups:** `labs/dock-pins-mockup/dock-mockup.html` → `shot_*.png` (1280×720).

---

## 0. The finding that reframes the task

The brief (and its correction) both assume the dock already renders a light pill **and** a
temp pill from real entities. Live data says otherwise:

| claim | reality |
|---|---|
| dock renders `light.*` | **0 `light.*` entities exist.** `_ha.lights` falls through to `input_boolean.*` |
| dock renders a temp pill from `climate.*` | **0 `climate.*` entities exist.** `_ha.climate === null` → **`.pc.temp` never renders. It is dead code on this panel.** |
| scenes have no dock tile | correct — 3 `scene.*` exist, rooms card renders them, dock does not |

`home.html:975-977`:
```js
_ha.lights = arr.filter(e=>dom(e)==='light').concat(arr.filter(e=>dom(e)==='input_boolean'));
_ha.climate = arr.filter(e=>dom(e)==='climate')[0] || null;   // ← null in this house
_ha.scenes  = arr.filter(e=>dom(e)==='scene');                // ← loaded, never docked
```

So the operator's "temp control for the room" **cannot** be delivered by pinning the existing
`.pc.temp` as-is. The pill's *design* is right and reusable; its *data source* is wrong.
The house's thermostat is an `input_number` + `sensor` pair, not a `climate` entity.

Second finding, direct from the operator's own words:

> "i will want the user to set a single word name… so no Living Room, it would be just Living."

Today's label logic is `nm.replace(/\s+light$/i,'')` — it strips a trailing " Light" only.
`input_boolean.living_room_light` → **"Living Room"**. The exact string he complained about is
what the dock renders right now, and at **193px** it is the widest tile in the dock
(see `shot_today.png`). The one-word rule isn't cosmetic; it's the width budget.

---

## 1. Real entity inventory (45 entities)

| domain | n | pinnable? |
|---|---|---|
| `sensor` | 11 | read-only — pairs into temp tile |
| `input_boolean` | 6 | **YES** — these are the de-facto lights |
| `select` | 5 | no |
| `scene` | 3 | **YES** — the genuine gap |
| `automation` | 3 | no |
| `conversation` | 2 | no |
| `script` | 2 | no |
| `tts` | 2 | no |
| `switch` | 2 | no (both `unavailable`) |
| `event`,`zone`,`person`,`input_number`,`sun`,`todo`,`media_player`,`stt`,`assist_satellite` | 1 each | `input_number` **YES** (as temp write-target) |
| **`light`** | **0** | — |
| **`climate`** | **0** | — |

**`input_boolean.*` (6)** — `living_room_light` "Living Room Light", `kitchen_light` "Kitchen Light",
`bedroom_light` "Bedroom Light", `porch_light` "Porch Light", `fan` **"Ceiling Fan"**, `tv` **"TV"**.
All `off`. Note the last two: they are in `_ha.lights` and would render **with a lightbulb icon**.
A ceiling fan is not a light. Only `slice(0,3)` hides this today.

**`scene.*` (3)** — `good_morning` "Good Morning", `good_night` "Good Night", `movie_time` "Movie Time".
State is `unknown` or a **timestamp of last activation** — never `on`/`off`:
```json
{"entity_id":"scene.good_night","state":"2026-04-06T04:58:23.796694+00:00",
 "attributes":{"entity_id":["input_boolean.living_room_light","input_boolean.kitchen_light",
                            "input_boolean.bedroom_light","input_boolean.porch_light",
                            "input_boolean.fan"],"friendly_name":"Good Night"}}
```
That timestamp is **not** glanceable state. A scene is fire-and-forget. Do not render it as on/off.

**The thermostat is two entities, not one:**
```json
{"entity_id":"input_number.thermostat_temperature","state":"21.0",
 "attributes":{"min":16.0,"max":30.0,"step":0.5,"mode":"box","unit_of_measurement":"°C",
               "friendly_name":"Thermostat"}}
{"entity_id":"sensor.current_temperature","state":"21.0",
 "attributes":{"friendly_name":"Current Temperature"}}
```
`sensor.current_temperature` = **read** (what the room is → the big glanceable number).
`input_number.thermostat_temperature` = **write** (the target → the slider), range **16–30, step 0.5**.

---

## 2. Per-domain tile spec

### 2.1 Light / toggle tile — `.pc.light` — REUSE UNCHANGED
- **Shows:** domain icon + one-word label. State is carried by `.pc.on`: amber radial glow +
  dark ink + `box-shadow:0 0 26px rgba(255,190,100,.45)`. Off = flat white-10% glass.
- **Glance:** the amber glow is legible in peripheral vision at 1280×720 — the strongest state
  signal in the dock. Keep it.
- **Tap:** optimistic `classList.toggle('on')` → `POST /api/ha/control {action:'turn_on'|'turn_off'}`,
  revert on failure. Already implemented (`home.html:998`).
- **Change needed:** *icon must follow the entity, not the domain.* `input_boolean.fan` → fan icon,
  `input_boolean.tv` → tv icon. `.pc.on` amber = "energised", generic; the icon carries meaning.
- **Width:** 116–149px measured (`Fan` 116, `Kitchen` 149). Within budget **only with one-word labels**
  ("Bedroom" 163, "Living Room" 193 — both blow it).

### 2.2 Temp tile — `.pc.temp` + `.tpop` — REUSE THE DESIGN, REWIRE THE DATA
- **Shows:** `.tv` = current temp (19px, tabular-nums) over `.tl` = one-word label (9px, uppercase,
  `.1em` tracking). Mockup shows `21°` / `BED`.
- **Glance:** a number, not a state colour. Deliberately quiet — a thermostat is ambient info.
- **Tap → popover.** This is the interaction, and it already exists (`home.html:1001`):
  ```js
  tb.onclick = function(e){ if(e.target.closest('.tpop')) return; tb.classList.toggle('open'); };
  ```
  `.tpop` is 112×200px, opens **above** the dock (`bottom:64px`, `z-index:50`), glass
  `backdrop-filter:blur(16px)`, containing `.tpv` (target), `.tsl` vertical slider
  (`.tff` fill + `.tkn` 30px knob), `.tpl` label. Drag the slider → commit on `pointerup`.
  A tap cannot set a temperature; a tap **opens** the control. Clicks inside don't dismiss.
- **MUST PRESERVE:** `refreshHA()` skips re-render while `.pc.temp.open` is present
  (`home.html:985`) — the 30s poll must not destroy the popover mid-drag. Any pinned-tile
  refactor has to keep this guard, and generalise it if other tiles gain popovers.
- **Changes needed (all forced by the data, none cosmetic):**
  1. **Two entities per pin.** Read `sensor.current_temperature` for `.tv`; write
     `input_number.thermostat_temperature`. The pin schema needs `read_eid` + `write_eid`.
     `_ha.climate` (a single `climate.*` lookup) cannot express this.
  2. **Service:** `set_temperature` → **`set_value`** with `{value: N}`.
     `ha_control.py:127` is `action_map.get(action, action)` — unmapped actions pass through,
     so `set_value` reaches the bridge without a backend change. Worth an explicit map entry anyway.
  3. **Range is hardcoded and wrong.** `home.html:1002` pins 15–28:
     ```js
     function pt(){ var p=Math.max(0,Math.min(100,(tv-15)/13*100)); ... }
     var at=y=>{ tv=Math.round(15 + clamp(1-(y-r.top)/r.height)*13); }
     ```
     Must read `min`/`max`/`step` from attributes (**16/30/0.5**). Today the knob would misreport
     position and `Math.round` would silently discard the 0.5 step.
- **Width:** **71px** — by far the cheapest tile, well under the 100–140px budget. The column
  layout earns its keep.

### 2.3 Scene tile — `.pc.scene` — **NEW** (the only genuinely missing tile)
- **Why new:** a scene has no state to show and nothing to toggle. Neither `.pc.light`
  (state-bearing, amber) nor `.pc.temp` (numeric, popover) can express it.
- **Shows:** sparkle icon (22px, 55% white — deliberately dimmer than a light's 25px icon) +
  one-word label. **Never** `.on`. Always flat glass — a scene has no resting state, and showing
  one would be a lie.
- **Tap:** fire `{action:'turn_on'}` → flash `.fire` for ~900ms → clear. Borrows the rooms card's
  existing momentary pattern verbatim (`home.html:1521`), which already does exactly this with
  `.rchip.on` + `setTimeout(...,900)`. The flash uses the launcher's accent
  (`rgba(123,150,255,.9)`, `--accent`), **not** the light amber — a scene firing is an *event*,
  and must not be mistaken for a light turning on.
- **Optimistic-UI note:** unlike a light, there is nothing to revert to on failure. Flash regardless;
  a scene that silently failed is indistinguishable from one that worked, which is acceptable for
  fire-and-forget. Do not attempt state reconciliation.
- **Width:** 116–122px measured. Within budget.

```css
/* new — the only CSS this feature adds */
#dock .pc.scene{padding:0 18px;gap:9px}
#dock .pc.scene svg{width:22px;height:22px;color:rgba(255,255,255,.55)}
#dock .pc.scene.fire{background:rgba(123,150,255,.9);border-color:rgba(123,150,255,.6);
                     color:#fff;box-shadow:0 0 24px rgba(123,150,255,.5)}
#dock .pc.scene.fire svg{color:#fff}
```

### 2.4 Not worth pinning — and why

| domain | verdict |
|---|---|
| `switch.*` (2) | Both `unavailable` — `lva_...mute`, `lva_...thinking_sound`. Panel plumbing, not house controls. If real switches ever appear they should reuse `.pc.light` with a switch icon. |
| `media_player.*` (1) | `unavailable`, and the dock **already** has `.pc.dnp` for music. A second music control is a bug, not a feature. |
| `sensor.*` (11) | Read-only → nothing to tap; a pin that can't be pressed is a widget, not a control. 8 are backup/sun timestamps (no glance value). `sensor.current_temperature` is the exception and is consumed **inside** the temp tile. |
| `select.*` (5) | Wake-word / assistant pickers. Config, not daily control. 2 are `unavailable`. Belongs in settings. |
| `automation.*`, `script.*` (5) | Toggling an automation's *enabled* flag looks identical to toggling a light but means something completely different — dangerous ambiguity on a glance surface. |
| `todo.shopping_list` | Has a launcher screen already; a count is not a control. |
| `conversation`, `tts`, `stt`, `assist_satellite`, `person`, `zone`, `event` | Infrastructure. No user-facing control surface. |

**Pinnable universe today: 6 `input_boolean` + 3 `scene` + 1 thermostat pair = 10 candidates for ~4 slots.**

---

## 3. Width budget — measured, not estimated

Dock renders **74px tall** (54px inner + 9px×2 padding + 2px border) — matches the brief.
Now-playing chip's **true max is 326px**, not ~380: `.dnp` has `max-width:380px` but `.dnm` is
capped at `max-width:132px`, which binds first. Measured with a saturated title
("Everything In Its Right Place (Remastered)").

| scenario | dock width | free | verdict |
|---|---|---|---|
| B — Bedroom: light + temp, no music | **307px** | 973 | trivially fits |
| A — Bedroom: light + temp + music | **577px** | 703 | fits |
| D — Lounge: 2 scenes + music | **626px** | 654 | fits |
| E — Kitchen: 2 lights + music | **671px** | 609 | fits |
| F — **4 pins + saturated music** | **945px** | **335** | **fits** |
| G — today (unpinned 3 lights + music) | 903px | 377 | fits, but nobody chose them |

**The width worry does not materialise.** The operator's stated max (4 pins) with music playing
and a worst-case track title lands at 945px of 1280 — 335px of margin. The binding constraint is
**not** the number of pins; it is **label length**. Three two-word labels ("Living Room" 193 +
"Bedroom" 163 + "Kitchen" 149 = 505px) cost more than four one-word pins with a temp tile
(149+134+116+71 = 470px). The one-word rule *is* the width solution.

Per-tile measured widths: `temp` **71** · `scene` **116–122** · `light` **116–149** (one-word) ·
`light` **163–193** (two-word — over budget) · `apps` 54 · `now-playing` 259–326.

**Recommendation:** cap pins at 4 **and** hard-cap the label. Enforce one word at input time in
settings (reject/trim whitespace), plus a defensive `max-width:110px` + ellipsis on `.nm` so a bad
pin can never push the dock wide. With those two guards the dock cannot overflow.

---

## 4. What an implementer needs to build

1. **Pin schema** (per room/panel, ordered, max 4):
   `{kind:'toggle'|'scene'|'temp', label:'Bed', write_eid, read_eid?, icon?}`
   `read_eid` exists solely for `temp`. `label` is operator-set, one word, ≤10 chars.
2. **`renderDock()` dispatches on `kind`**, replacing `_ha.lights.slice(0,3)` +
   the `_ha.climate` special-case with a loop over pins. Keep the `.pc.temp.open` refresh guard.
3. **Generalise `_ha`**: it currently pre-buckets into `lights`/`climate`/`scenes`. Pins need
   lookup **by entity_id** across all entities. Keep the buckets for the rooms/wake screens
   (`home.html:1514`, `1555`) which still use them.
4. **Fix the temp slider range** to read `min`/`max`/`step` from attributes, and send `set_value`.
5. **Icon registry** keyed by entity_id/device_class, not domain (fan ≠ light).
6. **Fallback:** with no pins configured, keep today's `slice(0,3)` behaviour so a fresh panel
   isn't empty. Pins are an override, not a prerequisite.
7. **Settings UI** — out of scope here; per the operator, "the real work isn't the settings screen".

Backend needs **no change**: `ha_control.py` passes unmapped actions through
(`service = action_map.get(action, action)`), so `set_value` works today. Adding it to
`action_map` explicitly is cheap insurance.

---

## 5. Mockups

`labs/dock-pins-mockup/dock-mockup.html` — throwaway. Dock CSS lifted **verbatim** from
`home.html` (`#dock`, `.pc`, `.pc.light`, `.pc.temp`+`.tpop`, `.pc.dnp`, `.pc.apps`, `.ddiv`);
`.pc.scene` is the only new rule. All entity ids and friendly names are real.
Every shot is 1280×720 with a live width read-out along the bottom.

| file | scenario |
|---|---|
| `shot_bedroom_music.png` | A — 1 light + temp + music (577px) |
| `shot_bedroom_nomusic.png` | B — 1 light + temp (307px) |
| `shot_temp_open.png` | C — temp engaged, `.tpop` slider at 22° of 16–30 |
| `shot_lounge.png` | D — 2 scenes + music (626px) |
| `shot_kitchen.png` | E — 2 lights + music (671px) |
| `shot_max4.png` / `shot_max4_longtitle.png` | F — 4 pins + music (878 / **945px**) |
| `shot_today.png` | G — today's unchosen `slice(0,3)`, "Living Room" at 193px |
