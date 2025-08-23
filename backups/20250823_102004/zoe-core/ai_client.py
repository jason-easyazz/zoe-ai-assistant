"""Multi-model AI client with timeout handling"""
import os
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MultiModelAI:
    def __init__(self):
        """Initialize AI system"""
        self.usage_file = "/app/data/ai_usage.json"
        self.daily_budget = 5.00
        self.load_usage()
        
        # Initialize Ollama
        self.ollama_client = None
        self.ollama_available = False
        
        try:
            import ollama
            self.ollama_client = ollama.Client(
                host='http://zoe-ollama:11434',
                timeout=20  # Set timeout
            )
            models = self.ollama_client.list()
            self.ollama_available = True
            logger.info(f"✅ Ollama connected with {len(models.get('models', []))} models")
        except Exception as e:
            logger.error(f"❌ Ollama not available: {e}")
            
        # Initialize Anthropic
        self.anthropic_client = None
        api_key = os.getenv('ANTHROPIC_API_KEY', '').strip()
        if api_key and api_key not in ['', 'your-key-here', 'sk-ant-api03-YOUR-KEY-HERE']:
            try:
                import anthropic
                self.anthropic_client = anthropic.Anthropic(api_key=api_key)
                logger.info("✅ Anthropic initialized")
            except Exception as e:
                logger.warning(f"⚠️ Anthropic not available: {e}")
    
    def load_usage(self):
        try:
            if os.path.exists(self.usage_file):
                with open(self.usage_file, 'r') as f:
                    data = json.load(f)
                    if data.get('date') != str(datetime.now().date()):
                        self.usage = {'date': str(datetime.now().date()), 'total': 0.0}
                    else:
                        self.usage = data
            else:
                self.usage = {'date': str(datetime.now().date()), 'total': 0.0}
        except:
            self.usage = {'date': str(datetime.now().date()), 'total': 0.0}
    
    def save_usage(self):
        try:
            os.makedirs(os.path.dirname(self.usage_file), exist_ok=True)
            with open(self.usage_file, 'w') as f:
                json.dump(self.usage, f)
        except:
            pass
    
    def classify_complexity(self, message: str) -> str:
        msg = message.lower()
        
        # Simple - use fast model
        if len(message) < 50 and any(w in msg for w in ['hello', 'hi', 'test', 'time']):
            return 'simple'
        
        # Complex - use best model
        elif any(w in msg for w in ['debug', 'analyze', 'optimize', 'script']):
            return 'complex'
        
        return 'medium'
    
    async def generate_response(self, message: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Generate AI response with timeout handling"""
        complexity = self.classify_complexity(message)
        response_text = "Hello! I'm ready to help."
        model_used = "none"
        
        # Add instruction for brevity to complex queries
        if complexity == 'complex' and 'script' in message.lower():
            message += "\n\nPlease provide a concise solution (max 20 lines of code)."
        
        try:
            # Try Claude for complex queries if available
            if complexity == 'complex' and self.anthropic_client and self.usage['total'] < self.daily_budget:
                try:
                    response = self.anthropic_client.messages.create(
                        model="claude-3-sonnet-20240229",
                        max_tokens=500,  # Limit tokens
                        messages=[{"role": "user", "content": message}],
                        timeout=15  # 15 second timeout
                    )
                    response_text = response.content[0].text
                    model_used = "claude-3-sonnet"
                    self.usage['total'] += 0.01
                    self.save_usage()
                except Exception as e:
                    logger.error(f"Claude error: {e}")
            
            # Use Ollama for other queries
            if model_used == "none" and self.ollama_available:
                model_map = {
                    'simple': 'llama3.2:1b',
                    'medium': 'llama3.2:3b',
                    'complex': 'llama3.2:3b'
                }
                
                selected_model = model_map.get(complexity, 'llama3.2:3b')
                
                try:
                    # Set options for faster responses
                    options = {
                        'temperature': 0.7,
                        'top_p': 0.9,
                        'max_tokens': 300 if complexity == 'simple' else 500
                    }
                    
                    response = self.ollama_client.chat(
                        model=selected_model,
                        messages=[{'role': 'user', 'content': message}],
                        options=options
                    )
                    response_text = response['message']['content']
                    model_used = selected_model
                    
                except asyncio.TimeoutError:
                    response_text = "Response took too long. Try a simpler query."
                    model_used = "timeout"
                except Exception as e:
                    logger.error(f"Ollama error: {e}")
                    response_text = f"AI is processing. Please try again."
                    model_used = "error"
        
        except Exception as e:
            logger.error(f"Response generation error: {e}")
        
        return {
            'response': response_text,
            'model_used': model_used,
            'complexity': complexity,
            'usage': self.usage
        }
    
    def get_usage_stats(self) -> Dict[str, Any]:
        self.load_usage()
        return {
            'daily_budget': self.daily_budget,
            'used_today': self.usage['total'],
            'remaining': max(0, self.daily_budget - self.usage['total']),
            'date': self.usage['date'],
            'models_available': {
                'claude': self.anthropic_client is not None,
                'llama3.2:3b': self.ollama_available,
                'llama3.2:1b': self.ollama_available
            }
        }

ai_client = MultiModelAI()
