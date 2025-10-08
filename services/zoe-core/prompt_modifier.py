"""
Minimal Prompt Modifier - Forces code generation for Zack
"""

def modify_prompt_for_code(original_message: str, mode: str = "user") -> str:
    """Only modify prompts when in developer mode and code is needed"""
    
    if mode != "developer":
        return original_message
    
    # Keywords that indicate code generation needed
    code_indicators = ['build', 'create', 'implement', 'fix', 'generate', 'write', 'make', 'add', 'endpoint', 'api', 'router', 'function', 'class', 'script']
    
    # Check if this needs code forcing
    needs_code = any(indicator in original_message.lower() for indicator in code_indicators)
    
    if not needs_code:
        return original_message
    
    # Wrap the message to force code output
    return f"""CRITICAL INSTRUCTION: Output ONLY actual, executable code.
No explanations. No advice. No descriptions.
Just the complete, working code file.

User request: {original_message}

Response format MUST be:
```python
# File: /app/routers/[name].py
[COMPLETE WORKING CODE HERE]
```

Now output the ACTUAL CODE:"""
