# 🔗 Zoe Feature Integration Guide

## How Journal, Calendar, Lists, Chat & Memory Work Together

---

## 🎯 Overview

Zoe has **multiple interconnected systems** that all work together to provide a comprehensive AI companion experience:

| Feature | Purpose | Database | Memory Integration |
|---------|---------|----------|-------------------|
| **Memories** | Store people, projects, notes | `zoe.db` (memories table) | ✅ Core system |
| **Journal** | Personal diary, mood tracking | `zoe.db` (journal_entries) | ✅ Auto-creates memories |
| **Calendar** | Events, appointments, scheduling | `zoe.db` (calendar_events) | ✅ Remembers attendees |
| **Lists** | Todo lists, shopping, tasks | `zoe.db` (lists, list_items) | ✅ Links to projects |
| **Chat** | AI conversations with context | N/A (stateless) | ✅ Uses all memories |
| **Tasks** | Project management | `zoe.db` (tasks) | ✅ Auto-links to projects |

---

## 🔄 How They Integrate

### 1. **Chat ↔ Memory Integration**

The chat system automatically pulls from ALL memory sources:

```python
# routers/chat.py
async def chat(msg: ChatMessage, user_id: str):
    context = {
        "user_id": user_id,  # User isolation
        "mode": "user"
    }
    
    # AI client retrieves relevant memories
    response = await get_ai_response(msg.message, context)
    # Response includes context from:
    # - People memories
    # - Project memories
    # - Journal entries
    # - Calendar events
    # - Recent tasks
```

**Example:**
```
User: "What am I doing tomorrow?"

Zoe: "Let me check your calendar and tasks...
      
      Tomorrow you have:
      📅 10am - Team standup (from calendar)
      📝 Finish authentication module (from tasks)
      📖 Remember to journal about the project (from journal reminder)
      👤 Meeting with Sarah about Arduino project (from people + calendar)"
```

---

### 2. **Journal → Memory Auto-Creation**

Journal entries automatically create memory records:

```python
# routers/journal.py
@router.post("/")
async def create_journal_entry(entry: JournalEntryCreate, user_id: str):
    # 1. Store journal entry
    conn.execute("""INSERT INTO journal_entries 
                    (title, content, mood, user_id) VALUES (?, ?, ?, ?)""",
                 (entry.title, entry.content, entry.mood, user_id))
    
    # 2. Extract entities and create memories
    if mentions_person_in_content(entry.content):
        # Auto-create person memory
        create_memory(type="people", data=extracted_person)
    
    if mentions_project_in_content(entry.content):
        # Link to existing project or create new
        link_to_project(extracted_project)
```

**Example:**
```json
// Journal Entry
{
  "title": "Great day with Sarah",
  "content": "Worked on greenhouse automation with Sarah today. 
              She helped debug the Arduino temperature sensor."
}

// Automatically Creates:
1. Memory: Person "Sarah" → linked to greenhouse project
2. Memory: Note about debugging session
3. Updates: Greenhouse project status
```

---

### 3. **Calendar → Memory Integration**

Calendar events are automatically available to memory search:

```python
# When AI searches for context about "meeting"
memories = search_memories("meeting with Sarah", user_id)
# Returns:
# - Calendar event: "Sarah - Arduino Discussion"
# - Person memory: "Sarah loves Arduino"
# - Journal entry: "Worked with Sarah today"
```

**Example:**
```
User: "When am I meeting Sarah?"

Zoe: "You have a meeting with Sarah tomorrow at 2pm to discuss 
      the greenhouse automation project. Based on your journal, 
      you recently worked together on debugging the Arduino 
      temperature sensor."
```

---

### 4. **Lists → Projects → Memory**

Lists automatically link to project memories:

```python
# routers/lists.py
@router.post("/")
async def create_list(list_data: ListCreate, user_id: str):
    # If list is project-related
    if list_data.list_type == "work":
        # Link to project memory
        project = find_related_project(list_data.name)
        if project:
            list_data.metadata["project_id"] = project.id
```

**Example:**
```
List: "Greenhouse Automation Tasks"
Items:
  - [ ] Order temperature sensors
  - [ ] Test Arduino code
  - [x] Debug with Sarah

Memory Integration:
  ✅ Linked to Project: "Greenhouse Automation"
  ✅ Linked to Person: "Sarah"
  ✅ AI knows: "You're waiting on temperature sensors for greenhouse"
```

---

## 💬 Real Integration Examples

### Example 1: Full Context Conversation

**User:** "What should I focus on today?"

