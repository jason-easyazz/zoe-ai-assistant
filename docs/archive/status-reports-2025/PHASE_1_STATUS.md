# Phase 1: Merge Dual Memory Systems - STATUS

## ✅ COMPLETED

### MCP Tool Fixed
- Modified `_get_self_info` in `services/zoe-mcp-server/main.py`
- Now queries BOTH `self_facts` and `people.is_self` tables
- Defensive JSON parsing implemented
- Suffix matching for fact keys implemented
- User ID extraction fixed in HTTP endpoint

### Test Results
**Direct tool calls work perfectly:**
```bash
# jason's facts
curl http://localhost:8003/tools/get_self_info -d '{"user_id": "jason"}'
Response: "About User_jason:\n\n- favorite color: purple"

# demo_test_user's facts  
curl http://localhost:8003/tools/get_self_info -d '{"user_id": "demo_test_user"}'
Response: "About User:\n\n- name: Alex Thompson\n- favorite color: Blue\n- pet: Golden Retriever named Max..."
```

## ⚠️ REMAINING ISSUE

### LLM Not Using Tool Response
**Problem:** When called through chat interface, tool executes successfully but LLM doesn't use the response.

**Example:**
```
User: "What is my favorite color?"
Tool call: get_self_info(fact_key="favorite_color")
Tool response: "Your favorite color is purple"
Chat router: Replaces with "Executed get_self_info successfully"
LLM sees: "Executed get_self_info successfully"
LLM responds: "I'm not sure what your favorite color is"
```

**Root Cause:** The chat router's tool execution logic replaces tool responses with generic success messages instead of passing the actual data to the LLM.

**Solution:** Phase 3 - Include self_facts directly in system prompt so LLM has access regardless of tool execution flow.

## Files Modified

1. `/home/zoe/assistant/services/zoe-mcp-server/main.py`
   - `_get_self_info` method (lines 2477-2560)
   - Dual-table query with merge logic
   - Defensive JSON parsing
   - Suffix matching

2. `/home/zoe/assistant/services/zoe-mcp-server/http_mcp_server.py`
   - Added `user_id` field to `ToolRequest` base class
   - Fixed `get_self_info` endpoint to extract user_id correctly
   - Added debug logging

## Next Steps

**Phase 2:** Deterministic tool routing (calendar vs shopping)
**Phase 3:** Include self_facts in system prompt (fixes recall issue)

## Rollback Command

```bash
git checkout HEAD -- services/zoe-mcp-server/main.py services/zoe-mcp-server/http_mcp_server.py
docker compose restart zoe-mcp-server
```

