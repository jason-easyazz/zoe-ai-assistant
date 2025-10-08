# 🧠 Light RAG Intelligence Documentation

## Overview

Light RAG (Lightweight Retrieval-Augmented Generation) is a revolutionary enhancement to Zoe's memory system that combines vector embeddings with relationship graphs for intelligent information retrieval. This system makes Zoe significantly more intelligent and natural-feeling, bringing us closer to the "Samantha from Her" experience.

## What is Light RAG?

Light RAG enhances traditional RAG systems by:
- **Vector Embeddings**: Converting text into high-dimensional vectors for semantic understanding
- **Relationship Awareness**: Understanding connections between entities (people, projects, events)
- **Contextual Retrieval**: Finding relevant information even when not explicitly mentioned
- **Incremental Learning**: Continuously improving as new memories are added

## Key Benefits

### 🎯 Enhanced Search Intelligence
**Before Light RAG:**
```
Query: "Arduino projects with Sarah"
Result: Only memories containing both "Arduino" AND "Sarah"
```

**With Light RAG:**
```
Query: "Arduino projects with Sarah"
Result: 
- Sarah's electronics interests
- Garden automation projects (Sarah's friend Bob works on these)
- Arduino workshop memories
- Related project connections
```

### 🧠 Contextual Understanding
Light RAG understands implicit connections:
- **Entity Relationships**: Who knows whom, project collaborations
- **Semantic Similarity**: "electronics" relates to "Arduino", "sensors" to "automation"
- **Temporal Context**: Recent vs. historical information relevance
- **Importance Weighting**: Prioritizes high-importance memories

## Architecture

### Core Components

1. **Embedding Engine**
   - Uses `all-MiniLM-L6-v2` model (384 dimensions)
   - Generates embeddings for all memory facts
   - Caches embeddings for performance

2. **Relationship Graph**
   - Tracks connections between people, projects, events
   - Calculates relationship strength and context
   - Enables multi-hop reasoning

3. **Search Engine**
   - Combines vector similarity with relationship awareness
   - Implements caching for performance
   - Supports incremental updates

4. **Context Generator**
   - Builds entity context from relationships
   - Generates relationship paths for enhanced understanding
   - Maintains entity embeddings

### Database Schema

```sql
-- Enhanced memory_facts table
ALTER TABLE memory_facts ADD COLUMN embedding_vector BLOB;
ALTER TABLE memory_facts ADD COLUMN entity_context TEXT;
ALTER TABLE memory_facts ADD COLUMN relationship_path TEXT;
ALTER TABLE memory_facts ADD COLUMN embedding_hash TEXT;

-- Entity embeddings table
CREATE TABLE entity_embeddings (
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
);

-- Relationship embeddings table
CREATE TABLE relationship_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    relationship_id INTEGER,
    relationship_type TEXT,
    entity1_name TEXT,
    entity2_name TEXT,
    embedding_vector BLOB,
    strength_score REAL DEFAULT 0.5,
    context_summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Search cache table
CREATE TABLE search_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_hash TEXT UNIQUE NOT NULL,
    query_text TEXT NOT NULL,
    results_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP
);
```

## API Endpoints

### Enhanced Search
```bash
POST /api/memories/search/light-rag
```
**Parameters:**
- `query` (string): Search query
- `limit` (int, default: 10): Number of results
- `use_cache` (bool, default: true): Use search cache

**Response:**
```json
{
  "results": [
    {
      "fact_id": 123,
      "fact": "Alice loves Arduino projects",
      "entity_type": "person",
      "entity_id": 1,
      "entity_name": "Alice",
      "category": "interests",
      "importance": 8,
      "similarity_score": 0.85,
      "relationship_boost": 0.2,
      "final_score": 1.05,
      "entity_context": "Person: Alice | Relationship: friend",
      "relationship_path": "friend: Bob | family: Charlie",
      "created_at": "2024-01-15T10:30:00"
    }
  ],
  "query": "Arduino projects",
  "search_type": "light_rag",
  "total_results": 5,
  "limit": 10,
  "cache_used": true
}
```

### Enhanced Memory Creation
```bash
POST /api/memories/enhanced
```
**Parameters:**
- `entity_type` (string): "person", "project", "general"
- `entity_id` (int): Entity ID
- `fact` (string): Fact to remember
- `category` (string, default: "general"): Fact category
- `importance` (int, default: 5): Importance level 1-10
- `source` (string, default: "user"): Source of the memory

### Contextual Memory Retrieval
```bash
GET /api/memories/contextual/{entity_name}
```
**Parameters:**
- `entity_name` (string): Name of the entity
- `context_type` (string, default: "all"): "all", "direct", "related"

### Search Comparison
```bash
POST /api/memories/search/comparison
```
Compare traditional search vs Light RAG search for the same query.

### System Statistics
```bash
GET /api/memories/stats/light-rag
```
Get Light RAG system statistics and performance metrics.

### Migration
```bash
POST /api/memories/migrate
```
Migrate existing memories to include embeddings and relationship context.

