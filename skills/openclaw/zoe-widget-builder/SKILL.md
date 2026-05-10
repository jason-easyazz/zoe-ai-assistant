# zoe-widget-builder

Build new dashboard widgets for Zoe's touch panel and desktop UI.

## Trigger conditions
This skill activates when the system message begins with `[ZOE_SELF_BUILD: widget]`.

## Prerequisites
- Caller must have admin role. Check via `zoe_self_capabilities` tool (role field). If not admin, reply: "Widget building requires admin access."
- Do NOT build if the widget already exists (`zoe_self_capabilities` returns it in existing_widgets).

## Step-by-step workflow

### 1. Check admin role
```
capabilities = zoe_self_capabilities()
if capabilities.role != "admin": STOP — reply with access denied message.
```

### 2. Understand the request
Ask clarifying questions if the request is ambiguous:
- What data should the widget display?
- How large should it be (small/medium/full)?
- Should it update automatically? If so, how often?

### 3. Spec + confirm
Draft a brief spec (≤4 bullet points) and present it to the user. Wait for confirmation before building.

### 4. Generate the widget
Write a self-contained HTML/JS widget that:
- Uses `fetch('/api/...')` for data (existing zoe-data endpoints only)
- Has a CSS class `zoe-widget` on the root element
- Accepts `data-refresh-seconds` attribute for auto-refresh
- Uses CSS custom properties `--zoe-bg`, `--zoe-accent`, `--zoe-text` for theming
- Is fully responsive
- Has no external dependencies (no CDN links)

### 5. Stage the widget
```bash
python3 ~/assistant/scripts/preview/stage_widget.py \
  --name "<widget-name>" \
  --description "<1-line description>" \
  --code "<escaped HTML+JS string>"
```

The script prints `preview_url` and `task_id`. Capture both.

### 6. Output contract (MANDATORY — do not deviate)
Your final reply must be ≤2 short sentences describing what the widget does.
NEVER include code fences (```), JS, HTML, or CSS in the chat reply.

Emit exactly these two :::zoe-ui blocks at the end of your reply:
```
:::zoe-ui
{"action":"navigate","url":"<preview_url>","target":"iframe"}
:::
:::zoe-ui
{"action":"orb_prompt","prompt":"Here's your widget — does it look right?","auto_mic":true,"task_id":"<task_id>"}
:::
```

### 7. After user approval
If user confirms: call `zoe_promote_preview(task_id)` to promote to production.
If user requests changes: iterate on the spec and regenerate.

## Error handling
- If `stage_widget.py` fails: report the error clearly. Do not fake a preview URL.
- If the widget needs an API endpoint that doesn't exist: explain what's needed and stop.
- Never write files outside `services/zoe-ui/dist/_preview/` without explicit `zoe_promote_preview` approval.
