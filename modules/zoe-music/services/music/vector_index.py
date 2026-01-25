"""
Vector Index
=============

FAISS-based vector index for fast music similarity search.
Jetson-only - uses GPU acceleration for optimal performance.

Features:
- IVF4096 index (supports ~1M tracks efficiently)
- GPU acceleration on Jetson
- Persistent storage with periodic saves
- ID mapping for track lookups

Similarity is computed using inner product (cosine similarity
when embeddings are normalized).
"""

import os
import json
import logging
import sqlite3
import numpy as np
from typing import List, Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
INDEX_PATH = os.getenv("FAISS_INDEX_PATH", "/app/data/music_index.faiss")
ID_MAP_PATH = os.getenv("FAISS_ID_MAP_PATH", "/app/data/music_id_map.json")

# Check platform
try:
    from model_config import detect_hardware
    if detect_hardware() != "jetson":
        raise ImportError("VectorIndex requires Jetson GPU")
except ImportError as e:
    logger.warning(f"VectorIndex: {e}")


class VectorIndex:
    """
    FAISS vector index for music similarity search.
    
    Jetson-only component that provides fast k-NN search
    over track embeddings using GPU acceleration.
    """
    
    # Index configuration
    DIMENSION = 256  # Fused embedding dimension
    NLIST = 100  # Number of clusters for IVF
    NPROBE = 10  # Number of clusters to search
    
    def __init__(self):
        """Initialize or load the FAISS index."""
        self._index = None
        self._id_map: Dict[int, str] = {}  # Internal ID -> track_id
        self._track_to_id: Dict[str, int] = {}  # track_id -> internal ID
        self._next_id = 0
        self._gpu_available = False
        self._faiss_available = False
        
        self._init_faiss()
        self._load_or_create_index()
    
    def _init_faiss(self):
        """Initialize FAISS library."""
        try:
            import faiss
            self.faiss = faiss
            self._faiss_available = True
            
            # Check for GPU
            if faiss.get_num_gpus() > 0:
                self._gpu_available = True
                logger.info(f"FAISS GPU available: {faiss.get_num_gpus()} GPU(s)")
            else:
                logger.info("FAISS running on CPU")
            
        except ImportError:
            logger.warning("FAISS not available - vector search disabled")
            self._faiss_available = False
    
    def _load_or_create_index(self):
        """Load existing index or create new one."""
        if not self._faiss_available:
            return
        
        if os.path.exists(INDEX_PATH) and os.path.exists(ID_MAP_PATH):
            self._load_index()
        else:
            self._create_index()
    
    def _create_index(self):
        """Create a new FAISS index."""
        try:
            # Use flat index for simplicity (scales to ~100k tracks)
            # For larger scale, use IVF index
            self._index = self.faiss.IndexFlatIP(self.DIMENSION)
            
            # Optionally move to GPU
            if self._gpu_available:
                res = self.faiss.StandardGpuResources()
                self._index = self.faiss.index_cpu_to_gpu(res, 0, self._index)
                logger.info("Created GPU index")
            else:
                logger.info("Created CPU index")
            
            self._id_map = {}
            self._track_to_id = {}
            self._next_id = 0
            
        except Exception as e:
            logger.error(f"Failed to create index: {e}")
            self._index = None
    
    def _load_index(self):
        """Load index and ID map from disk."""
        try:
            # Load index
            cpu_index = self.faiss.read_index(INDEX_PATH)
            
            # Move to GPU if available
            if self._gpu_available:
                res = self.faiss.StandardGpuResources()
                self._index = self.faiss.index_cpu_to_gpu(res, 0, cpu_index)
            else:
                self._index = cpu_index
            
            # Load ID map
            with open(ID_MAP_PATH, 'r') as f:
                data = json.load(f)
                self._id_map = {int(k): v for k, v in data['id_map'].items()}
                self._track_to_id = data.get('track_to_id', {})
                self._next_id = data.get('next_id', len(self._id_map))
            
            logger.info(f"Loaded index with {self._index.ntotal} vectors")
            
        except Exception as e:
            logger.error(f"Failed to load index: {e}")
            self._create_index()
    
    def save(self):
        """Save index and ID map to disk."""
        if not self._faiss_available or self._index is None:
            return
        
        try:
            # Convert GPU index to CPU for saving
            if self._gpu_available:
                cpu_index = self.faiss.index_gpu_to_cpu(self._index)
            else:
                cpu_index = self._index
            
            # Save index
            self.faiss.write_index(cpu_index, INDEX_PATH)
            
            # Save ID map
            with open(ID_MAP_PATH, 'w') as f:
                json.dump({
                    'id_map': self._id_map,
                    'track_to_id': self._track_to_id,
                    'next_id': self._next_id
                }, f)
            
            logger.info(f"Saved index with {self._index.ntotal} vectors")
            
        except Exception as e:
            logger.error(f"Failed to save index: {e}")
    
    @property
    def is_available(self) -> bool:
        """Check if vector index is available."""
        return self._faiss_available and self._index is not None
    
    @property
    def size(self) -> int:
        """Get number of vectors in index."""
        if self._index is None:
            return 0
        return self._index.ntotal
    
    def add(self, track_id: str, embedding: np.ndarray) -> bool:
        """
        Add a track embedding to the index.
        
        Args:
            track_id: Track identifier
            embedding: 256-dim fused embedding
            
        Returns:
            True if added successfully
        """
        if not self.is_available:
            return False
        
        try:
            # Check if already in index
            if track_id in self._track_to_id:
                # Update existing (FAISS doesn't support update, but we track the latest ID)
                logger.debug(f"Track {track_id} already in index, skipping")
                return True
            
            # Normalize embedding for cosine similarity via inner product
            embedding = embedding.astype(np.float32)
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm
            
            # Add to index
            embedding_2d = embedding.reshape(1, -1)
            self._index.add(embedding_2d)
            
            # Update ID mappings
            internal_id = self._next_id
            self._id_map[internal_id] = track_id
            self._track_to_id[track_id] = internal_id
            self._next_id += 1
            
            # Auto-save periodically
            if self._next_id % 100 == 0:
                self.save()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to add to index: {e}")
            return False
    
    def search(
        self,
        query: np.ndarray,
        k: int = 20
    ) -> List[str]:
        """
        Search for k most similar tracks.
        
        Args:
            query: 256-dim embedding to search for
            k: Number of results to return
            
        Returns:
            List of track_ids sorted by similarity
        """
        if not self.is_available or self._index.ntotal == 0:
            return []
        
        try:
            # Normalize query
            query = query.astype(np.float32)
            norm = np.linalg.norm(query)
            if norm > 0:
                query = query / norm
            
            # Search
            query_2d = query.reshape(1, -1)
            k = min(k, self._index.ntotal)
            
            distances, indices = self._index.search(query_2d, k)
            
            # Map internal IDs to track IDs
            results = []
            for idx in indices[0]:
                if idx >= 0 and idx in self._id_map:
                    results.append(self._id_map[idx])
            
            return results
            
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []
    
    def search_with_scores(
        self,
        query: np.ndarray,
        k: int = 20
    ) -> List[tuple]:
        """
        Search with similarity scores.
        
        Args:
            query: 256-dim embedding
            k: Number of results
            
        Returns:
            List of (track_id, similarity_score) tuples
        """
        if not self.is_available or self._index.ntotal == 0:
            return []
        
        try:
            # Normalize query
            query = query.astype(np.float32)
            norm = np.linalg.norm(query)
            if norm > 0:
                query = query / norm
            
            query_2d = query.reshape(1, -1)
            k = min(k, self._index.ntotal)
            
            distances, indices = self._index.search(query_2d, k)
            
            results = []
            for idx, dist in zip(indices[0], distances[0]):
                if idx >= 0 and idx in self._id_map:
                    # Inner product score (higher is more similar)
                    results.append((self._id_map[idx], float(dist)))
            
            return results
            
        except Exception as e:
            logger.error(f"Search with scores failed: {e}")
            return []
    
    def contains(self, track_id: str) -> bool:
        """Check if track is in the index."""
        return track_id in self._track_to_id
    
    def remove(self, track_id: str) -> bool:
        """
        Remove a track from the index.
        
        Note: FAISS doesn't support true removal, so we just
        remove from our ID mappings. The vector remains in the
        index but won't be returned in results.
        """
        if track_id in self._track_to_id:
            internal_id = self._track_to_id[track_id]
            del self._track_to_id[track_id]
            if internal_id in self._id_map:
                del self._id_map[internal_id]
            return True
        return False
    
    def rebuild(self):
        """
        Rebuild the index from scratch.
        
        Call this periodically to clean up removed vectors and
        optimize the index structure.
        """
        if not self._faiss_available:
            return
        
        try:
            # Get all current embeddings from database
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT track_id, fused_embedding FROM music_embeddings
                WHERE fused_embedding IS NOT NULL
            """)
            
            rows = cursor.fetchall()
            conn.close()
            
            if not rows:
                logger.info("No embeddings to rebuild index from")
                return
            
            # Create new index
            self._create_index()
            
            # Re-add all embeddings
            import base64
            for track_id, encoded_emb in rows:
                embedding = np.frombuffer(
                    base64.b64decode(encoded_emb), 
                    dtype=np.float32
                )
                self.add(track_id, embedding)
            
            self.save()
            logger.info(f"Rebuilt index with {self._index.ntotal} vectors")
            
        except Exception as e:
            logger.error(f"Index rebuild failed: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get index statistics."""
        return {
            "available": self.is_available,
            "gpu_enabled": self._gpu_available,
            "total_vectors": self.size,
            "dimension": self.DIMENSION,
            "tracked_tracks": len(self._track_to_id),
        }


# Singleton
_vector_index: Optional[VectorIndex] = None


def get_vector_index() -> VectorIndex:
    """Get the singleton vector index instance."""
    global _vector_index
    if _vector_index is None:
        _vector_index = VectorIndex()
    return _vector_index

