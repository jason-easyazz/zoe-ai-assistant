"""RouteLLM-Inspired Intelligent Model Router for Zoe"""
import re
import os
import json
from typing import Dict, Tuple, Optional, List
from datetime import datetime
from pathlib import Path

class ZoeRouteLLM:
    """Intelligent router that decides which AI model to use"""
    
    def __init__(self):
        self.usage_log = Path("/app/data/routing_metrics.json")
        self.daily_budget = {
            "claude": 50,  # API calls per day
            "gpt-4": 50,
            "local": float('inf')
        }
        self.usage = self._load_usage()
        
        # Query complexity patterns
        self.patterns = {
            "simple": {
                "patterns": [
                    r"what time", r"hello", r"hi", r"thanks",
                    r"turn (on|off)", r"weather", r"remind",
                    r"add to list", r"what day", r"timer"
                ],
                "model": "llama3.2:1b",
                "confidence": 0.95
            },
            "medium": {
                "patterns": [
                    r"explain", r"how (do|does|to)",
                    r"summarize", r"create.*list",
                    r"plan", r"schedule", r"organize"
                ],
                "model": "llama3.2:3b",
                "confidence": 0.85
            },
            "complex": {
                "patterns": [
                    r"(write|create|generate).*(script|code|program)",
                    r"debug", r"analyze.*error", r"optimize",
                    r"architect", r"design.*system",
                    r"fix.*broken", r"diagnose"
                ],
                "model": "llama3.2:3b",  # Use Claude if available
                "confidence": 0.70,
                "prefer_cloud": True
            },
            "system": {
                "patterns": [
                    r"docker", r"container", r"service.*status",
                    r"cpu.*temp", r"memory.*usage", r"disk.*space",
                    r"restart", r"rebuild", r"backup"
                ],
                "model": "llama3.2:3b",
                "confidence": 0.90,
                "needs_execution": True
            }
        }
        
        # API availability
        self.has_claude = bool(os.getenv("ANTHROPIC_API_KEY"))
        self.has_openai = bool(os.getenv("OPENAI_API_KEY"))
    
    def classify_query(self, message: str, context: Dict) -> Dict:
        """Classify query complexity and select optimal model"""
        
        msg_lower = message.lower()
        word_count = len(message.split())
        
        # Check each complexity level
        for level, config in self.patterns.items():
            for pattern in config["patterns"]:
                if re.search(pattern, msg_lower):
                    return self._make_routing_decision(
                        level, config, context, word_count
                    )
        
        # Default routing based on context
        if context.get("mode") == "developer":
            return self._developer_routing(message, context)
        else:
            return self._user_routing(message, context)
    
    def _make_routing_decision(self, level: str, config: Dict, 
                                context: Dict, word_count: int) -> Dict:
        """Make intelligent routing decision"""
        
        # Check if we should use cloud
        use_cloud = False
        if config.get("prefer_cloud") and self._can_use_cloud():
            if context.get("mode") == "developer" or level == "complex":
                use_cloud = True
        
        # Get system capabilities if needed
        needs_exec = config.get("needs_execution", False)
        
        return {
            "model": "claude-3-sonnet" if use_cloud else config["model"],
            "provider": "anthropic" if use_cloud else "ollama",
            "temperature": 0.3 if level == "system" else 0.7,
            "confidence": config["confidence"],
            "complexity": level,
            "needs_execution": needs_exec,
            "reasoning": f"Query classified as {level} complexity",
            "word_count": word_count
        }
    
    def _developer_routing(self, message: str, context: Dict) -> Dict:
        """Special routing for developer mode"""
        
        # Developer mode prefers precision
        if self.has_claude and self.usage["claude"] < self.daily_budget["claude"]:
            return {
                "model": "claude-3-sonnet",
                "provider": "anthropic",
                "temperature": 0.3,
                "confidence": 0.85,
                "complexity": "developer",
                "needs_execution": True
            }
        
        return {
            "model": "llama3.2:3b",
            "provider": "ollama",
            "temperature": 0.3,
            "confidence": 0.75,
            "complexity": "developer",
            "needs_execution": True
        }
    
    def _user_routing(self, message: str, context: Dict) -> Dict:
        """Routing for user mode (privacy-first)"""
        
        # User mode prefers local for privacy
        return {
            "model": "llama3.2:3b",
            "provider": "ollama",
            "temperature": 0.7,
            "confidence": 0.90,
            "complexity": "standard",
            "needs_execution": False
        }
    
    def _can_use_cloud(self) -> bool:
        """Check if we can use cloud services"""
        if not (self.has_claude or self.has_openai):
            return False
        
        # Check daily budget
        if self.has_claude:
            return self.usage.get("claude", 0) < self.daily_budget["claude"]
        elif self.has_openai:
            return self.usage.get("gpt-4", 0) < self.daily_budget["gpt-4"]
        
        return False
    
    def _load_usage(self) -> Dict:
        """Load usage metrics"""
        if self.usage_log.exists():
            with open(self.usage_log) as f:
                data = json.load(f)
                # Reset if new day
                if data.get("date") != datetime.now().date().isoformat():
                    return {"date": datetime.now().date().isoformat()}
                return data
        return {"date": datetime.now().date().isoformat()}
    
    def track_usage(self, provider: str):
        """Track API usage"""
        self.usage[provider] = self.usage.get(provider, 0) + 1
        with open(self.usage_log, 'w') as f:
            json.dump(self.usage, f)

# Global router instance
router = ZoeRouteLLM()
