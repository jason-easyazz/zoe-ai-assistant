# Skybridge design system

The single source of truth for how every panel card looks and moves. Re-skin all
existing cards to this; the generative engine (later) composes new cards from the
same primitives so **Zoe-authored cards are Apple-quality by default**. If a value
isn't here, it's a bug in the system, not a per-card decision.

Implemented by: `css/skybridge-ds.css` (tokens + `.sky-material`),
`css/skybridge-type.css` + `css/fonts/` (self-hosted Inter), `css/skybridge-glyphs.svg`
(the glyph sprite). Validated on the real 1280×720 panel via CDP.

---

## 1. Principles (Apple: clarity · deference · depth)
- **Content first, chrome quiet.** Big legible values, muted secondary text, one
  accent per card. The UI never competes with the data.
- **Colour with intent, not decoration.** Colour encodes meaning (condition,
  domain, state) — never a rainbow. But *use* it: muted-grey reads as "sad/dead".
- **Depth, not flatness.** Cards are translucent glass that float above an ambient
  background with a soft cast shadow + a 1px top inner-highlight.
- **Crafted, not generic.** Custom filled/dimensional glyphs — never thin uniform
  line icons (they read as generic UI chrome; this was the single biggest tell).
- **Motion is purposeful and cheap.** Only transforms/opacity (Pi-safe); it
  confirms, guides, or shows liveness — it never distracts.

## 2. Material — the one glass recipe (`.sky-material`)
```
background: linear-gradient(157deg, rgba(255,255,255,.11), rgba(255,255,255,.035));
backdrop-filter: blur(26px) saturate(140%);
border: 1px solid rgba(255,255,255,.14);
box-shadow: 0 26px 60px -18px rgba(0,0,0,.6), inset 0 1px 0 rgba(255,255,255,.22);
```
A card that is itself a coloured surface (weather, music artwork) replaces the
gradient with its theme gradient but keeps the shadow + top-highlight.

## 3. Typography — Inter (SF Pro stand-in), self-hosted
- Family: `Inter`, system fallback. Weights loaded: **300 / 400 / 500 / 600**.
- **Numerals always `font-variant-numeric: tabular-nums`** (clocks, temps, timers).
- Scale (see `--sky-text-*`): hero numeral 300 weight (weather temp), clock/timer
  600, title 28, heading 20, body 16, label 14, caption 13, micro 12.
- **Micro label** = 12px / 600 / `letter-spacing: 2.5px` / uppercase — the small
  "PASTA", "NEXT DAYS" note. Used sparingly.
- Negative tracking (`-1px`) on the largest numerals only.

## 4. Colour & themes
- **Neutral ramp** (`--sky-ink-0..4`): white → 26%-white. All on-glass text.
- **Accents** (each a 2-stop gradient): mint→cyan (default/calm), amber→coral→magenta
  (warm/cooking/timer), blue (cool/info), violet, amber, red (alert).
- **Light + dark are two token sets, switched by ONE variable.** Dark = the values
  above. Light = dark text (`#16324d` primary, `#3a5e80` secondary) on bright
  surfaces, hairlines darken to `rgba(20,50,80,.16)`.
- **`prefers` / panel theme is chosen by SUN POSITION** — light between sunrise and
  sunset, dark after sundown — with a manual override (§10).
- **Condition → theme** (weather and any weather-aware surface):
  | condition | gradient | glyph |
  |---|---|---|
  | clear-night | `#1b2454 → #3a2a72 → #6a3470` | moon |
  | clear-day | `#5aa9ec → #8fcdf2 → #dcefff` | sun |
  | cloudy | slate `#4a5570 → #6a7390` | cloud |
  | rain/drizzle | `#2b3a63 → #4a5e86` | rain |
  | storm | deep slate `#262d44 → #3d4868` | storm |

## 5. Iconography — the glyph sprite (`skybridge-glyphs.svg`)
Custom filled, dimensional SVG symbols `<use href="#i-…">`, gradients defined once:
- `i-moon` — warm crescent (radial cream→gold, masked).
- `i-sun` — radial gold core + 8 rays.
- `i-cloud` — white→blue-grey fill, hairline edge.
- `i-rain` — cloud + blue droplets. `i-storm` — slate cloud + amber bolt.
**To add a glyph:** new `<symbol viewBox>` with a *filled* shape + a 2-stop gradient;
never reach for a line-icon font. The glyph set is a catalog primitive — Zoe's
cards draw from it.

