---
type: Reference
title: Panel home - time and temp on the edges, the big sun/moon crossing the whole panel
description: Owner request of 2026-11-09 (corrected the same day). The home screen's time lines up with the Zoe orb's left edge, the temp mirrors it on the right, and the big glowing sun (day) or moon with its real phase (night) rises out of the bottom-left, crosses the whole panel on an arc to the top-middle at noon, and sets below the bottom-right, following the real sunrise/sunset. Acceptance list, how it works, how it is verified.
tags: [panel, touch-ui, home, weather, estate]
timestamp: 2026-11-09T00:00:00+08:00
---

# Panel home: time / temp alignment and the sun/moon arc

## Acceptance - true when done

Owner words (2026-11-09): "the time's edge lines up with the zoe orb and then the temp is the same distance from the edge on the other side"; then, correcting a first small-icon reading: "the sun use to be big, I wanted it to move across the whole panel in an arc, like it was rising and setting in front of me in real life".

- The time's left edge lines up with the orb's left edge (44 px from the screen edge) - the glyph ink, not the text box.
- The temp mirrors it: its right edge is the same 44 px from the right screen edge.
- The sun is the BIG glowing canvas sun the home always had (same 360 px sprite, same glow). At sunrise it rises from below the bottom edge at the LEFT of the panel, climbs to the TOP-MIDDLE at solar noon (high, fully on screen), and sets below the bottom edge at the RIGHT at sunset. x is linear in the day fraction across nearly the full width; y = bottom - amplitude * sin(pi * p).
- At night the moon does the same between sunset and the next sunrise, at a disc size comparable to the sun's, with a soft glow and its real current phase (waxing lights the left limb: southern-sky view).
- It follows the real sunrise/sunset the weather feed carries (stale feeds are day-shifted; with no feed it falls back to 6am-6pm). It never replays a sunrise on a visit and never slides at the sun-to-moon handover.
- It sits behind every UI element (clock, date, temp, cards, dock, orb) and the text stays readable over it. Overcast / rain scenes show no body (as before); the clouds still drift over a partly-cloudy one.
- Reduced-motion keeps the still-sky repaint (once a second).
- There is no small icon beside the temp.

## How it works (`dist/touch/home.html`)

- Layout: `.hclock` is `left:44px`; `.htemp` is `right:44px`. `homeInk()` trims each text box's glyph side bearing (canvas `measureText`) so the ink lands on the line for any digits.
- Position: `celPos(now, sunrise, sunset)` (pure, between `CEL-BEGIN` and `CEL-END`) gives `{kind, p, h}`; `h = sin(pi * p)` over the day (sun) or the night (moon). `celXY(p, h)` maps it onto the 1280x720 stage: `x = 40 + 1200 p`, `y = 790 - 650 h` (centre below the bottom edge at both ends, 140 at the apex). `frame()` reads `Date.now()` and the cached `sunrise`/`sunset` (set by `loadWeather()` from `GET /api/weather/current`) every frame, so there is no state to seat or slide.
- Drawing: the sun is the existing `skySprites().sun`; the moon is `moonSprite(phase)` - the old moon glow, a faint dark disc, and the lit part from `moonPath()` (a half circle + half ellipse), one cached sprite per 1/60 of a month. Every other screen (weather, etc.) keeps the old static sprites.
- Phase: `moonPhase()` from the synodic month (no new API). `MOON_WAXING_LIT_LEFT` picks the lit limb while waxing.

## Verification

- `node services/zoe-ui/dist/test_touch_home_sun_moon.js` - positions, stale/absent feed, the arc endpoints off-screen bottom-left/right, the noon apex top-middle, sprite sizes, phase and lit area, CSS contract, no small icon left (CI: `services/zoe-data/tests/test_touch_home_sun_moon_harness.py`; negative controls via `CEL_SRC`).
- `scripts/maintenance/estate_browser_verify.py` check 0b - a real Chromium on the Pi measures the time's ink edge against the orb and the temp's ink edge from the right.
