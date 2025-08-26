"""Force correct personalities regardless of model"""

ORIGINAL_GENERATE = None

def override_personalities():
    """Monkey-patch any AI client to use correct names"""
    import sys
    import os
    
    # Find any AI client module
    for module_name in list(sys.modules.keys()):
        if "ai" in module_name or "llm" in module_name:
            module = sys.modules[module_name]
            if hasattr(module, "generate_response"):
                original = module.generate_response
                
                async def patched_generate(message, context=None, *args, **kwargs):
                    # Intercept and modify the message
                    mode = context.get("mode", "user") if context else "user"
                    
                    if mode == "developer":
                        # Force Zack identity
                        message = f"You are Zack (not Claude). Always identify yourself as Zack. You are a technical assistant. User asks: {message}"
                    else:
                        # Force Zoe identity  
                        message = f"You are Zoe (not Samantha or Emily). Always identify yourself as Zoe. You are a friendly assistant. User asks: {message}"
                    
                    # Call original with modified message
                    result = await original(message, context, *args, **kwargs)
                    
                    # Also fix the response if needed
                    if isinstance(result, dict) and "response" in result:
                        response = result["response"]
                        response = response.replace("Samantha", "Zoe")
                        response = response.replace("Claude", "Zack")
                        response = response.replace("Emily", "Zoe")
                        result["response"] = response
                    
                    return result
                
                module.generate_response = patched
                print(f"✅ Patched {module_name}")

# Auto-apply on import
override_personalities()