## 6. Spacing · radius · elevation
- **Spacing** = 4px scale (`--sky-space-1..12`). Internal gaps 8/12/16; section
  rhythm 20/24/32; card padding 24 (small) → 46 (full-screen).
- **Radius**: sm 12, md 20, lg 28, **xl 38** (cards), pill. Big soft corners read
  premium. No rounded corners on single-sided borders.
- **Elevation**: the card shadow + top highlight (§2). One elevation level; don't
  stack shadows.

## 7. Motion
- Ambient background **aurora** drifts slowly (15–26s, translate+scale, opacity ~.42).
- Active progress ring **glows** (opacity 2.4–2.6s). Stars **twinkle** (opacity 3s).
- Equaliser/now-playing bars **scaleY** (~.9s). Durations: `--sky-dur-*`; easings
  `--sky-ease-standard` / `-spring`. All gated by `prefers-reduced-motion`.

## 8. Layout patterns (the primitives a card is composed from)
- **Card frame** — `.sky-material`, radius xl, content.
- **Header row** — title (location/label) + secondary line, with the domain glyph
  top-right.
- **Hero block** — the big numeral + a small detail stack beside it (H/L, humidity…).
- **Strip** — evenly spaced columns (hourly): time / glyph / value, centred.
- **Side panel + divider** — `flex 0 0` column behind a 1px hairline for secondary
  data (multi-day forecast). Main : side ≈ 1.7 : (fixed).
- **Centred content** — absolutely-centred numeral + micro-label (timer/clock).
- **Progress border** — rounded-rect SVG stroke, `pathLength=100`,
  `dashoffset = spent%`, gradient stroke + glow (the timer).
- **Metric stack** — label (caption, muted) over value (medium). For people health,
  list counts, etc.

## 9. Sizing — fill the stage (lesson)
Cards render in `#skyCards` and should **fill the stage at panel scale**
(~1180px wide on the 1280×720 panel), NOT a narrow centred column. Type scales with
the card: full-screen weather temp ≈ 150px, glyphs 44–84px. Always verify at real
panel scale via CDP — a design that looks right in a chat mockup can render tiny or
collapse on the panel (see the clock/timer CSS bugs).

## 10. Light/dark behaviour
- **Auto (default):** light from local sunrise → sunset, dark otherwise. The panel
  already has location; sunrise/sunset are computed from it.
- **Override:** a Settings control `Auto / Light / Dark`. Override pins the theme
  (bedroom panel → always dark, kitchen → always light). Stored per panel.
- Implementation: a `data-theme="light|dark"` (or body class) selects the token set;
  Auto sets it on a timer at the sun thresholds.

## 11. Applying it to the other cards
- **Weather** — reference implementation (done in mockup): condition theme + hero
  temp + hourly strip + side forecast.
- **Timer** — centred numeral + micro-label + progress-border (warm accent); square
  tile; tiles side-by-side for multiples.
- **Clock** — centred numeral (hero), date as caption; uses the live-clock spans.
- **Calendar** — header (day + "N events") + a strip/list of event rows
  (time · title · location), category as a coloured accent dot.
- **Lists** — header + item rows with a metric (qty) + state (checked); category
  tabs as quiet segmented control.
- **People** — header + a grid of metric stacks (avatar, name, relationship, a
  health bar using the accent gradient).
- **Auth** — profile grid; glass tiles, avatar initials, no chrome.

## 12. Anti-patterns (caught the hard way)
- ✗ Thin uniform line icons → generic. ✓ filled dimensional glyphs.
- ✗ All-grey "minimal" → reads as sad/dead. ✓ colour with intent.
- ✗ Per-card magic numbers / `!important` patches → drift + the collapse bugs.
  ✓ tokens.
- ✗ Narrow centred card → tiny on panel. ✓ fill the stage, verify at scale.
- ✗ One mode only. ✓ light + dark, sun-driven.
