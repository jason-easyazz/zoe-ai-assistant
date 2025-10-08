from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import sqlite3
import json
import numpy as np
try:
    from sentence_transformers import SentenceTransformer
except ImportError as e:
    print(f"❌ Failed to import SentenceTransformer: {e}")
    # Fallback to a simpler approach
    SentenceTransformer = None
import faiss
import pickle
import os
from datetime import datetime
import logging

router = APIRouter(prefix="/api/vector-search", tags=["vector-search"])

logger = logging.getLogger(__name__)

class SearchQuery(BaseModel):
    query: str
    search_type: str = "all"  # all, tasks, memories, lists
    limit: int = 10
    threshold: float = 0.5

class SearchResult(BaseModel):
    id: str
    content: str
    type: str  # task, memory, list_item
    similarity_score: float
    metadata: Dict[str, Any]
    created_at: str

class VectorSearchEngine:
    def __init__(self):
        self.model = None
        self.index = None
        self.metadata = []
        self.embeddings = []
        self.db_path = "/app/data/vector_search.db"
        self.index_path = "/app/data/faiss_index.pkl"
        self.embeddings_path = "/app/data/embeddings.pkl"
        self.metadata_path = "/app/data/metadata.pkl"
        
        # Initialize with lightweight model for Pi 5
        self.load_model()
        self.load_or_create_index()
    
    def load_model(self):
        """Load lightweight sentence transformer model"""
        try:
            if SentenceTransformer is None:
                logger.error("❌ SentenceTransformer not available")
                self.model = None
                return
                
            # Use a lightweight model suitable for Pi 5
            self.model = SentenceTransformer('all-MiniLM-L6-v2')
            logger.info("✅ Vector search model loaded")
        except Exception as e:
            logger.error(f"❌ Failed to load model: {e}")
            self.model = None
    
    def load_or_create_index(self):
        """Load existing FAISS index or create new one"""
        try:
            if os.path.exists(self.index_path) and os.path.exists(self.embeddings_path):
                # Load existing index
                with open(self.index_path, 'rb') as f:
                    self.index = pickle.load(f)
                with open(self.embeddings_path, 'rb') as f:
                    self.embeddings = pickle.load(f)
                with open(self.metadata_path, 'rb') as f:
                    self.metadata = pickle.load(f)
                logger.info(f"✅ Loaded existing vector index with {len(self.metadata)} items")
            else:
                # Create new index
                self.index = faiss.IndexFlatIP(384)  # 384 dimensions for all-MiniLM-L6-v2
                self.embeddings = []
                self.metadata = []
                logger.info("✅ Created new vector index")
        except Exception as e:
            logger.error(f"❌ Failed to load/create index: {e}")
            # Create fresh index
            self.index = faiss.IndexFlatIP(384)
            self.embeddings = []
            self.metadata = []
    
    def save_index(self):
        """Save FAISS index and metadata to disk"""
        try:
            with open(self.index_path, 'wb') as f:
                pickle.dump(self.index, f)
            with open(self.embeddings_path, 'wb') as f:
                pickle.dump(self.embeddings, f)
            with open(self.metadata_path, 'wb') as f:
                pickle.dump(self.metadata, f)
            logger.info("✅ Vector index saved")
        except Exception as e:
            logger.error(f"❌ Failed to save index: {e}")
    
    def add_document(self, content: str, doc_id: str, doc_type: str, metadata: Dict[str, Any] = None):
        """Add a document to the vector index"""
        try:
            if self.model is None:
                logger.error("❌ Model not available for adding document")
                return False
                
            # Generate embedding
            embedding = self.model.encode([content])[0]
            
            # Normalize for cosine similarity
            embedding = embedding / np.linalg.norm(embedding)
            
            # Add to index
            self.index.add(embedding.reshape(1, -1))
            self.embeddings.append(embedding)
            self.metadata.append({
                'id': doc_id,
                'content': content,
                'type': doc_type,
                'metadata': metadata or {},
                'created_at': datetime.now().isoformat()
            })
            
            # Save index
            self.save_index()
            
            logger.info(f"✅ Added document {doc_id} to vector index")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to add document: {e}")
            return False
    
    def search(self, query: str, search_type: str = "all", limit: int = 10, threshold: float = 0.5):
        """Search for similar documents"""
        try:
            if len(self.metadata) == 0:
                return []
            
            if self.model is None:
                logger.error("❌ Model not available for search")
                return []
            
            # Generate query embedding
            query_embedding = self.model.encode([query])[0]
            query_embedding = query_embedding / np.linalg.norm(query_embedding)
            
            # Search
            scores, indices = self.index.search(query_embedding.reshape(1, -1), min(limit * 2, len(self.metadata)))
            
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx >= len(self.metadata):
                    continue
                    
                item = self.metadata[idx]
                
                # Filter by type if specified
                if search_type != "all" and item['type'] != search_type:
                    continue
                
                # Apply threshold
                if score < threshold:
                    continue
                
                results.append(SearchResult(
                    id=item['id'],
                    content=item['content'],
                    type=item['type'],
                    similarity_score=float(score),
                    metadata=item['metadata'],
                    created_at=item['created_at']
                ))
            
            # Sort by similarity score
            results.sort(key=lambda x: x.similarity_score, reverse=True)
            
            return results[:limit]
            
        except Exception as e:
            logger.error(f"❌ Search failed: {e}")
            return []
    
    def rebuild_index(self):
        """Rebuild the entire index from database"""
        try:
            logger.info("🔄 Rebuilding vector index...")
            
            # Clear existing index
            self.index = faiss.IndexFlatIP(384)
            self.embeddings = []
            self.metadata = []
            
            # Get all tasks from developer_tasks.db
            conn = sqlite3.connect('/app/data/developer_tasks.db')
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Add developer tasks
            cursor.execute("SELECT id, title, objective, requirements, constraints, acceptance_criteria FROM dynamic_tasks")
            for row in cursor.fetchall():
                content = f"{row['title']} {row['objective']} {' '.join(json.loads(row['requirements'] or '[]'))}"
                self.add_document(content, row['id'], 'task', {
                    'title': row['title'],
                    'objective': row['objective']
                })
            
            conn.close()
            
            # Get all list items from zoe.db
            conn = sqlite3.connect('/app/data/zoe.db')
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Add list items
            cursor.execute("SELECT id, text, category, list_type FROM list_items")
            for row in cursor.fetchall():
                self.add_document(row['text'], f"list_item_{row['id']}", 'list_item', {
                    'category': row['category'],
                    'list_type': row['list_type']
                })
            
            conn.close()
            
            # Save rebuilt index
            self.save_index()
            
            logger.info(f"✅ Rebuilt vector index with {len(self.metadata)} items")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to rebuild index: {e}")
            return False

