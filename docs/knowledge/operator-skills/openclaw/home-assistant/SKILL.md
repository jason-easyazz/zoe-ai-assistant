# Home Assistant Skill

<!-- metadata.when: user wants to control lights, switches, thermostat, locks, sensors, or any smart home device -->


You control smart home devices through your browser. Home Assistant is at `http://localhost:8123`.

## Shorthand = full bootstrap

If the user says any of these (or close variants like “please set up…”), treat it as **the same request** as the long form below — **do not** wait for them to repeat the full instructions:

- “setup home assistant” / “set up home assistant”
- “configure home assistant” / “home assistant setup” / “install home assistant”
- “setup home automation” / “set up home automation” / “home automation setup”
- “setup hass” / “set up hass”

**Do immediately:** run the full **First-Time Setup (Onboarding)** section (browser onboarding, admin account, token `zoe-data`, `.env`, bridge restart, `trusted_proxies`, confirm control). The chat pipeline may also expand their message for you; either way, execute the full checklist.

**Never** respond with a generic “what do you want to automate” menu (lighting / security / routines lists). That is wrong for this request: they mean **install and wire up Home Assistant**, not brainstorm automations.

## First-Time Setup (Onboarding)

**Use the setup script from a terminal — never ask for or accept passwords in the chat window.**

Passwords typed in chat are captured in conversation memory. To set up HA securely:

### Step 1 — Ask the user to open a terminal

Tell the user:
> "I'll guide you through Home Assistant setup. For security, credentials must be entered in a terminal, not here. Please open a terminal on the Jetson (or SSH in) and run:"
> ```bash
> ha-setup --username <USERNAME>
> ```
> "The script will prompt for a password securely. What username would you like?"

Wait for the username. Then give them the exact command with that username. Do not ask for the password — the script prompts for it in the terminal.

### What ha-setup does

That single command will:
- Complete all onboarding steps via HA's API
- Create a long-lived `zoe-data` token and write it to `.env` (the password is never persisted)
- Set the location and timezone from the home profile
- Write `configuration.yaml` with trusted proxy settings
- Restart HA if needed, recreate the MCP bridge
- Verify everything: HA API + MCP entity count

### Step 2 — Confirm with the user

When they confirm the script ran, tell them:
- Login at `http://localhost:8123` with their chosen credentials
- Zoe now has access to control their smart home devices
- They can start adding devices via the HA UI or ask Zoe to help

### If the script fails

Run `ha-setup --help` to see all options.

Common issues:
- `exit code 2` — HA container not running: `docker ps | grep homeassistant`
- Long-lived token warning — re-run the script; the existing token in `.env` may still be valid
- MCP bridge still shows 0 entities after setup — wait 30s then run `curl http://localhost:8007/entities`

---

## When to Use This Skill

Activate for any request about:
- **Lights** — "Turn on the kitchen light", "Dim the bedroom to 50%", "What lights are on?"
- **Climate** — "Set the thermostat to 22", "Is the heating on?", "What's the temperature inside?"
- **Switches and plugs** — "Turn off the TV", "Is the fan on?"
- **Sensors** — "Is the front door open?", "What's the humidity?", "Any motion detected?"
- **Scenes** — "Set movie mode", "Goodnight", "Activate morning routine"
- **Automations** — "Trigger the morning routine", "Is the away mode automation enabled?"
- **Cameras** — "Show me the front door camera", "Who's at the door?"
- **Security** — "Is the alarm armed?", "Lock the front door"
- **Energy** — "How much power are we using?", "What's drawing the most energy?"

---

## How to Control Devices (Browser Method)

HA is accessible from the Jetson at `http://localhost:8123`. Your browser session persists via the `openclaw` Playwright profile — you stay logged in between requests.

### Step 1: Navigate and authenticate (if needed)

```
browser_navigate http://localhost:8123
browser_snapshot
```

If you see a login page, fill in credentials:
```
browser_fill [username field] <your-username>
browser_fill [password field] <your-password>
browser_click [Sign In button]
```

Note: Your HA session persists after first login via the `openclaw` browser profile — you will not need to log in again for subsequent requests.

### Step 2: Make service calls via browser_evaluate

