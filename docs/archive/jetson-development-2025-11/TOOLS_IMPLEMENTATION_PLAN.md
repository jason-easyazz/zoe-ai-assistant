# Missing Tools Implementation Plan

**Current**: 25/79 tools (32%)
**Target**: 79/79 tools (100%)
**Missing**: 54 tools

---

## ✅ ALREADY EXIST (25 tools):

### Calendar (4/8):
- ✅ create_calendar_event
- ✅ get_calendar_events
- ✅ update_calendar_event
- ✅ delete_calendar_event

### Lists (2/8):
- ✅ add_to_list
- ✅ get_lists

### Memory/Collections (4/14):
- ✅ search_memories
- ✅ create_collection
- ✅ get_collections
- ✅ get_collection_analysis

### Person (3/10):
- ✅ create_person
- ✅ get_people
- ✅ get_person_analysis

### HomeAssistant (6/12):
- ✅ get_home_assistant_devices
- ✅ control_home_assistant_device
- ✅ get_home_assistant_automations
- ✅ trigger_home_assistant_automation
- ✅ get_home_assistant_scenes
- ✅ activate_home_assistant_scene

### N8N (4/8):
- ✅ get_n8n_workflows
- ✅ create_n8n_workflow
- ✅ execute_n8n_workflow
- ✅ get_n8n_executions
- ✅ get_n8n_nodes (5 total!)

### Developer (1/4):
- ✅ get_developer_tasks

---

## 🔧 TO IMPLEMENT (54 tools):

### Priority 1: Lists Expert (6 tools) - CRITICAL
1. `create_list` - Create new list
2. `delete_list` - Delete entire list
3. `update_list_item` - Update item text/priority
4. `delete_list_item` - Remove specific item
5. `mark_item_complete` - Mark task as done
6. `get_list_items` - Get items in specific list

### Priority 2: Calendar Expert (4 tools)
7. `search_calendar_events` - Search by title/description
8. `get_event_by_id` - Get specific event details
9. `get_recurring_events` - Get repeating events
10. `cancel_event` - Cancel without deleting

### Priority 3: Person Expert (7 tools) - CRITICAL
11. `update_person` - Update person details
12. `delete_person` - Remove person
13. `search_people` - Search by name/attributes
14. `add_person_attribute` - Add email/phone/birthday
15. `update_relationship` - Change relationship type
16. `add_interaction` - Log interaction
17. `get_person_by_name` - Find by name

### Priority 4: Memory Expert (10 tools)
18. `create_memory` - Add fact/memory
19. `update_memory` - Modify memory
20. `delete_memory` - Remove memory
21. `update_collection` - Update collection details
22. `delete_collection` - Remove collection
23. `add_to_collection` - Add items to collection
24. `remove_from_collection` - Remove items
25. `search_collections` - Find collections
26. `get_memory_timeline` - Chronological view
27. `link_memories` - Connect related memories

### Priority 5: HomeAssistant Expert (6 tools)
28. `get_device_state` - Get specific device state
29. `get_device_history` - State history
30. `create_automation` - New automation
31. `update_automation` - Modify automation
32. `delete_automation` - Remove automation
33. `get_automation_logs` - Execution history

### Priority 6: Planning Expert (10 tools)
34. `create_project` - New project
35. `update_project` - Modify project
36. `delete_project` - Remove project
37. `get_projects` - List all projects
38. `get_project_analysis` - Project insights
39. `add_project_task` - Add task to project
40. `update_project_task` - Modify task
41. `complete_project_task` - Mark done
42. `get_project_timeline` - Gantt/timeline
43. `link_project_resources` - Attach files/links

### Priority 7: Matrix Expert (7 tools)
44. `send_matrix_message` - Send message to room
45. `get_matrix_rooms` - List joined rooms
46. `create_matrix_room` - New room
47. `invite_to_matrix_room` - Invite user
48. `get_matrix_messages` - Read messages
49. `upload_matrix_file` - Send file
50. `react_to_matrix_message` - Add reaction

### Priority 8: N8N Expert (3 more tools)
51. `update_n8n_workflow` - Modify workflow
52. `delete_n8n_workflow` - Remove workflow
53. `get_n8n_credentials` - List credentials

### Priority 9: General/System (4 tools)
54. `get_weather` - Weather info
55. `set_reminder` - Future reminders
56. `get_system_status` - Health check
57. `export_data` - Data export

---

## 🚀 Implementation Strategy:

### Phase 1: Critical Missing (Tonight - 2 hours)
- Lists tools (6) - Most requested
- Person tools (7) - Essential for relationships
- **Total**: 13 tools

### Phase 2: Core Features (Tomorrow AM - 2 hours)
- Calendar tools (4)
- Memory tools (10)
- **Total**: 14 tools

### Phase 3: Integrations (Tomorrow PM - 2 hours)
- HomeAssistant tools (6)
- Planning tools (10)
- **Total**: 16 tools

### Phase 4: Communication & Final (Tomorrow Eve - 2 hours)
- Matrix tools (7)
- N8N tools (3)
- General tools (4)
- **Total**: 14 tools

**Total Time**: 8 hours
**Completion**: Tomorrow evening

---

## 📋 Implementation Template:

For each tool, need:

1. **Tool Definition** (in `_setup_tools()`)
```python
Tool(
    name="tool_name",
    description="What it does",
    inputSchema={...}
)
```

2. **Handler Registration** (in `handle_tool_call()`)
```python
elif name == "tool_name":
    return await self._tool_name(arguments, user_context)
```

3. **Implementation** (method)
```python
async def _tool_name(self, args: Dict, user_context) -> CallToolResult:
    # Implementation
    pass
```

---

## 🎯 Starting NOW with Phase 1 (Lists + Person):

Adding 13 critical tools:
- create_list
- delete_list
- update_list_item
- delete_list_item
- mark_item_complete
- get_list_items
- update_person
- delete_person
- search_people
- add_person_attribute
- update_relationship
- add_interaction
- get_person_by_name

Let's go! 🚀

