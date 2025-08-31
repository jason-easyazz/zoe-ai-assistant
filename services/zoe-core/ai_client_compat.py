"""
MINIMAL COMPATIBILITY WRAPPER
Just adds missing function names without changing ANY logic
"""
import sys
import logging
from typing import Dict, Optional

sys.path.append('/app')
logger = logging.getLogger(__name__)

# Import the EXISTING working system - try multiple sources
_imported_from = None

try:
    # First try ai_client_complete (has RouteLLM)
    from ai_client_complete import *
    _imported_from = "ai_client_complete"
    logger.info("✅ Using ai_client_complete with RouteLLM")
except ImportError:
    try:
        # Try regular ai_client
        from ai_client import *
        _imported_from = "ai_client"
        logger.info("✅ Using ai_client")
    except ImportError:
        logger.error("❌ No existing AI client found")
        _imported_from = None

# Add compatibility functions ONLY if missing
if _imported_from:
    # Check what functions already exist
    import ai_client_complete if _imported_from == "ai_client_complete" else ai_client as ai_module
    
    # Add get_ai_response if missing
    if not hasattr(ai_module, 'get_ai_response'):
        logger.info("Adding get_ai_response compatibility")
        
        async def get_ai_response(message: str, context: Dict = None) -> str:
            """Compatibility wrapper for get_ai_response"""
            context = context or {}
            
            # Try different function names that might exist
            if hasattr(ai_module, 'generate_response'):
                result = await ai_module.generate_response(message, context)
            elif hasattr(ai_module, 'ai_client'):
                result = await ai_module.ai_client.generate_response(message, context)
            elif hasattr(ai_module, 'route_request'):
                result = await ai_module.route_request(message, context)
            else:
                # Fallback to Ollama directly
                import httpx
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "http://zoe-ollama:11434/api/generate",
                        json={
                            "model": "llama3.2:3b",
                            "prompt": f"User: {message}\nAssistant:",
                            "stream": False
                        }
                    )
                    if response.status_code == 200:
                        result = response.json().get("response", "Processing...")
                    else:
                        result = "AI temporarily unavailable"
            
            # Handle dict responses
            if isinstance(result, dict):
                return result.get('response', result.get('text', str(result)))
            return str(result)
    
    # Add other compatibility names
    if not hasattr(ai_module, 'generate_ai_response'):
        generate_ai_response = get_ai_response
    
    if not hasattr(ai_module, 'generate_response'):
        generate_response = get_ai_response

else:
    # Emergency fallback if nothing exists
    logger.error("Creating emergency fallback")
    
    async def get_ai_response(message: str, context: Dict = None) -> str:
        """Emergency fallback to Ollama"""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "http://zoe-ollama:11434/api/generate",
                    json={
                        "model": "llama3.2:3b",
                        "prompt": f"User: {message}\nAssistant:",
                        "stream": False
                    }
                )
                if response.status_code == 200:
                    return response.json().get("response", "Processing...")
        except:
            pass
        return "AI service temporarily unavailable"
    
    generate_response = get_ai_response
    generate_ai_response = get_ai_response

logger.info(f"Compatibility wrapper ready. Imported from: {_imported_from}")
