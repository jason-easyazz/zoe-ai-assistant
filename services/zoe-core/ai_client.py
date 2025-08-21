"""
AI Client Module - Handles both User Zoe and Developer Claude personalities
"""

import httpx
import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# User Zoe System Prompt
USER_SYSTEM_PROMPT = """
You are Zoe, a warm and friendly AI companion.

Your personality:
- Cheerful, empathetic, and conversational
- You speak naturally, like a helpful friend
- You remember personal details and preferences
- You're encouraging and supportive

Your capabilities:
- Help with daily planning and organization
- Manage calendar events and reminders
- Track tasks and shopping lists
- Provide emotional support and encouragement
- Share interesting facts and conversations

Your approach:
- Use casual, friendly language
- Avoid technical jargon
- Focus on being helpful and understanding
- Add personality with occasional emojis
- Be proactive with suggestions

Remember: You're a companion, not just an assistant. Build a relationship with the user.
"""

async def get_ai_response(
    message: str,
    system_prompt: str = USER_SYSTEM_PROMPT,
    context: Optional[Dict[str, Any]] = None,
    temperature: float = 0.7
) -> str:
    """
    Get AI response with configurable personality
    
    Args:
        message: User message
        system_prompt: System prompt defining AI personality
        context: Additional context for the AI
        temperature: Response randomness (0.0-1.0)
    
    Returns:
        AI response as string
    """
    try:
        # Check if we're in developer mode
        is_developer = context and context.get("mode") == "developer"
        
        # Prepare the full prompt
        full_prompt = system_prompt + "\n\n"
        
        # Add context if provided
        if context:
            if "system_status" in context:
                full_prompt += f"System Status: {json.dumps(context['system_status'])}\n"
            if "chat_history" in context:
                full_prompt += "Recent conversation:\n"
                for msg in context.get("chat_history", [])[-3:]:
                    full_prompt += f"- {msg.get('sender', 'unknown')}: {msg.get('content', '')[:100]}...\n"
            full_prompt += "\n"
        
        full_prompt += f"User: {message}\nAssistant:"
        
        # Try to use Ollama first
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "http://zoe-ollama:11434/api/generate",
                    json={
                        "model": "llama3.2:3b",
                        "prompt": full_prompt,
                        "temperature": temperature,
                        "stream": False
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return data.get("response", "I'm having trouble responding right now.")
        except Exception as ollama_error:
            logger.warning(f"Ollama unavailable: {ollama_error}")
        
        # Fallback responses based on mode
        if is_developer:
            return generate_developer_fallback(message)
        else:
            return generate_user_fallback(message)
            
    except Exception as e:
        logger.error(f"AI client error: {e}")
        return "I encountered an error. Please check the system logs for details."

def generate_developer_fallback(message: str) -> str:
    """Generate helpful developer response when AI is offline"""
    message_lower = message.lower()
    
    if "error" in message_lower or "fix" in message_lower:
        return """I'm currently offline, but here's a diagnostic script:

```bash
#!/bin/bash
# System diagnostic script
echo "🔍 Running diagnostics..."

# Check containers
docker ps --format "table {{.Names}}\t{{.Status}}"

# Check API
curl -s http://localhost:8000/health | jq '.'

# Check logs
docker logs zoe-core --tail 20

# Check resources
free -m
df -h
```

Run this script to diagnose the issue."""
    
    elif "backup" in message_lower:
        return """Here's a backup script:

```bash
#!/bin/bash
# Create timestamped backup
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/home/pi/zoe/backups/$TIMESTAMP"

mkdir -p $BACKUP_DIR
cp -r /home/pi/zoe/services $BACKUP_DIR/
cp /home/pi/zoe/docker-compose.yml $BACKUP_DIR/
echo "✅ Backup created at $BACKUP_DIR"
```"""
    
    else:
        return "I'm currently offline, but you can check system status with: `docker ps` and `curl http://localhost:8000/health`"

def generate_user_fallback(message: str) -> str:
    """Generate friendly response when AI is offline"""
    return "Hi! I'm temporarily offline, but I'll be back soon. In the meantime, you can check my status at the top of the screen. 💙"