## Usage Examples

### Basic Search
```python
from light_rag_memory import LightRAGMemorySystem

# Initialize system
light_rag = LightRAGMemorySystem("/path/to/memory.db")

# Search with relationship awareness
results = light_rag.light_rag_search("Sarah's Arduino projects", limit=5)

for result in results:
    print(f"Fact: {result.fact}")
    print(f"Similarity: {result.similarity_score}")
    print(f"Final Score: {result.final_score}")
    print(f"Context: {result.entity_context}")
    print("---")
```

### Adding Enhanced Memories
```python
# Add memory with automatic embedding generation
result = light_rag.add_memory_with_embedding(
    entity_type="person",
    entity_id=1,
    fact="Sarah is learning Python for data analysis",
    category="learning",
    importance=7,
    source="conversation"
)

print(f"Memory added with ID: {result['fact_id']}")
print(f"Embedding generated: {result['embedding_generated']}")
```

### Contextual Memory Retrieval
```python
# Get all memories related to Alice
memories = light_rag.get_contextual_memories("Alice")

for memory in memories:
    print(f"Type: {memory['type']}")
    print(f"Fact: {memory['fact']}")
    print(f"Importance: {memory['importance']}")
    print("---")
```

## Performance Characteristics

### Benchmarks
- **Embedding Generation**: ~0.1-0.3 seconds per memory
- **Search Performance**: ~0.5-2.0 seconds for 1000+ memories
- **Memory Usage**: ~50MB for embedding model + ~1MB per 1000 memories
- **Cache Hit Rate**: ~80% for repeated queries

### Optimization Features
- **Search Caching**: 24-hour TTL for repeated queries
- **Incremental Updates**: Only processes new/changed memories
- **Vector Indexing**: Efficient similarity calculations
- **Relationship Caching**: Pre-computed relationship paths

## Migration Guide

### Automatic Migration
```bash
# Run the migration script
python /home/pi/zoe/scripts/migrate_to_light_rag.py
```

### Manual Migration
```python
from light_rag_memory import LightRAGMemorySystem

light_rag = LightRAGMemorySystem("/path/to/memory.db")
result = light_rag.migrate_existing_memories()

print(f"Migrated: {result['migrated_count']}")
print(f"Errors: {result['error_count']}")
```

### Migration Checklist
- [ ] Backup existing database
- [ ] Install dependencies: `pip install sentence-transformers numpy torch`
- [ ] Run migration script
- [ ] Verify migration success
- [ ] Test Light RAG functionality
- [ ] Update application to use new endpoints

## Troubleshooting

### Common Issues

1. **ImportError: No module named 'sentence_transformers'**
   ```bash
   pip install sentence-transformers numpy torch
   ```

2. **Memory usage too high**
   - Reduce embedding model size
   - Implement memory cleanup
   - Use smaller batch sizes

3. **Slow search performance**
   - Enable search caching
   - Optimize similarity threshold
   - Reduce result limit

4. **Migration failures**
   - Check database permissions
   - Verify backup exists
   - Run migration in smaller batches

### Performance Tuning

1. **Adjust Similarity Threshold**
   ```python
   light_rag.similarity_threshold = 0.2  # Lower = more results
   ```

2. **Optimize Cache Settings**
   ```python
   light_rag.cache_ttl_hours = 12  # Shorter cache lifetime
   ```

3. **Batch Processing**
   ```python
   # Process memories in batches
   for batch in memory_batches:
       light_rag.migrate_batch(batch)
   ```

## Future Enhancements

### Planned Features
- **Multi-language Support**: Embeddings for different languages
- **Advanced Relationship Types**: More sophisticated relationship modeling
- **Temporal Awareness**: Time-based relevance scoring
- **Graph Visualization**: Interactive relationship graphs
- **Federated Learning**: Distributed embedding updates

### Research Areas
- **Dynamic Embeddings**: Context-aware embeddings
- **Relationship Learning**: Automatic relationship discovery
- **Semantic Clustering**: Grouping related memories
- **Cross-modal Integration**: Images, audio, and text

## Contributing

### Development Setup
```bash
# Clone repository
git clone https://github.com/yourusername/zoe.git
cd zoe

# Install dependencies
pip install -r services/zoe-core/requirements.txt

# Run tests
pytest tests/test_light_rag.py -v

# Run migration
python scripts/migrate_to_light_rag.py
```

### Testing
```bash
# Run all Light RAG tests
pytest tests/test_light_rag.py -v

# Run performance tests
pytest tests/test_light_rag.py::TestLightRAGPerformance -v

# Run integration tests
pytest tests/test_light_rag.py::TestLightRAGIntegration -v
```

## Support

For issues, questions, or contributions:
- **Issues**: [GitHub Issues](https://github.com/yourusername/zoe/issues)
- **Documentation**: This file and `/docs` endpoint
- **API Docs**: `http://localhost:8000/docs`

---

**Light RAG makes Zoe truly intelligent - understanding not just what you say, but what you mean and how everything connects.**
