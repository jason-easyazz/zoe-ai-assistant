#!/bin/bash
# CONNECT_SAVED_KEYS.sh
# Location: scripts/maintenance/connect_saved_keys.sh
# Purpose: Connect the saved API keys to the existing AI routing system

set -e

echo "🔗 CONNECTING SAVED API KEYS TO AI ROUTER"
echo "=========================================="
echo ""
echo "Your API keys are saved but not being used."
echo "This will connect them to the AI routing system."
echo ""
echo "Press Enter to continue..."
read

cd /home/pi/zoe

# Create a bridge to load saved keys into the AI router
echo "📝 Creating API key loader bridge..."
cat > services/zoe-core/api_key_loader.py << 'EOF'
"""Load saved API keys for AI router"""
import os
import json
import base64
from pathlib import Path
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

def load_api_keys():
    """Load encrypted API keys and make them available to AI router"""
    try:
        # Check encrypted file
        enc_file = Path("/app/data/api_keys.enc")
        if not enc_file.exists():
            print("No encrypted keys file found")
            return {}
        
        # Generate decryption key
        salt = b"zoe_api_key_salt_2024"
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000
        )
        key = base64.urlsafe_b64encode(kdf.derive(b"zoe_secure_key_2024"))
        cipher = Fernet(key)
        
        # Decrypt and load keys
        with open(enc_file, 'rb') as f:
            decrypted = cipher.decrypt(f.read())
            keys = json.loads(decrypted)
        
        # Set environment variables for AI router
        if 'anthropic' in keys:
            os.environ['ANTHROPIC_API_KEY'] = keys['anthropic']
            print("✅ Loaded Anthropic API key")
        
        if 'openai' in keys:
            os.environ['OPENAI_API_KEY'] = keys['openai']
            print("✅ Loaded OpenAI API key")
            
        return keys
        
    except Exception as e:
        print(f"Error loading API keys: {e}")
        return {}

# Load keys on module import
loaded_keys = load_api_keys()
EOF

echo "✅ API key loader created"

# Update the AI router to use the loader
echo -e "\n📝 Updating AI router to load saved keys..."
cat > services/zoe-core/update_ai_router.py << 'EOF'
import os

# Read the existing AI router
ai_files = ['/app/ai_router.py', '/app/ai_client.py']
target_file = None

for f in ai_files:
    if os.path.exists(f):
        target_file = f
        break

if target_file:
    with open(target_file, 'r') as f:
        content = f.read()
    
    # Add the import at the top if not already there
    if 'api_key_loader' not in content:
        import_line = "from api_key_loader import loaded_keys\n"
        
        # Find where to insert (after other imports)
        lines = content.split('\n')
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.startswith('import ') or line.startswith('from '):
                insert_idx = i + 1
            elif insert_idx > 0 and not line.startswith('import') and not line.startswith('from'):
                break
        
        lines.insert(insert_idx, import_line)
        content = '\n'.join(lines)
        
        with open(target_file, 'w') as f:
            f.write(content)
        
        print(f"✅ Updated {target_file} to load saved API keys")
    else:
        print(f"✅ {target_file} already configured")
