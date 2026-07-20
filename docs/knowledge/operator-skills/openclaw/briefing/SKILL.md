# Daily Briefing Skill

<!-- metadata.when: user asks for morning briefing, daily update, what's happening today, or schedule overview -->


You provide comprehensive daily briefings by combining multiple data sources.

## When to Use

Activate when someone asks:
- "what's on today", "what's my day like", "daily briefing", "morning update"
- "what do I have on", "anything happening today"
- "give me a rundown"

## How to Build a Briefing

Call these tools in sequence, then combine results into a natural summary:

### 1. Weather
```
mcporter-safe call zoe-data.weather_current
```

### 2. Today's calendar
```
mcporter-safe call zoe-data.calendar_today
```

### 3. Today's reminders
```
mcporter-safe call zoe-data.reminder_list today_only=true
```

### 4. Pending tasks
```
mcporter-safe call zoe-data.list_get_items list_type=tasks
```

## Response Format

Structure the briefing naturally, like a personal assistant:

"Good morning! Here's your day:

It's 24°C and partly cloudy in Perth -- perfect for being outside.

You have 3 things on your calendar today:
- Dentist at 10am
- Team meeting at 2pm
- Pick up kids at 3:30pm

Reminders for today:
- Take medication at 7pm
- Call the plumber

Your task list has 4 items remaining."

## Guidelines

- Adjust tone by time of day: morning = energetic, evening = relaxed
- Highlight conflicts or tight schedules: "You have back-to-back meetings from 1-4pm"
- Mention weather impacts on events: "Bring an umbrella -- it might rain during school pickup"
- If the day is empty, be positive: "Clear schedule today! What would you like to focus on?"
- Mention upcoming birthdays if within the next 3 days
