# MCP Tools & Experts Audit
## Comparing Available Tools vs Prompt Template

### Available MCP Tools (29 total)

From MCP server at `http://localhost:8003/tools/list`:

#### 1. Zoe Memory (7 tools)
- `search_memories` - Search through Zoe's memory system
- `create_person` - Create a new person
- `create_collection` - Create a new collection
- `get_people` - Get all people
- `get_person_analysis` - Comprehensive person analysis
- `get_collections` - Get all collections  
- `get_collection_analysis` - Comprehensive collection analysis

#### 2. Zoe Lists (2 tools)
- `add_to_list` - Add item to todo list
- `get_lists` - Get all user's todo lists

#### 3. Calendar (2 tools)
- `create_calendar_event` - Create new calendar event
- `get_calendar_events` - Get events for date range

#### 4. Home Assistant (6 tools)
- `get_home_assistant_devices` - Get all devices
- `control_home_assistant_device` - Control device
- `get_home_assistant_automations` - Get automations
- `trigger_home_assistant_automation` - Trigger automation
- `get_home_assistant_scenes` - Get scenes
- `activate_home_assistant_scene` - Activate scene

#### 5. N8N Workflows (5 tools)
- `get_n8n_workflows` - Get all workflows
- `create_n8n_workflow` - Create new workflow
- `execute_n8n_workflow` - Execute workflow
- `get_n8n_executions` - Get workflow executions
- `get_n8n_nodes` - Get available nodes

#### 6. Developer (1 tool)
- `get_developer_tasks` - Get tasks from roadmap

#### 7. Matrix Messaging (6 tools)
- `send_matrix_message` - Send message to room
- `get_matrix_rooms` - Get list of rooms
- `create_matrix_room` - Create new room
- `join_matrix_room` - Join a room
- `get_matrix_messages` - Get recent messages
- `set_matrix_presence` - Set presence status

---

### Current Prompt Template Experts

From `prompt_templates.py`:

✅ **Calendar Expert** → `create_calendar_event`, `get_calendar_events`
✅ **Lists Expert** → `add_to_list`, `get_lists`
✅ **Memory Expert** → `search_memories`, `create_person`, `get_people`, etc.
❓ **Planning Expert** → Backend coordinator (no direct tools)
✅ **HomeAssistant Expert** → All 6 Home Assistant tools
❓ **Weather Expert** → Backend integration (no direct tools?)
❓ **TTS Expert** → Backend integration (no direct tools?)
✅ **Person Expert** → `create_person`, `get_people`, `get_person_analysis`
✅ **Development Expert** → `get_developer_tasks`

---

### ❌ MISSING EXPERTS in Prompt Template

1. **N8N Expert** (5 tools not documented!)
   - Workflow automation
   - Process orchestration
   - Integration creation

2. **Matrix Expert** (6 tools not documented!)
   - Messaging integration
   - Room management
   - Communication hub

3. **Collections Expert** (merged into Memory Expert?)
   - `create_collection`
   - `get_collections`
   - `get_collection_analysis`

---

### 📋 RECOMMENDATIONS

#### Add Missing Experts to Prompt Template:

```
• 🔄 N8N Expert (use tools: `create_n8n_workflow`, `execute_n8n_workflow`)
• 💬 Matrix Expert (use tools: `send_matrix_message`, `get_matrix_rooms`)
• 📦 Collections Expert (use tools: `create_collection`, `get_collections`)
```

#### Clarify Weather/TTS:
- If no actual MCP tools exist, mark as "future integration"
- Or remove from expert list to avoid confusion

---

### ✅ ACTION ITEMS

1. Add N8N Expert to prompt template with 5 tools
2. Add Matrix Expert to prompt template with 6 tools  
3. Explicitly list collection tools under Memory Expert
4. Verify Weather/TTS tools exist or remove from template
5. Update "What can you do?" example to include ALL 29 tools

---

### 🎯 GOAL

Ensure the LLM knows about **ALL 29 available tools** and uses the correct tool names (not "Lists Expert") when executing actions.

