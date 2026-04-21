"""
Embedding Service
=================

Generates audio and metadata embeddings for music similarity.
Jetson-only - requires GPU for CLAP model.

Embedding Types:
- Audio: 512-dim CLAP embedding from audio waveform
- Metadata: 384-dim text embedding from title + artist
- Fused: 256-dim combined embedding for similarity search

Uses LAION's CLAP model for audio understanding and
sentence-transformers for metadata embeddings.
"""

import os
import json
import base64
import logging
import sqlite3
import asyncio
import numpy as np
from typing import Dict, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

# Check platform
try:
    from model_config import detect_hardware
    if detect_hardware() != "jetson":
        raise ImportError("EmbeddingService requires Jetson GPU")
except ImportError as e:
    logger.warning(f"EmbeddingService: {e}")


class EmbeddingService:
    """
    Generate and manage embeddings for music similarity.
    
    Jetson-only component that uses:
    - CLAP model for audio embeddings (understands "vibe")
    - sentence-transformers for metadata embeddings
    - Learned projection for fused embeddings
    """
    
    # Embedding dimensions
    AUDIO_DIM = 512
    METADATA_DIM = 384
    FUSED_DIM = 256
    
    def __init__(self):
        """Initialize embedding models (lazy loaded)."""
        self._clap_model = None
        self._clap_processor = None
        self._text_model = None
        self._projection = None
        
        self._clap_available = False
        self._text_available = False
        
        self._init_db()
        self._init_models()
    
    def _init_db(self):
        """Ensure embeddings table exists."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS music_embeddings (
                    track_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL DEFAULT 'youtube_music',
                    audio_embedding TEXT,
                    metadata_embedding TEXT,
                    fused_embedding TEXT,
                    track_title TEXT,
                    artist TEXT,
                    album TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to init embeddings table: {e}")
    
    def _init_models(self):
        """Initialize embedding models."""
        # Try to load CLAP
        try:
            from transformers import ClapModel, ClapProcessor
            import torch
            
            # Use smaller CLAP model for efficiency
            model_name = "laion/clap-htsat-unfused"
            
            logger.info(f"Loading CLAP model: {model_name}")
            self._clap_model = ClapModel.from_pretrained(model_name)
            self._clap_processor = ClapProcessor.from_pretrained(model_name)
            
            # Move to GPU if available
            if torch.cuda.is_available():
                self._clap_model = self._clap_model.cuda()
                logger.info("CLAP model on GPU")
            
            self._clap_model.eval()
            self._clap_available = True
            logger.info("✅ CLAP model loaded")
            
        except Exception as e:
            logger.warning(f"CLAP model not available: {e}")
            self._clap_available = False
        
        # Try to load sentence-transformers
        try:
            from sentence_transformers import SentenceTransformer
            
            # Use same model as mem-agent for consistency
            self._text_model = SentenceTransformer('all-MiniLM-L6-v2')
            self._text_available = True
            logger.info("✅ Text embedding model loaded")
            
        except Exception as e:
            logger.warning(f"Text embedding model not available: {e}")
            self._text_available = False
        
        # Initialize projection layer (simple linear projection)
        self._init_projection()
    
    def _init_projection(self):
        """Initialize projection layer for fused embeddings."""
        try:
            import torch
            import torch.nn as nn
            
            # Simple linear projection from combined dims to fused dim
            input_dim = self.AUDIO_DIM + self.METADATA_DIM
            
            self._projection = nn.Sequential(
                nn.Linear(input_dim, self.FUSED_DIM * 2),
                nn.ReLU(),
                nn.Linear(self.FUSED_DIM * 2, self.FUSED_DIM),
                nn.LayerNorm(self.FUSED_DIM)
            )
            
            # Initialize with Xavier
            for module in self._projection.modules():
                if isinstance(module, nn.Linear):
                    nn.init.xavier_uniform_(module.weight)
            
            if torch.cuda.is_available():
                self._projection = self._projection.cuda()
            
            self._projection.eval()
            
        except Exception as e:
            logger.warning(f"Projection layer init failed: {e}")
            self._projection = None
    
    @property
    def is_available(self) -> bool:
        """Check if embedding service is fully available."""
        return self._text_available  # At minimum, text embeddings should work
    
    async def get_audio_embedding(self, audio_path: str) -> Optional[np.ndarray]:
        """
        Generate audio embedding from audio file using CLAP.
        
        Args:
            audio_path: Path to audio file
            
        Returns:
            512-dim numpy array or None
        """
        if not self._clap_available:
            return None
        
        try:
            import torch
            import librosa
            
            # Load audio
            audio, sr = librosa.load(audio_path, sr=48000)
            
            # Process through CLAP
            inputs = self._clap_processor(
                audios=audio,
                return_tensors="pt",
                sampling_rate=48000
            )
            
            if torch.cuda.is_available():
                inputs = {k: v.cuda() for k, v in inputs.items()}
            
            with torch.no_grad():
                audio_features = self._clap_model.get_audio_features(**inputs)
            
            embedding = audio_features.cpu().numpy().flatten()
            
            return embedding
            
        except Exception as e:
            logger.warning(f"Audio embedding failed: {e}")
            return None
    
    async def get_metadata_embedding(
        self,
        title: str,
        artist: str,
        album: Optional[str] = None
    ) -> Optional[np.ndarray]:
        """
        Generate text embedding from track metadata.
        
        Args:
            title: Track title
            artist: Artist name
            album: Optional album name
            
        Returns:
            384-dim numpy array or None
        """
        if not self._text_available:
            return None
        
        try:
            # Build text representation
            text = f"{artist} - {title}"
            if album:
                text += f" from {album}"
            
            # Generate embedding
            embedding = self._text_model.encode(text)
            
            return embedding.astype(np.float32)
            
        except Exception as e:
            logger.warning(f"Metadata embedding failed: {e}")
            return None
    
    async def get_fused_embedding(
        self,
        audio_embedding: Optional[np.ndarray],
        metadata_embedding: np.ndarray
    ) -> Optional[np.ndarray]:
        """
        Generate fused embedding from audio and metadata embeddings.
        
        If audio embedding is not available, uses zero-padded metadata.
        
        Args:
            audio_embedding: 512-dim audio embedding (or None)
            metadata_embedding: 384-dim text embedding
            
        Returns:
            256-dim fused numpy array
        """
        try:
            import torch
            
            # Handle missing audio embedding
            if audio_embedding is None:
                audio_embedding = np.zeros(self.AUDIO_DIM, dtype=np.float32)
            
            # Concatenate embeddings
            combined = np.concatenate([audio_embedding, metadata_embedding])
            
            if self._projection is not None:
                # Apply learned projection
                with torch.no_grad():
                    tensor = torch.tensor(combined).unsqueeze(0)
                    if torch.cuda.is_available():
                        tensor = tensor.cuda()
                    
                    fused = self._projection(tensor)
                    return fused.cpu().numpy().flatten()
            else:
                # Simple dimensionality reduction (PCA-like)
                # Take first FUSED_DIM dimensions
                return combined[:self.FUSED_DIM]
            
        except Exception as e:
            logger.warning(f"Fused embedding failed: {e}")
            return None
    
    async def create_embedding(
        self,
        track_id: str,
        title: str,
        artist: str,
        album: Optional[str] = None,
        audio_path: Optional[str] = None
    ) -> Optional[np.ndarray]:
        """
        Create and cache all embeddings for a track.
        
        Args:
            track_id: Track identifier
            title: Track title
            artist: Artist name
            album: Optional album name
            audio_path: Optional path to audio file
            
        Returns:
            256-dim fused embedding or None
        """
        try:
            # Get audio embedding (optional)
            audio_emb = None
            if audio_path and self._clap_available:
                audio_emb = await self.get_audio_embedding(audio_path)
            
            # Get metadata embedding (required)
            meta_emb = await self.get_metadata_embedding(title, artist, album)
            
            if meta_emb is None:
                return None
            
            # Get fused embedding
            fused_emb = await self.get_fused_embedding(audio_emb, meta_emb)
            
            if fused_emb is None:
                return None
            
            # Cache embeddings
            await self._save_embeddings(
                track_id=track_id,
                audio_embedding=audio_emb,
                metadata_embedding=meta_emb,
                fused_embedding=fused_emb,
                title=title,
                artist=artist,
                album=album
            )
            
            return fused_emb
            
        except Exception as e:
            logger.error(f"Create embedding failed for {track_id}: {e}")
            return None
    
    async def get_cached_embedding(self, track_id: str) -> Optional[np.ndarray]:
        """
        Get cached fused embedding for a track.
        
        Args:
            track_id: Track identifier
            
        Returns:
            256-dim fused embedding or None
        """
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT fused_embedding FROM music_embeddings
                WHERE track_id = ?
            """, (track_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row and row[0]:
                # Decode from base64
                return self._decode_embedding(row[0])
            
            return None
            
        except Exception as e:
            logger.warning(f"Get cached embedding failed: {e}")
            return None
    
    async def _save_embeddings(
        self,
        track_id: str,
        audio_embedding: Optional[np.ndarray],
        metadata_embedding: np.ndarray,
        fused_embedding: np.ndarray,
        title: str,
        artist: str,
        album: Optional[str]
    ):
        """Save embeddings to database."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO music_embeddings
                (track_id, provider, audio_embedding, metadata_embedding, 
                 fused_embedding, track_title, artist, album, updated_at)
                VALUES (?, 'youtube_music', ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                track_id,
                self._encode_embedding(audio_embedding) if audio_embedding is not None else None,
                self._encode_embedding(metadata_embedding),
                self._encode_embedding(fused_embedding),
                title,
                artist,
                album
            ))
            
            conn.commit()
            conn.close()
            
            logger.debug(f"Saved embeddings for {track_id}")
            
        except Exception as e:
            logger.error(f"Save embeddings failed: {e}")
    
    def _encode_embedding(self, embedding: np.ndarray) -> str:
        """Encode embedding to base64 string."""
        return base64.b64encode(embedding.astype(np.float32).tobytes()).decode()
    
    def _decode_embedding(self, encoded: str) -> np.ndarray:
        """Decode embedding from base64 string."""
        return np.frombuffer(base64.b64decode(encoded), dtype=np.float32)


# Singleton
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """Get the singleton embedding service instance."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service

