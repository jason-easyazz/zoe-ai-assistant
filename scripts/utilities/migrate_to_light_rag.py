#!/usr/bin/env python3
"""
Migration script to enhance existing Zoe memories with Light RAG capabilities
This script adds vector embeddings and relationship context to existing memories
"""
import sys
import os
import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime

# Add the services directory to the path
sys.path.append('/home/zoe/assistant/services/zoe-core')

from light_rag_memory import LightRAGMemorySystem

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/zoe/assistant/logs/light_rag_migration.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def backup_database(db_path: str) -> str:
    """Create a backup of the database before migration"""
    backup_path = f"{db_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    try:
        import shutil
        shutil.copy2(db_path, backup_path)
        logger.info(f"Database backed up to: {backup_path}")
        return backup_path
    except Exception as e:
        logger.error(f"Failed to create backup: {e}")
        raise

def check_system_requirements():
    """Check if all required dependencies are installed"""
    try:
        import numpy
        logger.info("✓ numpy is installed")
        
        # Try sentence-transformers, but allow fallback
        try:
            import sentence_transformers
            logger.info("✓ sentence-transformers is installed")
        except ImportError:
            logger.warning("⚠ sentence-transformers not available, will use fallback embeddings")
        
        return True
    except ImportError as e:
        logger.error(f"Missing required dependency: {e}")
        logger.error("Install with: pip install numpy")
        return False

def get_migration_stats(db_path: str) -> dict:
    """Get statistics about the current database state"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Count total memories
        cursor.execute("SELECT COUNT(*) FROM memory_facts")
        total_memories = cursor.fetchone()[0]
        
        # Count memories with embeddings
        cursor.execute("SELECT COUNT(*) FROM memory_facts WHERE embedding_vector IS NOT NULL")
        embedded_memories = cursor.fetchone()[0]
        
        # Count people
        cursor.execute("SELECT COUNT(*) FROM people")
        total_people = cursor.fetchone()[0]
        
        # Count projects
        cursor.execute("SELECT COUNT(*) FROM projects")
        total_projects = cursor.fetchone()[0]
        
        # Count relationships
        cursor.execute("SELECT COUNT(*) FROM relationships")
        total_relationships = cursor.fetchone()[0]
        
        return {
            "total_memories": total_memories,
            "embedded_memories": embedded_memories,
            "total_people": total_people,
            "total_projects": total_projects,
            "total_relationships": total_relationships,
            "embedding_coverage": (embedded_memories / total_memories * 100) if total_memories > 0 else 0
        }
    finally:
        conn.close()

def migrate_memories(db_path: str) -> dict:
    """Perform the actual migration"""
    logger.info("Starting Light RAG migration...")
    
    # Initialize Light RAG system
    light_rag_system = LightRAGMemorySystem(db_path)
    
    # Get pre-migration stats
    pre_stats = get_migration_stats(db_path)
    logger.info(f"Pre-migration stats: {pre_stats}")
    
    # Perform migration
    migration_result = light_rag_system.migrate_existing_memories()
    
    # Get post-migration stats
    post_stats = get_migration_stats(db_path)
    logger.info(f"Post-migration stats: {post_stats}")
    
    return {
        "pre_migration": pre_stats,
        "post_migration": post_stats,
        "migration_result": migration_result
    }

def validate_migration(db_path: str) -> bool:
    """Validate that the migration was successful"""
    logger.info("Validating migration...")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check that embedding columns exist
        cursor.execute("PRAGMA table_info(memory_facts)")
        columns = [row[1] for row in cursor.fetchall()]
        
        required_columns = ['embedding_vector', 'entity_context', 'relationship_path', 'embedding_hash']
        missing_columns = [col for col in required_columns if col not in columns]
        
        if missing_columns:
            logger.error(f"Missing required columns: {missing_columns}")
            return False
        
        # Check that new tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        
        required_tables = ['entity_embeddings', 'relationship_embeddings', 'search_cache']
        missing_tables = [table for table in required_tables if table not in tables]
        
        if missing_tables:
            logger.error(f"Missing required tables: {missing_tables}")
            return False
        
        # Check that some memories have embeddings
        cursor.execute("SELECT COUNT(*) FROM memory_facts WHERE embedding_vector IS NOT NULL")
        embedded_count = cursor.fetchone()[0]
        
        if embedded_count == 0:
            logger.error("No memories have embeddings after migration")
            return False
        
        logger.info(f"✓ Migration validation successful: {embedded_count} memories have embeddings")
        return True
        
    finally:
        conn.close()

def test_light_rag_functionality(db_path: str) -> bool:
    """Test that Light RAG functionality works correctly"""
    logger.info("Testing Light RAG functionality...")
    
    try:
        light_rag_system = LightRAGMemorySystem(db_path)
        
        # Test search functionality
        test_query = "test search"
        results = light_rag_system.light_rag_search(test_query, limit=5)
        
        if not isinstance(results, list):
            logger.error("Light RAG search did not return a list")
            return False
        
        # Test adding a memory
        test_result = light_rag_system.add_memory_with_embedding(
            "general", 0, "This is a test memory for Light RAG", "test", 5, "migration_test"
        )
        
        if not test_result.get("embedding_generated"):
            logger.error("Failed to generate embedding for test memory")
            return False
        
        logger.info("✓ Light RAG functionality test passed")
        return True
        
    except Exception as e:
        logger.error(f"Light RAG functionality test failed: {e}")
        return False

def main():
    """Main migration function"""
    logger.info("=" * 60)
    logger.info("ZOE LIGHT RAG MIGRATION SCRIPT")
    logger.info("=" * 60)
    
    # Configuration
    db_path = "/home/zoe/assistant/data/memory.db"
    
    # Check if database exists
    if not os.path.exists(db_path):
        logger.error(f"Database not found at: {db_path}")
        logger.error("Please ensure Zoe is properly installed and the memory database exists")
        return False
    
    # Check system requirements
    if not check_system_requirements():
        return False
    
    try:
        # Create backup
        backup_path = backup_database(db_path)
        
        # Perform migration
        migration_stats = migrate_memories(db_path)
        
        # Validate migration
        if not validate_migration(db_path):
            logger.error("Migration validation failed!")
            return False
        
        # Test functionality
        if not test_light_rag_functionality(db_path):
            logger.error("Light RAG functionality test failed!")
            return False
        
        # Print summary
        logger.info("=" * 60)
        logger.info("MIGRATION COMPLETED SUCCESSFULLY!")
        logger.info("=" * 60)
        logger.info(f"Pre-migration memories: {migration_stats['pre_migration']['total_memories']}")
        logger.info(f"Post-migration embedded: {migration_stats['post_migration']['embedded_memories']}")
        logger.info(f"Embedding coverage: {migration_stats['post_migration']['embedding_coverage']:.1f}%")
        logger.info(f"Migration errors: {migration_stats['migration_result']['error_count']}")
        logger.info(f"Backup created at: {backup_path}")
        logger.info("=" * 60)
        
        return True
        
    except Exception as e:
        logger.error(f"Migration failed with error: {e}")
        logger.error("Check the backup and restore if necessary")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
