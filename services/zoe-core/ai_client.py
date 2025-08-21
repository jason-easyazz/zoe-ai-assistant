"""Multi-model AI client with smart routing"""
import os
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import traceback

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MultiModelAI:
    def __init__(self):
        """Initialize multi-model AI system"""
        self.usage_file = "/app/data/ai_usage.json"
        self.daily_budget = 5.00  # $5 daily limit for Claude
        self.load_usage()
        
        # Initialize Ollama client
        try:
            import ollama
            # Use container name when running in Docker
            self.ollama_client = ollama.Client(host='http://zoe-ollama:11434')
            self.ollama_available = True
            logger.info("✅ Ollama client initialized")
        except Exception as e:
            logger.error(f"❌ Ollama initialization failed: {e}")
            self.ollama_available = False
            
        # Initialize Anthropic client if key exists
        self.anthropic_client = None
        api_key = os.getenv('ANTHROPIC_API_KEY')
        if api_key and api_key != 'your-key-here':
            try:
                import anthropic
                self.anthropic_client = anthropic.Anthropic(api_key=api_key)
                logger.info("✅ Anthropic client initialized")
            except ImportError:
                logger.warning("⚠️ Anthropic package not installed")
            except Exception as e:
                logger.error(f"❌ Anthropic initialization failed: {e}")
    
    def load_usage(self):
        """Load today's usage stats"""
        try:
            if os.path.exists(self.usage_file):
                with open(self.usage_file, 'r') as f:
                    data = json.load(f)
                    # Reset if new day
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
        except Exception as e:
            logger.error(f"Failed to save usage: {e}")
    
    def classify_complexity(self, message: str) -> str:
        """Classify message complexity"""
        message_lower = message.lower()
        
        # Simple queries (use fast model)
        simple_keywords = ['time', 'date', 'hello', 'hi', 'weather', 'help', 
                          'status', 'test', 'check', 'what is', 'how much']
        if any(keyword in message_lower for keyword in simple_keywords):
            return 'simple'
        
        # Complex queries (use best model available)
        complex_keywords = ['debug', 'analyze', 'optimize', 'architecture', 
                           'refactor', 'implement', 'design', 'troubleshoot',
                           'performance', 'integration', 'security']
        if any(keyword in message_lower for keyword in complex_keywords):
            return 'complex'
        
        # Default to medium
        return 'medium'
    
    async def generate_response(self, message: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Generate response using appropriate model"""
        complexity = self.classify_complexity(message)
        response_text = "AI service temporarily unavailable"
        model_used = "none"
        
        try:
            # Try Claude for complex queries if available and within budget
            if complexity == 'complex' and self.anthropic_client and self.usage['total'] < self.daily_budget:
                try:
                    response = self.anthropic_client.messages.create(
                        model="claude-3-sonnet-20240229",
                        max_tokens=1000,
                        messages=[{"role": "user", "content": message}]
                    )
                    response_text = response.content[0].text
                    model_used = "claude-api"
                    # Estimate cost (rough estimate)
                    self.usage['total'] += 0.01
                    self.save_usage()
                    logger.info(f"✅ Claude API response generated")
                except Exception as e:
                    logger.error(f"Claude API error: {e}")
                    # Fall back to local model
                    complexity = 'medium'
            
            # Use local Ollama models
            if model_used == "none" and self.ollama_available:
                model_map = {
                    'simple': 'llama3.2:1b',
                    'medium': 'llama3.2:3b',
                    'complex': 'llama3.2:3b'  # Use best local model
                }
                
                selected_model = model_map.get(complexity, 'llama3.2:3b')
                
                try:
                    # Add context if provided
                    full_prompt = message
                    if context:
                        full_prompt = f"Context: {json.dumps(context)}\n\nUser: {message}"
                    
                    response = self.ollama_client.chat(
                        model=selected_model,
                        messages=[{'role': 'user', 'content': full_prompt}]
                    )
                    response_text = response['message']['content']
                    model_used = selected_model
                    logger.info(f"✅ Ollama response generated with {selected_model}")
                except Exception as e:
                    logger.error(f"Ollama error with {selected_model}: {e}")
                    # Try fallback model
                    if selected_model != 'llama3.2:1b':
                        try:
                            response = self.ollama_client.chat(
                                model='llama3.2:1b',
                                messages=[{'role': 'user', 'content': message}]
                            )
                            response_text = response['message']['content']
                            model_used = 'llama3.2:1b'
                            logger.info("✅ Fallback to llama3.2:1b successful")
                        except Exception as e2:
                            logger.error(f"Fallback model error: {e2}")
            
        except Exception as e:
            logger.error(f"Generate response error: {e}\n{traceback.format_exc()}")
        
        return {
            'response': response_text,
            'model_used': model_used,
            'complexity': complexity,
            'usage': self.usage
        }
    
    def get_usage_stats(self) -> Dict[str, Any]:
        """Get current usage statistics"""
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

# Global instance
ai_client = MultiModelAI()
