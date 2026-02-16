"""
Location Services Router
Provides geocoding, reverse geocoding, and location search for journal entries
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import httpx
import os
import asyncio
from datetime import datetime

from auth_integration import AuthenticatedSession, validate_session

router = APIRouter(prefix="/api/location", tags=["location"])

# Configuration
NOMINATIM_BASE = "https://nominatim.openstreetmap.org"
GOOGLE_PLACES_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")
USER_AGENT = "ZoeJournal/1.0"

# Response Models
class LocationResult(BaseModel):
    place_id: str
    name: str
    display_name: str
    lat: float
    lng: float
    type: str
    country: Optional[str] = None
    
class LocationSearchResponse(BaseModel):
    results: List[LocationResult]
    source: str  # "nominatim" or "google"

class ReverseGeocodeResponse(BaseModel):
    address: str
    components: Dict[str, str]
    lat: float
    lng: float

@router.get("/search", response_model=LocationSearchResponse)
async def search_location(
    query: str = Query(..., description="Location search query"),
    limit: int = Query(5, description="Maximum results to return")
):
    """
    Search for locations by name using Nominatim (OpenStreetMap)
    Falls back to Google Places if API key is available
    """
    if not query or len(query.strip()) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters")
    
    try:
        # Try Nominatim first (free, no API key needed)
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{NOMINATIM_BASE}/search",
                params={
                    "q": query,
                    "format": "json",
                    "limit": limit,
                    "addressdetails": 1
                },
                headers={"User-Agent": USER_AGENT}
            )
            
            if response.status_code == 200:
                data = response.json()
                results = []
                
                for item in data:
                    address = item.get("address", {})
                    results.append(LocationResult(
                        place_id=item.get("place_id", ""),
                        name=item.get("name", query),
                        display_name=item.get("display_name", ""),
                        lat=float(item.get("lat", 0)),
                        lng=float(item.get("lon", 0)),
                        type=item.get("type", "location"),
                        country=address.get("country", None)
                    ))
                
                return LocationSearchResponse(results=results, source="nominatim")
            
    except Exception as e:
        # If Nominatim fails and Google key available, try Google
        if GOOGLE_PLACES_KEY:
            try:
                return await _search_google_places(query, limit)
            except Exception:
                pass
        
        raise HTTPException(status_code=500, detail=f"Location search failed: {str(e)}")
    
    # If we get here, return empty results
    return LocationSearchResponse(results=[], source="nominatim")

async def _search_google_places(query: str, limit: int = 5) -> LocationSearchResponse:
    """Search using Google Places API (fallback)"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={
                "query": query,
                "key": GOOGLE_PLACES_KEY
            }
        )
        
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Google Places API error")
        
        data = response.json()
        results = []
        
        for item in data.get("results", [])[:limit]:
            location = item.get("geometry", {}).get("location", {})
            results.append(LocationResult(
                place_id=item.get("place_id", ""),
                name=item.get("name", query),
                display_name=item.get("formatted_address", ""),
                lat=location.get("lat", 0),
                lng=location.get("lng", 0),
                type=item.get("types", ["location"])[0],
                country=None
            ))
        
        return LocationSearchResponse(results=results, source="google")

@router.get("/reverse", response_model=ReverseGeocodeResponse)
async def reverse_geocode(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude")
):
    """
    Convert coordinates to human-readable address
    Useful for extracting location from photo EXIF data
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{NOMINATIM_BASE}/reverse",
                params={
                    "lat": lat,
                    "lon": lng,
                    "format": "json",
                    "addressdetails": 1
                },
                headers={"User-Agent": USER_AGENT}
            )
            
            if response.status_code == 200:
                data = response.json()
                address_components = data.get("address", {})
                
                return ReverseGeocodeResponse(
                    address=data.get("display_name", f"{lat}, {lng}"),
                    components=address_components,
                    lat=lat,
                    lng=lng
                )
            else:
                raise HTTPException(status_code=404, detail="Location not found")
                
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reverse geocoding failed: {str(e)}")

@router.get("/nearby")
async def find_nearby_entries(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
    radius: int = Query(5000, description="Search radius in meters"),
    session: AuthenticatedSession = Depends(validate_session)
):
    """
    Find journal entries or journey stops near a location
    Uses Haversine formula for distance calculation
    """
    user_id = session.user_id
    import sqlite3
    import json
    import math
    
    DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
    
    def haversine_distance(lat1, lon1, lat2, lon2):
        """Calculate distance between two points in meters"""
        R = 6371000  # Earth radius in meters
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        
        a = math.sin(delta_phi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        
        return R * c
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get all entries with place tags for this user
        cursor.execute("""
            SELECT id, title, place_tags, created_at
            FROM journal_entries
            WHERE user_id = ? AND place_tags IS NOT NULL
        """, (user_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        nearby_entries = []
        
        for row in rows:
            entry_id, title, place_tags_json, created_at = row
            
            if place_tags_json:
                place_tags = json.loads(place_tags_json)
                
                for place in place_tags:
                    place_lat = place.get("lat")
                    place_lng = place.get("lng")
                    
                    if place_lat and place_lng:
                        distance = haversine_distance(lat, lng, place_lat, place_lng)
                        
                        if distance <= radius:
                            nearby_entries.append({
                                "entry_id": entry_id,
                                "title": title,
                                "location": place.get("name", "Unknown"),
                                "distance_meters": int(distance),
                                "created_at": created_at
                            })
        
        # Sort by distance
        nearby_entries.sort(key=lambda x: x["distance_meters"])
        
        return {
            "center": {"lat": lat, "lng": lng},
            "radius_meters": radius,
            "count": len(nearby_entries),
            "entries": nearby_entries
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Nearby search failed: {str(e)}")




