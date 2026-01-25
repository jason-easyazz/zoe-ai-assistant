"""
Household Router
================

API endpoints for household and multi-user management.
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional
from pydantic import BaseModel
import logging

from services.household import (
    get_household_manager,
    get_device_binding_manager,
    get_family_mix_generator
)
from auth_integration import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/household", tags=["household"])


# ========================================
# Request/Response Models
# ========================================

class CreateHouseholdRequest(BaseModel):
    name: str
    settings: Optional[dict] = None


class UpdateHouseholdRequest(BaseModel):
    name: Optional[str] = None
    settings: Optional[dict] = None


class AddMemberRequest(BaseModel):
    user_id: str
    role: str = "member"
    display_name: Optional[str] = None
    content_filter: str = "off"


class UpdateMemberRequest(BaseModel):
    role: Optional[str] = None
    display_name: Optional[str] = None
    content_filter: Optional[str] = None
    time_limits: Optional[dict] = None


class RegisterDeviceRequest(BaseModel):
    name: str
    type: str
    room: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    capabilities: Optional[List[str]] = None
    ip_address: Optional[str] = None


class BindDeviceRequest(BaseModel):
    user_id: str
    binding_type: str = "primary"
    priority: int = 1
    duration_minutes: Optional[int] = None


class MusicPreferencesRequest(BaseModel):
    default_provider: Optional[str] = None
    default_volume: Optional[int] = None
    crossfade_enabled: Optional[bool] = None
    crossfade_seconds: Optional[int] = None
    audio_quality: Optional[str] = None
    autoplay_enabled: Optional[bool] = None
    autoplay_source: Optional[str] = None
    share_listening_activity: Optional[bool] = None
    explicit_content_allowed: Optional[bool] = None


class CreateSharedPlaylistRequest(BaseModel):
    name: str
    description: Optional[str] = None
    playlist_type: str = "collaborative"


class AddToPlaylistRequest(BaseModel):
    track_id: str
    provider: str = "youtube_music"
    title: Optional[str] = None
    artist: Optional[str] = None
    album_art_url: Optional[str] = None
    duration_ms: Optional[int] = None


# ========================================
# Household Endpoints
# ========================================

@router.post("/create")
async def create_household(
    request: CreateHouseholdRequest,
    user_id: str = Depends(get_current_user)
):
    """Create a new household with the current user as owner."""
    manager = await get_household_manager()
    
    household = await manager.create_household(
        name=request.name,
        owner_id=user_id,
        settings=request.settings
    )
    
    return {
        "id": household.id,
        "name": household.name,
        "owner_id": household.owner_id,
        "created_at": str(household.created_at)
    }


@router.get("/mine")
async def get_my_households(user_id: str = Depends(get_current_user)):
    """Get all households the current user belongs to."""
    manager = await get_household_manager()
    
    households = await manager.get_user_households(user_id)
    
    return {
        "households": [
            {
                "id": h.id,
                "name": h.name,
                "owner_id": h.owner_id,
                "member_count": len(h.members),
                "my_role": next(
                    (m.role for m in h.members if m.user_id == user_id),
                    "unknown"
                )
            }
            for h in households
        ]
    }


@router.get("/{household_id}")
async def get_household(
    household_id: str,
    user_id: str = Depends(get_current_user)
):
    """Get household details."""
    manager = await get_household_manager()
    
    household = await manager.get_household(household_id)
    
    if not household:
        raise HTTPException(404, "Household not found")
    
    # Check membership
    is_member = any(m.user_id == user_id for m in household.members)
    if not is_member:
        raise HTTPException(403, "Not a member of this household")
    
    return {
        "id": household.id,
        "name": household.name,
        "owner_id": household.owner_id,
        "settings": household.settings,
        "members": [
            {
                "user_id": m.user_id,
                "role": m.role,
                "display_name": m.display_name,
                "content_filter": m.content_filter
            }
            for m in household.members
        ]
    }


@router.put("/{household_id}")
async def update_household(
    household_id: str,
    request: UpdateHouseholdRequest,
    user_id: str = Depends(get_current_user)
):
    """Update household details. Requires owner or admin role."""
    manager = await get_household_manager()
    
    household = await manager.get_household(household_id)
    
    if not household:
        raise HTTPException(404, "Household not found")
    
    # Check permission
    member = next((m for m in household.members if m.user_id == user_id), None)
    if not member or member.role not in ("owner", "admin"):
        raise HTTPException(403, "Not authorized to update household")
    
    success = await manager.update_household(
        household_id,
        name=request.name,
        settings=request.settings
    )
    
    return {"success": success}


@router.delete("/{household_id}")
async def delete_household(
    household_id: str,
    user_id: str = Depends(get_current_user)
):
    """Delete a household. Requires owner role."""
    manager = await get_household_manager()
    
    household = await manager.get_household(household_id)
    
    if not household:
        raise HTTPException(404, "Household not found")
    
    if household.owner_id != user_id:
        raise HTTPException(403, "Only the owner can delete a household")
    
    success = await manager.delete_household(household_id)
    
    return {"success": success}


# ========================================
# Member Endpoints
# ========================================

@router.post("/{household_id}/members")
async def add_member(
    household_id: str,
    request: AddMemberRequest,
    user_id: str = Depends(get_current_user)
):
    """Add a member to a household."""
    manager = await get_household_manager()
    
    household = await manager.get_household(household_id)
    
    if not household:
        raise HTTPException(404, "Household not found")
    
    # Check permission
    member = next((m for m in household.members if m.user_id == user_id), None)
    if not member or member.role not in ("owner", "admin"):
        raise HTTPException(403, "Not authorized to add members")
    
    new_member = await manager.add_member(
        household_id,
        request.user_id,
        role=request.role,
        display_name=request.display_name,
        content_filter=request.content_filter
    )
    
    return {
        "user_id": new_member.user_id,
        "household_id": new_member.household_id,
        "role": new_member.role
    }


@router.put("/{household_id}/members/{member_user_id}")
async def update_member(
    household_id: str,
    member_user_id: str,
    request: UpdateMemberRequest,
    user_id: str = Depends(get_current_user)
):
    """Update a member's settings."""
    manager = await get_household_manager()
    
    household = await manager.get_household(household_id)
    
    if not household:
        raise HTTPException(404, "Household not found")
    
    # Check permission (owner/admin or self)
    current_member = next((m for m in household.members if m.user_id == user_id), None)
    is_admin = current_member and current_member.role in ("owner", "admin")
    is_self = member_user_id == user_id
    
    if not (is_admin or is_self):
        raise HTTPException(403, "Not authorized to update this member")
    
    # Self can't change role
    if is_self and not is_admin and request.role:
        raise HTTPException(403, "Cannot change your own role")
    
    success = await manager.update_member(
        household_id,
        member_user_id,
        role=request.role,
        display_name=request.display_name,
        content_filter=request.content_filter,
        time_limits=request.time_limits
    )
    
    return {"success": success}


