"""
Light RAG Enhanced Memory System for Zoe
Production-ready implementation with comprehensive testing
"""
import sqlite3
import json
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
import os
import logging
from pathlib import Path
import hashlib
import pickle
from dataclasses import dataclass
from enum import Enum

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EntityType(Enum):
    PERSON = "person"
    PROJECT = "project"
    GENERAL = "general"
    EVENT = "event"
    LOCATION = "location"

@dataclass
class MemoryResult:
    fact_id: int
    fact: str
    entity_type: str
    entity_id: int
    entity_name: str
    category: str
    importance: int
    similarity_score: float
    relationship_boost: float
    final_score: float
    entity_context: str
    relationship_path: str
    created_at: str

class LightRAGMemorySystem:
    """
    Production-ready Light RAG memory system for Zoe
    Combines vector embeddings with relationship graphs for intelligent retrieval
    """
    
    def __init__(self, db_path="/app/data/memory.db", embedding_model="all-MiniLM-L6-v2"):
        self.db_path = db_path
        self.embedding_model_name = embedding_model
        self.embedding_dim = 384  # Dimension for all-MiniLM-L6-v2
        self.similarity_threshold = 0.3
        self.relationship_boost_max = 0.4
        self.cache_ttl_hours = 24
        
        # Initialize embedding model
        self._init_embedding_model()
        
        # Initialize database
        self.init_enhanced_database()
        
        logger.info(f"Light RAG Memory System initialized with model: {embedding_model}")
    
    def _init_embedding_model(self):
        """Initialize the embedding model"""
        try:
            # Try to use sentence-transformers first
            from sentence_transformers import SentenceTransformer
            self.embedding_model = SentenceTransformer(self.embedding_model_name)
            logger.info("Embedding model loaded successfully")
        except ImportError:
            logger.warning("sentence-transformers not available, using fallback")
            # Fallback to simple hash-based embeddings for testing
            self.embedding_model = None
            self._use_fallback_embeddings = True
        except Exception as e:
            logger.warning(f"Failed to load embedding model: {e}, using fallback")
            self.embedding_model = None
            self._use_fallback_embeddings = True
    
    def init_enhanced_database(self):
        """Initialize enhanced database schema for Light RAG"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create base tables if they don't exist
        self._create_base_tables(cursor)
        
        # Add new columns to existing memory_facts table
        self._add_column_if_not_exists(cursor, "memory_facts", "embedding_vector", "BLOB")
        self._add_column_if_not_exists(cursor, "memory_facts", "entity_context", "TEXT")
        self._add_column_if_not_exists(cursor, "memory_facts", "relationship_path", "TEXT")
        self._add_column_if_not_exists(cursor, "memory_facts", "embedding_hash", "TEXT")
        
        # Create entity embeddings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entity_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                entity_name TEXT NOT NULL,
                embedding_vector BLOB NOT NULL,
                context_summary TEXT,
                embedding_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(entity_type, entity_id)
            )
        """)
        
        # Create relationship embeddings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS relationship_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                relationship_id INTEGER,
                relationship_type TEXT,
                entity1_name TEXT,
                entity2_name TEXT,
                embedding_vector BLOB,
                strength_score REAL DEFAULT 0.5,
                context_summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create search cache table for performance
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS search_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_hash TEXT UNIQUE NOT NULL,
                query_text TEXT NOT NULL,
                results_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        """)
        
        # Create indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_facts_embedding ON memory_facts(embedding_hash)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_entity_embeddings_type_id ON entity_embeddings(entity_type, entity_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_search_cache_hash ON search_cache(query_hash)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_search_cache_expires ON search_cache(expires_at)")
        
        conn.commit()
        conn.close()
        logger.info("Enhanced database schema initialized")
    
    def _create_base_tables(self, cursor):
        """Create base tables if they don't exist"""
        # People table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                relationship TEXT,
                notes TEXT,
                profile TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Projects table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Relationships table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person1_id INTEGER,
                person2_id INTEGER,
                relationship_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (person1_id) REFERENCES people(id),
                FOREIGN KEY (person2_id) REFERENCES people(id)
            )
        """)
        
        # Memory facts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT,
                entity_id INTEGER,
                fact TEXT NOT NULL,
                category TEXT,
                importance INTEGER DEFAULT 5,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    
    def _add_column_if_not_exists(self, cursor, table_name, column_name, column_type):
        """Add column to table if it doesn't exist"""
        try:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    
    def generate_embedding(self, text: str) -> bytes:
        """Generate embedding for text"""
        try:
            if self.embedding_model is not None:
                embedding = self.embedding_model.encode(text)
                return embedding.tobytes()
            else:
                # Fallback: create a simple hash-based embedding
                return self._generate_fallback_embedding(text)
        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            raise
    
    def _generate_fallback_embedding(self, text: str) -> bytes:
        """Generate a simple fallback embedding for testing"""
        import hashlib
        import struct
        
        # Create a simple hash-based embedding
        hash_obj = hashlib.sha256(text.encode())
        hash_bytes = hash_obj.digest()
        
        # Convert to float32 array of the right size
        embedding = []
        for i in range(0, len(hash_bytes), 4):
            chunk = hash_bytes[i:i+4]
            if len(chunk) == 4:
                val = struct.unpack('>I', chunk)[0] / (2**32)  # Normalize to 0-1
                embedding.append(val)
        
        # Pad or truncate to the right size
        while len(embedding) < self.embedding_dim:
            embedding.append(0.0)
        embedding = embedding[:self.embedding_dim]
        
        # Convert to bytes
        return struct.pack('f' * len(embedding), *embedding)
    
    def generate_embedding_hash(self, text: str) -> str:
        """Generate hash for embedding cache"""
        return hashlib.md5(text.encode()).hexdigest()
    
    def cosine_similarity(self, vec1: bytes, vec2: bytes) -> float:
        """Calculate cosine similarity between two embeddings"""
        try:
            arr1 = np.frombuffer(vec1, dtype=np.float32)
            arr2 = np.frombuffer(vec2, dtype=np.float32)
            
            dot_product = np.dot(arr1, arr2)
            norm1 = np.linalg.norm(arr1)
            norm2 = np.linalg.norm(arr2)
            
            if norm1 == 0 or norm2 == 0:
                return 0.0
            
            return dot_product / (norm1 * norm2)
        except Exception as e:
            logger.error(f"Failed to calculate cosine similarity: {e}")
            return 0.0
    
    def add_memory_with_embedding(self, entity_type: str, entity_id: int, 
                                 fact: str, category: str = "general", 
                                 importance: int = 5, source: str = "user") -> Dict:
        """Add memory fact with automatic embedding generation"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Generate embedding for the fact
            embedding = self.generate_embedding(fact)
            embedding_hash = self.generate_embedding_hash(fact)
            
            # Get entity context
            entity_context = self._get_entity_context(entity_type, entity_id)
            
            # Generate relationship path
            relationship_path = self._get_relationship_path(entity_type, entity_id)
            
            cursor.execute("""
                INSERT INTO memory_facts 
                (entity_type, entity_id, fact, category, importance, source,
                 embedding_vector, entity_context, relationship_path, embedding_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (entity_type, entity_id, fact, category, importance, source,
                  embedding, entity_context, relationship_path, embedding_hash))
            
            fact_id = cursor.lastrowid
            
            # Update entity embedding
            self._update_entity_embedding(entity_type, entity_id, entity_context)
            
            conn.commit()
            
            logger.info(f"Added memory with embedding: fact_id={fact_id}, entity={entity_type}:{entity_id}")
            
            return {
                "fact_id": fact_id,
                "embedding_generated": True,
                "entity_context": entity_context,
                "relationship_path": relationship_path,
                "embedding_hash": embedding_hash
            }
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to add memory with embedding: {e}")
            raise
        finally:
            conn.close()
    
    def _get_entity_context(self, entity_type: str, entity_id: int) -> str:
        """Get contextual information about an entity"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        context_parts = []
        
        try:
            if entity_type == "person":
                cursor.execute("SELECT name, profile FROM people WHERE id = ?", (entity_id,))
                person = cursor.fetchone()
                if person:
                    context_parts.append(f"Person: {person[0]}")
                    if person[1]:
                        try:
                            profile_data = json.loads(person[1])
                            if isinstance(profile_data, dict) and 'relationship' in profile_data:
                                context_parts.append(f"Relationship: {profile_data['relationship']}")
                        except:
                            pass
            
            elif entity_type == "project":
                cursor.execute("SELECT name, description FROM projects WHERE id = ?", (entity_id,))
                project = cursor.fetchone()
                if project:
                    context_parts.append(f"Project: {project[0]}")
                    if project[1]:
                        context_parts.append(f"Description: {project[1]}")
            
            return " | ".join(context_parts)
            
        finally:
            conn.close()
    
    def _get_relationship_path(self, entity_type: str, entity_id: int) -> str:
        """Get relationship path for an entity"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        relationships = []
        
        try:
            if entity_type == "person":
                cursor.execute("""
                    SELECT p2.name, r.relationship_type
                    FROM relationships r
                    JOIN people p2 ON r.person2_id = p2.id
                    WHERE r.person1_id = ?
                """, (entity_id,))
                
                for row in cursor.fetchall():
                    relationships.append(f"{row[1]}: {row[0]}")
            
            return " | ".join(relationships)
            
        finally:
            conn.close()
    
    def _update_entity_embedding(self, entity_type: str, entity_id: int, context: str):
        """Update entity embedding with current context"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Generate embedding for entity context
            embedding = self.generate_embedding(context)
            embedding_hash = self.generate_embedding_hash(context)
            
            # Get entity name
            if entity_type == "person":
                cursor.execute("SELECT name FROM people WHERE id = ?", (entity_id,))
            elif entity_type == "project":
                cursor.execute("SELECT name FROM projects WHERE id = ?", (entity_id,))
            else:
                return
            
            entity_row = cursor.fetchone()
            if not entity_row:
                return
            
            entity_name = entity_row[0]
            
            # Insert or update entity embedding
            cursor.execute("""
                INSERT OR REPLACE INTO entity_embeddings 
                (entity_type, entity_id, entity_name, embedding_vector, context_summary, embedding_hash)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (entity_type, entity_id, entity_name, embedding, context, embedding_hash))
            
            conn.commit()
            
        except Exception as e:
            logger.error(f"Failed to update entity embedding: {e}")
        finally:
            conn.close()
    
    def light_rag_search(self, query: str, limit: int = 10, use_cache: bool = True) -> List[MemoryResult]:
        """Light RAG search combining text and relationship awareness"""
        
        # Check cache first
        if use_cache:
            cached_result = self._get_cached_search(query)
            if cached_result:
                logger.info(f"Returning cached search results for: {query}")
                return cached_result
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Generate query embedding
            query_embedding = self.generate_embedding(query)
            
            # Get all memory facts with embeddings
            cursor.execute("""
                SELECT 
                    mf.id, mf.fact, mf.entity_type, mf.entity_id,
                    mf.category, mf.importance, mf.embedding_vector,
                    mf.entity_context, mf.relationship_path, mf.created_at,
                    CASE 
                        WHEN mf.entity_type = 'person' THEN COALESCE(p.name, 'Unknown Person')
                        WHEN mf.entity_type = 'project' THEN COALESCE(pr.name, 'Unknown Project')
                        ELSE 'General'
                    END as entity_name
                FROM memory_facts mf
                LEFT JOIN people p ON mf.entity_type = 'person' AND mf.entity_id = p.id
                LEFT JOIN projects pr ON mf.entity_type = 'project' AND mf.entity_id = pr.id
                WHERE mf.embedding_vector IS NOT NULL
            """)
            
            results = []
            for row in cursor.fetchall():
                # Calculate similarity
                similarity = self.cosine_similarity(query_embedding, row[6])
                
                # Skip low similarity results
                if similarity < self.similarity_threshold:
                    continue
                
                # Boost score based on relationships
                relationship_boost = self._calculate_relationship_boost(query, row[8])
                
                # Boost score based on importance
                importance_boost = (row[5] - 5) * 0.05  # Scale importance 1-10 to boost -0.2 to +0.25
                
                # Combined score
                final_score = similarity + relationship_boost + importance_boost
                
                result = MemoryResult(
                    fact_id=row[0],
                    fact=row[1],
                    entity_type=row[2],
                    entity_id=row[3],
                    entity_name=row[10],  # Fixed index
                    category=row[4],
                    importance=row[5],
                    similarity_score=similarity,
                    relationship_boost=relationship_boost,
                    final_score=final_score,
                    entity_context=row[7],
                    relationship_path=row[8],
                    created_at=row[9]
                )
                
                results.append(result)
            
            # Sort by final score and return top results
            results.sort(key=lambda x: x.final_score, reverse=True)
            top_results = results[:limit]
            
            # Cache results
            if use_cache:
                self._cache_search_results(query, top_results)
            
            logger.info(f"Light RAG search completed: {len(top_results)} results for '{query}'")
            return top_results
            
        except Exception as e:
            logger.error(f"Light RAG search failed: {e}")
            raise
        finally:
            conn.close()
    
    def _calculate_relationship_boost(self, query: str, relationship_path: str) -> float:
        """Calculate boost score based on relationship relevance"""
        if not relationship_path:
            return 0.0
        
        # Simple keyword matching for now
        query_lower = query.lower()
        path_lower = relationship_path.lower()
        
        # Check for relationship keywords in query
        relationship_keywords = [
            "friend", "family", "colleague", "partner", "team", "project",
            "together", "with", "knows", "related", "connected", "works with"
        ]
        
        boost = 0.0
        for keyword in relationship_keywords:
            if keyword in query_lower and keyword in path_lower:
                boost += 0.1
        
        return min(boost, self.relationship_boost_max)
    
    def _get_cached_search(self, query: str) -> Optional[List[MemoryResult]]:
        """Get cached search results"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            query_hash = self.generate_embedding_hash(query)
            cursor.execute("""
                SELECT results_json FROM search_cache 
                WHERE query_hash = ? AND expires_at > datetime('now')
            """, (query_hash,))
            
            row = cursor.fetchone()
            if row:
                results_data = json.loads(row[0])
                return [MemoryResult(**result) for result in results_data]
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get cached search: {e}")
            return None
        finally:
            conn.close()
    
    def _cache_search_results(self, query: str, results: List[MemoryResult]):
        """Cache search results"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            query_hash = self.generate_embedding_hash(query)
            expires_at = datetime.now() + timedelta(hours=self.cache_ttl_hours)
            
            # Convert MemoryResult objects to dictionaries with proper serialization
            results_data = []
            for result in results:
                result_dict = result.__dict__.copy()
                # Convert numpy types to Python types for JSON serialization
                for key, value in result_dict.items():
                    if hasattr(value, 'item'):  # numpy scalar
                        result_dict[key] = value.item()
                results_data.append(result_dict)
            
            cursor.execute("""
                INSERT OR REPLACE INTO search_cache 
                (query_hash, query_text, results_json, expires_at)
                VALUES (?, ?, ?, ?)
            """, (query_hash, query, json.dumps(results_data), expires_at))
            
            conn.commit()
            
        except Exception as e:
            logger.error(f"Failed to cache search results: {e}")
        finally:
            conn.close()
    
    def get_contextual_memories(self, entity_name: str, context_type: str = "all") -> List[Dict]:
        """Get memories with full contextual awareness"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Find entity
            cursor.execute("SELECT id FROM people WHERE name = ?", (entity_name,))
            person = cursor.fetchone()
            
            if not person:
                return []
            
            person_id = person[0]
            
            # Get direct memories
            cursor.execute("""
                SELECT fact, category, importance, entity_context, relationship_path, created_at
                FROM memory_facts
                WHERE entity_type = 'person' AND entity_id = ?
                ORDER BY importance DESC, created_at DESC
            """, (person_id,))
            
            memories = []
            for row in cursor.fetchall():
                memories.append({
                    "fact": row[0],
                    "category": row[1],
                    "importance": row[2],
                    "entity_context": row[3],
                    "relationship_path": row[4],
                    "created_at": row[5],
                    "type": "direct"
                })
            
            # Get related memories through relationships
            cursor.execute("""
                SELECT DISTINCT mf.fact, mf.category, mf.importance, mf.entity_context, 
                               mf.relationship_path, mf.created_at
                FROM memory_facts mf
                JOIN relationships r ON mf.entity_type = 'person' AND mf.entity_id = r.person2_id
                WHERE r.person1_id = ?
                ORDER BY mf.importance DESC
                LIMIT 5
            """, (person_id,))
            
            for row in cursor.fetchall():
                memories.append({
                    "fact": row[0],
                    "category": row[1],
                    "importance": row[2],
                    "entity_context": row[3],
                    "relationship_path": row[4],
                    "created_at": row[5],
                    "type": "related"
                })
            
            return memories
            
        except Exception as e:
            logger.error(f"Failed to get contextual memories: {e}")
            return []
        finally:
            conn.close()
    
    def migrate_existing_memories(self) -> Dict:
        """Migrate existing memories to include embeddings"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Get all memory facts without embeddings
            cursor.execute("""
                SELECT id, fact, entity_type, entity_id
                FROM memory_facts
                WHERE embedding_vector IS NULL
            """)
            
            facts = cursor.fetchall()
            logger.info(f"Found {len(facts)} memories to migrate")
            
            migrated_count = 0
            error_count = 0
            
            for fact_id, fact_text, entity_type, entity_id in facts:
                try:
                    # Generate embedding
                    embedding = self.generate_embedding(fact_text)
                    embedding_hash = self.generate_embedding_hash(fact_text)
                    
                    # Get context
                    entity_context = self._get_entity_context(entity_type, entity_id)
                    relationship_path = self._get_relationship_path(entity_type, entity_id)
                    
                    # Update the record
                    cursor.execute("""
                        UPDATE memory_facts 
                        SET embedding_vector = ?, entity_context = ?, relationship_path = ?, embedding_hash = ?
                        WHERE id = ?
                    """, (embedding, entity_context, relationship_path, embedding_hash, fact_id))
                    
                    migrated_count += 1
                    
                    if migrated_count % 10 == 0:
                        logger.info(f"Migrated {migrated_count} memories...")
                    
                except Exception as e:
                    logger.error(f"Error migrating memory {fact_id}: {e}")
                    error_count += 1
            
            conn.commit()
            
            logger.info(f"Migration complete: {migrated_count} migrated, {error_count} errors")
            
            return {
                "migrated_count": migrated_count,
                "error_count": error_count,
                "total_facts": len(facts)
            }
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise
        finally:
            conn.close()
    
    def get_system_stats(self) -> Dict:
        """Get system statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Count memories with embeddings
            cursor.execute("SELECT COUNT(*) FROM memory_facts WHERE embedding_vector IS NOT NULL")
            embedded_memories = cursor.fetchone()[0]
            
            # Count total memories
            cursor.execute("SELECT COUNT(*) FROM memory_facts")
            total_memories = cursor.fetchone()[0]
            
            # Count entity embeddings
            cursor.execute("SELECT COUNT(*) FROM entity_embeddings")
            entity_embeddings = cursor.fetchone()[0]
            
            # Count cached searches
            cursor.execute("SELECT COUNT(*) FROM search_cache WHERE expires_at > datetime('now')")
            cached_searches = cursor.fetchone()[0]
            
            return {
                "total_memories": total_memories,
                "embedded_memories": embedded_memories,
                "embedding_coverage": (embedded_memories / total_memories * 100) if total_memories > 0 else 0,
                "entity_embeddings": entity_embeddings,
                "cached_searches": cached_searches,
                "embedding_model": self.embedding_model_name,
                "similarity_threshold": self.similarity_threshold
            }
            
        finally:
            conn.close()
