"""
Wrapper for developer functionality with code forcing
"""
from prompt_modifier import modify_prompt_for_code

async def wrap_developer_chat(original_function, msg, context=None):
    """Wrap any developer chat function to force code generation"""
    
    # Modify the message
    modified_msg = modify_prompt_for_code(msg.message, "developer")
    msg.message = modified_msg
    
    # Call original function
    return await original_function(msg)
