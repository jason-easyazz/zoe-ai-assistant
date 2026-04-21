"""Settings UI Backend Router"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any, Optional
import json
import os
from pathlib import Path

router = APIRouter(prefix="/api/settings-ui", tags=["settings-ui"])

# Paths
PERSONALITY_CONFIG = Path("/app/data/personality_config.json")
LLM_MODELS_FILE = Path("/app/data/llm_models.json")
ROUTING_METRICS = Path("/app/data/routing_metrics.json")

@router.get("/personalities")
async def get_personalities():
    """Get personality settings"""
    if PERSONALITY_CONFIG.exists():
        with open(PERSONALITY_CONFIG) as f:
            return json.load(f)
    
    return {
        "zoe": {
            "name": "Zoe",
            "temperature": 0.7,
            "response_length": "balanced",
            "emoji_usage": "moderate",
            "friendliness": 8
        },
        "zack": {
            "name": "Zack", 
            "temperature": 0.3,
            "response_length": "detailed",
            "emoji_usage": "minimal",
            "technical_depth": 9
        }
    }

@router.put("/personalities")
async def update_personalities(settings: Dict[str, Any]):
    """Update personality settings"""
    current = await get_personalities()
    current.update(settings)
    
    PERSONALITY_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(PERSONALITY_CONFIG, 'w') as f:
        json.dump(current, f, indent=2)
    
    return {"status": "success"}

@router.get("/apikeys/status")
async def get_apikeys_status():
    """Check which API keys are configured"""
    status = {}
    
    # Check environment variables
    providers = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "cohere": "COHERE_API_KEY",
        "groq": "GROQ_API_KEY"
    }
    
    for provider, env_var in providers.items():
        key = os.getenv(env_var, "")
        if key and key not in ["", "your-key-here"]:
            status[provider] = "configured"
        else:
            status[provider] = "not_configured"
    
    return status

@router.get("/routellm/status")
async def get_routellm_status():
    """Get RouteLLM status"""
    status = {
        "total_models": 0,
        "active_providers": 0,
        "requests_today": 0,
        "cost_today": 0.0,
        "providers": {}
    }
    
    # Check models file
    if LLM_MODELS_FILE.exists():
        try:
            with open(LLM_MODELS_FILE) as f:
                data = json.load(f)
                
            for provider, info in data.get("providers", {}).items():
                enabled = info.get("enabled", False)
                models = info.get("models", [])
                
                status["providers"][provider] = {
                    "enabled": enabled,
                    "models": models[:5]  # Limit for UI
                }
                
                if enabled:
                    status["active_providers"] += 1
                    status["total_models"] += len(models)
        except:
            pass
    
    # Check metrics
    if ROUTING_METRICS.exists():
        try:
            with open(ROUTING_METRICS) as f:
                metrics = json.load(f)
                status["requests_today"] = metrics.get("requests_today", 0)
                status["cost_today"] = metrics.get("cost_today", 0.0)
        except:
            pass
    
    return status

@router.post("/routellm/discover")
async def trigger_discovery():
    """Trigger model discovery"""
    # For now, just return success
    # Real discovery would happen here
    return {"status": "success", "message": "Discovery triggered"}
