---
name: personal-facts
description: "Store and recall personal facts, preferences, and profile details for the user. Use when the user shares personal info ('my name is', 'I live in'), asks 'what's my…', or says 'do you remember'."
version: 1.0.0
author: zoe-team
api_only: true
priority: 5
tags:
  - memory
  - preferences
  - personal
triggers:
  - "my favorite"
  - "my favourite"
  - "my name is"
  - "i live in"
  - "i work as"
  - "i work at"
  - "my birthday is"
  - "i was born"
  - "my email is"
  - "my phone number is"
  - "what's my"
  - "what is my"
  - "do you remember my"
  - "remember that I"
  - "forget my"
  - "update my"
allowed_endpoints:
  - "GET /api/memories/facts"
  - "POST /api/memories/facts"
  - "GET /api/memories/facts/{key}"
  - "DELETE /api/memories/facts/{key}"
  - "GET /api/user-data/preferences"
  - "PUT /api/user-data/preferences"
---
# Personal Facts Skill

## Workflow

1. **Detect intent** — determine if the user is storing a new fact, recalling an existing one, updating, or deleting
2. **Extract key-value pair** — parse the fact into a structured format:
   - `category`: `identity`, `contact`, `preference`, `location`, `work`
   - `key`: the specific attribute (e.g., `name`, `birthday`, `favorite_color`)
   - `value`: the user's answer
3. **Check for duplicates** — `GET /api/memories/facts/{key}` to see if the fact already exists
4. **Store or retrieve** — use the appropriate API call (see below)
5. **Confirm** — acknowledge storage warmly, or present recalled facts naturally in context

## API Endpoints

### Store a fact
```
POST /api/memories/facts
{
  "category": "identity",
  "key": "name",
  "value": "Jason"
}
```

### Recall a specific fact
```
GET /api/memories/facts/name
```

### Recall all facts
```
GET /api/memories/facts
```

### Delete a fact
```
DELETE /api/memories/facts/birthday
```

### Update preferences
```
PUT /api/user-data/preferences
{
  "theme": "dark",
  "language": "en"
}
```

## Example

**User:** "My birthday is March 15th"

**Steps:**
- Detect intent: store
- Extract: category=`identity`, key=`birthday`, value=`March 15`
- Check: `GET /api/memories/facts/birthday` — not found
- Store: `POST /api/memories/facts` with extracted data
- Respond: "Got it — I'll remember your birthday is March 15th! 🎂"

**User:** "What's my birthday?"

**Steps:**
- Detect intent: recall
- Retrieve: `GET /api/memories/facts/birthday` → `March 15`
- Respond: "Your birthday is March 15th!"

## Error Handling

- **Fact not found on recall**: Respond "I don't have that on file yet — want to tell me?"
- **Duplicate on store**: Show existing value and ask if the user wants to update it
- **Ambiguous key**: Ask for clarification (e.g., "Do you mean your work email or personal email?")

## Response Style

Warm and personal. When storing: brief confirmation. When recalling: weave the fact naturally into the response.
