"""
RouteLLM Priority Patch - Adjusts routing without hardcoding models
This ENHANCES the existing dynamic discovery
"""
import json
import os

# Load the existing discovered models
models_file = "/app/data/llm_models.json"
with open(models_file, 'r') as f:
    config = json.load(f)

# Update routing priorities WITHOUT hardcoding models
# Just adjust the routing rules to prefer Claude when available
if "routing_rules" not in config:
    config["routing_rules"] = {}

config["routing_rules"]["provider_priority"] = {
    "complex_queries": ["anthropic", "openai", "google", "groq", "ollama"],
    "medium_queries": ["openai", "anthropic", "ollama"],
    "simple_queries": ["ollama", "groq", "openai"]
}

config["routing_rules"]["prefer_claude_for_developer"] = True
config["routing_rules"]["complexity_thresholds"] = {
    "use_best_model_above_words": 20,
    "use_claude_for_code": True
}

# Save updated config
with open(models_file, 'w') as f:
    json.dump(config, f, indent=2)

print("✅ RouteLLM priorities updated to prefer Claude for complex queries")
