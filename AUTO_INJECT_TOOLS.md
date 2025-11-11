# Auto-Inject Tool Calls Solution

## Problem
`gemma3n-e2b-gpu-fixed` model does NOT generate `[TOOL_CALL:...]` format despite:
- Extremely aggressive prompts
- Multiple examples
- Warnings about failures
- Tools list in context

The model just says "I've added X" without calling tools.

## Solution: Auto-Inject Tool Calls

When an action request is detected but NO tool call generated:
1. Parse the user's intent
2. Auto-generate the correct tool call
3. Execute it
4. Return confirmation

### Implementation

Add function before `parse_and_execute_code_or_tools`:

```python
async def auto_inject_tool_calls(response: str, message: str, routing: Dict, user_id: str) -> str:
    """
    Auto-inject tool calls when LLM doesn't generate them
    This is a fallback for models that don't follow tool calling instructions
    """
    # If tool call already exists, don't inject
    if "[TOOL_CALL:" in response:
        return response
    
    # If not an action request, don't inject
    if routing.get("type") != "action":
        return response
    
    # Parse intent from user message
    message_lower = message.lower()
    
    # Shopping list patterns
    if any(word in message_lower for word in ["add", "put"]) and any(word in message_lower for word in ["shopping", "list", "grocery"]):
        # Extract item name
        item = message_lower
        for remove in ["add", "to", "shopping", "list", "my", "the", "a", "please", "can", "you"]:
            item = item.replace(remove, "")
        item = item.strip()
        
        if item:
            tool_call = f'[TOOL_CALL:add_to_list:{{"list_name":"shopping","task_text":"{item}","priority":"medium"}}]'
            logger.info(f"🔧 AUTO-INJECTED tool call: {tool_call}")
            return tool_call + "\n" + response
    
    # Calendar patterns
    if any(word in message_lower for word in ["schedule", "create event", "add event", "calendar"]):
        # Would need more sophisticated parsing
        logger.warning(f"⚠️ Calendar action detected but no tool call generated: {message}")
    
    return response
```

Call it before parse_and_execute_code_or_tools:

```python
# Auto-inject tool calls if LLM didn't generate them
response = await auto_inject_tool_calls(response, msg.message, routing, actual_user_id)

# Parse and execute any tool calls in the response
original_response = response
response = await parse_and_execute_code_or_tools(response, actual_user_id)
```

This way, even if the LLM doesn't cooperate, we still execute the action!

