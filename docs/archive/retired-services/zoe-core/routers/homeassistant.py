"""
Home Assistant Integration
Basic integration for home automation control

Phase -1 Fix 1: Replaced mock endpoints with proper error responses.
When HA is not configured, endpoints return a clear error instead of fake data.
"""
from fastapi import APIRouter, HTTPException, Query
from auth_integration import require_permission, validate_session
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import requests
import os
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/homeassistant", tags=["homeassistant"])

# Home Assistant configuration
HA_BASE_URL = os.getenv("HOMEASSISTANT_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.getenv("HOMEASSISTANT_TOKEN", "")


def _check_ha_configured() -> bool:
    """Check if Home Assistant is properly configured with a real token."""
    return bool(HA_TOKEN) and not HA_TOKEN.startswith("your-")


def _ha_not_configured_error():
    """Return a consistent error when HA is not configured."""
    raise HTTPException(
        status_code=503,
        detail={
            "error": "Home Assistant not configured",
            "message": "Set HOMEASSISTANT_TOKEN in .env to connect to Home Assistant. "
                       "No mock data is returned -- this endpoint requires a real HA instance.",
            "help": "Get a long-lived access token from HA: Settings > User > Long-Lived Access Tokens"
        }
    )


def _ha_headers() -> dict:
    """Get standard HA API headers."""
    return {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json"
    }

class ServiceCall(BaseModel):
    service: str
    entity_id: Optional[str] = None
    data: Optional[Dict[str, Any]] = None

@router.get("/states")
async def get_states():
    """Get all Home Assistant states"""
    if not _check_ha_configured():
        _ha_not_configured_error()

    try:
        response = requests.get(
            f"{HA_BASE_URL}/api/states", headers=_ha_headers(), timeout=10
        )
        response.raise_for_status()

        states = response.json()

        # Organize states by domain
        organized_states = {
            "lights": [s for s in states if s["entity_id"].startswith("light.")],
            "sensors": [s for s in states if s["entity_id"].startswith("sensor.")],
            "switches": [s for s in states if s["entity_id"].startswith("switch.")],
            "covers": [s for s in states if s["entity_id"].startswith("cover.")],
            "climate": [s for s in states if s["entity_id"].startswith("climate.")]
        }

        return {"states": organized_states}

    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot connect to Home Assistant at {HA_BASE_URL}. Is it running?"
        )
    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=504,
            detail=f"Home Assistant at {HA_BASE_URL} timed out"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to Home Assistant: {str(e)}")

@router.post("/service")
async def call_service(service_call: ServiceCall):
    """Call a Home Assistant service"""
    if not _check_ha_configured():
        _ha_not_configured_error()

    try:
        data = {
            "entity_id": service_call.entity_id,
            **(service_call.data or {})
        }

        response = requests.post(
            f"{HA_BASE_URL}/api/services/{service_call.service.replace('.', '/')}",
            headers=_ha_headers(),
            json=data,
            timeout=10
        )
        response.raise_for_status()

        return {"message": "Service called successfully", "response": response.json()}

    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot connect to Home Assistant at {HA_BASE_URL}. Is it running?"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to call service: {str(e)}")

@router.get("/entities")
async def get_entities(domain: Optional[str] = None):
    """Get entities by domain"""
    if not _check_ha_configured():
        _ha_not_configured_error()

    try:
        response = requests.get(
            f"{HA_BASE_URL}/api/states", headers=_ha_headers(), timeout=10
        )
        response.raise_for_status()

        states = response.json()

        if domain:
            entities = [s for s in states if s["entity_id"].startswith(f"{domain}.")]
            return {"entities": entities}
        else:
            # Get all entities grouped by domain
            entities_by_domain = {}
            for state in states:
                d = state["entity_id"].split(".")[0]
                if d not in entities_by_domain:
                    entities_by_domain[d] = []
                entities_by_domain[d].append(state)
            return {"entities": entities_by_domain}

    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot connect to Home Assistant at {HA_BASE_URL}. Is it running?"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get entities: {str(e)}"
        )

@router.get("/config")
async def get_config():
    """Get Home Assistant configuration"""
    if not _check_ha_configured():
        _ha_not_configured_error()

    try:
        response = requests.get(
            f"{HA_BASE_URL}/api/config", headers=_ha_headers(), timeout=10
        )
        response.raise_for_status()
        return {"config": response.json()}

    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot connect to Home Assistant at {HA_BASE_URL}. Is it running?"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get config: {str(e)}")

@router.get("/health")
async def health_check():
    """Check Home Assistant connectivity. This endpoint always returns 200 with status info."""
    if not _check_ha_configured():
        return {
            "status": "not_configured",
            "message": "Home Assistant token not set. Set HOMEASSISTANT_TOKEN in .env.",
            "configured": False
        }

    try:
        response = requests.get(
            f"{HA_BASE_URL}/api/", headers=_ha_headers(), timeout=5
        )
        response.raise_for_status()
        return {
            "status": "connected",
            "message": "Connected to Home Assistant",
            "configured": True,
            "base_url": HA_BASE_URL
        }

    except requests.exceptions.ConnectionError:
        return {
            "status": "disconnected",
            "message": f"Cannot reach Home Assistant at {HA_BASE_URL}",
            "configured": True,
            "base_url": HA_BASE_URL
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to connect: {str(e)}",
            "configured": True,
            "base_url": HA_BASE_URL
        }
