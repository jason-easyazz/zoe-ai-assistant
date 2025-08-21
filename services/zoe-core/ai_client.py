"""Multi-model AI client - simplified and robust"""
import os
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MultiModelAI:
    def __init__(self):
        """Initialize multi-model AI system"""
        self.usage_file = "/app/data/ai_usage.json"
        self.daily_budget = 5.00
        self.load_usage()
        
        # Initialize Ollama
        self.ollama_client = None
        self.ollama_available = False
        
        try:
            import ollama
            # Use the container name that we know works
            self.ollama_client = ollama.Client(host='http://zoe-ollama:11434')
            # Test the connection
            models = self.ollama_client.list()
            self.ollama_available = True
            logger.info(f"✅ Ollama connected with {len(models.get('models', []))} models")
            for model in models.get('models', []):
                logger.info(f"   - {model.get('name')}")
        except Exception as e:
            logger.error(f"❌ Ollama not available: {e}")
            
        # Initialize Anthropic
        self.anthropic_client = None
        api_key = os.getenv('ANTHROPIC_API_KEY', '').strip()
        if api_key and api_key != 'your-key-here' and len(api_key) > 10:
            try:
                import anthropic
                self.anthropic_client = anthropic.Anthropic(api_key=api_key)
                logger.info("✅ Anthropic client initialized")
            except Exception as e:
                logger.warning(f"⚠️ Anthropic not available: {e}")
    
    def load_usage(self):
        """Load usage stats"""
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
        """Save usage stats"""
        try:
            os.makedirs(os.path.dirname(self.usage_file), exist_ok=True)
            with open(self.usage_file, 'w') as f:
                json.dump(self.usage, f)
        except:
            pass
    
    def classify_complexity(self, message: str) -> str:
        """Classify message complexity"""
        msg = message.lower()
        
        if any(w in msg for w in ['hello', 'hi', 'test', 'time', 'date']) and len(message) < 50:
            return 'simple'
        elif any(w in msg for w in ['debug', 'analyze', 'optimize', 'refactor']):
            return 'complex'
        
        return 'medium'
    
    async def generate_response(self, message: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Generate response using appropriate model"""
        complexity = self.classify_complexity(message)
        response_text = "Hello! I'm the AI assistant."
        model_used = "fallback"
        
        try:
            # Try Claude for complex queries
            if complexity == 'complex' and self.anthropic_client and self.usage['total'] < self.daily_budget:
                try:
                    response = self.anthropic_client.messages.create(
                        model="claude-3-sonnet-20240229",
                        max_tokens=1000,
                        messages=[{"role": "user", "content": message}]
                    )
                    response_text = response.content[0].text
                    model_used = "claude-api"
                    self.usage['total'] += 0.01
                    self.save_usage()
                except Exception as e:
                    logger.error(f"Claude error: {e}")
            
            # Use Ollama for everything else
            if model_used == "fallback" and self.ollama_available:
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
                except Exception as e:
                    logger.error(f"Ollama error: {e}")
                    # Try simpler model
                    try:
                        response = self.ollama_client.chat(
                            model='llama3.2:1b',
                            messages=[{'role': 'user', 'content': message}]
                        )
                        response_text = response['message']['content']
                        model_used = 'llama3.2:1b-fallback'
                    except:
                        response_text = f"I understand you said: '{message}'. The AI models are initializing. Please try again in a moment."
                        
        except Exception as e:
            logger.error(f"Response generation error: {e}")
            response_text = f"I'm having trouble connecting to the AI models. Error: {str(e)}"
        
        return {
            'response': response_text,
            'model_used': model_used,
            'complexity': complexity,
            'usage': self.usage
        }
    
    def get_usage_stats(self) -> Dict[str, Any]:
        """Get usage statistics"""
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

# Create global instance
ai_client = MultiModelAI()