**Zoe's Process:**
```
1. Check Calendar → "Team standup at 10am"
2. Check Tasks → "Finish authentication module (high priority)"
3. Check Lists → "Buy Arduino sensors (shopping list)"
4. Check Journal → "Yesterday you were stuck on auth bug"
5. Check Memories → "Sarah offered to help with Arduino"

Response:
"Good morning! Here's what I recommend:

🔴 Priority 1: Finish the authentication module (you were working 
   on a bug yesterday, still marked high priority)
   
📅 10am: Team standup meeting

🛒 Later: Pick up Arduino sensors for the greenhouse project
   
💡 Consider: Reach out to Sarah about the Arduino project - 
   she's been helpful before!"
```

---

### Example 2: Cross-System Memory Recall

**Scenario:** User adds journal entry, then asks chat

**Journal Entry (9am):**
```json
{
  "title": "Morning Reflection",
  "content": "Feeling anxious about the presentation. Need to practice more."
}
```

**Chat (2pm):**
```
User: "How am I feeling today?"

Zoe: "Based on your journal this morning, you were feeling anxious 
      about the presentation and wanted to practice more. How did 
      the practice go? Your presentation is on your calendar for 
      tomorrow at 3pm."
```

**Breakdown:**
- ✅ Journal entry → stored mood
- ✅ Calendar → knows about presentation
- ✅ Chat → combines both for contextual response

---

### Example 3: Task → Calendar → Memory Flow

**User creates task:**
```
POST /api/tasks
{
  "title": "Review Sarah's PR",
  "due_date": "2025-10-01",
  "assigned_to": "Sarah"
}
```

**Zoe automatically:**
1. Creates task
2. Checks if "Sarah" exists in memories → YES
3. Links task to Sarah's profile
4. Checks calendar for conflicts on Oct 1
5. Suggests time slot: "You're free at 2pm on Oct 1 to review Sarah's PR"

**Later conversation:**
```
User: "What do I need to do for Sarah?"

Zoe: "You need to review Sarah's pull request by October 1st. 
      Based on your calendar, you're free at 2pm that day. 
      Also, Sarah helped you with the Arduino project last week - 
      might be worth thanking her!"
```

---

## 🏗️ Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                         CHAT SYSTEM                         │
│                    (Contextual AI Responses)                │
└─────────────────┬───────────────────────────────────────────┘
                  │
      ┌───────────┴───────────┐
      │   Memory Search       │
      │   (Unified Context)   │
      └───────────┬───────────┘
                  │
  ┌───────────────┼───────────────┬──────────────┬──────────┐
  │               │               │              │          │
  ▼               ▼               ▼              ▼          ▼
┌──────┐    ┌──────────┐    ┌─────────┐    ┌───────┐  ┌──────┐
│People│    │ Journal  │    │Calendar │    │ Lists │  │Tasks │
│Notes │    │ Entries  │    │ Events  │    │ Items │  │      │
│Proj. │    │          │    │         │    │       │  │      │
└──────┘    └──────────┘    └─────────┘    └───────┘  └──────┘
   │             │               │              │          │
   └─────────────┴───────────────┴──────────────┴──────────┘
                          │
                    ┌─────▼─────┐
                    │  SQLite   │
                    │  zoe.db   │
                    └───────────┘
```

---

## 📊 Database Schema Integration

### Shared User Isolation
```sql
-- All tables have user_id for isolation
memories         → user_id (default or authenticated)
journal_entries  → user_id
calendar_events  → user_id  
lists            → user_id
tasks            → user_id

-- Memory search across all tables:
SELECT * FROM (
  SELECT 'person' as type, name as title, notes as content 
  FROM people WHERE user_id = ?
  UNION
  SELECT 'journal' as type, title, content 
  FROM journal_entries WHERE user_id = ?
  UNION
  SELECT 'event' as type, title, description as content 
  FROM calendar_events WHERE user_id = ?
  UNION
  SELECT 'list' as type, name as title, items as content 
  FROM lists WHERE user_id = ?
) WHERE content LIKE ?
```

---

## 🔧 API Integration Examples

### 1. Create Journal Entry That Updates Memory

```bash
# Create journal entry
curl -X POST http://localhost:8000/api/journal/ \
  -H "Authorization: Bearer TOKEN" \
  -d '{
    "title": "Arduino Success!",
    "content": "Finally got the temperature sensor working with Sarah help",
    "mood": "happy"
  }'

# Automatically creates/updates:
# 1. Journal entry
# 2. Memory note about Arduino success
# 3. Links to Sarah (person memory)
# 4. Updates greenhouse project status
```

### 2. Chat With Full Context

```bash
# Chat leverages everything
curl -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer TOKEN" \
  -d '{
    "message": "Summarize my week"
  }'