@router.delete("/{household_id}/members/{member_user_id}")
async def remove_member(
    household_id: str,
    member_user_id: str,
    user_id: str = Depends(get_current_user)
):
    """Remove a member from a household."""
    manager = await get_household_manager()
    
    household = await manager.get_household(household_id)
    
    if not household:
        raise HTTPException(404, "Household not found")
    
    # Check permission
    current_member = next((m for m in household.members if m.user_id == user_id), None)
    is_admin = current_member and current_member.role in ("owner", "admin")
    is_self = member_user_id == user_id
    
    if not (is_admin or is_self):
        raise HTTPException(403, "Not authorized to remove this member")
    
    success = await manager.remove_member(household_id, member_user_id)
    
    return {"success": success}


# ========================================
# Device Endpoints
# ========================================

@router.get("/{household_id}/devices")
async def get_household_devices(
    household_id: str,
    user_id: str = Depends(get_current_user)
):
    """Get all devices in a household."""
    device_manager = await get_device_binding_manager()
    
    devices = await device_manager.get_household_devices(household_id)
    
    return {
        "devices": [
            {
                "id": d.id,
                "name": d.name,
                "type": d.type,
                "room": d.room,
                "is_online": d.is_online,
                "capabilities": d.capabilities
            }
            for d in devices
        ]
    }


@router.post("/{household_id}/devices")
async def register_device(
    household_id: str,
    request: RegisterDeviceRequest,
    user_id: str = Depends(get_current_user)
):
    """Register a new device in a household."""
    device_manager = await get_device_binding_manager()
    
    device = await device_manager.register_device(
        name=request.name,
        device_type=request.type,
        household_id=household_id,
        room=request.room,
        manufacturer=request.manufacturer,
        model=request.model,
        capabilities=request.capabilities,
        ip_address=request.ip_address
    )
    
    return {
        "id": device.id,
        "name": device.name,
        "type": device.type,
        "room": device.room
    }


