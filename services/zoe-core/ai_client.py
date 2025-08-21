"""Multi-model AI client with robust connection handling"""
import os
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime
import traceback

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MultiModelAI:
    def __init__(self):
        """Initialize multi-model AI system with robust connection"""
        self.usage_file = "/app/data/ai_usage.json"
        self.daily_budget = 5.00
        self.load_usage()
        
        # Try multiple Ollama connection methods
        self.ollama_client = None
        self.ollama_available = False
        
        try:
            import ollama
            
            # Try different host configurations
            hosts_to_try = [
                'http://zoe-ollama:11434',
                'http://host.docker.internal:11434',
                'http://172.17.0.1:11434',
                'http://ollama:11434',
            ]
            
            for host in hosts_to_try:
                try:
                    logger.info(f"Trying Ollama at {host}...")
                    client = ollama.Client(host=host)
                    # Test the connection
                    models = client.list()
                    if models:
                        self.ollama_client = client
                        self.ollama_available = True
                        self.ollama_host = host
                        logger.info(f"✅ Ollama connected at {host}")
                        logger.info(f"   Available models: {[m.get('name') for m in models.get('models', [])]}")
                        break
                except Exception as e:
                    logger.warning(f"   Failed: {str(e)[:50]}")
                    continue
                    
            if not self.ollama_available:
                logger.error("❌ Could not connect to Ollama on any host")
                
        except ImportError:
            logger.error("❌ Ollama package not installed")
        except Exception as e:
            logger.error(f"❌ Ollama initialization error: {e}")
            
        # Initialize Anthropic if available
        self.anthropic_client = None
        api_key = os.getenv('ANTHROPIC_API_KEY', '').strip()
        if api_key and api_key != 'your-key-here' and len(api_key) > 10:
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
        
        # Simple queries
        simple_keywords = ['time', 'date', 'hello', 'hi', 'test', 'what is']
        if any(kw in message_lower for kw in simple_keywords) and len(message) < 50:
            return 'simple'
        
        # Complex queries
        complex_keywords = ['debug', 'analyze', 'optimize', 'refactor', 'architecture']
        if any(kw in message_lower for kw in complex_keywords):
            return 'complex'
        
        return 'medium'
    
    async def generate_response(self, message: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Generate response using appropriate model"""
        complexity = self.classify_complexity(message)
        response_text = "AI service temporarily unavailable"
        model_used = "none"
        
        try:
            # Try Claude for complex queries if available
            if complexity == 'complex' and self.anthropic_client and self.usage['total'] < self.daily_budget:
                try:
                    logger.info("Attempting Claude API...")
                    response = self.anthropic_client.messages.create(
                        model="claude-3-sonnet-20240229",
                        max_tokens=1000,
                        messages=[{"role": "user", "content": message}]
                    )
                    response_text = response.content[0].text
                    model_used = "claude-api"
                    self.usage['total'] += 0.01
                    self.save_usage()
                    logger.info("✅ Claude response generated")
                except Exception as e:
                    logger.error(f"Claude error: {e}")
                    complexity = 'medium'  # Fall back to local
            
            # Use Ollama if available and Claude didn't work
            if model_used == "none" and self.ollama_available and self.ollama_client:
                model_map = {
                    'simple': 'llama3.2:1b',
                    'medium': 'llama3.2:3b',
                    'complex': 'llama3.2:3b'
                }
                
                selected_model = model_map.get(complexity, 'llama3.2:3b')
                
                try:
                    logger.info(f"Using Ollama model: {selected_model}")
                    
                    # Build prompt with context
                    full_prompt = message
                    if context:
                        full_prompt = f"Context: {json.dumps(context)}\n\nUser: {message}"
                    
                    # Generate response
                    response = self.ollama_client.chat(
                        model=selected_model,
                        messages=[{'role': 'user', 'content': full_prompt}]
                    )
                    
                    response_text = response['message']['content']
                    model_used = selected_model
                    logger.info(f"✅ Ollama response from {selected_model}")
                    
                except Exception as e:
                    logger.error(f"Ollama error: {e}")
                    # Try simpler model as fallback
                    if selected_model != 'llama3.2:1b':
                        try:
                            response = self.ollama_client.chat(
                                model='llama3.2:1b',
                                messages=[{'role': 'user', 'content': message}]
                            )
                            response_text = response['message']['content']
                            model_used = 'llama3.2:1b'
                            logger.info("✅ Fallback to 1b model worked")
                        except Exception as e2:
                            logger.error(f"Fallback failed: {e2}")
            
            # If still no response, provide helpful error
            if model_used == "none":
                response_text = (
                    "AI models are currently unavailable. Please check:\n"
                    "1. Ollama container is running: docker ps | grep ollama\n"
                    "2. Models are loaded: docker exec zoe-ollama ollama list\n"
                    "3. Network connectivity: docker exec zoe-core curl http://zoe-ollama:11434"
                )
                            
        except Exception as e:
            logger.error(f"Generate response error: {e}\n{traceback.format_exc()}")
            response_text = f"Error generating response: {str(e)}"
        
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
                'llama3.2:1b': self.ollama_available,
                'ollama_host': getattr(self, 'ollama_host', 'not connected')
            }
        }

# Global instance
ai_client = MultiModelAI()
