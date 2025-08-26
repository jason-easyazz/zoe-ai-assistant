"""
Claude Integration with Multi-Model Fallback
Priority: Claude API -> Ollama 3b -> Ollama 1b
"""
import os
import json
import httpx
import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class ClaudeIntegration:
    def __init__(self):
        self.claude_api_key = os.getenv("CLAUDE_API_KEY", "")
        self.ollama_host = os.getenv("OLLAMA_HOST", "http://zoe-ollama:11434")
        self.has_claude = bool(self.claude_api_key)
        
        # Model configuration
        self.models = {
            "claude": "claude-3-5-sonnet-20241022",
            "ollama_3b": "llama3.2:3b",
            "ollama_1b": "llama3.2:1b"
        }
        
        logger.info(f"Claude Integration initialized. Has Claude API: {self.has_claude}")
        
    async def generate_response(
        self, 
        prompt: str, 
        context: Optional[Dict[str, Any]] = None,
        prefer_model: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate response using best available model"""
        # Determine model based on request complexity
        model_choice = self._select_model(prompt, prefer_model)
        
        try:
            if model_choice == "claude" and self.has_claude:
                return await self._claude_request(prompt, context)
            else:
                # Fallback to Ollama
                return await self._ollama_request(prompt, context, model_choice)
        except Exception as e:
            logger.error(f"Model {model_choice} failed: {e}")
            # Try fallback
            if model_choice != "ollama_1b":
                return await self._ollama_request(prompt, context, "ollama_1b")
            # If all fails, return a simple response
            return {
                "response": "I'm having trouble processing your request. Please check the system logs.",
                "model": "fallback",
                "error": str(e)
            }
    
    def _select_model(self, prompt: str, prefer_model: Optional[str]) -> str:
        """Select appropriate model based on request complexity"""
        if prefer_model:
            return prefer_model
            
        # Simple heuristic based on prompt length and keywords
        prompt_lower = prompt.lower()
        
        # Complex requests for Claude
        complex_keywords = ["analyze", "architecture", "implement", "debug", "optimize"]
        if any(keyword in prompt_lower for keyword in complex_keywords):
            return "claude" if self.has_claude else "ollama_3b"
        
        # Medium complexity for 3b
        if len(prompt) > 100:
            return "ollama_3b"
        
        # Simple requests for 1b
        return "ollama_1b"
    
    async def _claude_request(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Make request to Claude API"""
        headers = {
            "x-api-key": self.claude_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        
        # Build system prompt with context
        system_prompt = self._build_system_prompt(context)
        
        data = {
            "model": self.models["claude"],
            "max_tokens": 4096,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=data,
                timeout=30.0
            )
            response.raise_for_status()
            result = response.json()
            
            return {
                "response": result["content"][0]["text"],
                "model": "claude",
                "tokens": result.get("usage", {})
            }
    
    async def _ollama_request(
        self, 
        prompt: str, 
        context: Dict[str, Any],
        model_key: str
    ) -> Dict[str, Any]:
        """Make request to Ollama"""
        model_name = self.models.get(model_key, self.models["ollama_1b"])
        
        # Build context-aware prompt
        full_prompt = self._build_ollama_prompt(prompt, context, model_key)
        
        data = {
            "model": model_name,
            "prompt": full_prompt,
            "stream": False
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.ollama_host}/api/generate",
                    json=data,
                    timeout=30.0
                )
                response.raise_for_status()
                result = response.json()
                
                return {
                    "response": result.get("response", "No response generated"),
                    "model": model_key,
                    "tokens": {
                        "eval_count": result.get("eval_count", 0),
                        "prompt_eval_count": result.get("prompt_eval_count", 0)
                    }
                }
        except Exception as e:
            logger.error(f"Ollama request failed: {e}")
            # Return a fallback response
            return {
                "response": "I'm currently unable to process complex requests. Please try again.",
                "model": "error",
                "error": str(e)
            }
    
    def _build_system_prompt(self, context: Dict[str, Any]) -> str:
        """Build system prompt for Claude"""
        context_str = json.dumps(context, indent=2) if context else 'No specific context'
        return f"""You are Zack, the technical assistant for the Zoe AI system.
You are helping with development, debugging, and system management.

Current System Context:
{context_str}

Provide clear, actionable responses with code examples when appropriate.
Be concise but thorough. Focus on practical solutions."""
    
    def _build_ollama_prompt(self, prompt: str, context: Dict[str, Any], model: str) -> str:
        """Build prompt for Ollama models"""
        if model == "ollama_3b":
            prefix = "You are a helpful AI assistant for the Zoe system. "
        else:
            prefix = "You are Zoe's assistant. "
        
        if context:
            return f"{prefix}\nContext: {json.dumps(context)}\n\nUser: {prompt}\n\nAssistant:"
        return f"{prefix}\n\nUser: {prompt}\n\nAssistant:"

# Global instance
claude = ClaudeIntegration()
