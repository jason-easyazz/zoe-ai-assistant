# 🚀 Light RAG Installation Guide

## Prerequisites

- Python 3.11+
- Existing Zoe installation
- ~2GB free disk space for embedding model
- ~1GB RAM for embedding operations

## Installation Steps

### 1. Install Dependencies

```bash
# Install core Light RAG dependencies
pip install sentence-transformers numpy torch transformers scikit-learn huggingface-hub

# Or install from requirements
pip install -r /home/pi/zoe/services/zoe-core/requirements.txt
```

### 2. Run Migration

```bash
# Run the migration script (creates backup automatically)
python /home/pi/zoe/scripts/migrate_to_light_rag.py
```

### 3. Verify Installation

```bash
# Test the system
pytest /home/pi/zoe/tests/test_light_rag.py -v

# Run quick benchmarks
python /home/pi/zoe/scripts/light_rag_benchmarks.py --quick
```

### 4. Test API Endpoints

```bash
# Test Light RAG search
curl -X POST http://localhost:8000/api/memories/search/light-rag \
  -H "Content-Type: application/json" \
  -d '{"query": "test search", "limit": 5}'

# Check system stats
curl -X GET http://localhost:8000/api/memories/stats/light-rag
```

## Troubleshooting

### Common Issues

1. **ImportError: sentence-transformers**
   ```bash
   pip install sentence-transformers
   ```

2. **ImportError: huggingface_hub**
   ```bash
   pip install huggingface-hub>=0.16.0
   ```

3. **CUDA/GPU Issues**
   ```bash
   # Install CPU-only version
   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
   ```

4. **Memory Issues**
   - Reduce batch size in migration
   - Use smaller embedding model
   - Increase system RAM

### Performance Tuning

1. **For Raspberry Pi**
   ```python
   # Use smaller model
   light_rag = LightRAGMemorySystem(db_path, embedding_model="all-MiniLM-L6-v2")
   ```

2. **For High Performance**
   ```python
   # Use larger model
   light_rag = LightRAGMemorySystem(db_path, embedding_model="all-mpnet-base-v2")
   ```

## Verification Checklist

- [ ] Dependencies installed successfully
- [ ] Migration completed without errors
- [ ] Tests pass (pytest)
- [ ] API endpoints respond correctly
- [ ] Search results include similarity scores
- [ ] System stats show embedded memories
- [ ] Performance benchmarks complete

## Next Steps

1. **Start Using Light RAG**: Use the new API endpoints
2. **Monitor Performance**: Check `/api/memories/stats/light-rag`
3. **Optimize Settings**: Adjust similarity threshold if needed
4. **Scale Up**: Add more memories and relationships

## Support

- **Documentation**: `/LIGHT_RAG_DOCUMENTATION.md`
- **API Reference**: `http://localhost:8000/docs`
- **Issues**: GitHub Issues
- **Logs**: `/home/pi/zoe/logs/light_rag_*.log`

---

**Light RAG Installation Status: Ready for Production** ✅

