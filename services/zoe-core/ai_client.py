"""Multi-model AI client"""
import os
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MultiModelAI:
    def __init__(self):
        """Initialize AI system with Ollama and Anthropic"""
        self.usage_file = "/app/data/ai_usage.json"
        self.daily_budget = 5.00
        self.load_usage()
        
        # Initialize Ollama
        self.ollama_client = None
        self.ollama_available = False
        
        try:
            import ollama
            self.ollama_client = ollama.Client(host='http://zoe-ollama:11434')
            models = self.ollama_client.list()
            self.ollama_available = True
            model_names = [m.get('name') for m in models.get('models', [])]
            logger.info(f"✅ Ollama initialized with models: {model_names}")
        except ImportError:
            logger.error("❌ Ollama package not installed")
        except Exception as e:
            logger.error(f"❌ Ollama connection failed: {e}")
            
        # Initialize Anthropic
        self.anthropic_client = None
        api_key = os.getenv('ANTHROPIC_API_KEY', '').strip()
        if api_key and api_key not in ['', 'your-key-here']:
            try:
                import anthropic
                self.anthropic_client = anthropic.Anthropic(api_key=api_key)
                logger.info("✅ Anthropic initialized")
            except:
                logger.warning("⚠️ Anthropic not available")
    
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
        if len(message) < 50 and any(w in msg for w in ['hello', 'hi', 'test']):
            return 'simple'
        elif any(w in msg for w in ['debug', 'analyze', 'optimize']):
            return 'complex'
        return 'medium'
    
    async def generate_response(self, message: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Generate AI response"""
        complexity = self.classify_complexity(message)
        response_text = "Hello from Zoe AI!"
        model_used = "none"
        
        # Try Claude for complex queries
        if complexity == 'complex' and self.anthropic_client and self.usage['total'] < self.daily_budget:
            try:
                response = self.anthropic_client.messages.create(
                    model="claude-3-sonnet-20240229",
                    max_tokens=1000,
                    messages=[{"role": "user", "content": message}]
                )
                response_text = response.content[0].text
                model_used = "claude-3-sonnet"
                self.usage['total'] += 0.01
                self.save_usage()
                logger.info("Used Claude API")
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
                response = self.ollama_client.chat(
                    model=selected_model,
                    messages=[{'role': 'user', 'content': message}]
                )
                response_text = response['message']['content']
                model_used = selected_model
                logger.info(f"Used Ollama {selected_model}")
            except Exception as e:
                logger.error(f"Ollama error: {e}")
                response_text = f"I understand: '{message}'. The AI is warming up, please try again."
                model_used = "warming-up"
        
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

# Initialize on import
ai_client = MultiModelAI()
