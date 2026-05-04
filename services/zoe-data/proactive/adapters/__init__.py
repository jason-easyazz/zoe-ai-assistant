"""
Proactive adapters — thin wrappers that translate external service events
into TriggerResult objects.

Add adapter modules here as the engine grows (e.g. calendar_adapter.py,
shopping_adapter.py).  Each module should import ProactiveTrigger from
proactive.triggers.base and register itself via engine.register_trigger().
"""