# Initialize vector search engine
vector_engine = VectorSearchEngine()

@router.post("/search")
async def search_documents(query: SearchQuery):
    """Search for similar documents using vector similarity"""
    try:
        results = vector_engine.search(
            query.query,
            query.search_type,
            query.limit,
            query.threshold
        )
        
        return {
            "query": query.query,
            "results": [result.dict() for result in results],
            "total_found": len(results),
            "search_time": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.post("/add-document")
async def add_document(
    content: str,
    doc_id: str,
    doc_type: str,
    metadata: Optional[Dict[str, Any]] = None
):
    """Add a document to the vector index"""
    try:
        success = vector_engine.add_document(content, doc_id, doc_type, metadata)
        
        if success:
            return {"message": "Document added successfully", "doc_id": doc_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to add document")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add document: {str(e)}")

@router.post("/rebuild-index")
async def rebuild_index():
    """Rebuild the entire vector index from database"""
    try:
        success = vector_engine.rebuild_index()
        
        if success:
            return {
                "message": "Index rebuilt successfully",
                "total_documents": len(vector_engine.metadata),
                "rebuild_time": datetime.now().isoformat()
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to rebuild index")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to rebuild index: {str(e)}")

@router.get("/stats")
async def get_index_stats():
    """Get vector index statistics"""
    try:
        return {
            "total_documents": len(vector_engine.metadata),
            "index_size": vector_engine.index.ntotal if vector_engine.index else 0,
            "model_name": "all-MiniLM-L6-v2",
            "embedding_dimensions": 384,
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")

@router.get("/similar/{doc_id}")
async def find_similar_documents(
    doc_id: str,
    limit: int = Query(5, ge=1, le=20),
    threshold: float = Query(0.5, ge=0.0, le=1.0)
):
    """Find documents similar to a specific document"""
    try:
        # Find the document
        doc_metadata = None
        for i, meta in enumerate(vector_engine.metadata):
            if meta['id'] == doc_id:
                doc_metadata = meta
                break
        
        if not doc_metadata:
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Search for similar documents
        results = vector_engine.search(
            doc_metadata['content'],
            "all",
            limit + 1,  # +1 to exclude the original document
            0.0  # Lower threshold to get more results
        )
        
        # Filter out the original document
        similar_results = [r for r in results if r.id != doc_id]
        
        return {
            "original_document": {
                "id": doc_metadata['id'],
                "content": doc_metadata['content'],
                "type": doc_metadata['type']
            },
            "similar_documents": [result.dict() for result in similar_results[:limit]],
            "total_found": len(similar_results)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to find similar documents: {str(e)}")

@router.delete("/document/{doc_id}")
async def remove_document(doc_id: str):
    """Remove a document from the vector index"""
    try:
        # Find and remove the document
        for i, meta in enumerate(vector_engine.metadata):
            if meta['id'] == doc_id:
                # Remove from metadata
                vector_engine.metadata.pop(i)
                
                # Remove from embeddings
                vector_engine.embeddings.pop(i)
                
                # Rebuild FAISS index
                vector_engine.index = faiss.IndexFlatIP(384)
                if vector_engine.embeddings:
                    vector_engine.index.add(np.array(vector_engine.embeddings))
                
                # Save updated index
                vector_engine.save_index()
                
                return {"message": "Document removed successfully", "doc_id": doc_id}
        
        raise HTTPException(status_code=404, detail="Document not found")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to remove document: {str(e)}")
