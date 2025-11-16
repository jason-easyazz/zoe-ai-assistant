"""
Mem-Agent Service for Zoe
Fast semantic memory search using FAISS and sentence-transformers
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
import os
import json
import sqlite3
import logging
from pathlib import Path
import numpy as np

# Try to import vector libraries
try:
    from sentence_transformers import SentenceTransformer
    import faiss
    VECTOR_LIBS_AVAILABLE = True
except ImportError:
    VECTOR_LIBS_AVAILABLE = False
    logging.warning("sentence-transformers or faiss not available, using fallback")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe Mem-Agent", version="1.0.0")

# Configuration
MEMORY_DIR = os.getenv("MEMORY_DIR", "/app/memory")
DB_PATH = os.getenv("DB_PATH", "/app/data/zoe.db")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "2"))
TIMEOUT = float(os.getenv("TIMEOUT", "2.0"))

# Global model and index
model = None
index = None
memory_vectors = []
memory_metadata = []


class SearchRequest(BaseModel):
    query: str
    user_id: str
    max_results: int = 5
    include_graph: bool = True


class SearchResult(BaseModel):
    results: List[Dict]
    graph: Dict
    confidence: float
    fallback: bool = False


def load_memory_data():
    """Load memory data from database and files"""
    global memory_vectors, memory_metadata
    
    memory_vectors = []
    memory_metadata = []
    
    # Load from SQLite database
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Load people
            cursor.execute("SELECT name, profile, facts FROM people")
            for row in cursor.fetchall():
                name, profile_json, facts_json = row
                profile = json.loads(profile_json) if profile_json else {}
                facts = json.loads(facts_json) if facts_json else {}
                
                # Create searchable text
                text = f"{name} {profile.get('relationship', '')} {facts.get('notes', '')}"
                memory_metadata.append({
                    "entity": name,
                    "content": text,
                    "type": "person",
                    "file": None
                })
            
            # Load projects
            cursor.execute("SELECT name, description, status FROM projects")
            for row in cursor.fetchall():
                name, description, status = row
                text = f"{name} {description or ''} {status or ''}"
                memory_metadata.append({
                    "entity": name,
                    "content": text,
                    "type": "project",
                    "file": None
                })
            
            conn.close()
            logger.info(f"✅ Loaded {len(memory_metadata)} memory entries from database")
        except Exception as e:
            logger.warning(f"Failed to load from database: {e}")
    
    # Load from memory directory files
    memory_path = Path(MEMORY_DIR)
    if memory_path.exists():
        for file_path in memory_path.rglob("*.json"):
            try:
                with open(file_path, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        text = json.dumps(data)
                        memory_metadata.append({
                            "entity": data.get("name", file_path.stem),
                            "content": text,
                            "type": "file",
                            "file": str(file_path)
                        })
            except Exception as e:
                logger.debug(f"Failed to load {file_path}: {e}")
    
    # Generate vectors if model is available
    if VECTOR_LIBS_AVAILABLE and model and memory_metadata:
        try:
            texts = [m["content"] for m in memory_metadata]
            vectors = model.encode(texts, show_progress_bar=False)
            memory_vectors = vectors.astype('float32')
            
            # Normalize for cosine similarity
            faiss.normalize_L2(memory_vectors)
            
            # Create FAISS index
            dimension = memory_vectors.shape[1]
            global index
            index = faiss.IndexFlatIP(dimension)
            index.add(memory_vectors)
            
            logger.info(f"✅ Created FAISS index with {len(memory_metadata)} vectors")
        except Exception as e:
            logger.warning(f"Failed to create vector index: {e}")
            memory_vectors = []


@app.on_event("startup")
async def startup():
    """Initialize model and load memory data"""
    global model
    
    if VECTOR_LIBS_AVAILABLE:
        try:
            logger.info("Loading sentence transformer model...")
            model = SentenceTransformer('all-MiniLM-L6-v2')
            logger.info("✅ Model loaded")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            model = None
    
    load_memory_data()


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "vector_libs": VECTOR_LIBS_AVAILABLE,
        "model_loaded": model is not None,
        "index_size": len(memory_metadata),
        "has_vectors": len(memory_vectors) > 0
    }


@app.post("/search", response_model=SearchResult)
async def search(request: SearchRequest):
    """Semantic search in memory"""
    
    if not memory_metadata:
        return SearchResult(
            results=[],
            graph={},
            confidence=0.0,
            fallback=True
        )
    
    # If we have vectors, use semantic search
    if VECTOR_LIBS_AVAILABLE and model and index is not None and len(memory_vectors) > 0:
        try:
            # Encode query
            query_vector = model.encode([request.query], show_progress_bar=False)
            query_vector = query_vector.astype('float32')
            faiss.normalize_L2(query_vector)
            
            # Search
            k = min(request.max_results, len(memory_metadata))
            scores, indices = index.search(query_vector, k)
            
            # Format results
            results = []
            for i, (score, idx) in enumerate(zip(scores[0], indices[0])):
                if idx < len(memory_metadata):
                    metadata = memory_metadata[idx]
                    results.append({
                        "entity": metadata["entity"],
                        "content": metadata["content"][:500],  # Truncate
                        "score": float(score),
                        "related_entities": [],
                        "file": metadata.get("file")
                    })
            
            # Calculate confidence (average score)
            confidence = float(np.mean(scores[0])) if len(scores[0]) > 0 else 0.0
            
            return SearchResult(
                results=results,
                graph={},
                confidence=confidence,
                fallback=False
            )
        except Exception as e:
            logger.warning(f"Vector search failed: {e}, using fallback")
    
    # Fallback: simple text matching
    query_lower = request.query.lower()
    results = []
    
    for metadata in memory_metadata:
        content_lower = metadata["content"].lower()
        if query_lower in content_lower:
            # Simple relevance score
            score = content_lower.count(query_lower) / max(len(content_lower), 1)
            results.append({
                "entity": metadata["entity"],
                "content": metadata["content"][:500],
                "score": score,
                "related_entities": [],
                "file": metadata.get("file")
            })
    
    # Sort by score and limit
    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:request.max_results]
    
    return SearchResult(
        results=results,
        graph={},
        confidence=0.5 if results else 0.0,
        fallback=True
    )


@app.post("/reload")
async def reload():
    """Reload memory data"""
    load_memory_data()
    return {"status": "reloaded", "count": len(memory_metadata)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=MAX_WORKERS)

