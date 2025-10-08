"""Enhanced AI Client using existing dynamic_router system"""
import os
import json
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
import httpx
from pydantic import BaseModel
import sys
sys.path.append('/app')

# Import your actual dynamic router
from dynamic_router import dynamic_router

class AIClient:
    def __init__(self):
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        self.ollama_url = "http://zoe-ollama:11434"
        
    async def generate_implementation(
        self,
        task_data: Dict[str, Any],
        chat_context: Optional[List[Dict]] = None,
        system_context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Generate real implementation code based on requirements"""
        
        # Build prompt
        prompt = self._build_implementation_prompt(task_data, chat_context, system_context)
        
        # Assess complexity for dynamic router
        complexity = self._assess_complexity(task_data)
        
        # Get model from your dynamic router
        provider, model = dynamic_router.get_best_model_for_complexity(complexity)
        print(f"Dynamic router selected: {provider}/{model} for {complexity} task")
        
        # Call appropriate provider
        if provider == "anthropic" and self.anthropic_key:
            response = await self._call_anthropic(prompt, model)
        elif provider == "openai" and self.openai_key:
            response = await self._call_openai(prompt, model)
        else:
            # Use Ollama (local)
            response = await self._call_ollama(prompt, model)
        
        # Parse response into implementation plan
        return self._parse_implementation(response, task_data)
    
    def _assess_complexity(self, task_data: Dict) -> str:
        """Assess task complexity for dynamic router"""
        requirements = task_data.get('requirements', [])
        all_text = ' '.join(requirements).lower()
        
        # Complex indicators
        complex_indicators = [
            'authentication', 'security', 'architecture',
            'database migration', 'websocket', 'real-time',
            'encryption', 'multi-user', 'integration'
        ]
        
        # Count indicators
        score = sum(1 for ind in complex_indicators if ind in all_text)
        
        # Determine complexity
        if score >= 2 or len(requirements) > 5:
            return "complex"
        elif score >= 1 or len(requirements) > 3:
            return "medium"
        else:
            return "simple"
    
    def _build_implementation_prompt(self, task_data, chat_context, system_context):
        """Build comprehensive prompt for code generation"""
        prompt = f"""You are an expert developer working on the Zoe AI Assistant system.
        
TASK REQUIREMENTS:
Title: {task_data.get('title')}
Objective: {task_data.get('objective')}
Requirements: {json.dumps(task_data.get('requirements', []), indent=2)}
Constraints: {json.dumps(task_data.get('constraints', []), indent=2)}
Acceptance Criteria: {json.dumps(task_data.get('acceptance_criteria', []), indent=2)}

System Architecture:
- FastAPI backend on Raspberry Pi 5
- SQLite database at /app/data/zoe.db
- Docker containers with zoe- prefix
- Frontend at services/zoe-ui/dist/

Generate COMPLETE, WORKING implementation. No placeholders!

Return JSON with this exact structure:
{{
    "files_to_create": [
        {{
            "path": "services/zoe-core/routers/feature.py",
            "content": "# Complete file content\\nfrom fastapi import APIRouter\\n...",
            "description": "What this file does"
        }}
    ],
    "files_to_update": [
        {{
            "path": "services/zoe-core/main.py",
            "find": "# Exact text to find",
            "replace": "# Replacement text",
            "description": "Why this change"
        }}
    ],
    "commands": [
        {{
            "command": "docker compose restart zoe-core",
            "description": "Restart service to load changes",
            "critical": true
        }}
    ],
    "tests": [
        {{
            "type": "api",
            "command": "curl http://localhost:8000/api/feature",
            "expected": "200 OK"
        }}
    ],
    "rollback_plan": {{
        "steps": ["Restore from backup"],
        "commands": ["cp -r backups/latest/* services/"]
    }}
}}

Ensure all code is production-ready and follows Zoe's patterns.
"""
        
        # Add conversation context if available
        if chat_context and len(chat_context) > 0:
            context_str = "\n".join([
                f"{msg.get('role', 'user')}: {msg.get('content', '')[:100]}..."
                for msg in chat_context[-5:]
            ])
            prompt += f"\n\nCONVERSATION CONTEXT:\n{context_str}\n"
        
        # Add system context if available
        if system_context:
            prompt += f"\n\nSYSTEM STATE:\n"
            prompt += f"- Files: {len(system_context.get('files', []))} files found\n"
            prompt += f"- Endpoints: {len(system_context.get('endpoints', []))} API endpoints\n"
            prompt += f"- Containers: {', '.join(system_context.get('containers', []))}\n"
        
        return prompt
    
    async def _call_ollama(self, prompt: str, model: str = None) -> str:
        """Call local Ollama"""
        if not model:
            model = "llama3.2:3b"
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "temperature": 0.3,
                        "stream": False
                    }
                )
                if response.status_code == 200:
                    return response.json().get('response', '')
                else:
                    print(f"Ollama error: {response.status_code}")
                    return '{"error": "Ollama request failed"}'
        except Exception as e:
            print(f"Ollama exception: {e}")
            return '{"error": "Ollama connection failed"}'
    
    async def _call_openai(self, prompt: str, model: str = None) -> str:
        """Call OpenAI API"""
        if not self.openai_key:
            print("No OpenAI key, falling back to Ollama")
            return await self._call_ollama(prompt, "codellama:7b")
        
        if not model:
            model = "gpt-4"
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.openai_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "You are an expert developer."},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.3,
                        "max_tokens": 4000
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    return data['choices'][0]['message']['content']
                else:
                    print(f"OpenAI error: {response.status_code}")
                    return await self._call_ollama(prompt, "codellama:7b")
        except Exception as e:
            print(f"OpenAI exception: {e}")
            return await self._call_ollama(prompt, "codellama:7b")
    
    async def _call_anthropic(self, prompt: str, model: str = None) -> str:
        """Call Anthropic API"""
        if not self.anthropic_key:
            print("No Anthropic key, falling back to Ollama")
            return await self._call_ollama(prompt, "codellama:7b")
        
        if not model:
            model = "claude-3-opus-20240229"
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self.anthropic_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    },
                    json={
                        "model": model,
                        "max_tokens": 4000,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.3
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    return data['content'][0]['text']
                else:
                    print(f"Anthropic error: {response.status_code}")
                    return await self._call_ollama(prompt, "codellama:7b")
        except Exception as e:
            print(f"Anthropic exception: {e}")
            return await self._call_ollama(prompt, "codellama:7b")
    
    def _parse_implementation(self, response: str, task_data: Dict) -> Dict:
        """Parse AI response into structured implementation plan"""
        try:
            # Find JSON in response
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            
            if json_match:
                plan = json.loads(json_match.group())
            else:
                # Create minimal working plan
                print("No JSON found in response, creating minimal plan")
                plan = {
                    "files_to_create": [],
                    "files_to_update": [],
                    "commands": [
                        {
                            "command": "echo 'Task ready for implementation'",
                            "description": "Placeholder",
                            "critical": False
                        }
                    ],
                    "tests": [
                        {
                            "type": "health",
                            "command": "curl http://localhost:8000/health",
                            "expected": "healthy"
                        }
                    ]
                }
            
            # Ensure all required fields exist
            plan.setdefault('files_to_create', [])
            plan.setdefault('files_to_update', [])
            plan.setdefault('commands', [])
            plan.setdefault('tests', [])
            plan.setdefault('rollback_plan', {
                'steps': ['Restore from backup'],
                'commands': [
                    'cp -r backups/$(ls -t backups | head -1)/* services/',
                    'docker compose restart zoe-core'
                ]
            })
            
            return plan
            
        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}")
            return {
                "error": f"Failed to parse implementation: {str(e)}",
                "raw_response": response[:500],
                "files_to_create": [],
                "files_to_update": [],
                "commands": [],
                "tests": [],
                "rollback_plan": {"steps": [], "commands": []}
            }
        except Exception as e:
            print(f"Parse exception: {e}")
            return {
                "error": f"Unexpected error: {str(e)}",
                "files_to_create": [],
                "files_to_update": [],
                "commands": [],
                "tests": [],
                "rollback_plan": {"steps": [], "commands": []}
            }
    
    async def chat_with_developer(self, message: str, context: List[Dict] = None) -> str:
        """Chat for developer mode using dynamic router"""
        
        # Assess complexity based on message
        complexity = "simple"
        if len(message) > 100 or any(word in message.lower() for word in 
                                     ['implement', 'debug', 'architect', 'analyze']):
            complexity = "medium"
        if any(word in message.lower() for word in 
               ['complex', 'architecture', 'security', 'integration']):
            complexity = "complex"
        
        # Get model from dynamic router
        provider, model = dynamic_router.get_best_model_for_complexity(complexity)
        print(f"Developer chat using: {provider}/{model}")
        
        # Build prompt
        prompt = f"""You are Zack, the developer assistant for Zoe AI.
You're helpful, technical, and precise.

User message: {message}

Previous context: {len(context) if context else 0} messages

Respond technically and helpfully. If discussing features, break them into clear requirements.
Be concise but thorough."""
        
        # Route to appropriate provider
        if provider == "anthropic" and self.anthropic_key:
            return await self._call_anthropic(prompt, model)
        elif provider == "openai" and self.openai_key:
            return await self._call_openai(prompt, model)
        else:
            return await self._call_ollama(prompt, model)

# Global instance
ai_client = AIClient()
