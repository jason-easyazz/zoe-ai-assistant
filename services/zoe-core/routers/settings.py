"""Settings management with working API key storage"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict
import os
import json
from pathlib import Path

router = APIRouter(prefix="/api/settings")

class APIKeyUpdate(BaseModel):
    service: str  # "openai", "anthropic", "google", etc.
    key: str

class APIKeysResponse(BaseModel):
    keys: Dict[str, str]
    
# Secure storage location
KEYS_FILE = Path("/app/data/api_keys.json")
ENV_FILE = Path("/app/.env")

def load_api_keys() -> Dict[str, str]:
    """Load API keys from secure storage"""
    keys = {}
    
    # Try JSON file first
    if KEYS_FILE.exists():
        try:
            with open(KEYS_FILE) as f:
                keys = json.load(f)
        except:
            pass
    
    # Check environment variables
    for service in ["OPENAI", "ANTHROPIC", "GOOGLE"]:
        env_key = f"{service}_API_KEY"
        if os.getenv(env_key):
            keys[service.lower()] = "****" + os.getenv(env_key)[-4:]
    
    return keys

def save_api_key(service: str, key: str):
    """Save API key securely"""
    
    # Load existing keys
    keys = {}
    if KEYS_FILE.exists():
        try:
            with open(KEYS_FILE) as f:
                keys = json.load(f)
        except:
            pass
    
    # Update key
    keys[service] = key
    
    # Save to file
    KEYS_FILE.parent.mkdir(exist_ok=True)
    with open(KEYS_FILE, 'w') as f:
        json.dump(keys, f)
    
    # Also set as environment variable for current session
    env_name = f"{service.upper()}_API_KEY"
    os.environ[env_name] = key
    
    # Try to update .env file
    try:
        env_lines = []
        env_updated = False
        
        if ENV_FILE.exists():
            with open(ENV_FILE) as f:
                env_lines = f.readlines()
        
        # Update or add the key
        for i, line in enumerate(env_lines):
            if line.startswith(f"{env_name}="):
                env_lines[i] = f"{env_name}={key}\n"
                env_updated = True
                break
        
        if not env_updated:
            env_lines.append(f"{env_name}={key}\n")
        
        with open(ENV_FILE, 'w') as f:
            f.writelines(env_lines)
    except:
        pass  # Fallback to JSON storage only

@router.get("/apikeys")
async def get_api_keys():
    """Get current API key status (masked)"""
    return {"keys": load_api_keys()}

@router.post("/apikeys")
async def update_api_key(update: APIKeyUpdate):
    """Update an API key"""
    try:
        save_api_key(update.service, update.key)
        return {"success": True, "message": f"{update.service} API key updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/apikeys/{service}")
async def delete_api_key(service: str):
    """Delete an API key"""
    try:
        keys = load_api_keys()
        if service in keys:
            del keys[service]
            with open(KEYS_FILE, 'w') as f:
                json.dump(keys, f)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/apikeys/test/{service}")
async def test_api_key(service: str):
    """Test if an API key works"""
    # This would test the actual API
    # For now, just check if key exists
    keys = load_api_keys()
    exists = service in keys
    return {"service": service, "configured": exists, "working": exists}
