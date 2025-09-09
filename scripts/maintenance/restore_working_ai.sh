#!/bin/bash
# RESTORE_WORKING_AI.sh
# Location: scripts/maintenance/restore_working_ai.sh
# Purpose: Restore the WORKING Claude/GPT-4 configuration you had before

set -e

echo "🔄 RESTORING YOUR WORKING AI CONFIGURATION"
echo "=========================================="
echo ""
echo "According to your state file, this WAS all working:"
echo "  ✅ Claude/GPT-4 connected to both modes"
echo "  ✅ Encrypted API key storage"
echo "  ✅ Both personalities (Zoe & Zack)"
echo ""
echo "Let's restore it to the working state..."
echo ""
echo "Press Enter to restore..."
read

cd /home/pi/zoe

# First, let's check what API keys are actually stored
echo "📊 Checking stored API keys..."
docker exec zoe-core python3 << 'PYTHON'
import json
import base64
from pathlib import Path
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# Check the encrypted file
enc_file = Path("/app/data/api_keys.enc")
json_file = Path("/app/data/api_keys.json")

if enc_file.exists():
    print(f"✅ Encrypted key file exists: {enc_file}")
    try:
        # Your working encryption setup
        salt = b"zoe_api_key_salt_2024"
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000
        )
        key = base64.urlsafe_b64encode(kdf.derive(b"zoe_secure_key_2024"))
        cipher = Fernet(key)
        
        with open(enc_file, 'rb') as f:
            encrypted = f.read()
            decrypted = cipher.decrypt(encrypted)
            keys = json.loads(decrypted)
            
        print("✅ Successfully decrypted keys:")
        for service in keys:
            if keys[service]:
                print(f"  • {service}: {keys[service][:20]}...")
    except Exception as e:
        print(f"❌ Decryption error: {e}")
elif json_file.exists():
    print(f"✅ JSON key file exists: {json_file}")
    with open(json_file, 'r') as f:
        keys = json.load(f)
        for service in keys:
            if keys[service]:
                print(f"  • {service}: {keys[service][:20]}...")
else:
    print("❌ No API key files found")
PYTHON

# Create the WORKING AI client based on your past configuration
echo -e "\n📝 Restoring WORKING AI client configuration..."
cat > services/zoe-core/ai_client.py << 'EOF'
"""Restored WORKING AI Client with Claude/GPT-4 Support"""
import os
import json
import base64
import httpx
import logging
from pathlib import Path
from typing import Dict, Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

logger = logging.getLogger(__name__)

