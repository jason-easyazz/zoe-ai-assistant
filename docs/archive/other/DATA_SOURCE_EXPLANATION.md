# 📊 Where Zoe's Information Comes From

## 🎯 The Data You're Seeing

When you ask Zoe "What did I do this week?", she's pulling from **4 main data sources**:

### 1. 📅 Calendar Events
**Source**: `events` table in SQLite database

Current events for user "default":
- "Do a developer bug system" (Oct 2nd, Oct 4th)
- "Test Personal" (Oct 2nd)
- "Work" entries
- Shopping trips, Beach visits, etc.

### 2. 📔 Journal Entries
**Source**: `journal_entries` table

Current entries:
- "Productive Monday" (happy mood) - "Finished website redesign mockups"
- "Stressful Tuesday" (anxious mood) - "Back-to-back meetings"
- Other test entries

### 3. 👥 People
**Source**: `people` table

Current people:
- **Sarah** (colleague) - "Works in engineering team, loves coffee"
- **John Doe** (Friend) - "Loves hiking and photography"

### 4. 🎯 Projects
**Source**: `projects` table

Current projects:
- **Zoe AI Assistant** (active) - "Building a personal AI assistant"
- **Website Redesign** (in_progress) - "Modernizing company website"

---

## 🔄 How It Works

```
1. You ask: "What did I do this week?"
                    ↓
2. Zoe's routing system detects this is a "memory-retrieval" query
                    ↓
3. System queries all 4 data sources for user_id="default"
                    ↓
4. Finds 13 total memories:
   - 5 calendar events (this week)
   - 3 journal entries (recent)
   - 3 people (you interact with)
   - 2 projects (active)
                    ↓
5. Passes this context to phi3:mini LLM
                    ↓
6. LLM generates response using the context
                    ↓
7. Response streams to your browser token-by-token
```

---

## ⚠️ Important: This is TEST DATA!

**The data you're seeing was auto-generated for testing.** It includes:
- Sample calendar events (developer bug system, testing)
- Sample journal entries (productive/stressful days)
- Sample people (Sarah the colleague, John Doe)
- Sample projects (Zoe AI, Website Redesign)

### To Use YOUR Real Data:

1. **Add Calendar Events**: Use the calendar UI or API
2. **Write Journal Entries**: Use the journal feature
3. **Add People**: Through the people/contacts system
4. **Create Projects**: Through the projects interface

Once you add real data, Zoe will use that instead!

---

## 🧠 Memory Architecture

```
┌─────────────────────────────────────────────────────┐
│              Zoe Memory System                      │
├─────────────────────────────────────────────────────┤
│                                                     │
│  SQLite Database (/app/data/zoe.db)                │
│                                                     │
│  ┌──────────────┐  ┌──────────────┐               │
│  │   events     │  │journal_entries│              │
│  │ - title      │  │ - title       │              │
│  │ - date       │  │ - content     │              │
│  │ - description│  │ - mood        │              │
│  └──────────────┘  └──────────────┘               │
│                                                     │
│  ┌──────────────┐  ┌──────────────┐               │
│  │   people     │  │   projects    │              │
│  │ - name       │  │ - name        │              │
│  │ - relationship│ │ - status      │              │
│  │ - notes      │  │ - description │              │
│  └──────────────┘  └──────────────┘               │
│                                                     │
└─────────────────────────────────────────────────────┘
                      ↓
              [Query by user_id]
                      ↓
         [Context passed to LLM (phi3:mini)]
                      ↓
              [Intelligent Response]
```

---

## 🎭 Why Responses Seem "Made Up"

The LLM (phi3:mini) receives the **facts from your database**, but:

1. **Adds Natural Language**: Turns data into conversational text
2. **Fills Gaps**: Makes assumptions to create coherent narratives
3. **Adds Context**: "You worked with Sarah this week" (inferred from Sarah being in your people list)
4. **Personality**: Adds warmth and structure (bullets, organization)

**Example:**

**Database has:**
```
- Event: "Do a developer bug system" (Oct 2, Oct 4)
- Person: "Sarah" (colleague)
```

**LLM outputs:**
```
- Conducted a developer bug system on Tuesday and Thursday
- Worked collaboratively with Sarah, your colleague this past week
```

The second line is **inferred** (not explicitly in the database), but based on the fact that Sarah exists in your contacts.

---

## 🔍 To See Raw Data Anytime

```bash
docker exec zoe-core python3 << 'EOF'
import sqlite3
conn = sqlite3.connect("/app/data/zoe.db")
cursor = conn.cursor()

# See YOUR events
cursor.execute("SELECT * FROM events WHERE user_id='default' LIMIT 5")
print(cursor.fetchall())
EOF
```

Or use the Zoe UI to view/edit your data directly!

---

## ✅ Summary

**Where info comes from:**
1. SQLite database (`zoe.db`)
2. Your actual calendar, journal, people, projects data
3. Currently populated with test data for demonstration

**To make it personal:**
- Add your real calendar events
- Write actual journal entries  
- Add real people you know
- Create real projects you're working on

Then Zoe will remember YOUR life, not test data! 🎉

