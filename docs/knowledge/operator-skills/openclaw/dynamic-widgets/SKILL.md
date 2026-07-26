# Dashboard Widget Management

You can add, remove, and rearrange widgets on the user's dashboard.

## When to Manage Widgets

- User asks: "Add the weather widget to my dashboard"
- User asks: "Put the shopping list on my dashboard"
- User asks: "Remove the calendar widget"
- User asks: "What widgets are available?"
- User asks: "Reset my dashboard" / "Tidy up my dashboard"

## Available Widget IDs

These are the only valid widget IDs:

| Widget ID | What it shows |
|-----------|--------------|
| `weather` | Current weather and forecast |
| `events` | Upcoming calendar events |
| `calendar` | Full calendar view |
| `tasks` | Task list |
| `shopping` | Shopping list |
| `notes` | Recent notes |
| `reminders` | Active reminders |
| `people` | Family contacts |
| `journal` | Recent journal entries |
| `home` | Home Assistant smart home summary |
| `time` | Clock and date |
| `zoe-orb` | Zoe interaction orb |
| `week-planner` | Weekly overview |
| `personal` | Personal tasks list |
| `work` | Work tasks list |
| `bucket` | Bucket list items |

## Tools

### See what's available
```
mcporter-safe call zoe-data.dashboard_available_widgets
```

### Add one or more widgets
```
mcporter-safe call zoe-data.dashboard_add_widget widgets='["weather","shopping"]'
```

### Get current layout
```
mcporter-safe call zoe-data.dashboard_get_layout
```

### Save a full layout (positions and sizes)
```
mcporter-safe call zoe-data.dashboard_save_layout layout='[{"id":"weather","x":0,"y":0,"w":2,"h":1},{"id":"events","x":2,"y":0,"w":2,"h":2}]'
```

## Guidelines

- `dashboard_add_widget` accepts an array of widget IDs — do not invent custom IDs
- Only use IDs from the list above; unrecognised IDs are silently ignored
- After adding widgets, navigate the user to their dashboard so they can see the changes
- When removing widgets, use `dashboard_get_layout` to find current positions, then `dashboard_save_layout` with the widget removed from the array
- Widget position (`x`, `y`) uses a 12-column grid; width (`w`) and height (`h`) are in grid units
