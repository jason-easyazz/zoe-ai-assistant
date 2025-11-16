# Model-Adaptive Prompting System

## Problem Solved
Different LLMs have different strengths in function calling:
- **Hermes-3**: Native function calling support (trained specifically for it)
- **Qwen 2.5**: Strong function calling (trained on tool use)
- **Gemma 3**: Needs heavy examples (not trained for function calling)
- **Phi/Llama**: Moderate - need clear patterns

## Solution: Adaptive Prompts, Consistent Output

### The Key Insight
**ALL models produce the SAME output format:**
```
[TOOL_CALL:function_name:{"param":"value"}]
```

**But INSTRUCTIONS vary by model:**

### Hermes-3 / Qwen 2.5
```
"You have access to functions..."
- Concise function list
- Structured approach
- Relies on training
```

### Gemma 3
```
"PATTERN MATCHING:
If user says 'add X' → [TOOL_CALL:add_to_list:...]"
- Heavy examples
- Explicit pattern matching
- Step-by-step
```

### Phi / Llama / Default
```
"Use this EXACT format..."
- Clear examples
- Mandatory rules
- Simple instructions
```

## Implementation

See `routers/chat.py`:
- `get_model_adaptive_action_prompt(model_name)` - Detects model, generates appropriate prompt
- Used in both streaming and non-streaming paths
- Logging shows which model variant is used

## Benefits

1. **Model Agnostic**: Easy to switch models in `model_config.py`
2. **Optimal Performance**: Each model gets instructions it understands best
3. **Consistent Output**: Parser always expects same format
4. **Easy to Extend**: Add new model in one function

## Testing

```bash
# Test with Hermes-3
curl -X POST "http://localhost:8000/api/chat?stream=false" \
  -H "X-Session-ID: dev-localhost" \
  -d '{"message": "add bread", "user_id": "test"}'

# Switch to Gemma (in model_config.py: self.current_model = "gemma3n-e2b-gpu-fixed")
# Test again - different prompt, same output format!
```

## Future: Multi-Model Comparison

Could run SAME request through multiple models and compare:
- Which generates tool calls most reliably?
- Which is fastest?
- Which gives best natural language alongside the tool call?

This adaptive system makes that comparison fair and easy!

