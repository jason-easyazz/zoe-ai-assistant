"""Fallback AI client for testing"""
import httpx
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class SimpleAI:
    async def generate_response(self, message: str, context: Dict = None) -> Dict:
        """Simple Ollama-only response"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    'http://zoe-ollama:11434/api/generate',
                    json={
                        'model': 'llama3.2:3b',
                        'prompt': f'User: {message}\nAssistant:',
                        'stream': False
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    return {
                        'response': data.get('response', 'Processing...'),
                        'model': 'llama3.2:3b'
                    }
        except Exception as e:
            logger.error(f'Fallback error: {e}')
        
        return {'response': 'System is restarting. Please try again in a moment.'}

ai_client = SimpleAI()

async def get_ai_response(message: str, context: Dict = None) -> str:
    result = await ai_client.generate_response(message, context or {})
    return result.get('response', 'Processing...')
