# Expert Architecture - Complete System Design

**Date**: October 8, 2025  
**Purpose**: Define all experts needed for comprehensive natural language AI

---

## 🧠 CURRENT EXPERTS (6)

### ✅ PersonExpert (NEW!)
**Purpose**: People, relationships, social memory  
**Handles**: "Remember person named X", "Who is X?", "My friend/colleague/family"  
**Status**: Created, needs service auth  
**Priority**: Critical - social memory is core

### ✅ ListExpert
**Purpose**: Shopping lists, task lists, todos  
**Handles**: "Add X to shopping list", "What's on my list?"  
**Status**: Working ✅

### ✅ CalendarExpert  
**Purpose**: Events, scheduling, appointments  
**Handles**: "Create event on X", "What's on my calendar?"  
**Status**: Working ✅

### ✅ MemoryExpert
**Purpose**: Notes, projects, general facts  
**Handles**: "Remember this note", "Save project X"  
**Status**: Working ✅

### ✅ PlanningExpert
**Purpose**: Goal decomposition, task planning  
**Handles**: "Plan my week", "Break this down into steps"  
**Status**: Working ✅

### ✅ BirthdayExpert
**Purpose**: Birthday management, gift ideas  
**Handles**: "Remember X's birthday", "Gift ideas for X"  
**Status**: Working ✅

---

## 🆕 RECOMMENDED NEW EXPERTS

### 1. JournalExpert 🔥 HIGH PRIORITY
**Why**: You have `journal_entries` table and `journal.py` router  
**Purpose**: Daily journals, reflections, mood tracking  
**Handles**:
- "Journal: Had a great day today..."
- "How was I feeling last week?"
- "Show my recent journal entries"
- "What did I write about X?"

**Tables**: `journal_entries`  
**APIs**: `/api/journal/*`  
**Benefit**: Personal reflection & emotional intelligence

---

### 2. ReminderExpert 🔥 HIGH PRIORITY
**Why**: You have `reminders`, `notifications`, `reminder_history` tables  
**Purpose**: Dedicated reminder management  
**Handles**:
- "Remind me to X at Y time"
- "What reminders do I have?"
- "Snooze this reminder"
- "Clear all reminders"

**Tables**: `reminders`, `notifications`, `reminder_history`  
**APIs**: `/api/reminders/*`  
**Benefit**: Currently handled by CalendarExpert - deserves its own

---

### 3. WeatherExpert 🌤️ MEDIUM PRIORITY
**Why**: You have `weather.py` router  
**Purpose**: Weather queries, contextual suggestions  
**Handles**:
- "What's the weather today?"
- "Will it rain tomorrow?"
- "Should I bring an umbrella?"
- Weather-based scheduling suggestions

**APIs**: `/api/weather/*`  
**Benefit**: Context for calendar (outdoor events, commute planning)

---

### 4. HomeAssistantExpert 🏠 HIGH PRIORITY
**Why**: You have `homeassistant.py` router  
**Purpose**: Smart home control, automation  
**Handles**:
- "Turn on living room lights"
- "Set temperature to 72"
- "Is the garage door closed?"
- "Run bedtime routine"

**APIs**: `/api/homeassistant/*`  
**Benefit**: Voice control of home - major feature

---

### 5. WorkflowExpert 🔄 MEDIUM PRIORITY
**Why**: You have `workflows.py`, `n8n_integration.py` routers  
**Purpose**: Automation, integrations, workflows  
**Handles**:
- "Run my morning routine"
- "Automate email to calendar"
- "Set up integration for X"
- "What workflows are active?"

**Tables**: (N8N external)  
**APIs**: `/api/workflows/*`, `/api/n8n/*`  
**Benefit**: Power user automation

---

### 6. SettingsExpert ⚙️ LOW PRIORITY
**Why**: You have `settings.py`, `settings_ui.py` routers  
**Purpose**: Configuration, preferences  
**Handles**:
- "Change my timezone to X"
- "Set dark mode"
- "Update my preferences"
- "Show my settings"

**APIs**: `/api/settings/*`  
**Benefit**: Natural language configuration

---

### 7. FamilyExpert 👨‍👩‍👧‍👦 MEDIUM PRIORITY
**Why**: You have `families`, `family_members`, `family_invitations` tables  
**Purpose**: Family management, shared calendars  
**Handles**:
- "Add X to my family"
- "What's on the family calendar?"
- "Share this event with family"
- "Family member list"

**Tables**: `families`, `family_members`, `family_invitations`  
**APIs**: `/api/family/*`  
**Benefit**: Multi-user households

---

### 8. InsightsExpert 📊 LOW PRIORITY
**Why**: You have `person_timeline`, `person_activities`, `performance_metrics` tables  
**Purpose**: Analytics, patterns, suggestions  
**Handles**:
- "How productive was I this week?"
- "Show my patterns"
- "What insights do you have?"
- "Suggest improvements"

**Tables**: `performance_metrics`, `system_metrics`, `person_activities`  
**APIs**: `/api/performance/*`, `/api/proactive-insights/*`  
**Benefit**: Proactive AI assistance

---

### 9. SearchExpert 🔍 MEDIUM PRIORITY  
**Why**: You have `vector_search.py` and Light RAG  
**Purpose**: Universal search across all data  
**Handles**:
- "Search for X"
- "Find anything about Y"
- "When did I mention Z?"
- Cross-entity search

**APIs**: `/api/vector-search/*`, `/api/memories/search`  
**Benefit**: Unified search experience

---

## 🎯 RECOMMENDED IMPLEMENTATION ORDER

### Phase 1: Core Missing Features (Week 1)
1. **JournalExpert** - You have journal UI, needs NL integration
2. **ReminderExpert** - Separate from calendar, dedicated handling
3. **HomeAssistantExpert** - Smart home control is major value

### Phase 2: Enhanced Intelligence (Week 2)
4. **WeatherExpert** - Context for scheduling
5. **SearchExpert** - Universal search
6. **FamilyExpert** - Multi-user support

### Phase 3: Power Features (Week 3)
7. **WorkflowExpert** - Automation for power users
8. **InsightsExpert** - Proactive intelligence
9. **SettingsExpert** - NL configuration

---

## 📊 COMPLETE EXPERT ROSTER

After implementation, you'll have **15 experts**:

**Core** (6 current):
- PersonExpert, ListExpert, CalendarExpert, MemoryExpert, PlanningExpert, BirthdayExpert

**Essential** (3):
- JournalExpert, ReminderExpert, HomeAssistantExpert

**Enhanced** (3):
- WeatherExpert, SearchExpert, FamilyExpert

**Power** (3):
- WorkflowExpert, InsightsExpert, SettingsExpert

---

## 🎯 MY RECOMMENDATION

**Install NOW** (while fixing auth):
1. ✅ **JournalExpert** - You have journal UI, just needs expert
2. ✅ **ReminderExpert** - Separate from calendar
3. ✅ **HomeAssistantExpert** - Smart home is huge value

**Total**: 9 experts (6 current + 3 critical)

**Add Later**:
- WeatherExpert, FamilyExpert, WorkflowExpert (as needed)

---

**Want me to create these 3 experts while I'm working on the auth fix?** 🚀

*This would give you comprehensive NL coverage for all major features!*

