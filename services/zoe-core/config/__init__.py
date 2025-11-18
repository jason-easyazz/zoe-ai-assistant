# Config module - Re-export FeatureFlags from parent config.py
import sys
from pathlib import Path

# Add parent directory to path to import config.py
parent_dir = Path(__file__).parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

# Import and re-export
try:
    # Import from parent-level config.py (not this package)
    import imp
    config_module = imp.load_source('feature_config', str(parent_dir / 'config.py'))
    FeatureFlags = config_module.FeatureFlags
    __all__ = ["FeatureFlags"]
except Exception as e:
    # Fallback: define a dummy FeatureFlags
    class FeatureFlags:
        USE_CONTEXT_VALIDATION = False
        USE_CONFIDENCE_FORMATTING = False
        USE_DYNAMIC_TEMPERATURE = False
        USE_GROUNDING_CHECKS = False
        USE_BEHAVIORAL_MEMORY = False
        PLATFORM = "jetson"
        
        @classmethod
        def get_platform_config(cls):
            return {"max_context_tokens": 8192, "grounding_method": "async_llm"}
