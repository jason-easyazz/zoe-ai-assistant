# Expert Completion Plan
## Practical Strategy for Adding Missing Tools

## 📊 Current Status
- **32/79 tools** exist (41% complete)
- **Main issue**: Existing tools don't work properly (LLM generating "Lists Expert" instead of "add_to_list")

## 🎯 STRATEGY: Fix First, Then Expand

### Phase 1: FIX EXISTING 32 TOOLS ⚡ (CURRENT PRIORITY)
**Goal**: Get existing tools actually executing actions

1. ✅ Fix prompt template (DONE - clarified Expert vs Tool names)
2. 🔄 Test if LLM now generates correct tool names (IN PROGRESS)
3. 🔄 Verify tool execution happens (shopping list, calendar)
4. 🔄 Run test suite to measure improvement

**ETA**: 30 minutes
**Impact**: Enables 32 tools to work = 41% functional system

---

### Phase 2: ADD CRITICAL CRUD TOOLS 🔧 (NEXT)
**Goal**: Enable update/delete for core entities

#### 2A. Calendar (Priority 1)
Add to `/home/zoe/assistant/services/zoe-core/routers/calendar.py`:
- `update_calendar_event` - Modify existing event
- `delete_calendar_event` - Remove event

Then expose via MCP server.

**ETA**: 1 hour
**Impact**: +2 tools = 34/79 (43%)

#### 2B. Lists (Priority 1)
Add to zoe-core (events table APIs):
- `update_list_item` - Modify item text/priority
- `delete_list_item` - Remove item
- `mark_item_complete` - Toggle completion status
- `get_list_items` - Get items in specific list

**ETA**: 1.5 hours
**Impact**: +4 tools = 38/79 (48%)

#### 2C. People (Priority 1)
Add to zoe-core (people table APIs):
- `update_person` - Update person details
- `delete_person` - Remove person

**ETA**: 45 minutes
**Impact**: +2 tools = 40/79 (51%)

---

### Phase 3: ENHANCE EXISTING TOOLS 🎨 (LATER)
**Goal**: Make existing tools more capable

#### 3A. Enhance create_person
Add more fields:
```python
{
  "name": str,
  "relationship": str,
  "notes": str,
  "birthday": str,  # NEW
  "email": str,      # NEW
  "phone": str,      # NEW
  "address": str,    # NEW
  "preferences": dict # NEW
}
```

**ETA**: 30 minutes
**Impact**: Better person management (no new tool count)

#### 3B. Add Search/Filter Tools
- `search_calendar_events` - Find events by keyword
- `search_people` - Find people by name
- `create_list` - Create new list

**ETA**: 1 hour
**Impact**: +3 tools = 43/79 (54%)

---

### Phase 4: EXPAND TO 80%+ COVERAGE 🚀 (FUTURE)
**Goal**: Full CRUD for all entities

#### Add remaining tools:
- Collections: update, delete, add_to, remove_from (+4)
- Matrix: leave_room, invite, delete_message (+3)
- N8N: update, delete, activate, deactivate (+4)
- HomeAssistant: get_device_state, create_automation (+2)
- Planning: create_plan, get_plans, execute_step (+3)
- Development: create_task, update_task, complete_task (+3)

**ETA**: 4-6 hours
**Impact**: +19 tools = 51/79 (65%)

---

## 🎯 IMMEDIATE ACTION

**RIGHT NOW**: Continue Phase 1

1. Test if tool call fix worked:
   - Send "Add bread to shopping list"
   - Check if LLM generates `[TOOL_CALL:add_to_list:{"list_name":"shopping","task_text":"bread"}]`
   - Verify bread is actually added

2. If Phase 1 works:
   - Run test suite
   - Measure pass rate improvement
   - Document successes

3. Then move to Phase 2:
   - Add update/delete for calendar, lists, people
   - Test each new tool
   - Update prompt template

---

## 💡 KEY INSIGHT

**Before adding 47 new tools, let's make the 32 existing tools actually work!**

Current problem: LLM says "Hi there! I can definitely help you add bread to your shopping list." but DOESN'T CALL THE TOOL.

Once that's fixed, we'll have:
- ✅ 32 working tools
- ✅ Actions actually execute
- ✅ Real-time assistant functionality

Then we can systematically add missing tools to reach 65%+ coverage.

---

## 📋 PROGRESS TRACKING

- [x] Audit all experts (COMPLETE_EXPERT_AUDIT.md)
- [x] Fix prompt template (clarified Expert vs Tool names)
- [ ] Test tool call generation
- [ ] Verify tool execution
- [ ] Run test suite
- [ ] Add update/delete for calendar
- [ ] Add update/delete for lists
- [ ] Add update/delete for people
- [ ] Re-run tests to measure improvement
- [ ] Add remaining tools to reach 65%+

---

## 🎯 SUCCESS METRICS

### Phase 1 Success:
- LLM generates correct tool names (not "Lists Expert")
- Actions actually execute (items added to lists)
- Test pass rate improves from 14.3% to 60%+

### Phase 2 Success:
- Can update/delete calendar events
- Can update/delete list items
- Can update/delete people
- Test pass rate reaches 75%+

### Phase 3+ Success:
- 65%+ tool coverage (51/79 tools)
- 90%+ test pass rate
- Full CRUD for core entities

