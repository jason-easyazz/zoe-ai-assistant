# Browser Skill

You have a full Playwright Chromium browser available. Use it when you need to see web pages, interact with them, or show the user what you're looking at.

## When to Use Browser (vs web_search / web_fetch)

| Situation | Use |
|---|---|
| Fast factual lookup, news, quick answers | `web_search` |
| Reading a specific URL (mostly static content) | `web_fetch` |
| Page requires JavaScript to load (SPAs, dashboards) | `browser` |
| Site has login / authentication | `browser` |
| You need a screenshot to show the user | `browser` |
| Interactive flow (fill form, click button, multi-step) | `browser` |
| Home Assistant control | `browser` (see home-assistant skill) |
| User says "show me what you're looking at" | `browser` + screenshot |

## Basic Workflow

Always snapshot before interacting — refs from the snapshot are what you use to click/fill:

```
1. browser_navigate <url>
2. browser_snapshot           ← see page structure + get refs
3. browser_click [ref]        ← use ref from snapshot, not guesses
4. browser_snapshot           ← check what changed
5. browser_fill [ref] "value" ← for text inputs
6. browser_snapshot           ← verify
```

**Never guess at refs.** Always take a fresh snapshot after any navigation or click that changes the page.

## Taking Screenshots

To include a visual in your reply or send to the touch panel:

```
browser_take_screenshot
```

Embed the screenshot in your response so the user can see what you're looking at. To push the current browser view to the touch panel, use `panel_browser_screenshot` (see "Showing Browser Activity" section below).

## Persistent Sessions

Your browser runs with the `openclaw` Playwright profile. **Sessions persist between requests** — cookies, local storage, and login state are saved. This means:
- Log into a site once → stay logged in
- Home Assistant login → permanent until HA session expires
- Any site you authenticate → stays authenticated

## Home Assistant (Special Case)

HA is at `http://localhost:8123`. See the `home-assistant` skill for the full pattern. Key shortcut: use `browser_evaluate` with `fetch()` to call HA's REST API directly using the browser's session cookie — faster than clicking through the UI.

```javascript
// Example: turn off all lights
await fetch('/api/services/light/turn_off', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({entity_id: 'all'})
}).then(r => r.json())
```

## Web Research

For research tasks where you need to see the actual page (not just extracted text):

1. Navigate to the URL
2. Take a screenshot — embed it in your reply as visual evidence
3. Read the page content via snapshot
4. If the page has more content below the fold, scroll and snapshot again

For JS-heavy sites (React apps, dashboards, live data): always use browser, not `web_fetch` — `web_fetch` gets the pre-JS HTML which may be mostly empty.

## Forms and Login

```
browser_navigate https://example.com/login
browser_snapshot                              ← see form fields
browser_fill [username ref] "jason"
browser_fill [password ref] "password"
browser_click [submit button ref]
browser_snapshot                              ← confirm logged in
```

If you can't find a ref, use `browser_take_screenshot` to see the visual layout and try `browser_mouse_click_xy` with coordinates as a fallback.

## Showing Browser Activity on the Touch Panel

When the user asks you to "show me" something on the panel:

1. Start browsing (navigate, interact)
2. After each significant step, call `panel_browser_screenshot` via mcporter-safe — this captures a screenshot of the current browser page and displays it full-screen on the panel:
   ```
   mcporter-safe call zoe-data.panel_browser_screenshot caption="Looking at results" panel_id=zoe-touch-pi
   ```
3. Optionally navigate first then screenshot in one call:
   ```
   mcporter-safe call zoe-data.panel_browser_screenshot navigate_to=https://example.com caption="Here's the page" panel_id=zoe-touch-pi
   ```
4. When done, call `panel_clear` to return to ambient:
   ```
   mcporter-safe call zoe-data.panel_clear panel_id=zoe-touch-pi
   ```

Note: The touch panel is a thin kiosk client. All browser automation runs headless on the Jetson. Screenshots are the visibility bridge — use `panel_browser_screenshot` to let the user see what Zoe is doing.

## Error Handling

- **Page not loading**: check the URL, try without trailing slash, check if server is up
- **Element not found**: take a fresh snapshot, the DOM may have changed
- **Login redirect**: the page needs authentication — log in first (see persistent sessions above)
- **Blank page**: JS app still loading — use `browser_wait 2000` then snapshot again
- **If 4 attempts fail**: stop, report what you observed, ask the user for guidance
