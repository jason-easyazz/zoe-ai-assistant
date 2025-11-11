# Complete Expert System Audit
## Checking All 9 Experts for Full Capabilities

### 🗓️ 1. CALENDAR EXPERT

**Current MCP Tools (2)**:
- ✅ `create_calendar_event` - Create new event
- ✅ `get_calendar_events` - Get events for date range

**Missing Tools**:
- ❌ `update_calendar_event` - Update existing event
- ❌ `delete_calendar_event` - Delete event
- ❌ `search_calendar_events` - Search by title/description
- ❌ `get_event_by_id` - Get specific event details

**Status**: ⚠️ **INCOMPLETE** - Can create and read, but cannot update or delete

---

### 📝 2. LISTS EXPERT

**Current MCP Tools (2)**:
- ✅ `add_to_list` - Add item to list
- ✅ `get_lists` - Get all lists

**Missing Tools**:
- ❌ `create_list` - Create new list
- ❌ `delete_list` - Delete list
- ❌ `update_list_item` - Update existing item
- ❌ `delete_list_item` - Remove item from list
- ❌ `mark_item_complete` - Mark task as done
- ❌ `get_list_items` - Get items in specific list

**Status**: ⚠️ **INCOMPLETE** - Can add items but cannot manage lists or items

---

### 🧠 3. MEMORY EXPERT

**Current MCP Tools (7)**:
- ✅ `search_memories` - Search all memory types
- ✅ `create_collection` - Create new collection
- ✅ `get_collections` - Get all collections
- ✅ `get_collection_analysis` - Comprehensive collection analysis
- ✅ `create_person` - Create new person
- ✅ `get_people` - Get all people
- ✅ `get_person_analysis` - Comprehensive person analysis

**Missing Tools**:
- ❌ `update_collection` - Update collection details
- ❌ `delete_collection` - Delete collection
- ❌ `add_to_collection` - Add items to collection
- ❌ `remove_from_collection` - Remove items from collection
- ❌ `create_memory` - Add arbitrary memory/fact
- ❌ `update_memory` - Update existing memory
- ❌ `delete_memory` - Remove memory

**Status**: ⚠️ **INCOMPLETE** - Good read capabilities, limited write/update

---

### 👥 4. PERSON EXPERT

**Current MCP Tools (3)**:
- ✅ `create_person` - Create new person (name, relationship, notes only)
- ✅ `get_people` - Get all people
- ✅ `get_person_analysis` - Comprehensive analysis

**Missing Tools**:
- ❌ `update_person` - Update person details
- ❌ `delete_person` - Delete person
- ❌ `search_people` - Search by name/attributes
- ❌ `add_person_attribute` - Add custom attributes (birthday, email, phone)
- ❌ `update_relationship` - Modify relationship type
- ❌ `add_interaction` - Log interaction with person
- ❌ `get_person_by_name` - Find person by name

**Missing Attributes in create_person**:
- Birthday, email, phone, address, preferences, custom fields

**Status**: ❌ **VERY INCOMPLETE** - Basic create/read only, no updates or rich attributes

---

### 🏠 5. HOMEASSISTANT EXPERT

**Current MCP Tools (6)**:
- ✅ `get_home_assistant_devices` - Get all devices
- ✅ `control_home_assistant_device` - Control device
- ✅ `get_home_assistant_automations` - Get automations
- ✅ `trigger_home_assistant_automation` - Trigger automation
- ✅ `get_home_assistant_scenes` - Get scenes
- ✅ `activate_home_assistant_scene` - Activate scene

**Missing Tools**:
- ❌ `get_device_state` - Get specific device state
- ❌ `get_device_history` - Get device state history
- ❌ `create_automation` - Create new automation
- ❌ `update_automation` - Modify automation
- ❌ `delete_automation` - Remove automation

**Status**: ✅ **MOSTLY COMPLETE** - Good coverage, some advanced features missing

---

### 🔄 6. N8N EXPERT

**Current MCP Tools (5)**:
- ✅ `get_n8n_workflows` - Get all workflows
- ✅ `create_n8n_workflow` - Create new workflow
- ✅ `execute_n8n_workflow` - Execute workflow
- ✅ `get_n8n_executions` - Get workflow executions
- ✅ `get_n8n_nodes` - Get available nodes

**Missing Tools**:
- ❌ `update_n8n_workflow` - Update workflow
- ❌ `delete_n8n_workflow` - Delete workflow
- ❌ `activate_n8n_workflow` - Activate workflow
- ❌ `deactivate_n8n_workflow` - Deactivate workflow

**Status**: ✅ **MOSTLY COMPLETE** - Good coverage, some lifecycle management missing

---

### 💬 7. MATRIX EXPERT

