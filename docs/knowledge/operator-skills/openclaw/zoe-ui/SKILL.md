# Zoe UI Control Skill

You can control Zoe's web UI through conversation by embedding structured JSON blocks in your responses. The frontend renders these as rich interactive components.

## How to Use

Embed UI actions in your response using `:::zoe-ui` fenced blocks:

```
Here's what you asked for:

:::zoe-ui
{"component": "qr_code", "props": {"data": "https://example.com", "label": "Scan this code"}}
:::

Let me know if you need anything else!
```

You can include multiple blocks in one response. Text outside the blocks is rendered normally with markdown.

## Chat Components (Tier 1)

These render inline in the chat conversation.

### QR Code
Show a scannable QR code. Use for WhatsApp setup, sharing links, WiFi passwords.
```json
{"component": "qr_code", "props": {"data": "https://wa.me/...", "label": "Scan with WhatsApp", "size": 200}}
```

### Info Card
Display structured information with optional action buttons.
```json
{"component": "card", "props": {"icon": "🌤️", "title": "Weather", "description": "25°C, Sunny in Perth", "actions": [{"label": "Details", "type": "weather_detail"}]}}
```

### Confirmation
Ask user to confirm an action before proceeding.
```json
{"component": "confirmation", "props": {"description": "Remove the weather widget from your dashboard?", "yes_label": "Remove", "no_label": "Keep it", "yes_action": "confirm remove weather widget"}}
```

### Progress Steps
Show a multi-step setup or process flow.
```json
{"component": "progress", "props": {"steps": ["Connect Account", "Choose Settings", "Done"], "current": 1}}
```

### Status Banner
Show success, error, warning, or info messages.
```json
{"component": "status", "props": {"level": "success", "message": "WhatsApp connected successfully!"}}
```

### Form
Collect structured input from the user.
```json
{"component": "form", "props": {"title": "WiFi Setup", "action": "setup wifi", "fields": [{"name": "ssid", "label": "Network Name", "required": true}, {"name": "password", "label": "Password", "type": "password"}], "submit_label": "Connect"}}
```

### Link Preview
Rich preview of an external link.
```json
{"component": "link_preview", "props": {"url": "https://example.com", "title": "Example Site", "description": "A useful website"}}
```

### Image
Display an image with optional caption.
```json
{"component": "image", "props": {"url": "/photos/sunset.jpg", "caption": "Sunset from the backyard"}}
```

## Dashboard Commands (Tier 2)

These modify the user's dashboard layout.

### Add Widgets
Add widgets to the user's dashboard. Available widgets: weather, events, tasks, shopping, notes, reminders, calendar, people, journal, home, time, zoe-orb, week-planner, personal, work, bucket.
```json
{"command": "add_widgets", "params": {"widgets": ["weather", "tasks", "shopping"]}}
```

### Remove Widgets
Remove widgets (always confirm first with a confirmation component).
```json
{"command": "remove_widgets", "params": {"widgets": ["weather"]}}
```

### Navigate
Send the user to a specific page.
```json
{"command": "navigate", "params": {"page": "settings.html"}}
```

### Notify
Show a toast notification.
```json
{"command": "notify", "params": {"message": "Dashboard updated!", "level": "success"}}
```

## Live Action Pattern: Navigate After Creating

When you create something via MCP tools, navigate the user to the relevant page so they can see the result. This creates a "watch Zoe work" experience.

### Example: "Add a dentist appointment on Thursday"
1. Create the event using calendar_create_event
2. Respond with a brief confirmation AND a navigate command:

```
I've added your dentist appointment on Thursday.

:::zoe-ui
{"command": "navigate", "params": {"page": "calendar.html"}}
:::
```

### Example: "Create Rod as a friend"
1. Create the person using people_create
2. Navigate to the people page:

```
I've added Rod to your contacts.

:::zoe-ui
{"command": "navigate", "params": {"page": "people.html"}}
:::
```

### Pages to navigate to:
- Calendar events: `calendar.html`
- People/contacts: `people.html`
- Shopping/lists: `lists.html`
- Journal entries: `journal.html`
- Reminders: (stay on current page, just confirm)
- Settings: `settings.html`
- Dashboard: `dashboard.html`

### When NOT to navigate:
- If the user is already on the correct page (check page_context)
- If the user is in a multi-step conversation (don't interrupt)
- For simple confirmations that don't need visual verification
- For read-only queries (weather, search results)

## When to Use UI Components

**DO use components for:**
- Setup flows (WhatsApp, Telegram, WiFi) -> QR code + progress
- Confirmations before destructive actions -> confirmation
- Showing structured data -> card, table
- Dashboard modifications -> add_widgets, remove_widgets
- Status updates after completing actions -> status
- Collecting structured input -> form
- After creating data, navigate to show the result

**DON'T use components for:**
- Regular conversation and explanations -> use plain text/markdown
- Simple yes/no questions -> just ask in text
- Listing a few items -> use markdown lists

## Safety Rules

1. **Always confirm** before removing widgets or navigating away from chat
2. **Never navigate** mid-conversation -- finish the response first
3. **Ask before layout changes** -- "Would you like me to add weather and tasks to your dashboard?"
4. **One setup flow at a time** -- don't start a new setup before the current one finishes
5. **Graceful fallback** -- if a component fails to render, the text around it still makes sense

## MCP Tools for Dashboard

You can also use these MCP tools via `mcporter-safe call zoe-data.<tool>`:

- `dashboard_get_layout` -- read the user's current widget layout
- `dashboard_save_layout` -- save a complete layout (advanced)
- `dashboard_add_widget` -- add widget(s) by ID (simpler than save_layout)
- `dashboard_available_widgets` -- list all available widget IDs and names

Use these tools when you need to read the current state before making changes, or when a :::zoe-ui command wouldn't be appropriate.
