# Intent System

HassIL-based intent classification for Zoe AI Assistant.

## Quick Start

**Test the system:**
```bash
curl http://localhost:8000/api/intent/health
```

**Use in chat:**
```
"add bread to shopping list"
```

## Performance

- **Latency**: <10ms (vs 300-1000ms LLM-only)
- **Accuracy**: 100% for matched patterns
- **Coverage**: 29 intents, 200+ patterns
- **Status**: ✅ Production ready

## Documentation

- **Architecture**: `docs/architecture/WHY_HASSIL.md`
- **Development Rules**: `docs/governance/INTENT_SYSTEM_RULES.md`
- **Implementation Guide**: `docs/architecture/INTENT_SYSTEM_IMPLEMENTATION.md`

## Analytics

```bash
# Performance summary
curl http://localhost:8000/api/intent/analytics

# Health check
curl http://localhost:8000/api/intent/health
```

## Validation

```bash
python3 tools/intent/validate_intents.py
```

## Tests

```bash
pytest tests/intent_system/test_classification.py -v
```

## Adding New Intents

1. Create YAML pattern in `intents/en/[domain].yaml`
2. Create handler in `handlers/[domain]_handlers.py`
3. Register in `executors/intent_executor.py`
4. Run `tools/intent/validate_intents.py`
5. Test and deploy

See `docs/architecture/INTENT_SYSTEM_IMPLEMENTATION.md` for details.