**Current MCP Tools (6)**:
- ✅ `send_matrix_message` - Send message
- ✅ `get_matrix_rooms` - Get rooms
- ✅ `create_matrix_room` - Create room
- ✅ `join_matrix_room` - Join room
- ✅ `get_matrix_messages` - Get recent messages
- ✅ `set_matrix_presence` - Set presence

**Missing Tools**:
- ❌ `leave_matrix_room` - Leave room
- ❌ `invite_to_matrix_room` - Invite user to room
- ❌ `delete_matrix_message` - Delete message
- ❌ `edit_matrix_message` - Edit message
- ❌ `get_room_members` - Get room members

**Status**: ✅ **MOSTLY COMPLETE** - Good coverage, some management features missing

---

### 📊 8. PLANNING EXPERT

**Current MCP Tools (0)**:
- No direct tools (backend coordinator only)

**Missing Tools**:
- ❌ `create_plan` - Create multi-step plan
- ❌ `get_plans` - Get user's plans
- ❌ `update_plan` - Update plan
- ❌ `execute_plan_step` - Execute next step
- ❌ `get_plan_status` - Check plan progress

**Status**: ❌ **NO TOOLS** - Backend only, no direct MCP interface

---

### 💻 9. DEVELOPMENT EXPERT

**Current MCP Tools (1)**:
- ✅ `get_developer_tasks` - Get roadmap tasks

**Missing Tools**:
- ❌ `create_developer_task` - Add task to roadmap
- ❌ `update_developer_task` - Update task
- ❌ `complete_developer_task` - Mark task complete
- ❌ `get_task_by_id` - Get specific task

**Status**: ⚠️ **INCOMPLETE** - Read-only, no task management

---

## 📊 SUMMARY

| Expert | Tools | Status | Completeness |
|--------|-------|--------|--------------|
| 🗓️ Calendar | 2/6 | ⚠️ Incomplete | 33% |
| 📝 Lists | 2/8 | ⚠️ Incomplete | 25% |
| 🧠 Memory | 7/14 | ⚠️ Incomplete | 50% |
| 👥 Person | 3/10 | ❌ Very Incomplete | 30% |
| 🏠 HomeAssistant | 6/11 | ✅ Mostly Complete | 55% |
| 🔄 N8N | 5/9 | ✅ Mostly Complete | 56% |
| 💬 Matrix | 6/11 | ✅ Mostly Complete | 55% |
| 📊 Planning | 0/5 | ❌ No Tools | 0% |
| 💻 Development | 1/5 | ⚠️ Incomplete | 20% |

**Overall**: 32/79 tools = **41% Complete**

---

## 🎯 PRIORITY MISSING TOOLS

### HIGH PRIORITY (Core CRUD Operations)
1. ✅ Already have: create_calendar_event, add_to_list, create_person
2. ❌ **MISSING**: update_calendar_event, delete_calendar_event
3. ❌ **MISSING**: update_list_item, delete_list_item, mark_item_complete
4. ❌ **MISSING**: update_person, delete_person
5. ❌ **MISSING**: update_collection, delete_collection

### MEDIUM PRIORITY (Enhanced Features)
1. ❌ search_calendar_events, search_people
2. ❌ create_list, delete_list, get_list_items
3. ❌ add_to_collection, remove_from_collection
4. ❌ add_person_attribute (birthday, email, phone)

### LOW PRIORITY (Advanced Features)
1. ❌ get_device_history, create_automation
2. ❌ update_n8n_workflow, delete_n8n_workflow
3. ❌ leave_matrix_room, invite_to_matrix_room
4. ❌ Planning Expert tools (create_plan, execute_plan_step)

---

## 🚀 ACTION PLAN

### Phase 1: Add Critical CRUD Tools (Update/Delete)
Add these tools to MCP server `/home/zoe/assistant/services/zoe-mcp-server/main.py`:

```python
# Calendar
- update_calendar_event
- delete_calendar_event

# Lists
- update_list_item
- delete_list_item
- mark_item_complete
- create_list

# People
- update_person
- delete_person

# Collections
- update_collection
- delete_collection
```

### Phase 2: Test with Current Tools First
Before adding new tools, let's ensure the EXISTING 32 tools actually work:
1. Fix tool call format (currently broken - LLM generating "Lists Expert" instead of "add_to_list")
2. Test each of the 32 existing tools
3. Verify actions actually execute

### Phase 3: Add Missing Tools
Once existing tools work, systematically add missing CRUD operations.

---

## 🎯 IMMEDIATE NEXT STEPS

1. ✅ Document all experts (DONE - this file)
2. 🔄 Fix tool call format in prompt (IN PROGRESS)
3. 🔄 Test existing 32 tools work
4. ⏭️ Add missing CRUD tools to MCP server
5. ⏭️ Update prompt template with new tools
6. ⏭️ Re-run test suite

