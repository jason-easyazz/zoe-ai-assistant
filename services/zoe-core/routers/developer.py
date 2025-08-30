"""
Developer Router - Simple working version
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import sys
import os
from datetime import datetime

sys.path.append('/app')

router = APIRouter(prefix="/api/developer", tags=["developer"])

class DeveloperChat(BaseModel):
    message: str

@router.get("/status")
async def get_status():
    return {
        "status": "operational",
        "mode": "code-generator",
        "personality": "Zack",
        "timestamp": datetime.now().isoformat()
    }

@router.post("/chat")
async def developer_chat(request: DeveloperChat):
    """Generate code using AI"""
    
    try:
        # Import working AI
        from ai_client_complete import get_ai_response
        
        # Check if this needs code generation
        code_keywords = ['build', 'create', 'implement', 'fix', 'endpoint', 'api', 'function', 'class', 'script']
        needs_code = any(word in request.message.lower() for word in code_keywords)
        
        if needs_code:
            # Build code-forcing prompt WITHOUT format specifiers
            prompt = "You are Zack, a developer AI. OUTPUT ONLY CODE.\n\n"
            prompt += "User wants: " + request.message + "\n\n"
            prompt += "Requirements:\n"
            prompt += "1. Start with a comment: # File: /app/routers/feature.py\n"
            prompt += "2. Include ALL imports\n"
            prompt += "3. Create COMPLETE, WORKING code\n"
            prompt += "4. NO explanations outside code comments\n\n"
            prompt += "NOW OUTPUT THE COMPLETE CODE:"
        else:
            prompt = "You are Zack, a technical AI. " + request.message
        
        # Get AI response
        response = await get_ai_response(prompt, {"mode": "developer", "temperature": 0.2})
        
        # Return response
        if response:
            return {"response": response, "success": True}
        else:
            return {"response": "AI returned no response", "success": False}
            
    except ImportError as e:
        # Import error - provide template
        template = "# File: /app/routers/generated.py\n"
        template += "from fastapi import APIRouter\n\n"
        template += "router = APIRouter(prefix='/api/generated')\n\n"
        template += "@router.get('/')\n"
        template += "async def generated():\n"
        template += "    # TODO: Implement: " + request.message[:50] + "\n"
        template += "    return {'status': 'template'}\n"
        
        return {
            "response": template,
            "error": "Import error: " + str(e),
            "template": True
        }
        
    except Exception as e:
        # Any other error
        return {
            "response": "Error occurred",
            "error": str(e),
            "success": False
        }

@router.get("/test")
async def test_ai():
    """Test if AI is working"""
    try:
        from ai_client_complete import get_ai_response
        response = await get_ai_response("Say 'working'", {"mode": "developer"})
        return {"ai_status": "working", "response": response[:100] if response else None}
    except Exception as e:
        return {"ai_status": "error", "error": str(e)}
