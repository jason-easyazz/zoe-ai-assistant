---
type: research
title: "Common household voice questions: what Home Assistant Assist and OVOS cover, how they phrase answers, and where Zoe's fast paths miss (2026-10)"
date: 2026-10-14
status: research only. No code, flag, service or test changed. The measurements were made with a throwaway script (not committed) that ran Zoe's two deterministic classifiers over Home Assistant's public labelled test sentences. No household data, logs or voice samples were read or quoted. Every example utterance here is either invented or a public string from the Home Assistant intents test set.
description: "Owner asked for research into common voice questions and how to answer them, starting from Home Assistant. Covers: HA's built-in intent set, the hassil template grammar, phrasing counts, and HA's response templates read from source; OVOS weather-skill coverage; published smart-speaker usage data; answer-style rules; a measured gap map of Zoe's Skybridge and intent_router fast paths against HA's 1,192 labelled English test sentences; and a recommendation to adopt the HA corpus (CC-BY-4.0, verified) as a phrasing regression corpus."
evidence_labels: "[src] read in a cited source tree today | [measured] this run, scratch harness over the HA test set | [fetched] web page read today | [search] search-result summary only, the page itself was not read | [judgement] design opinion, not a sourced fact"
---

# Common household voice questions vs Home Assistant Assist, OVOS and Zoe (2026-10)

**Trigger.** Zoe fumbled ordinary weather questions. "How hot is it going to get today?" got the current conditions or a rambling answer. A separate branch (`fix/weather-today-high`) is fixing that bug in `skybridge_service.py`. This record is the wider study the owner asked for: what people actually ask, how mature assistants phrase the answers, and where Zoe's deterministic paths miss. It feeds follow-up fixes and does not make any itself.

## 0. Answer first

1. **Adopt HA's intents corpus as Zoe's phrasing regression corpus. This is the highest-leverage step** (section 6).
   - What it is: 1,192 labelled English test sentences plus 937 hassil sentence templates across 42 intents [src].
   - Licence: **CC-BY-4.0, verified** in the repo's `LICENSE.md` and by the GitHub licence API (`OHF-Voice/intents`, spdx `CC-BY-4.0`). Zoe is MIT; carrying CC-BY data only needs attribution.
   - Weather gap: HA's weather intent is current-conditions only, so pair it with the **OVOS weather skill's 475 `.intent` template lines** (Apache-2.0, verified). Those cover high/low, "how hot will it get", next rain and umbrella questions.
2. **Running the HA test set through Zoe already finds real defects** [measured].
   - The worst are **false writes**:
     - "add 5 minutes to the timer" and "remove 1 hour from the pizza timer" **create a new timer**. That happened for 74 of 78 increase sentences and 96 of 100 decrease sentences.
     - "turn off the lights in 5 minutes" turns them off **now** (14 of 14 delayed-command sentences).
     - "pause the 5 minute timer" creates a timer.
   - Next come **wrong-fact answers**, where `" temperature"` is matched anywhere in the utterance:
     - indoor/thermostat questions get the outdoor weather (12 of 19);
     - "set the bedroom lamp temperature to 2700 K" gets a weather report (37 of 99 light-set sentences);
     - "set temperature to 20.5 degrees" also gets the weather.
   - The substring `" list"` matches "are you always **list**ening?", which reads out a list.
3. **HA is weak exactly where Zoe fumbled** [src].
   - `HassGetWeather` is described in code as "Gets the current weather". It has 4 templates and answers `"{temperature} {unit} and {condition}"`, with no forecast, high/low or rain chance.
   - In HA, "how hot is it" belongs to `HassClimateGetTemperature`, which is the *indoor* thermostat.
   - So HA is the right source for **breadth of phrasing** (timers, lists, devices, media, time/date), and OVOS is the right source for **weather depth**.
4. **Answer style.** HA, OVOS and Alexa agree: **say the asked fact first, in one breath, with whole numbers and relative days, and leave detail for the screen** (section 4). Two Zoe replies break this:
   - `"Here is the forecast for {city}."` (`skybridge_service.py:2811`) contains no facts.
   - The voice calendar read `"You have {n} events {qualifier}."` (`skybridge_service.py:2766`) gives a count only.

## 1. Home Assistant Assist, read from source

Sources: `opensrc path home-assistant/intents` (the repo now lives at `OHF-Voice/intents`; head `c602e0b` on 2026-10-10 when checked) and `opensrc path home-assistant/core` (`dev`, head `f4765a4` when checked). Both snapshots were fetched 2026-10-14. Template matching is HassIL (`OHF-Voice/hassil`, Apache-2.0; the intents repo pins `hassil==3.12.1`) [src].

