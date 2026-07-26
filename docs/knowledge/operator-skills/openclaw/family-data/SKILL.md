# Family Data Skill

You manage the family's structured data: calendar events, shopping lists, to-do lists, reminders, contacts, and notes.

## When to Use This Skill

Activate when someone asks about:
- Calendar: "What's on today?", "Schedule a dinner for Friday"
- Lists: "Add milk to the shopping list", "What's on our to-do list?"
- Reminders: "Remind me to call the dentist tomorrow"
- People: "When is Sarah's birthday?", "Add Mike's phone number"
- Notes: "Save this recipe", "What notes do I have about the trip?"

## How to Access Data

Use the `exec` tool to call `mcporter-safe` for all data operations:

```
mcporter-safe call zoe-data.<tool_name> key=value key2=value2
```

### Calendar

List events (optionally by date range):
```
mcporter-safe call zoe-data.calendar_list_events start_date=2026-03-01 end_date=2026-03-07
```

Get today's events:
```
mcporter-safe call zoe-data.calendar_today
```

Create an event:
```
mcporter-safe call zoe-data.calendar_create_event title="Family dinner" start_date="2026-03-05" start_time="18:00" location="Home" category="family"
```

Create an all-day event:
```
mcporter-safe call zoe-data.calendar_create_event title="School holidays" start_date="2026-04-06" all_day=true
```

### Lists

Get items from a list:
```
mcporter-safe call zoe-data.list_get_items list_type=shopping
```

Get items from a specific named list:
```
mcporter-safe call zoe-data.list_get_items list_type=shopping list_name="Bunnings"
```

Add an item to the default shopping list:
```
mcporter-safe call zoe-data.list_add_item list_type=shopping text="Milk" quantity="2L" category="dairy"
```

Add an item to a specific named list (creates it if needed):
```
mcporter-safe call zoe-data.list_add_item list_type=shopping list_name="Bunnings" text="Wood screws" quantity="1 box" category="hardware"
```

Remove/check off an item:
```
mcporter-safe call zoe-data.list_remove_item list_type=shopping item_text="Milk"
```

### People

Search for a person:
```
mcporter-safe call zoe-data.people_search query="Sarah"
```

Create a person with details:
```
mcporter-safe call zoe-data.people_create name="Rod" relationship="friend" birthday="1985-01-05" phone="0412345678" notes="Met at footy"
```

### Reminders

Create a reminder:
```
mcporter-safe call zoe-data.reminder_create title="Call the dentist" due_date="2026-03-05" due_time="14:00"
```

Create a timed reminder:
```
mcporter-safe call zoe-data.reminder_create title="Take pills" due_time="19:00" priority="high" category="health"
```

List all active reminders:
```
mcporter-safe call zoe-data.reminder_list
```

List only today's reminders:
```
mcporter-safe call zoe-data.reminder_list today_only=true
```

### Notes

Create a note:
```
mcporter-safe call zoe-data.note_create title="Lasagna Recipe" content="Ingredients: pasta sheets, mince, tomato sauce..." category="recipes"
```

Search notes:
```
mcporter-safe call zoe-data.note_search query="recipe"
```

### Updating and Deleting

Update a calendar event:
```
mcporter-safe call zoe-data.calendar_update_event event_id="abc123" title="Updated dinner" start_time="19:00"
```

Delete a calendar event:
```
mcporter-safe call zoe-data.calendar_delete_event event_id="abc123"
```

Update a person:
```
mcporter-safe call zoe-data.people_update person_id="abc123" birthday="1985-04-17" phone="0412345678"
```

Delete a person:
```
mcporter-safe call zoe-data.people_delete person_id="abc123"
```

Update a reminder:
```
mcporter-safe call zoe-data.reminder_update reminder_id="abc123" title="Call dentist instead" due_time="15:00"
```

Delete a reminder:
```
mcporter-safe call zoe-data.reminder_delete reminder_id="abc123"
```

Snooze a reminder:
```
mcporter-safe call zoe-data.reminder_snooze reminder_id="abc123" minutes=30
```

Update a note:
```
mcporter-safe call zoe-data.note_update note_id="abc123" content="Updated content here"
```

Delete a note:
```
mcporter-safe call zoe-data.note_delete note_id="abc123"
```

## Guidelines

- Parse dates naturally: "tomorrow" = next day, "next Friday" = calculate the date, "the 24th" = the 24th of the current or next month
- For shopping items, categorize them (dairy, produce, meat, bakery, pantry, frozen, drinks, household)
- List types: "shopping" for groceries, "personal" for personal tasks, "work" for work tasks, "tasks" for general todos, "bucket" for bucket list
- Use list_name for store-specific lists: "Bunnings", "Woolworths", "Costco"
- Confirm actions briefly: "Added milk to your shopping list" not "I have successfully added the item 'milk' to your shopping list"
- Multiple items: "add milk, eggs, and bread" means three separate list_add_item calls
- When someone mentions a person's birthday, first people_search to find them, then people_update with the birthday
- Quantities: parse natural quantities like "a dozen" -> "12", "2 litres" -> "2L"
- To update or delete, you need the record ID. Use a search/list tool first to find the ID, then call update/delete.
