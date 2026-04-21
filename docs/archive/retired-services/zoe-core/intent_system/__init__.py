"""
Zoe Intent System
=================

HassIL-based intent classification and execution system.

This system provides:
- Fast, deterministic intent classification (<5ms)
- Multi-tier routing (HassIL → Keywords → Context → LLM)
- Direct execution bypassing LLM for simple actions
- 100% offline operation

Architecture:
    - classifiers/: Intent classification (HassIL, keywords, context)
    - executors/: Intent execution and routing to handlers
    - handlers/: Domain-specific intent handlers
    - intents/: YAML pattern definitions
    - formatters/: Natural language response formatting
    - analytics/: Performance metrics and monitoring
"""

__version__ = "1.0.0"

