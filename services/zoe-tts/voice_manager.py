"""
Voice Profile Manager for NeuTTS Air
Handles voice profile CRUD operations, reference audio encoding, and profile storage
"""

import json
import uuid
import shutil
from pathlib import Path
from typing import Optional, Dict, List, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class VoiceProfileManager:
    """Manages voice profiles for NeuTTS Air voice cloning"""
    
    def __init__(self, tts_engine, profiles_dir: Path, samples_dir: Path):
        self.tts_engine = tts_engine
        self.profiles_dir = profiles_dir
        self.samples_dir = samples_dir
        self.profiles: Dict[str, Dict[str, Any]] = {}
        
        # Ensure directories exist
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.samples_dir.mkdir(parents=True, exist_ok=True)
    
    async def load_sample_voices(self):
        """Pre-load sample voice profiles from samples directory"""
        sample_voices = [
            {
                "id": "default",
                "name": "Default Voice",
                "audio_file": None,
                "text_file": None,
                "is_system": True
            },
            {
                "id": "dave",
                "name": "Dave (British Male)",
                "audio_file": "dave.wav",
                "text_file": "dave.txt",
                "is_system": True
            },
            {
                "id": "jo",
                "name": "Jo (Female)",
                "audio_file": "jo.wav",
                "text_file": "jo.txt",
                "is_system": True
            },
            {
                "id": "zoe",
                "name": "Zoe (Friendly Female)",
                "audio_file": "zoe.wav",
                "text_file": "zoe.txt",
                "is_system": True
            }
        ]
        
        for voice in sample_voices:
            try:
                if voice["id"] == "default":
                    # Default voice doesn't need reference encoding
                    self.profiles[voice["id"]] = {
                        "id": voice["id"],
                        "name": voice["name"],
                        "user_id": None,
                        "reference_text": None,
                        "reference_audio_path": None,
                        "encoded_reference": None,
                        "created_at": datetime.now().isoformat(),
                        "is_system": True
                    }
                    logger.info(f"✓ Loaded default voice")
                    continue
                
                audio_path = self.samples_dir / voice["audio_file"]
                text_path = self.samples_dir / voice["text_file"]
                
                if not audio_path.exists():
                    logger.warning(f"Sample audio not found: {audio_path}")
                    continue
                
                if not text_path.exists():
                    logger.warning(f"Sample text not found: {text_path}")
                    continue
                
                # Read reference text
                reference_text = text_path.read_text().strip()
                
                # Encode reference audio
                logger.info(f"Encoding reference audio for {voice['name']}...")
                encoded_reference = self.tts_engine.encode_reference(str(audio_path))
                
                # Store profile
                self.profiles[voice["id"]] = {
                    "id": voice["id"],
                    "name": voice["name"],
                    "user_id": None,
                    "reference_text": reference_text,
                    "reference_audio_path": str(audio_path),
                    "encoded_reference": encoded_reference,
                    "created_at": datetime.now().isoformat(),
                    "is_system": True
                }
                
                logger.info(f"✓ Loaded voice: {voice['name']}")
                
            except Exception as e:
                logger.error(f"Failed to load sample voice {voice['id']}: {e}")
        
        # Load user-created profiles from disk
        await self._load_user_profiles()
    
    async def _load_user_profiles(self):
        """Load user-created voice profiles from disk"""
        try:
            for user_dir in self.profiles_dir.iterdir():
                if not user_dir.is_dir():
                    continue
                
                user_id = user_dir.name
                
                for profile_dir in user_dir.iterdir():
                    if not profile_dir.is_dir():
                        continue
                    
                    metadata_file = profile_dir / "metadata.json"
                    if not metadata_file.exists():
                        continue
                    
                    try:
                        metadata = json.loads(metadata_file.read_text())
                        
                        # Re-encode reference audio
                        audio_path = profile_dir / "reference.wav"
                        if audio_path.exists():
                            encoded_reference = self.tts_engine.encode_reference(str(audio_path))
                            
                            self.profiles[metadata["id"]] = {
                                "id": metadata["id"],
                                "name": metadata["name"],
                                "user_id": user_id,
                                "reference_text": metadata["reference_text"],
                                "reference_audio_path": str(audio_path),
                                "encoded_reference": encoded_reference,
                                "created_at": metadata.get("created_at", datetime.now().isoformat()),
                                "is_system": False
                            }
                            
                            logger.info(f"✓ Loaded user voice: {metadata['name']} (user: {user_id})")
                    
                    except Exception as e:
                        logger.error(f"Failed to load profile from {profile_dir}: {e}")
        
        except Exception as e:
            logger.error(f"Failed to load user profiles: {e}")
    
    async def create_profile(
        self,
        name: str,
        reference_audio_path: str,
        reference_text: str,
        user_id: Optional[str] = None
    ) -> str:
        """
        Create a new voice profile from reference audio
        
        Args:
            name: Display name for the voice profile
            reference_audio_path: Path to reference audio file
            reference_text: Transcription of the reference audio
            user_id: Optional user ID for user-specific profiles
        
        Returns:
            profile_id: Unique ID for the created profile
        """
        profile_id = str(uuid.uuid4())
        
        # Encode reference audio
        logger.info(f"Encoding reference audio for new profile '{name}'...")
        encoded_reference = self.tts_engine.encode_reference(reference_audio_path)
        
        # Create storage directory
        if user_id:
            profile_dir = self.profiles_dir / user_id / profile_id
        else:
            profile_dir = self.profiles_dir / "system" / profile_id
        
        profile_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy reference audio
        dest_audio_path = profile_dir / "reference.wav"
        shutil.copy2(reference_audio_path, dest_audio_path)
        
        # Save metadata
        metadata = {
            "id": profile_id,
            "name": name,
            "reference_text": reference_text,
            "created_at": datetime.now().isoformat()
        }
        
        metadata_file = profile_dir / "metadata.json"
        metadata_file.write_text(json.dumps(metadata, indent=2))
        
        # Store in memory
        self.profiles[profile_id] = {
            "id": profile_id,
            "name": name,
            "user_id": user_id,
            "reference_text": reference_text,
            "reference_audio_path": str(dest_audio_path),
            "encoded_reference": encoded_reference,
            "created_at": metadata["created_at"],
            "is_system": False
        }
        
        logger.info(f"✓ Created voice profile: {name} ({profile_id})")
        
        return profile_id
    
    def get_profile(self, profile_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get a voice profile by ID
        
        Args:
            profile_id: Profile ID to retrieve
            user_id: Optional user ID for access control
        
        Returns:
            Profile dict or None if not found or access denied
        """
        profile = self.profiles.get(profile_id)
        
        if profile is None:
            return None
        
        # System voices are accessible to everyone
        if profile["is_system"]:
            return profile
        
        # User-specific voices require matching user_id
        if profile["user_id"] != user_id:
            return None
        
        return profile
    
    def list_profiles(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all accessible voice profiles
        
        Args:
            user_id: Optional user ID to include user-specific profiles
        
        Returns:
            List of profile dicts (without encoded_reference for efficiency)
        """
        accessible_profiles = []
        
        for profile in self.profiles.values():
            # System voices are always accessible
            if profile["is_system"]:
                accessible_profiles.append(profile)
                continue
            
            # User voices only accessible to owner
            if user_id and profile["user_id"] == user_id:
                accessible_profiles.append(profile)
        
        # Remove encoded_reference from response (too large)
        result = []
        for profile in accessible_profiles:
            result.append({
                "id": profile["id"],
                "name": profile["name"],
                "user_id": profile["user_id"],
                "reference_text": profile["reference_text"],
                "created_at": profile["created_at"],
                "is_system": profile["is_system"]
            })
        
        return result
    
    async def delete_profile(self, profile_id: str, user_id: Optional[str] = None) -> bool:
        """
        Delete a voice profile
        
        System voices cannot be deleted.
        
        Args:
            profile_id: Profile ID to delete
            user_id: User ID for access control
        
        Returns:
            True if deleted, False if not found or access denied
        """
        profile = self.profiles.get(profile_id)
        
        if profile is None:
            return False
        
        # Cannot delete system voices
        if profile["is_system"]:
            logger.warning(f"Attempted to delete system voice: {profile_id}")
            return False
        
        # Must own the profile
        if profile["user_id"] != user_id:
            logger.warning(f"Access denied to delete profile {profile_id}")
            return False
        
        # Delete from disk
        try:
            if user_id:
                profile_dir = self.profiles_dir / user_id / profile_id
            else:
                profile_dir = self.profiles_dir / "system" / profile_id
            
            if profile_dir.exists():
                shutil.rmtree(profile_dir)
            
            # Remove from memory
            del self.profiles[profile_id]
            
            logger.info(f"✓ Deleted voice profile: {profile_id}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to delete profile {profile_id}: {e}")
            return False