### 1.1 The built-in intent set (English) [src]

**Template-matched intents.** The intents repo has 42 intents with English sentences:
- **Time/date:** HassGetCurrentTime, HassGetCurrentDate
- **Weather:** HassGetWeather
- **Timers:** HassStartTimer, HassTimerStatus, HassCancelTimer, HassCancelAllTimers, HassIncreaseTimer, HassDecreaseTimer, HassPauseTimer, HassUnpauseTimer
- **Lists:** HassListAddItem, HassListCompleteItem, HassListRemoveItem, HassShoppingListAddItem, HassShoppingListCompleteItem
- **Devices:** HassTurnOn, HassTurnOff, HassGetState, HassLightSet, HassSetPosition, HassFanSetSpeed
- **Climate:** HassClimateGetTemperature, HassClimateSetTemperature, HassClimateSetFanMode
- **Media:** HassMediaSearchAndPlay, HassMediaPause, HassMediaUnpause, HassMediaNext, HassMediaPrevious, HassSetVolume, HassSetVolumeRelative, HassMediaPlayerMute, HassMediaPlayerUnmute
- **Vacuum and mower:** HassVacuumStart, HassVacuumReturnToBase, HassVacuumCleanArea, HassLawnMowerStartMowing, HassLawnMowerDock
- **Other:** HassBroadcast, HassNevermind, HassRespond

**Core-only handlers.** Core also registers HassToggle, HassOpenCover, HassCloseCover, HassStopMoving, HassHumidifierSetpoint, HassHumidifierMode and HassShoppingListLastItems. No English sentence file for these was found in the snapshot.

**What HA does *not* template-match:**
- Calendar reads, to-do reads, reminders, alarms, news, sports, conversion, maths, jokes and definitions.
- Calendar and to-do reads exist only as **LLM tools**:
  - `calendar__get_events` takes `range` of `today` or `week`. Its description says "When asked if something happens, search the whole week" (`components/calendar/llm.py`).
  - `todo__get_items` lives in `components/todo/llm.py`.
- Everything else falls to the configured LLM conversation agent.
- The pipeline option `prefer_local_intents` (`assist_pipeline/models.py:57`) tries the template matcher first and falls back to the LLM. That is the same shape as Zoe's cascade, which is evidence that Zoe's design matches the field.

**Weather data model.**
- Forecasts come from the `weather.get_forecasts` action with `type` of `daily`, `hourly` or `twice_daily` (`components/weather/services.yaml`).
- The `Forecast` TypedDict (`components/weather/__init__.py:170`) has these fields: `condition`, `datetime`, `native_temperature` (the high), `native_templow`, `native_apparent_temperature`, `precipitation_probability`, `native_precipitation`, `humidity`, `cloud_coverage`, wind speed/gust/bearing, `uv_index` and `is_daytime`.
- **None of this is reachable from a template intent.** `GetWeatherIntent.async_handle` (`components/weather/intent.py`) returns only the matched entity's *state*.
- With an LLM agent, the "temperature outside" answer comes from `GetLiveContext`, which is also current state (`components/homeassistant/llm.py`, `DYNAMIC_CONTEXT_PROMPT`).

### 1.2 Grammar (hassil) [src]

- **Alternatives** `(a|b)` and **optionals** `[x]` can appear inside words too, for example `minute[s]` and `turn(s|ed)`.
- **Slot lists:**
  - `{list}` or `{list:slot_name}` are value lists, ranges (`timer_minutes`) or wildcards (`{search_query}`, `{shopping_list_item}`).
  - Wildcards are excluded from sampling by default (`sample_intents(..., exclude_sentences_with_wildcards=True)`).