# Response combines:
# - Calendar events this week
# - Journal entries
# - Completed tasks
# - People you interacted with
# - Project progress
```

### 3. Calendar Event Auto-Links

```bash
# Create calendar event
curl -X POST http://localhost:8000/api/calendar/events \
  -H "Authorization: Bearer TOKEN" \
  -d '{
    "title": "Code review with Sarah",
    "start_time": "2025-10-01T14:00:00",
    "attendees": ["Sarah"]
  }'

# Zoe automatically:
# 1. Creates calendar event
# 2. Finds Sarah in people memories
# 3. Links event to Sarah profile
# 4. Suggests related projects (greenhouse automation)
```

---

## 🎯 Smart Features Enabled by Integration

### 1. **Contextual Reminders**
```
Zoe: "You have a meeting with Sarah at 2pm. Last time you met, 
      you discussed the Arduino sensors - did you order them yet? 
      (I see it's still on your shopping list)"
```

### 2. **Mood-Based Suggestions**
```
# If journal shows "stressed" mood
Zoe: "I noticed you were stressed yesterday. You have 3 high-priority 
      tasks today - would you like me to help prioritize them?"
```

### 3. **Auto-Timeline**
```
User: "What happened with the greenhouse project?"

Zoe: "Let me trace the timeline:
      - Sept 15: Started project (from task creation)
      - Sept 18: Met with Sarah (from calendar)
      - Sept 20: Arduino debugging session (from journal)
      - Sept 22: Sensor ordered (from shopping list)
      - Today: Waiting on delivery"
```

### 4. **Smart Task Creation**
```
User: (in chat) "I need to follow up with Sarah about the sensors"

Zoe: "I've created a task: 'Follow up with Sarah - Arduino sensors'
      and scheduled it for tomorrow at 10am (your usual check-in time).
      Also linked it to your greenhouse automation project."
```

---

## 🚀 How to Use Everything Together

### Morning Routine Example

**1. Check Dashboard:**
```javascript
// UI shows unified view
- Today's calendar events (from calendar)
- High priority tasks (from tasks)
- Recent journal mood (from journal)
- Active projects (from memories)
- People to follow up with (from memories + calendar)
```

**2. Chat for Planning:**
```
User: "Plan my day"

Zoe: "Based on your calendar, tasks, and yesterday's journal:
      
      9am: Focus time - finish auth module (task)
      10am: Team standup (calendar)
      11am: Code review with Sarah (calendar)
      2pm: Shopping - pick up Arduino sensors (list)
      
      You mentioned anxiety about the presentation in your journal -
      I've blocked 4-5pm for practice time."
```

**3. Throughout Day:**
- ✅ Complete tasks → Updates project progress
- ✅ Journal reflection → Creates memories
- ✅ Chat questions → Uses full context

**4. Evening Review:**
```
User: "How was today?"

Zoe: "You completed 4/5 tasks (80% productivity!)
      Met with Sarah for code review
      Journal shows improved mood vs. yesterday
      Greenhouse project: 75% complete
      Tomorrow: Presentation practice scheduled"
```

---

## 📈 Data Flow Summary

```
User Action         →  System         →  Memory Impact
─────────────────────────────────────────────────────────
Journal entry       →  Journal DB     →  Creates notes/people
Calendar event      →  Calendar DB    →  Links to people
Create task         →  Tasks DB       →  Links to projects
Chat message        →  AI + Memory    →  Uses all context
Add person          →  Memories DB    →  Available everywhere
Create list         →  Lists DB       →  Links to projects
```

---

## ✅ Key Takeaways

1. **Everything is Connected**
   - Journal mentions Sarah → Links to Sarah memory
   - Task about project → Links to project memory
   - Calendar event → Links to attendee memories

2. **Chat Knows Everything**
   - Pulls from ALL databases
   - Contextual responses
   - Natural memory recall

3. **Auto-Linking**
   - System automatically finds connections
   - No manual linking required
   - Smart entity recognition

4. **User Isolation**
   - All systems respect `user_id`
   - No data leakage between users
   - Secure authentication

5. **Timeline Awareness**
   - Tracks when things happened
   - Understands sequence
   - Provides chronological context

---

## 🎉 Result

**You get a TRUE AI companion that:**
- ✅ Remembers everything across all systems
- ✅ Provides context-aware responses
- ✅ Automatically links related information
- ✅ Maintains timeline awareness
- ✅ Offers personalized suggestions
- ✅ Works seamlessly across features

**Just like Samantha from "Her"!** 🌟

---

## 📚 Related Documentation

- `MEMORY_DEMO.md` - Memory system demonstration
- `EVERYTHING_DONE.md` - Complete feature list
- API docs: `/docs` endpoint

---

**Now you understand how all of Zoe's features work together!** 🎯
