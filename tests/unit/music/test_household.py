"""
Unit Tests for Household System
===============================

Tests household management, device binding, and family mix.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
import tempfile
import os

import sys
sys.path.insert(0, '/home/zoe/assistant/services/zoe-core')


class TestHouseholdManager:
    """Tests for HouseholdManager."""
    
    @pytest.fixture
    def temp_db(self):
        """Create temporary database for testing."""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        yield path
        os.unlink(path)
    
    @pytest.fixture
    async def manager(self, temp_db):
        """Create HouseholdManager with temp database."""
        # Patch the schema path to use test schema
        from services.household.household_manager import HouseholdManager
        
        manager = HouseholdManager(db_path=temp_db)
        
        # Initialize with inline schema
        import aiosqlite
        manager._conn = await aiosqlite.connect(temp_db)
        manager._conn.row_factory = aiosqlite.Row
        
        # Create test tables
        await manager._conn.executescript("""
            CREATE TABLE IF NOT EXISTS households (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                settings TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS household_members (
                household_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                display_name TEXT,
                avatar_url TEXT,
                content_filter TEXT DEFAULT 'off',
                time_limits TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (household_id, user_id)
            );
            
            CREATE TABLE IF NOT EXISTS user_music_preferences (
                user_id TEXT PRIMARY KEY,
                default_provider TEXT DEFAULT 'youtube_music',
                default_volume INTEGER DEFAULT 75,
                crossfade_enabled BOOLEAN DEFAULT FALSE,
                crossfade_seconds INTEGER DEFAULT 5,
                audio_quality TEXT DEFAULT 'auto',
                autoplay_enabled BOOLEAN DEFAULT TRUE,
                autoplay_source TEXT DEFAULT 'radio',
                share_listening_activity BOOLEAN DEFAULT TRUE,
                explicit_content_allowed BOOLEAN DEFAULT TRUE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS music_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                device_id TEXT,
                household_id TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP,
                current_track_id TEXT,
                queue_snapshot TEXT,
                tracks_played INTEGER DEFAULT 0,
                tracks_skipped INTEGER DEFAULT 0,
                total_listen_time_ms INTEGER DEFAULT 0
            );
        """)
        await manager._conn.commit()
        manager._initialized = True
        
        yield manager
        
        await manager.close()
    
    @pytest.mark.asyncio
    async def test_create_household(self, manager):
        """Test creating a household."""
        household = await manager.create_household(
            name="Smith Family",
            owner_id="user123"
        )
        
        assert household.id is not None
        assert household.name == "Smith Family"
        assert household.owner_id == "user123"
        assert len(household.members) == 1
        assert household.members[0].role == "owner"
    
    @pytest.mark.asyncio
    async def test_get_household(self, manager):
        """Test getting a household."""
        created = await manager.create_household("Test Home", "user1")
        
        retrieved = await manager.get_household(created.id)
        
        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.name == "Test Home"
    
    @pytest.mark.asyncio
    async def test_add_member(self, manager):
        """Test adding a member to household."""
        household = await manager.create_household("Family", "owner1")
        
        member = await manager.add_member(
            household.id,
            "member1",
            role="member",
            display_name="Alice"
        )
        
        assert member.user_id == "member1"
        assert member.role == "member"
        assert member.display_name == "Alice"
        
        # Verify member was added
        retrieved = await manager.get_household(household.id)
        assert len(retrieved.members) == 2
    
    @pytest.mark.asyncio
    async def test_remove_member(self, manager):
        """Test removing a member from household."""
        household = await manager.create_household("Family", "owner1")
        await manager.add_member(household.id, "member1", role="member")
        
        success = await manager.remove_member(household.id, "member1")
        
        assert success is True
        
        retrieved = await manager.get_household(household.id)
        assert len(retrieved.members) == 1
    
    @pytest.mark.asyncio
    async def test_cannot_remove_owner(self, manager):
        """Test that owner cannot be removed."""
        household = await manager.create_household("Family", "owner1")
        
        success = await manager.remove_member(household.id, "owner1")
        
        assert success is False
    
    @pytest.mark.asyncio
    async def test_music_preferences(self, manager):
        """Test user music preferences."""
        # Get default preferences
        prefs = await manager.get_user_music_preferences("user1")
        
        assert prefs["default_volume"] == 75
        assert prefs["autoplay_enabled"] is True
        
        # Update preferences
        await manager.update_user_music_preferences("user1", {
            "default_volume": 50,
            "audio_quality": "high"
        })
        
        updated = await manager.get_user_music_preferences("user1")
        assert updated["default_volume"] == 50
        assert updated["audio_quality"] == "high"


class TestDeviceBindingManager:
    """Tests for DeviceBindingManager."""
    
    @pytest.fixture
    def temp_db(self):
        """Create temporary database for testing."""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        yield path
        os.unlink(path)
    
    @pytest.fixture
    async def manager(self, temp_db):
        """Create DeviceBindingManager with temp database."""
        from services.household.device_binding import DeviceBindingManager
        
        manager = DeviceBindingManager(db_path=temp_db)
        
        import aiosqlite
        manager._conn = await aiosqlite.connect(temp_db)
        manager._conn.row_factory = aiosqlite.Row
        
        await manager._conn.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                household_id TEXT,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                manufacturer TEXT,
                model TEXT,
                room TEXT,
                capabilities TEXT DEFAULT '["audio"]',
                ip_address TEXT,
                is_online BOOLEAN DEFAULT FALSE,
                last_seen TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS device_user_bindings (
                device_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                binding_type TEXT NOT NULL DEFAULT 'primary',
                priority INTEGER DEFAULT 1,
                bound_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                PRIMARY KEY (device_id, user_id)
            );
        """)
        await manager._conn.commit()
        manager._initialized = True
        
        yield manager
        
        await manager.close()
    
    @pytest.mark.asyncio
    async def test_register_device(self, manager):
        """Test registering a device."""
        device = await manager.register_device(
            name="Living Room Speaker",
            device_type="speaker",
            room="Living Room"
        )
        
        assert device.id is not None
        assert device.name == "Living Room Speaker"
        assert device.type == "speaker"
        assert device.room == "Living Room"
        assert device.is_online is True
    
    @pytest.mark.asyncio
    async def test_bind_device(self, manager):
        """Test binding a device to a user."""
        device = await manager.register_device("Speaker", "speaker")
        
        binding = await manager.bind_device(
            device.id,
            "user1",
            binding_type="primary"
        )
        
        assert binding.device_id == device.id
        assert binding.user_id == "user1"
        assert binding.binding_type == "primary"
    
    @pytest.mark.asyncio
    async def test_get_user_devices(self, manager):
        """Test getting devices bound to a user."""
        device1 = await manager.register_device("Device 1", "speaker")
        device2 = await manager.register_device("Device 2", "display")
        
        await manager.bind_device(device1.id, "user1")
        await manager.bind_device(device2.id, "user1")
        
        devices = await manager.get_user_devices("user1")
        
        assert len(devices) == 2
    
    @pytest.mark.asyncio
    async def test_voice_active_user(self, manager):
        """Test voice-activated user binding."""
        device = await manager.register_device("Smart Speaker", "speaker")
        
        # Set voice-activated user
        binding = await manager.set_voice_active_user(
            device.id,
            "user1",
            duration_minutes=30
        )
        
        assert binding.binding_type == "temporary"
        assert binding.priority == 0  # Highest
        assert binding.expires_at is not None
        
        # Check active user
        active = await manager.get_active_user_for_device(device.id)
        assert active == "user1"
    
    @pytest.mark.asyncio
    async def test_temporary_binding_expires(self, manager):
        """Test that temporary bindings can expire."""
        device = await manager.register_device("Speaker", "speaker")
        
        # Create binding with very short duration (already expired)
        await manager._conn.execute(
            """
            INSERT INTO device_user_bindings 
            (device_id, user_id, binding_type, priority, expires_at)
            VALUES (?, ?, 'temporary', 0, datetime('now', '-1 hour'))
            """,
            (device.id, "user1")
        )
        await manager._conn.commit()
        
        # Should not find the expired binding
        active = await manager.get_active_user_for_device(device.id)
        assert active is None