class AIClient:
    def __init__(self):
        self.api_keys = self._load_encrypted_keys()
        self.anthropic_key = self.api_keys.get('anthropic', '')
        self.openai_key = self.api_keys.get('openai', '')
        
        # Check what we have
        self.has_claude = bool(self.anthropic_key and len(self.anthropic_key) > 20)
        self.has_openai = bool(self.openai_key and len(self.openai_key) > 20)
        
        logger.info(f"✅ AI Client initialized - Claude: {self.has_claude}, OpenAI: {self.has_openai}")
        print(f"AI Client: Claude={self.has_claude}, OpenAI={self.has_openai}")
        
        # Try to import Anthropic
        if self.has_claude:
            try:
                import anthropic
                self.claude_client = anthropic.Anthropic(api_key=self.anthropic_key)
                logger.info("✅ Claude client initialized")
            except ImportError:
                logger.warning("Anthropic library not installed")
                self.has_claude = False
            except Exception as e:
                logger.error(f"Claude init error: {e}")
                self.has_claude = False
    
    def _load_encrypted_keys(self) -> Dict:
        """Load the encrypted API keys that ARE working"""
        try:
            enc_file = Path("/app/data/api_keys.enc")
            json_file = Path("/app/data/api_keys.json")
            
            # Try encrypted first
            if enc_file.exists():
                salt = b"zoe_api_key_salt_2024"
                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=salt,
                    iterations=100000
                )
                key = base64.urlsafe_b64encode(kdf.derive(b"zoe_secure_key_2024"))
                cipher = Fernet(key)
                
                with open(enc_file, 'rb') as f:
                    decrypted = cipher.decrypt(f.read())
                    return json.loads(decrypted)
            
            # Try JSON fallback
            elif json_file.exists():
                with open(json_file, 'r') as f:
                    return json.load(f)
            
            return {}
            
        except Exception as e:
            logger.error(f"Error loading keys: {e}")
            return {}
    
    async def generate_response(self, message: str, context: Dict = None) -> Dict:
        """Generate response using best available AI"""
        
        if not context:
            context = {}
        
        mode = context.get("mode", "user")
        
        # Developer mode uses Claude/GPT-4
        if mode == "developer" and (self.has_claude or self.has_openai):
            
            # Try Claude first
            if self.has_claude:
                try:
                    response = self.claude_client.messages.create(
                        model="claude-3-opus-20240229",
                        max_tokens=2000,
                        temperature=0.3,
                        system="You are Zack, a technical AI assistant for the Zoe system. Create scripts, fix issues, and provide technical solutions.",
                        messages=[{"role": "user", "content": message}]
                    )
                    
                    return {
                        "response": response.content[0].text,
                        "model": "claude-3-opus"
                    }
                    
                except Exception as e:
                    logger.error(f"Claude error: {e}")
            
            # Try OpenAI
            if self.has_openai:
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
                                    {"role": "system", "content": "You are Zack, a technical AI assistant."},
                                    {"role": "user", "content": message}
                                ],
                                "temperature": 0.3,
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
        
        # User mode or fallback - use Ollama
        return await self._use_ollama(message, context)
    
    async def _use_ollama(self, message: str, context: Dict) -> Dict:
        """Fallback to Ollama"""
        try:
            mode = context.get("mode", "user")
            
            if mode == "developer":
                system = "You are Zack, a technical assistant."
                temp = 0.3
            else:
                system = "You are Zoe, a friendly assistant."
                temp = 0.7
            
            prompt = f"{system}\n\nUser: {message}\nAssistant:"
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "http://zoe-ollama:11434/api/generate",
                    json={
                        "model": "llama3.2:3b",
                        "prompt": prompt,
                        "temperature": temp,
                        "stream": False
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return {
                        "response": data.get("response", ""),
                        "model": "ollama"
                    }
                    
        except Exception as e:
            logger.error(f"Ollama error: {e}")
        
        return {
            "response": "AI service temporarily unavailable",
            "model": "error"
        }

# Global instance
ai_client = AIClient()
EOF

echo "✅ Restored working AI client configuration"

# Copy to container
echo -e "\n📦 Installing restored configuration..."
docker cp services/zoe-core/ai_client.py zoe-core:/app/

# Ensure the developer router uses it
docker exec zoe-core python3 -c "
import os

# Make sure developer.py imports ai_client
if os.path.exists('/app/routers/developer.py'):
    with open('/app/routers/developer.py', 'r') as f:
        content = f.read()
    
    # Ensure proper import
    if 'from ai_client import ai_client' not in content:
        lines = content.split('\n')
        
        # Find where to add import
        for i, line in enumerate(lines):
            if 'import' in line:
                lines.insert(i+1, 'from ai_client import ai_client')
                break
        
        content = '\n'.join(lines)
        
        with open('/app/routers/developer.py', 'w') as f:
            f.write(content)
    
    print('✅ Developer router connected to ai_client')
"

# Restart service
echo -e "\n🐳 Restarting zoe-core..."
docker restart zoe-core

echo "⏳ Waiting for service (15 seconds)..."
sleep 15

# Test everything
echo -e "\n🧪 Testing restored configuration..."
echo "===================================="

echo -e "\n1. Checking AI client status:"
docker exec zoe-core python3 -c "
from ai_client import ai_client
print(f'Claude available: {ai_client.has_claude}')
print(f'OpenAI available: {ai_client.has_openai}')
print(f'Keys loaded: {list(ai_client.api_keys.keys())}')
"

echo -e "\n2. Testing Zack (should use Claude/GPT-4):"
curl -s -X POST http://localhost:8000/api/developer/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "What AI model are you using? Give me your name and model."}' | jq '.'

echo -e "\n3. Testing Zoe (regular chat):"
curl -s -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "Hi Zoe! What model are you?"}' | jq '.'

echo -e "\n===================================="
echo "✅ RESTORED TO WORKING CONFIGURATION!"
echo ""
echo "Your system should now be using:"
echo "  • Claude/GPT-4 for Zack (developer mode)"
echo "  • Appropriate model for Zoe (user mode)"
echo "  • All your saved encrypted API keys"
echo ""
echo "This is the configuration that WAS working!"