@router.post("/devices/{device_id}/bind")
async def bind_device_to_user(
    device_id: str,
    request: BindDeviceRequest,
    user_id: str = Depends(get_current_user)
):
    """Bind a device to a user."""
    device_manager = await get_device_binding_manager()
    
    binding = await device_manager.bind_device(
        device_id=device_id,
        user_id=request.user_id,
        binding_type=request.binding_type,
        priority=request.priority,
        duration_minutes=request.duration_minutes
    )
    
    return {
        "device_id": binding.device_id,
        "user_id": binding.user_id,
        "binding_type": binding.binding_type
    }


@router.get("/devices/{device_id}/active-user")
async def get_active_user_for_device(
    device_id: str,
    user_id: str = Depends(get_current_user)
):
    """Get the currently active user for a device."""
    device_manager = await get_device_binding_manager()
    
    active_user = await device_manager.get_active_user_for_device(device_id)
    
    return {"active_user_id": active_user}


# ========================================
# Music Preferences Endpoints
# ========================================

@router.get("/preferences/music")
async def get_music_preferences(user_id: str = Depends(get_current_user)):
    """Get user's music preferences."""
    manager = await get_household_manager()
    
    preferences = await manager.get_user_music_preferences(user_id)
    
    return preferences


@router.put("/preferences/music")
async def update_music_preferences(
    request: MusicPreferencesRequest,
    user_id: str = Depends(get_current_user)
):
    """Update user's music preferences."""
    manager = await get_household_manager()
    
    success = await manager.update_user_music_preferences(
        user_id,
        request.model_dump(exclude_unset=True)
    )
    
    return {"success": success}


# ========================================
# Family Mix Endpoints
# ========================================

@router.get("/{household_id}/family-mix")
async def get_family_mix(
    household_id: str,
    force_regenerate: bool = False,
    user_id: str = Depends(get_current_user)
):
    """Get the family mix for a household."""
    generator = await get_family_mix_generator()
    
    mix = await generator.get_or_generate_mix(
        household_id,
        force_regenerate=force_regenerate
    )
    
    return {
        "household_id": mix.household_id,
        "tracks": [
            {
                "track_id": t.track_id,
                "provider": t.provider,
                "title": t.title,
                "artist": t.artist,
                "album_art_url": t.album_art_url,
                "contributed_by": t.contributed_by
            }
            for t in mix.tracks
        ],
        "generated_at": mix.generated_at.isoformat(),
        "valid_until": mix.valid_until.isoformat(),
        "track_count": len(mix.tracks)
    }


@router.post("/{household_id}/family-mix/regenerate")
async def regenerate_family_mix(
    household_id: str,
    user_id: str = Depends(get_current_user)
):
    """Force regenerate the family mix."""
    generator = await get_family_mix_generator()
    
    mix = await generator.generate_mix(household_id)
    
    return {
        "success": True,
        "track_count": len(mix.tracks)
    }


# ========================================
# Shared Playlist Endpoints
# ========================================

@router.get("/{household_id}/playlists")
async def get_shared_playlists(
    household_id: str,
    user_id: str = Depends(get_current_user)
):
    """Get all shared playlists for a household."""
    generator = await get_family_mix_generator()
    
    playlists = await generator.get_shared_playlists(household_id)
    
    return {"playlists": playlists}


@router.post("/{household_id}/playlists")
async def create_shared_playlist(
    household_id: str,
    request: CreateSharedPlaylistRequest,
    user_id: str = Depends(get_current_user)
):
    """Create a new shared playlist."""
    generator = await get_family_mix_generator()
    
    playlist_id = await generator.create_shared_playlist(
        household_id=household_id,
        name=request.name,
        created_by=user_id,
        playlist_type=request.playlist_type,
        description=request.description
    )
    
    return {"playlist_id": playlist_id}


@router.get("/playlists/{playlist_id}/tracks")
async def get_shared_playlist_tracks(
    playlist_id: str,
    user_id: str = Depends(get_current_user)
):
    """Get tracks in a shared playlist."""
    generator = await get_family_mix_generator()
    
    tracks = await generator.get_shared_playlist_tracks(playlist_id)
    
    return {"tracks": tracks}


@router.post("/playlists/{playlist_id}/tracks")
async def add_to_shared_playlist(
    playlist_id: str,
    request: AddToPlaylistRequest,
    user_id: str = Depends(get_current_user)
):
    """Add a track to a shared playlist."""
    generator = await get_family_mix_generator()
    
    success = await generator.add_to_shared_playlist(
        playlist_id=playlist_id,
        track={
            "track_id": request.track_id,
            "provider": request.provider,
            "title": request.title,
            "artist": request.artist,
            "album_art_url": request.album_art_url,
            "duration_ms": request.duration_ms
        },
        added_by=user_id
    )
    
    return {"success": success}