Use `browser_evaluate` to call HA's REST API using the browser's authenticated session. This is faster than navigating the UI and does not require any token in headers — the browser session cookie handles authentication automatically.

**Turn a device on/off:**
```javascript
// browser_evaluate:
await fetch('/api/services/light/turn_on', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({entity_id: 'light.kitchen_ceiling'})
}).then(r => r.json())
```

**Set brightness:**
```javascript
await fetch('/api/services/light/turn_on', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({entity_id: 'light.bedroom', brightness_pct: 50})
}).then(r => r.json())
```

**Activate a scene:**
```javascript
await fetch('/api/services/scene/turn_on', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({entity_id: 'scene.movie_night'})
}).then(r => r.json())
```

**Read entity state:**
```javascript
await fetch('/api/states/light.kitchen_ceiling').then(r => r.json())
// Returns: {state: "on", attributes: {brightness: 255, ...}}
```

**List all entities in a domain:**
```javascript
await fetch('/api/states').then(r => r.json()).then(states =>
  states.filter(s => s.entity_id.startsWith('light.')).map(s => ({id: s.entity_id, state: s.state, name: s.attributes.friendly_name}))
)
```

**Trigger an automation:**
```javascript
await fetch('/api/services/automation/trigger', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({entity_id: 'automation.morning_routine'})
}).then(r => r.json())
```

### Step 3: Take a screenshot to confirm and show the user

After making a change, take a browser screenshot so you can describe what happened:
```
browser_navigate http://localhost:8123/lovelace/default_view
browser_snapshot
```

Include the screenshot in your reply so the user can see the result.

---

## Discovering Devices

When you don't know entity IDs, discover them:

```javascript
// Get all entities with their friendly names and states
await fetch('/api/states').then(r => r.json()).then(states =>
  states.map(s => ({
    id: s.entity_id,
    name: s.attributes.friendly_name || s.entity_id,
    state: s.state,
    domain: s.entity_id.split('.')[0]
  }))
)
```

Filter by room/name if you're looking for something specific. **Memorise entity IDs** when you discover them — save them in memory so you don't have to rediscover them. "The kitchen light = `light.kitchen_ceiling`."

---

## Showing HA on the Touch Panel

After a smart home action, you can show the relevant HA dashboard on the touch panel:

```
mcporter-safe call zoe-data.panel_navigate url=http://localhost:8123/lovelace/default_view panel_id=zoe-touch-pi
```

This loads the live HA dashboard in an iframe on the panel. The user can tap devices directly to toggle them. After 60 seconds of inactivity, use `panel_clear` to return to the ambient display:

```
mcporter-safe call zoe-data.panel_clear panel_id=zoe-touch-pi
```

For a smart home control overlay (without leaving the panel's ambient view):
```
mcporter-safe call zoe-data.panel_show_smart_home title="Living Room" panel_id=zoe-touch-pi
```

For a live browser screenshot of what Zoe is doing in HA:
```
mcporter-safe call zoe-data.panel_browser_screenshot navigate_to=http://localhost:8123/lovelace/default_view caption="HA dashboard" panel_id=zoe-touch-pi
```

---

## Safety Rules

- **Always confirm before**: unlocking doors, disabling security systems, opening the garage, arming/disarming alarms.
- **Never** expose camera feeds or security codes in chat — send them to the panel only.
- **Gradual dimming at night**: don't set lights to full brightness after 9pm. Max 30% unless the user explicitly asks for more.
- **Ambiguous device names**: if you're not sure which device the user means, ask before acting. "Did you mean the kitchen ceiling light or the kitchen bench light?"
- **Confirm destructive scenes**: "Goodnight mode will turn off all lights and lock the doors — shall I proceed?"

---

## Device Learning

Build a mental map of the home over time:
- Memorise entity IDs when you discover them: "kitchen ceiling light = `light.kitchen_ceiling`"
- Note room layouts: "The lounge has: `light.lounge_ceiling`, `light.lounge_floor_lamp`, `media_player.lounge_tv`"
- Remember preferences: "Jason likes the bedroom at 20% brightness at night"
- Track automations by name

Use memory (memU) to store all of this — it persists across conversations.
