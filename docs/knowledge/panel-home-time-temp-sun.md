---
type: Reference
title: Panel home - time and temp on the edges, sun/moon beside the temp
description: Owner request of 2026-11-09. The home screen's time lines up with the Zoe orb's left edge, the temp mirrors it on the right, and a small sun (day) or moon (night, with its phase) sits just left of the temp and rises and sets with the real sun. Acceptance list, how it works, how it is verified.
tags: [panel, touch-ui, home, weather, estate]
timestamp: 2026-11-09T00:00:00+08:00
---

# Panel home: time / temp alignment and the sun/moon

## Acceptance - true when done

Owner words (2026-11-09): "the sun and the moon need to be closer to the temp ... the time's edge lines up with the zoe orb and then the temp is the same distance from the edge on the other side and the sun or moon off to the left of the temp ... or the sun or moon could rise and set and the display follows those changes".

- The time's left edge lines up with the orb's left edge (44 px from the screen edge) - the glyph ink, not the text box.
- The temp mirrors it: its right edge is the same 44 px from the right screen edge.
- The sun (day) or moon (night) sits just left of the temp, close enough to read as one unit with it, and never overlaps the number or the description under it.
- It rises and sets: low at sunrise, highest at solar noon, low at sunset; at night the moon does the same between sunset and the next sunrise. Its height follows the real sunrise/sunset the weather feed already carries.
- The moon shows its phase (computed from the date; no new API).
- It re-seats every few minutes, moves subtly, and a visit to the home screen never replays a sunrise.
- Reduced-motion: it still sits at the right place, it just does not glide. Both skies (light day, dark night) read well.
- The home screen no longer also paints the big canvas sun/moon.

## How it works (`dist/touch/home.html`)

- Layout: `.hclock` is `left:44px`; `.htemp` is `right:44px` (the old `right:120px` "clear the home cog" override is gone - there is no cog on home). `homeInk()` trims each text box's glyph side bearing (canvas `measureText`) so the ink lands on the line for any digits.
- Position: `celPos(now, sunrise, sunset)` (pure, between `CEL-BEGIN` and `CEL-END`) gives `{kind, p, h}`; `h = sin(pi * p)` over the day (sun) or the night (moon). Sunrise/sunset come from `GET /api/weather/current` (`sunrise`, `sunset`), shifted by whole days so a stale fetch still brackets now; with no feed it falls back to 6am-6pm.
- Drawing: `.hcel` is a 72x76 band in the temp row; the icon's `--cx`/`--cy` are set from `p`/`h`. The band is clipped at its bottom edge (the horizon line) so the body rises out of and sets into it, and `x travel + icon width <= band width` is pinned so it cannot reach the number.
- Moon phase: `moonPhase()` from the synodic month; `MOON_WAXING_LIT_LEFT` picks which limb is lit while waxing (flip it for a northern panel).

## Verification

- `node services/zoe-ui/dist/test_touch_home_sun_moon.js` - sun/moon positions, stale feed, no feed, phase, drawing, CSS contract (CI: `services/zoe-data/tests/test_touch_home_sun_moon_harness.py`; negative controls via `CEL_SRC`).
- `scripts/maintenance/estate_browser_verify.py` check 0b - a real Chromium on the Pi measures the time's ink edge against the orb, the temp's ink edge from the right, and that the icon stays inside its band left of the number.
