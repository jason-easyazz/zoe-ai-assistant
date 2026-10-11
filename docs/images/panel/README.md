# Touch panel screenshots

Screenshots of the Zoe touch panel UI (`services/zoe-ui/dist/touch/home.html`) at the
panel's native 1280x720, for the top-level README.

Everything on screen is invented: the household is Alex, Sam and Juniper the dog, the
music is made up with generated cover art, and no real account, name, place, address or
photo appears. Nothing here was captured from a running Zoe install.

| File | Shows |
|------|-------|
| `home.png` | The ambient home screen: clock, weather, "Your day" from the calendar, a now-playing chip and a kitchen timer in the dock, and the Zoe orb. |
| `voice.png` | A spoken request just heard ("Add dog food to the shopping list"): the dock switches to what Zoe heard while she works on it. |
| `lists.png` | Lists: a shopping list (with ticked-off items) and a second "Weekend jobs" list. |
| `calendar.png` | The today-anchored week view of the family calendar, with today highlighted. |
| `music.png` | The music card: cover-art queue, scrub bar and transport. The "scan to queue" QR is hidden because it encodes the page origin. |

A real memory-view capture from a demo account is to follow.

## Regenerating

```
python3 scripts/maintenance/panel_screenshots.py
```

The script serves `services/zoe-ui/dist` from a throwaway local static server and drives one
headless Chromium. Every `/api/**` request is answered by an invented fixture inside the
script, anything without a fixture gets a 404, and every non-local request is aborted, so
it never talks to a Zoe backend and has nothing to clean up. Pass an output directory as
the first argument, and a comma-separated list of shots (`day`, `timers`, `reminders`,
`weather`, `rooms`, `contacts` are also available) as the second.