class TestFamilyMixGenerator:
    """Tests for FamilyMixGenerator."""
    
    @pytest.fixture
    def temp_db(self):
        """Create temporary database for testing."""
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        yield path
        os.unlink(path)
    
    @pytest.fixture
    async def generator(self, temp_db):
        """Create FamilyMixGenerator with temp database."""
        from services.household.family_mix import FamilyMixGenerator
        
        generator = FamilyMixGenerator(db_path=temp_db)
        
        import aiosqlite
        generator._conn = await aiosqlite.connect(temp_db)
        generator._conn.row_factory = aiosqlite.Row
        
        await generator._conn.executescript("""
            CREATE TABLE IF NOT EXISTS household_members (
                household_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                PRIMARY KEY (household_id, user_id)
            );
            
            CREATE TABLE IF NOT EXISTS music_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                track_id TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'youtube_music',
                track_title TEXT,
                artist TEXT,
                album TEXT,
                played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS music_likes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                track_id TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'youtube_music',
                track_title TEXT,
                artist TEXT,
                album_art_url TEXT,
                liked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS family_mix_state (
                household_id TEXT PRIMARY KEY,
                current_mix TEXT,
                user_weights TEXT DEFAULT '{}',
                generated_at TIMESTAMP,
                valid_until TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS shared_playlists (
                id TEXT PRIMARY KEY,
                household_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                playlist_type TEXT DEFAULT 'collaborative',
                created_by TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS shared_playlist_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id TEXT NOT NULL,
                track_id TEXT NOT NULL,
                provider TEXT DEFAULT 'youtube_music',
                track_title TEXT,
                artist TEXT,
                album_art_url TEXT,
                duration_ms INTEGER,
                position INTEGER NOT NULL,
                added_by TEXT NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await generator._conn.commit()
        generator._initialized = True
        
        yield generator
        
        await generator.close()
    
    @pytest.mark.asyncio
    async def test_generate_empty_household_mix(self, generator):
        """Test generating mix for empty household."""
        mix = await generator.generate_mix("nonexistent")
        
        assert mix.household_id == "nonexistent"
        assert len(mix.tracks) == 0
    
    @pytest.mark.asyncio
    async def test_create_shared_playlist(self, generator):
        """Test creating a shared playlist."""
        playlist_id = await generator.create_shared_playlist(
            household_id="household1",
            name="Party Mix",
            created_by="user1",
            description="Music for parties"
        )
        
        assert playlist_id is not None
        
        # Verify playlist was created
        playlists = await generator.get_shared_playlists("household1")
        assert len(playlists) == 1
        assert playlists[0]["name"] == "Party Mix"
    
    @pytest.mark.asyncio
    async def test_add_to_shared_playlist(self, generator):
        """Test adding tracks to shared playlist."""
        playlist_id = await generator.create_shared_playlist(
            "household1", "Test Playlist", "user1"
        )
        
        success = await generator.add_to_shared_playlist(
            playlist_id,
            {
                "track_id": "track123",
                "provider": "youtube_music",
                "title": "Test Song",
                "artist": "Test Artist"
            },
            added_by="user1"
        )
        
        assert success is True
        
        tracks = await generator.get_shared_playlist_tracks(playlist_id)
        assert len(tracks) == 1
        assert tracks[0]["track_title"] == "Test Song"
    
    @pytest.mark.asyncio
    async def test_diversity_rules(self, generator):
        """Test that diversity rules are applied."""
        candidates = [
            {"track_id": f"t{i}", "artist": "Artist1", "provider": "yt", 
             "track_title": f"Song {i}", "weight": 1.0}
            for i in range(10)
        ]
        
        # Add some variety
        candidates.extend([
            {"track_id": f"t{i+10}", "artist": "Artist2", "provider": "yt",
             "track_title": f"Song {i+10}", "weight": 0.5}
            for i in range(5)
        ])
        
        selected = generator._apply_diversity_rules(candidates, 10)
        
        # Should have max 3 from any artist
        artist_counts = {}
        for track in selected:
            artist = track["artist"]
            artist_counts[artist] = artist_counts.get(artist, 0) + 1
        
        assert all(count <= 3 for count in artist_counts.values())