- **Expansion rules** `<rule>` are shared per language. Examples: `what_is: (what's|whats|what is|tell me)`, `the: (the|my|our)`, `timer_set: (start|set|create)`.
- **Skip words** are stripped before matching: `please`, `can you`, `could you`, `would you`, `for me`, `i'd like`, `i want` (`sentences/en/_common.yaml`).
- **Context and slot combinations.**
  - `requires_context` / `excludes_context` (for example `area` from the satellite's room, which gives "turn on the lights **in here**").
  - Sentences are being regrouped by **slot combination** with an importance level (required / usable / complete / optional), declared in `intents.yaml` (`docs/slot_combinations.md`).
  - Each data block also carries `speech_to_phrase: true` on a lean subset: the phrasings a closed-vocabulary STT should know.
- **Tooling.** `python3 -m script.intentfest sample --language en --intents <...> -n N` emits `{"intent","text"}` JSON lines using hassil's `sample_intents` (`script/intentfest/sample.py`). That means the corpus can be expanded with HA's own tool; nothing needs re-implementing.

### 1.3 How many phrasings HA keeps [src, counted today]

| Intent | Template lines | Labelled test sentences |
|---|---|---|
| HassGetWeather | 4 | 5 |
| HassGetCurrentTime / Date | 2 / 2 | 4 / 4 |
| HassStartTimer | 93 | 99 |
| HassTimerStatus | 27 | 27 |
| HassIncreaseTimer / Decrease | 78 / 100 | 78 / 100 |
| HassListAddItem / ShoppingListAddItem | 2 / 2 | 2 / 2 |
| HassClimateGetTemperature | 12 | 19 |
| HassGetState | 128 | 128 |
| HassTurnOn / TurnOff | 115 / 98 | 139 / 128 |
| HassLightSet | 51 | 99 |
| HassSetVolumeRelative | 51 | 80 |
| **All 42 English intents** | **937** | **1,192** |

Each template expands into many concrete sentences, so a few hundred lines cover thousands of utterances. Investment follows *slot complexity*: timers and devices get the most, while time/date/weather get almost nothing. Those intents are simple for HA because the answer is one state value.

### 1.4 How HA phrases answers (`responses/en/*.yaml`) [src]

Every reply is a Jinja2 template keyed by response name. The pattern is **the fact and nothing else**:

| Intent | Response (rendered) | Notes |
|---|---|---|
| HassGetWeather | `47 °F and raining` | temperature + unit + condition phrase mapped from 16 condition codes ("and partly cloudy", "with fog") |
| HassGetCurrentTime | `3:05 PM` | 12-hour, no "It is" |
| HassGetCurrentDate | `October 14th, 2026` | ordinal day |
| HassStartTimer | `Timer set for 5 minutes named pizza` | "half" becomes 30; a delayed command says `Command will be executed in 5 minutes` |
| HassTimerStatus | `4 minutes and 10 seconds left.` | soonest active timer first; with several timers adds `"2 running timers."` and `"left on 5 minute pizza timer."` to disambiguate |
| HassListAddItem | `Added apples` | |
| HassClimateGetTemperature | `21 degrees` | current_temperature, else state; singular "degree" for 1 |
| Errors | `Sorry, I couldn't find that timer` / `Sorry, I am not aware of any area called {{ area }}` | specific, one line |

The conditionals are on available fields only (`if slots.hours is defined`, `if num_timers > 1`), so a missing field is left out rather than spoken as "None".

## 2. OVOS weather skill: the depth HA lacks [src]

Source: `opensrc path OpenVoiceOS/ovos-skill-weather` (`dev` head `991439b`, Apache-2.0). Its `locale/en-US/intents/*.intent` files hold 475 template lines across weather, temperature, condition, next_rain, humidity, wind, sunrise and sunset.

Phrasing families HA has no equivalent for (template fragments quoted from the source):

- **High/low and "how hot will it get":**
  - `how hot will it (get|be) [today|tomorrow|this week]`
  - `(what is|what's) the (high|highest|max|maximum) (temperature|temp) [today|tomorrow|tonight|this week|this weekend|on monday…]`
  - `(is it|is it going to be|will it be|will it get|is it gonna be) (hot|warm) (today|tomorrow|tonight|…)`
- **Time-of-day temperature:** `(what is|what's) the (temperature|temp) (this morning|this afternoon|tonight|later|at noon|at (1..12) (am|pm))`
- **Next rain:** `when will it rain (next|again)`, `how long until it rains`, `what is the next chance of rain`
- **Condition yes/no (263 lines):** `will it (rain|snow)`, `should I (bring|carry|pack) (an|my) umbrella`, `will it be cloudy [(today|tomorrow)]`

The dialog files show the answer style:
- `{day} it will be as high as {temperature}`
- `Today's forecast is for a high of {high_temperature} and a low of {low_temperature}.`
- `It's currently {condition} and {temperature} degrees {temperature_unit}.`
- `There is a {percent} chance of {precipitation} {day}`
- `Yes, expect {condition} {day}` / `No, the forecast calls for {condition} today`

Yes/no questions get "Yes"/"No" first.

Two cautions:
- The `.intent` syntax (`(a|b)`, `[x]`, `{entity}`) is close to hassil but is not hassil. Expand it with a small dedicated expander, or treat each line as one test string after choosing alternatives.
- Some lines are near-duplicates.

## 3. What people actually ask

| Source | Finding | Label |
|---|---|---|
| Voicebot.ai, U.S. adults (>1,000), article 2020-05-03 | Ranking stable since 2018: streaming music #1 (ever/monthly/daily); asking a question #2 (ever/monthly); weather #3 daily; timers and alarms #5 and #6; news/sports growing; daily "ask a question" use −7.6% year on year | [fetched] (article reports ranks, not percentages) |
| Edison Research, Australia smart speaker owners (reported 2021-07-13) | music 65%, weather forecast 56%, time 50%, set a timer 46% | [fetched] (EFTM report of Edison data) |
| Ammari, Kaye, Tsai, Bentley, "Music, Search, and IoT", ACM TOCHI 26(3) 2019; 259,164 logged commands (193,665 Alexa / 65,499 Google Home), 170 users | top three on both platforms: music, hands-free search, IoT control (IoT ≈17% of Alexa and ≈10% of Google Home commands); errors 11% / 17% | [fetched] (Mozilla UX blog summary, 2019-12-19; the paper's full category table was not read) |
| NPR/Edison Smart Audio Report 2017 | music 68%, weather 58%, general questions 52% used "regularly" | [search] |
| Nielsen Q2 2018 | 90% stream music weekly; real-time info (weather, traffic) 81%; facts 75% | [search] |
| Adobe survey (year not confirmed) | music 70%, weather 64%, alarms and reminders 46% | [search] |

**Demand ranking used for the gap map.** The sources agree on the order more than on the numbers.
1. Music
2. Weather (current + forecast)
3. General questions / search
4. Time
5. Timers
6. Smart-home control and state
7. Alarms and reminders
8. Shopping/to-do lists
9. Calendar
10. Date
11. News and sports
12. Small utilities: maths, unit conversion, spelling/definitions, jokes, "what song is this"

No published source gives a share for the long tail in item 12. Its position is [judgement], based on its absence from the top tiers of every survey.

## 4. Answer-style rules

Sources:
- the HA and OVOS templates above [src];
- the Alexa "Be brief" principles: "Get to the point already!", "Write responses that can be read out by a human in one or two breaths", "Give only one to a few options at a time", and send lengthy data to the app rather than read it out [fetched]. The Alexa Voice Design Guide's one-breath test is the stricter single-breath version [search];
- Google's conversation-design pages: the spoken prompt carries the core message and the display adds detail [search].

1. **Lead with the asked fact.** The first words are the answer: "Twenty-six today." / "3:05." / "Added milk." [src HA/OVOS]
2. **Answer the question asked, not the nearest data.**
   - A high-temperature question gets the high, not the current temperature.
   - A yes/no question gets "Yes"/"No" first, then the reason (OVOS `daily_condition_expected`).
   - "Is X on the list" gets yes/no, not the whole list.
3. **One breath.** About 15 words or fewer, one sentence, maybe two. No preamble ("Sure! Let me check…") and no "Here is…" without a fact.
4. **Speakable numbers.**
   - Whole degrees.
   - "degrees", not "°".
   - "15 times 4 is 60", not "15 * 4 = 60".
   - Round durations to the useful unit (HA `rounded_minutes_left`).
5. **Relative time words.** "today", "tomorrow afternoon", "in 20 minutes", "on Friday", never ISO dates [src HA/OVOS dialogs].
6. **Template on what is present.** Leave out a missing field and never say "None". Fall back to a smaller fact, not to no fact [src HA conditionals].
7. **Disambiguate only when needed.** Name the timer or room only when there is more than one (HA `HassTimerStatus`).
8. **Errors are specific and short.** "I couldn't find a pasta timer." [src HA `_common.yaml`]
9. **The screen carries the detail.** Show the hourly/weekly card or the full list, and speak the headline. The spoken reply must stand alone on a speaker with no screen [search Google; fetched Alexa].
10. **Follow-ups.** [judgement] Keep the last domain and slots so "and tomorrow?" or "what about the low?" work. Don't end every answer with "anything else?". Alexa's guidance is minimal steps.

## 5. Gap map against Zoe

### 5.1 Zoe's voice cascade (the order matters) [src]

`routers/voice_tts.py::voice_command`:
1. STT homophone fix (`_normalize_voice_command_text`, "whether" → "weather").
2. `semantic_router.route`; the two-stage decision exists only when `ZOE_ROUTER_HEAD=active`.
3. `fast_tiers.resolve(phase="conversation")`.
4. **Skybridge** `resolve_skybridge_request` (about line 4089). Matching is in `classify_skybridge_intent`, `skybridge_service.py:1329`. It is mostly *substring* matching on the padded, lower-cased utterance.
5. Voice quick-intent writes (`detect_and_extract_intent`).
6. **Public-intent short-circuit** (`voice_tts.py:4631-4700`): `intent_router.detect_intent` patterns are mostly anchored `^…$`. A public intent (time, date, weather, timer, calculate, smart_home, music…) is executed directly.
7. `fast_tiers.resolve(phase="domain")`: Tier-0 reads, the router domain, then `expert_dispatch`.
8. The brain.

**Live flags.** The code defaults are `ZOE_ROUTER_HEAD=off` and `ZOE_EXPERT_MODE=shadow`, but `scripts/maintenance/zoe_ground_truth.sh` on 2026-10-14 read **both as `active`** in the running process.

**Consequence.** Skybridge's substring matcher outranks everything after it for any utterance it claims, and an intent_router public hit runs before the expert planner. That is why the expert planner's weather advice slot (`expert_dispatch.py:276-279`, `advice="rain"|"warmth"`) is not reached for "do I need an umbrella". `detect_intent` matches it first as a plain `weather` intent with no `advice` slot (`intent_router.py:1184`) and answers with the current conditions [src + measured].

### 5.2 Measured: HA's labelled test set through Skybridge, then intent_router [measured]

**Method.**
- A scratch script (not committed) ran `classify_skybridge_intent(text)` on each of the 1,192 English test sentences in `tests/en/**`. When Skybridge claimed nothing, it ran `intent_router.detect_intent(text, log_miss=False)`.
- It used the zoe-data venv under the test-lane lock, on this branch's code (identical to `b02a04a7b`).
- The labels are HA's. **"miss" means neither deterministic matcher claimed the sentence**, so it falls to Tier-0, the router head, the experts or the brain. A miss is slower and less predictable but not necessarily wrong.
- **Wrong claims are the dangerous column**, because a claimed turn never reaches the smarter tiers.
- HA's fixture names ("pizza timer", "play corner lights", "office thermostat") are public test strings. They also make good adversarial entity names.

| HA intent (n) | Correct claim | Wrong claim (what Zoe did) | Miss |
|---|---|---|---|
| HassGetWeather (5) | 5 | — | 0 |
| HassGetCurrentTime (4) | 4 | — | 0 |
| HassGetCurrentDate (4) | 3 | — | 1 ("what is today's date") |
| HassTimerStatus (27) | 27 | — | 0 |
| HassCancelTimer / CancelAll (13 / 4) | 13 / 4 | — | 0 |
| HassStartTimer (99) | 83 | **14 → smart_home/turn_off now** (delayed commands) | 2 ("5 and a half minutes") |
| HassIncreaseTimer (78) | 0 | **74 → timer/create** (+2 → list_add) | 2 |
| HassDecreaseTimer (100) | 0 | **96 → timer/create** (+2 → list_remove) | 2 |
| HassPauseTimer / Unpause (13 / 13) | 0 | **6 / 6 → timer/create** | 7 / 7 |
| HassShoppingListAddItem / ListAddItem (2 / 2) | 2 / 2 | — | 0 |
| HassListRemoveItem (2) | 0 | **2 → lists/show** | 0 |
| HassListCompleteItem + ShoppingListComplete (10) | 3 | **7 → lists/show** | 0 |
| HassClimateGetTemperature (19) | 0 | **12 → weather/current** (outdoor) | 7 |
| HassClimateSetTemperature (11) | 0 | **6 → weather/current** | 5 |
| HassLightSet (99) | 5 | **37 → weather/current** (colour temperature) | 57 |
| HassTurnOn (139) | 47 + 6 scene + 6 IR | **16 → music/play** ("play corner lights") | 64 |
| HassTurnOff (128) | 59 + 8 IR | **12 → music/play**; 1 → status | 48 |
| HassGetState (128) | 63 (status, mostly whole-home) | 5 → weather; 1 → people/show ("how many people are home") | 58 (doors, windows, locks, sensors) |
| HassMediaSearchAndPlay (16) | 16 | — | 0 |
| HassMediaNext / Previous (26 / 29) | 16 / 23 | — | 10 / 6 |
| HassMediaPause / Unpause (14 / 14) | 7 / 5 | — | 7 / 9 ("resume the TV", "unpause my show") |
| HassSetVolume / Relative (18 / 80) | 11 / 56 | — | 7 / 24 ("set TV volume to 90 percent") |
| HassNevermind (2) | 0 | — | 2 |
| HassRespond (5) | 0 | **1 → lists/show** ("are you always listening?") | 4 |
| HassFanSetSpeed (18) | 0 | 10 → smart_home/set_brightness | 8 |
| Vacuum / mower / cover position / climate fan mode (51) | 0 | 1 → list_add | 50 |

**Invented spot checks** (same harness) [measured]:

| Phrasing | Result |
|---|---|
| "how hot will it get today", "what's today's high", "is it cold outside" | miss |
| "will it rain tomorrow" | Skybridge weather/forecast, spoken as `Here is the forecast for {city}.` |
| "do I need an umbrella" | IR `weather` with no advice slot |
| "remind me to bring the washing in if it rains", "help me wind down" | **weather/current** (substrings `" rain"` / `" wind "`) |
| "what time is it in Tokyo" | **local** time |
| "set a timer for half an hour" | **3600 s** (`_parse_timer_duration` reads "an hour") |
| "is milk on the shopping list" | whole-list read |
| "set an alarm for 6:30", "what's twelve times four", "how many cups in a litre", "never mind", "is today Wednesday", "when's my next meeting" | miss |

### 5.3 Gap table (prioritised impact × effort)

**How the scores were set:**
- **Impact** = demand rank (section 3) × severity. Severity runs from false write, through wrong fact, to miss. A miss usually still gets answered, just slower.
- **Effort**: S means a regex or template fix plus tests; M means a new action or handler; L means a new capability.

| # | Category | Zoe fast path today | Miss / misfire families (examples) | Spoken reply leads with the fact? | Impact | Effort | Priority |
|---|---|---|---|---|---|---|---|
| 1 | Timer adjust/pause | Skybridge timer: create/status/cancel only (`skybridge_service.py:299`, store 85) | "add 5 minutes to the timer", "take a minute off the egg timer", "pause the timer" create a **new timer**; "half an hour" sets 1 h | Yes for create/status/cancel | **High** (false write, timers are top-5) | M (add increase/decrease/pause/resume actions + guard: duration *to/from/off* a timer is never create) | **P0** |
| 2 | Delayed device commands | Skybridge smart_home | "turn off the lights in 10 minutes" executes **immediately** | n/a | **High** (false write, wrong time) | S to refuse/clarify; M to schedule (HA: "Command will be executed in …") | **P0** |
| 3 | Weather forecast facts (high/low, later today, tomorrow, rain chance, umbrella) | Skybridge weather: current or forecast (`skybridge_service.py:1439-1441`, reply 2807-2828); IR weather (1177-1199); advice only in expert planner | "how hot will it get", "today's high", "will it rain this afternoon", "do I need a jacket" | **No**: forecast = `Here is the forecast for {city}.`; umbrella = current temperature | **High** (weather is #2 by demand) | M (reply from `daily[0]` high/low and precipitation probability; yes/no for condition questions; reuse the advice templates at `intent_router.py:4953-4975`) | **P0** (in flight on `fix/weather-today-high` for the high; the rest is follow-up) |
| 4 | Greedy `" temperature"` / `" rain"` / `" wind "` / `" list"` substrings | Skybridge fallbacks (1439, 1445) | indoor temperature → outdoor weather; lamp colour temperature → weather; "set temperature to 21" → weather; "remind me … if it rains" → weather; "wind down" → weather; "listening" → list read | n/a | **High** (wrong fact; steals reminders and device writes) | S (word-boundary + exclusion of colour/thermostat/set/remind contexts; route indoor temperature to `get_climate()`) | **P0** |
| 5 | Calendar read | Skybridge calendar show (`_resolve_calendar`, 2744) | "when's my next meeting", "anything on Friday afternoon?", "do I have anything tomorrow morning" | **No**: `You have 2 events tomorrow.` names nothing unless `ZOE_VOICE_SESSION` + identified voice (`voice_tts.py:4160-4173`) | High (calendar is common and the reply is empty) | M (name first 1-2 events + time when identity allows; "next event" read) | P1 |
| 6 | Smart-home phrasing breadth + state | Skybridge `_classify_smart_home` (2029); IR `_SMART_HOME_RE` (lights only) | "activate the kitchen lights", "lights on in the kitchen", "deactivate the fan", "open the blinds", "lock the front door", "are all the doors locked", entity names containing "play" → music | Yes (`Turned … on.`); state reply is a whole-home summary, not the asked device | Med-High (#6 by demand; misfires to music) | M (verb set from HA `rules/en/verbs.yaml`-style list; device nouns; per-device state answer) | P1 |
| 7 | Date and time variants | IR `_DATE_QUERY_RE` (anchored); Skybridge clock | "what is today's date", "is today Wednesday", "what's the date tomorrow", "what time is it in Tokyo" (answers local) | Yes | Med (#4/#10 by demand; one false fact) | S | P1 |
| 8 | Volume / media resume | IR `_VOLUME_SET_RE`, music_control; Skybridge music | "set the TV volume to 30 percent", "change the volume to 40", "resume my show", "unpause the TV" | Yes (`Playing …`, `Paused.`) | Med (music is #1, these are its edges) | S-M | P2 |
| 9 | Reminders and alarms | IR `reminder_create` (anchored `^remind me …`); **no alarm intent anywhere** | "can you remind me to…" (lead-in not stripped, HA skip-words would fix), "remind me tomorrow to…", "wake me at 6:30", "set an alarm for 7" | Reminder: `Reminder set: {title} for {ISO date}…`: date may be ISO, list reply is bulleted text | Med-High (alarms/reminders #7) | S for skip-words; L for alarms (needs a ringing surface) | P1 (skip-words) / P2 (alarms) |
| 10 | Small utilities | IR `_execute_calculate` (digits only); nothing for conversion, nevermind, definitions, jokes | "what's twelve times four", "7 x 8", "how many cups in a litre", "never mind" (should stop silently), "tell me a joke" | Calculator: `15 * 4 = 60` (symbols, not speech) | Low-Med each, cheap | S (number words + spoken "times … is"; a pint/cup/litre/°F table; Nevermind = silent close) | P2 |
| 11 | Lists: membership and complete/remove word orders | Skybridge lists (766-899, `_resolve_lists` 2837) | "is milk on the list" (whole list read), "complete apples on my shopping list" / "remove X on my list" → list **read** | Add/remove: yes; read count includes completed items | Med (a write answered as a read) | S | P1 |
| 12 | News, sports, general knowledge, song ID | brain + `web_search` | n/a (no deterministic path, by design) | brain-dependent | Med | L | keep on brain; style rules apply |

**Top 10 in priority order:**
1. Timer adjust/pause misfires that create a timer
2. Delayed device commands run immediately
3. Weather forecast facts not spoken (high/low/rain/umbrella; partly in flight)
4. Greedy substrings steal turns (temperature/rain/wind/list)
5. Calendar voice read is a count only
6. List complete/remove word orders read the list; membership is not yes/no
7. Smart-home verb/noun breadth and per-device state answers
8. Date variants and time-in-another-city answered as local
9. "can you / could you" lead-ins defeat anchored patterns, which means no skip-word layer (affects reminders and others); no alarms
10. Calculator speech, number words, unit conversion, "never mind"

## 6. Recommendation: adopt HA's corpus as a phrasing regression corpus

**Why adopt rather than write our own.** HA's corpus is maintained by a large contributor base, already labelled, already split into templates and fixed test sentences, has an upstream expansion tool, and is licensed for reuse. This follows the standing adopt-don't-rebuild rule.

**Licence finding (verified).**
- `OHF-Voice/intents` is **CC-BY-4.0**: `LICENSE.md` in the source tree begins "Attribution 4.0 International", and the GitHub licence API returns `CC-BY-4.0`.
- hassil is **Apache-2.0** (GitHub licence API).
- `OpenVoiceOS/ovos-skill-weather` is **Apache-2.0** (`LICENSE` file + API).
- CC-BY-4.0 permits copying and adaptation, including inside an MIT-licensed public repo. The obligations are:
  - credit the creator;
  - link the licence;
  - **state that changes were made**;
  - add no further restrictions.
- Apache-2.0 needs the licence text and NOTICE kept with any copied files.
- This is a reading of the licence texts, not legal advice.

**Design sketch** (follow-up PR, not built here):
1. **Generator** `scripts/maintenance/ha_intents_corpus.py`:
   - Run offline by an operator, never in CI.
   - Points at a pinned commit of `OHF-Voice/intents` (via `opensrc`).
   - Takes `tests/en/**` sentences verbatim.
   - Calls HA's own `python -m script.intentfest sample --language en --intents <the intents Zoe covers> -n <cap>` in a throwaway venv with `hassil==3.12.1`.
   - Writes `services/zoe-data/tests/fixtures/ha_intents/en.jsonl` (`{"ha_intent","text","source":"test|sample","commit"}`), capped at a few thousand lines and de-duplicated.
   - Wildcard templates are skipped by hassil's default.
2. **Mapping file** `tests/fixtures/ha_intents/mapping.yaml`. For each HA intent, list:
   - the **acceptable** Zoe claims (Skybridge `domain/action` or intent_router name);
   - the **forbidden** claims. For example, HassIncreaseTimer must never be `timer/create`, and no HassLightSet or HassClimate* sentence may be `weather/*`.
   - Intents Zoe does not support (vacuum, mower, broadcast) become **negative controls**: any write claim is a failure; a miss is fine.
3. **Weather depth from OVOS.** Expand `locale/en-US/intents/{weather,temperature,weather_condition,next_rain}.intent` into the same JSONL with sub-labels (`current`, `high_low`, `rain_chance`, `condition_yes_no`). Map those to the actions the weather fix introduces.
4. **Metric**, per HA intent and overall. Tracked:
   - **claim-correct rate** (recall of the fast path);
   - **wrong-claim rate** (precision);
   - **wrong-write count**, the gate.
   - Commit a baseline JSON.
5. **A `ci_safe` unit test.** Pure regex, no models; roughly 1-2 s for a few thousand strings on this box, an estimate to measure when built. It **ratchets**:
   - the wrong-write count may only go down (start by pinning today's numbers, then fix #1/#2/#4);
   - the other rates may not regress by more than a small tolerance.
   - The test joins CI through the co-located marker, per `tests/AGENTS.md`.
6. **Router-head use.** The same JSONL can extend the two-stage router's training and eval sets (`labs/setfit-router`, `labs/functiongemma-finetune`). Keep a held-out split so the regression test never scores sentences the head trained on.
7. **Attribution.** Put `tests/fixtures/ha_intents/ATTRIBUTION.md` next to the data. It should name the source repo and commit, "© Home Assistant / Open Home Foundation contributors, CC-BY-4.0 (link)", "modified: sampled, filtered, relabelled for Zoe", plus the Apache-2.0 notice for the OVOS-derived lines.

**What the corpus will not do.**
- It will not test reply style. Add a separate small assertion set for the section 4 rules: the first clause contains the number, no "°", no ISO dates, no "Here is" without a fact.
- It will not cover what HA does not template: calendar reads, reminders, alarms, maths, conversion. For those, write Zoe-specific invented phrasings, or mine OVOS skills where an Apache-2.0 skill exists (not checked in this record).

## 7. Sources

**Read today [src]:**
- `~/.opensrc/repos/github.com/home-assistant/intents/main`: `sentences/en/**`, `responses/en/*.yaml`, `rules/en/*.yaml`, `tests/en/**`, `docs/templates.md`, `docs/slot_combinations.md`, `script/intentfest/sample.py`, `LICENSE.md`, `requirements.txt`
- `~/.opensrc/repos/github.com/home-assistant/core/dev/homeassistant`: `components/weather/{intent.py,__init__.py,services.yaml}`, `components/calendar/llm.py`, `components/todo/llm.py`, `components/homeassistant/llm.py`, `helpers/llm.py`, `components/assist_pipeline/models.py`; the intent constants grep
- `~/.opensrc/repos/github.com/OHF-Voice/hassil/3.12.1/hassil/sample.py`
- `~/.opensrc/repos/github.com/OpenVoiceOS/ovos-skill-weather/dev/locale/en-US/**`, `LICENSE`
- Zoe (this branch = `b02a04a7b` code), all under `services/zoe-data/`:
  - `skybridge_service.py` (`classify_skybridge_intent` 1329, weather 1439-1441 / 2788-2828, calendar 2766, timer parse 154)
  - `intent_router.py` (`detect_intent` 788, weather 1177-1199, advice 4953-4975, calculate 5953)
  - `routers/voice_tts.py` (public short-circuit 4631-4700, voice-session calendar 4160-4173)
  - `expert_dispatch.py` (276-279)
  - `semantic_router.py` (232)
  - `guest_policy.py` (22)

**Web [fetched]:**
- Voicebot.ai, "Streaming music, questions, weather, timers and alarms remain smart speaker killer apps" (2020-05-03): https://voicebot.ai/2020/05/03/streaming-music-questions-weather-timers-and-alarms-remain-smart-speaker-killer-apps-third-party-voice-app-usage-not-growing/
- EFTM, "1 in 4 Aussies have smart speakers…" (Edison Research data, 2021-07-13): https://eftm.com/2021/07/1-in-4-aussies-have-smart-speakers-music-weather-and-telling-time-top-the-use-cases-213718
- Mozilla UX blog, "How people really, really use smart speakers" (2019-12-19), summarising Ammari et al., ACM TOCHI 26(3):17, 2019: https://blog.mozilla.org/ux/2019/12/how-people-really-really-use-smart-speakers/
- Amazon Alexa Haus design principles, "Be brief": https://developer.amazon.com/en-US/alexa/alexa-haus/design-principles/be-brief

**Web [search] (page not read):**
- NPR/Edison Smart Audio Report 2017 via ARF
- Nielsen Q2 2018 via TechCrunch
- Adobe survey figures
- Alexa Voice Design Guide one-breath test: https://developer.amazon.com/en-US/blogs/alexa/post/531ffdd7-acf3-43ca-9831-9c375b08afe0/things-every-alexa-skill-should-do-pass-the-one-breath-test
- Google conversation design, "Scale your design": https://developers.google.com/assistant/conversation-design/scale-your-design

**GitHub API (licence + heads, 2026-10-14):**
- `repos/home-assistant/intents` → `OHF-Voice/intents`, `CC-BY-4.0`
- `repos/OHF-Voice/hassil` → `Apache-2.0`
- `repos/OpenVoiceOS/ovos-skill-weather` → `Apache-2.0`
