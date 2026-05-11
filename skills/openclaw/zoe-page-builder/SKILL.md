# zoe-page-builder

Build new HTML pages and views for Zoe's UI at `services/zoe-ui/dist/`.

## Trigger conditions

This skill activates when the system message begins with `[ZOE_SELF_BUILD: page]`, or when the user asks to create a new page, dashboard, or view in the Zoe UI.

## Prerequisites

- Caller must have admin role. Check via `zoe_self_capabilities` tool. If not admin, reply: "Page building requires admin access."
- Do NOT modify any existing page without explicit user instruction.
- NEVER touch `dist/css/`, `dist/js/`, or any CSS/JS asset files.

## Step-by-step workflow

### 1. Check admin role
```
capabilities = zoe_self_capabilities()
if capabilities.role != "admin": STOP — reply with access denied.
```

### 2. Understand the request
Ask clarifying questions if ambiguous:
- What should the page display or do?
- Should it appear in the nav bar? If so, what label and icon?
- Does it need real-time data? Which `/api/` endpoint?

### 3. Reference the existing style
Read `services/zoe-ui/dist/chat.html` for the design conventions:
- Dark background (`#0f0f14`), accent colour (`var(--zoe-accent)`), card surfaces (`var(--zoe-surface)`)
- Sans-serif font stack already loaded in `<head>` of chat.html — match it
- Reuse existing CSS custom properties; do not add new stylesheet links

### 4. Spec + confirm
Draft a brief spec (≤5 bullet points) and present it to the user. Wait for confirmation before writing any file.

### 5. Generate the page

All CSS and JS must be **inline** in a single `.html` file placed in `services/zoe-ui/dist/`.

Rules:
- One `<style>` block in `<head>`, one `<script>` block before `</body>`
- No external CDN links; no separate `.css` or `.js` files
- Use `fetch('/api/...')` for data — existing zoe-data endpoints only
- Match the visual language of `chat.html` (colours, spacing, card style)
- Page must be fully responsive (mobile-first)
- Include a back-link or nav reference so the user can return to the main UI

### 6. Add nav link (if requested)

If the page needs a nav entry, edit the nav section of `services/zoe-ui/dist/index.html` (or whichever shell page contains the nav). Add a single `<a>` element with the agreed label and a suitable SVG icon. Do not restructure the nav — append only.

### 7. Output contract

Your final chat reply must be ≤3 sentences summarising what the page does.
NEVER include raw HTML/CSS/JS in the chat reply.

If a preview mechanism is available, emit:
```
:::zoe-ui
{"action":"navigate","url":"<page_url>","target":"iframe"}
:::
```

### 8. After user approval

If user confirms, the file is already in `dist/` and live via nginx — no further promotion step is needed.
If user requests changes, iterate on the spec and regenerate the file in place.

## Error handling

- If a required API endpoint doesn't exist, describe what's needed and stop.
- Never write files outside `services/zoe-ui/dist/` without explicit user approval.
- Do not overwrite existing pages without reading them first and confirming with the user.
