"""
Comprehensive test suite for Light RAG Memory System
Tests all functionality including embeddings, relationships, and performance
"""
import pytest
import sqlite3
import tempfile
import os
import json
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import sys

# Add the services directory to the path
sys.path.append('/home/pi/zoe/services/zoe-core')

from light_rag_memory import LightRAGMemorySystem, MemoryResult, EntityType

class TestLightRAGMemorySystem:
    """Test suite for Light RAG Memory System"""
    
    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing"""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        yield db_path
        
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)
    
    @pytest.fixture
    def light_rag_system(self, temp_db):
        """Create a Light RAG system instance for testing"""
        return LightRAGMemorySystem(temp_db)
    
    @pytest.fixture
    def sample_data(self, light_rag_system, temp_db):
        """Set up sample data for testing"""
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        
        # Add sample people
        cursor.execute("""
            INSERT INTO people (name, relationship, notes)
            VALUES 
            ('Alice', 'friend', 'Loves Arduino projects'),
            ('Bob', 'colleague', 'Works on garden automation'),
            ('Charlie', 'family', 'Birthday next month')
        """)
        
        # Add sample projects
        cursor.execute("""
            INSERT INTO projects (name, description, status)
            VALUES 
            ('Garden Automation', 'Automated watering system', 'active'),
            ('Arduino Workshop', 'Teaching electronics basics', 'completed')
        """)
        
        # Add sample relationships
        cursor.execute("""
            INSERT INTO relationships (person1_id, person2_id, relationship_type)
            VALUES 
            (1, 2, 'friend'),
            (1, 3, 'family'),
            (2, 3, 'colleague')
        """)
        
        # Add sample memory facts
        cursor.execute("""
            INSERT INTO memory_facts (entity_type, entity_id, fact, category, importance)
            VALUES 
            ('person', 1, 'Alice loves Arduino projects and electronics', 'interests', 8),
            ('person', 2, 'Bob is working on garden automation project', 'projects', 7),
            ('person', 3, 'Charlie birthday is next month', 'important_dates', 9),
            ('project', 1, 'Garden automation uses Raspberry Pi and sensors', 'technical', 6),
            ('project', 2, 'Arduino workshop was successful', 'outcome', 5)
        """)
        
        conn.commit()
        conn.close()
        
        return {
            'people': ['Alice', 'Bob', 'Charlie'],
            'projects': ['Garden Automation', 'Arduino Workshop'],
            'relationships': 3,
            'memories': 5
        }
    
    def test_initialization(self, light_rag_system):
        """Test system initialization"""
        assert light_rag_system.db_path is not None
        assert light_rag_system.embedding_model is not None
        assert light_rag_system.embedding_dim == 384
        assert light_rag_system.similarity_threshold == 0.3
    
    def test_database_schema_creation(self, light_rag_system, temp_db):
        """Test that database schema is created correctly"""
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        
        # Check that new columns exist
        cursor.execute("PRAGMA table_info(memory_facts)")
        columns = [row[1] for row in cursor.fetchall()]
        
        required_columns = ['embedding_vector', 'entity_context', 'relationship_path', 'embedding_hash']
        for col in required_columns:
            assert col in columns, f"Column {col} not found"
        
        # Check that new tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        
        required_tables = ['entity_embeddings', 'relationship_embeddings', 'search_cache']
        for table in required_tables:
            assert table in tables, f"Table {table} not found"
        
        conn.close()
    
    def test_embedding_generation(self, light_rag_system):
        """Test embedding generation"""
        text = "Alice loves Arduino projects"
        embedding = light_rag_system.generate_embedding(text)
        
        assert isinstance(embedding, bytes)
        assert len(embedding) == light_rag_system.embedding_dim * 4  # 4 bytes per float32
    
    def test_embedding_hash(self, light_rag_system):
        """Test embedding hash generation"""
        text = "Test text"
        hash1 = light_rag_system.generate_embedding_hash(text)
        hash2 = light_rag_system.generate_embedding_hash(text)
        
        assert hash1 == hash2
        assert len(hash1) == 32  # MD5 hash length
    
    def test_cosine_similarity(self, light_rag_system):
        """Test cosine similarity calculation"""
        text1 = "Alice loves Arduino"
        text2 = "Alice enjoys electronics"
        text3 = "Bob likes cooking"
        
        emb1 = light_rag_system.generate_embedding(text1)
        emb2 = light_rag_system.generate_embedding(text2)
        emb3 = light_rag_system.generate_embedding(text3)
        
        sim12 = light_rag_system.cosine_similarity(emb1, emb2)
        sim13 = light_rag_system.cosine_similarity(emb1, emb3)
        
        assert 0 <= sim12 <= 1
        assert 0 <= sim13 <= 1
        assert sim12 > sim13  # Similar texts should have higher similarity
    
    def test_add_memory_with_embedding(self, light_rag_system, sample_data):
        """Test adding memory with automatic embedding generation"""
        result = light_rag_system.add_memory_with_embedding(
            "person", 1, "Alice is learning Python programming", "learning", 6, "test"
        )
        
        assert result["embedding_generated"] is True
        assert "fact_id" in result
        assert "entity_context" in result
        assert "relationship_path" in result
        
        # Verify the memory was stored with embedding
        conn = sqlite3.connect(light_rag_system.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT embedding_vector, entity_context, relationship_path
            FROM memory_facts WHERE id = ?
        """, (result["fact_id"],))
        
        row = cursor.fetchone()
        assert row[0] is not None  # embedding_vector
        assert row[1] is not None  # entity_context
        assert row[2] is not None  # relationship_path
        
        conn.close()
    
    def test_get_entity_context(self, light_rag_system, sample_data):
        """Test entity context generation"""
        context = light_rag_system._get_entity_context("person", 1)
        
        assert "Alice" in context
        assert "friend" in context
    
    def test_get_relationship_path(self, light_rag_system, sample_data):
        """Test relationship path generation"""
        path = light_rag_system._get_relationship_path("person", 1)
        
        assert "friend" in path
        assert "family" in path
    
    def test_light_rag_search(self, light_rag_system, sample_data):
        """Test Light RAG search functionality"""
        # First migrate the sample data
        light_rag_system.migrate_existing_memories()
        
        # Test search
        results = light_rag_system.light_rag_search("Arduino projects", limit=5)
        
        assert isinstance(results, list)
        assert len(results) > 0
        
        # Check result structure
        result = results[0]
        assert isinstance(result, MemoryResult)
        assert hasattr(result, 'fact_id')
        assert hasattr(result, 'similarity_score')
        assert hasattr(result, 'final_score')
        assert result.similarity_score >= light_rag_system.similarity_threshold
    
    def test_relationship_boost_calculation(self, light_rag_system):
        """Test relationship boost calculation"""
        query = "friend Alice"
        relationship_path = "friend: Bob | family: Charlie"
        
        boost = light_rag_system._calculate_relationship_boost(query, relationship_path)
        
        assert boost > 0
        assert boost <= light_rag_system.relationship_boost_max
    
    def test_search_caching(self, light_rag_system, sample_data):
        """Test search result caching"""
        # Migrate data first
        light_rag_system.migrate_existing_memories()
        
        query = "test cache query"
        
        # First search (should cache)
        results1 = light_rag_system.light_rag_search(query, use_cache=True)
        
        # Second search (should use cache)
        results2 = light_rag_system.light_rag_search(query, use_cache=True)
        
        # Results should be identical
        assert len(results1) == len(results2)
        for r1, r2 in zip(results1, results2):
            assert r1.fact_id == r2.fact_id
            assert r1.final_score == r2.final_score
    
    def test_contextual_memories(self, light_rag_system, sample_data):
        """Test contextual memory retrieval"""
        # Migrate data first
        light_rag_system.migrate_existing_memories()
        
        memories = light_rag_system.get_contextual_memories("Alice")
        
        assert isinstance(memories, list)
        assert len(memories) > 0
        
        # Check memory structure
        memory = memories[0]
        assert "fact" in memory
        assert "type" in memory
        assert memory["type"] in ["direct", "related"]
    
    def test_migration(self, light_rag_system, sample_data):
        """Test migration of existing memories"""
        result = light_rag_system.migrate_existing_memories()
        
        assert "migrated_count" in result
        assert "error_count" in result
        assert "total_facts" in result
        assert result["migrated_count"] > 0
        assert result["error_count"] == 0
    
    def test_system_stats(self, light_rag_system, sample_data):
        """Test system statistics"""
        # Migrate data first
        light_rag_system.migrate_existing_memories()
        
        stats = light_rag_system.get_system_stats()
        
        assert "total_memories" in stats
        assert "embedded_memories" in stats
        assert "embedding_coverage" in stats
        assert "entity_embeddings" in stats
        assert "cached_searches" in stats
        assert stats["embedding_coverage"] > 0
    
    def test_performance_benchmarks(self, light_rag_system, sample_data):
        """Test performance benchmarks"""
        import time
        
        # Migrate data first
        light_rag_system.migrate_existing_memories()
        
        # Test search performance
        start_time = time.time()
        results = light_rag_system.light_rag_search("Arduino", limit=10)
        search_time = time.time() - start_time
        
        assert search_time < 2.0  # Should complete within 2 seconds
        assert len(results) > 0
        
        # Test embedding generation performance
        start_time = time.time()
        embedding = light_rag_system.generate_embedding("Performance test text")
        embedding_time = time.time() - start_time
        
        assert embedding_time < 1.0  # Should complete within 1 second
        assert embedding is not None
    
    def test_error_handling(self, light_rag_system):
        """Test error handling"""
        # Test with invalid entity type
        with pytest.raises(Exception):
            light_rag_system.add_memory_with_embedding(
                "invalid_type", 999, "test fact", "test", 5, "test"
            )

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