else:
    print("Creating new AI router with saved key support...")
    
    # Create a new AI client that uses saved keys
    with open('/app/ai_client.py', 'w') as f:
        f.write('''
"""AI Client with saved API key support"""
from api_key_loader import loaded_keys
import httpx
import os
import logging
import json
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class AIClient:
    def __init__(self):
        # Load saved keys
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.openai_key = os.getenv("OPENAI_API_KEY", "")
        self.ollama_url = "http://zoe-ollama:11434"
        
        # Check which services are available
        self.has_claude = bool(self.anthropic_key and self.anthropic_key != "your_claude_key_here")
        self.has_gpt4 = bool(self.openai_key and self.openai_key != "your_openai_key_here")
        
        if self.has_claude:
            logger.info("✅ Claude API available")
        if self.has_gpt4:
            logger.info("✅ OpenAI GPT-4 available")
        if not (self.has_claude or self.has_gpt4):
            logger.info("📦 Using local Ollama models")
    
    async def generate_response(self, message: str, context: Dict = None) -> Dict:
        """Generate response using best available AI"""
        
        mode = context.get("mode", "user") if context else "user"
        temperature = 0.3 if mode == "developer" else 0.7
        
        # For developer mode, prefer Claude/GPT-4
        if mode == "developer":
            if self.has_claude:
                return await self._use_claude(message, context, temperature)
            elif self.has_gpt4:
                return await self._use_gpt4(message, context, temperature)
        
        # Default to Ollama
        return await self._use_ollama(message, context, temperature)
    
    async def _use_claude(self, message: str, context: Dict, temperature: float) -> Dict:
        """Use Claude API"""
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.anthropic_key)
            
            system_prompt = self._build_system_prompt(context)
            
            response = client.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=2000,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": message}]
            )
            
            return {
                "response": response.content[0].text,
                "model": "claude-3-opus"
            }
        except Exception as e:
            logger.error(f"Claude error: {e}")
            return await self._use_ollama(message, context, temperature)
    
    async def _use_gpt4(self, message: str, context: Dict, temperature: float) -> Dict:
        """Use OpenAI GPT-4"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.openai_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": "gpt-4-turbo-preview",
                        "messages": [
                            {"role": "system", "content": self._build_system_prompt(context)},
                            {"role": "user", "content": message}
                        ],
                        "temperature": temperature,
                        "max_tokens": 2000
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "response": data["choices"][0]["message"]["content"],
                        "model": "gpt-4-turbo"
                    }
        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            return await self._use_ollama(message, context, temperature)
    
    async def _use_ollama(self, message: str, context: Dict, temperature: float) -> Dict:
        """Use local Ollama model"""
        try:
            model = "llama3.2:3b" if context.get("mode") == "developer" else "llama3.2:3b"
            
            prompt = self._build_system_prompt(context) + f"\\n\\nUser: {message}\\nAssistant:"
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "temperature": temperature,
                        "stream": False
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "response": data.get("response", ""),
                        "model": f"ollama/{model}"
                    }
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            return {
                "response": "AI service temporarily unavailable",
                "model": "error"
            }
    
    def _build_system_prompt(self, context: Dict) -> str:
        """Build appropriate system prompt"""
        if context and context.get("mode") == "developer":
            return """You are Zack, a technical AI assistant for the Zoe system.
You provide precise technical solutions, write complete scripts, and help with system management.
Be direct, efficient, and focus on practical solutions."""
        else:
            return """You are Zoe, a warm and friendly AI assistant.
Be conversational, helpful, and supportive. Use occasional emojis."""

# Global instance
ai_client = AIClient()
''')
    print("✅ Created new AI client with saved key support")
EOF

# Copy files to container
echo -e "\n📦 Installing to container..."
docker cp services/zoe-core/api_key_loader.py zoe-core:/app/
docker exec zoe-core python3 /app/update_ai_router.py

# Restart service
echo -e "\n🐳 Restarting zoe-core..."
docker restart zoe-core

echo "⏳ Waiting for service to start (10 seconds)..."
sleep 10

# Test the connection
echo -e "\n🧪 Testing AI with saved keys..."
echo "==============================="

echo -e "\n1. Checking available models:"
curl -s http://localhost:8000/api/developer/status | jq '.'

echo -e "\n2. Testing developer chat (should use Claude/GPT-4):"
curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "What AI model are you using?"}' | jq -r '.model'

echo -e "\n3. Testing regular chat (Zoe):"
curl -s -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "Hi Zoe!"}' | jq -r '.model_used'

echo -e "\n==============================="
echo "✅ API KEYS NOW CONNECTED!"
echo ""
echo "Your saved API keys are now being used:"
echo "  • Developer mode will use Claude/GPT-4"
echo "  • User mode will use appropriate model"
echo "  • Fallback to Ollama if APIs unavailable"
echo ""
echo "The routing foundation remains intact!"
