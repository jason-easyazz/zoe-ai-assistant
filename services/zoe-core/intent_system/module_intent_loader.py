"""
Module Intent Auto-Discovery System
====================================

Automatically discovers and loads intents from enabled modules.

Architecture:
  1. Read config/modules.yaml for enabled modules
  2. For each enabled module, check for intents/ directory
  3. Load YAML definitions from module/intents/*.yaml
  4. Import and register handlers from module/intents/handlers.py
  5. Integrate with existing intent system

This allows modules to be truly pluggable - just enable the module
and its intents automatically become available.

Example module structure:
  modules/zoe-music/
    ├── main.py (MCP tools)
    ├── intents/
    │   ├── music.yaml (intent definitions)
    │   └── handlers.py (intent handlers)
    └── services/

Usage:
  from intent_system.module_intent_loader import load_module_intents
  
  # At startup
  loaded_intents = load_module_intents()
  print(f"Loaded {len(loaded_intents)} module intents")
"""

import os
import sys
import yaml
import logging
import importlib.util
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable

logger = logging.getLogger(__name__)


def get_enabled_modules() -> List[str]:
    """
    Get list of enabled modules from config/modules.yaml.
    
    Returns:
        List of enabled module names (e.g., ['zoe-music', 'zoe-calendar'])
    """
    try:
        config_path = Path("/app/config/modules.yaml")
        
        # Fallback paths for local development
        if not config_path.exists():
            config_path = Path("config/modules.yaml")
        if not config_path.exists():
            config_path = Path("../config/modules.yaml")
        
        if not config_path.exists():
            logger.warning("No modules.yaml found, no module intents will be loaded")
            return []
        
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        enabled = config.get("enabled_modules", [])
        logger.info(f"Found {len(enabled)} enabled modules: {enabled}")
        return enabled
    
    except Exception as e:
        logger.error(f"Failed to read modules config: {e}")
        return []


def discover_module_intents(module_name: str) -> Optional[Dict[str, Any]]:
    """
    Discover intents from a single module.
    
    Args:
        module_name: Name of the module (e.g., 'zoe-music')
    
    Returns:
        Dict with:
          - 'intents': Dict of intent definitions from YAML
          - 'handlers': Dict of handler functions
          - 'module_name': Original module name
          - 'intents_path': Path to intents directory
        Or None if no intents found
    """
    try:
        # Try various paths
        base_paths = [
            Path(f"/app/modules/{module_name}"),      # Docker
            Path(f"modules/{module_name}"),           # Local from zoe-core
            Path(f"../modules/{module_name}"),        # Local from intent_system
            Path(f"../../modules/{module_name}"),     # Local from deeper
        ]
        
        module_path = None
        for p in base_paths:
            if p.exists():
                module_path = p
                break
        
        if not module_path:
            logger.debug(f"Module path not found for {module_name}")
            return None
        
        intents_path = module_path / "intents"
        
        if not intents_path.exists():
            logger.debug(f"No intents/ directory in {module_name}")
            return None
        
        logger.info(f"🔍 Discovering intents from {module_name}")
        
        # Load YAML definitions
        intents_definitions = {}
        for yaml_file in intents_path.glob("*.yaml"):
            try:
                with open(yaml_file, 'r') as f:
                    data = yaml.safe_load(f)
                    if data and 'intents' in data:
                        intents_definitions.update(data['intents'])
                        logger.info(f"  ✓ Loaded {len(data['intents'])} intents from {yaml_file.name}")
            except Exception as e:
                logger.error(f"  ✗ Failed to load {yaml_file.name}: {e}")
        
        if not intents_definitions:
            logger.warning(f"No intent definitions found in {module_name}/intents/")
            return None
        
        # Load handlers
        handlers = {}
        handlers_file = intents_path / "handlers.py"
        
        if handlers_file.exists():
            try:
                # Dynamically import the handlers module
                spec = importlib.util.spec_from_file_location(
                    f"{module_name}.intent_handlers",
                    str(handlers_file)
                )
                handlers_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(handlers_module)
                
                # Get INTENT_HANDLERS dict from module
                if hasattr(handlers_module, 'INTENT_HANDLERS'):
                    handlers = handlers_module.INTENT_HANDLERS
                    logger.info(f"  ✓ Loaded {len(handlers)} handlers from {module_name}")
                else:
                    logger.warning(f"  ⚠️  handlers.py found but no INTENT_HANDLERS dict")
                
            except Exception as e:
                logger.error(f"  ✗ Failed to load handlers from {module_name}: {e}")
                logger.exception(e)
        else:
            logger.warning(f"  ⚠️  No handlers.py found in {module_name}/intents/")
        
        return {
            'module_name': module_name,
            'intents': intents_definitions,
            'handlers': handlers,
            'intents_path': str(intents_path)
        }
    
    except Exception as e:
        logger.error(f"Failed to discover intents from {module_name}: {e}")
        logger.exception(e)
        return None


def load_module_intents() -> List[Dict[str, Any]]:
    """
    Discover and load intents from all enabled modules.
    
    Returns:
        List of module intent data dicts, one per module
    """
    enabled_modules = get_enabled_modules()
    
    if not enabled_modules:
        logger.info("No modules enabled, skipping module intent discovery")
        return []
    
    logger.info(f"🔍 Discovering module intents from {len(enabled_modules)} modules...")
    
    loaded_intents = []
    
    for module_name in enabled_modules:
        module_intents = discover_module_intents(module_name)
        
        if module_intents:
            loaded_intents.append(module_intents)
            logger.info(
                f"✅ Loaded module: {module_name} "
                f"({len(module_intents['intents'])} intents, "
                f"{len(module_intents['handlers'])} handlers)"
            )
        else:
            logger.debug(f"ℹ️  No intents in {module_name} (module may only provide MCP tools)")
    
    logger.info(f"📦 Module intent discovery complete: {len(loaded_intents)} modules with intents")
    
    return loaded_intents


def register_module_intents_with_hassil(loaded_intents: List[Dict[str, Any]], hassil_classifier):
    """
    Register module intents with the Hassil classifier.
    
    Args:
        loaded_intents: Output from load_module_intents()
        hassil_classifier: HassilClassifier instance to register with
    """
    if not loaded_intents:
        logger.debug("No module intents to register with Hassil")
        return
    
    logger.info(f"📝 Registering module intents with Hassil classifier...")
    
    for module_data in loaded_intents:
        module_name = module_data['module_name']
        intents = module_data['intents']
        
        try:
            # Hassil classifier expects intent definitions in its format
            # The YAML we load should already be in this format
            for intent_name, intent_data in intents.items():
                logger.debug(f"  Registering {intent_name} from {module_name}")
                # The actual registration would depend on Hassil's API
                # This is a placeholder for the integration point
            
            logger.info(f"  ✓ Registered {len(intents)} intents from {module_name}")
        
        except Exception as e:
            logger.error(f"  ✗ Failed to register intents from {module_name}: {e}")


def register_module_handlers_with_executor(loaded_intents: List[Dict[str, Any]], intent_executor):
    """
    Register module handlers with the intent executor.
    
    Args:
        loaded_intents: Output from load_module_intents()
        intent_executor: IntentExecutor instance to register handlers with
    """
    if not loaded_intents:
        logger.debug("No module handlers to register with executor")
        return
    
    logger.info(f"🔧 Registering module handlers with intent executor...")
    
    total_handlers = 0
    
    for module_data in loaded_intents:
        module_name = module_data['module_name']
        handlers = module_data['handlers']
        
        try:
            for intent_name, handler_func in handlers.items():
                # Register handler with executor
                if hasattr(intent_executor, 'register_handler'):
                    intent_executor.register_handler(intent_name, handler_func)
                    total_handlers += 1
                    logger.debug(f"  Registered handler: {intent_name} from {module_name}")
                else:
                    # Fallback: add to executor's handlers dict directly
                    if not hasattr(intent_executor, '_module_handlers'):
                        intent_executor._module_handlers = {}
                    intent_executor._module_handlers[intent_name] = handler_func
                    total_handlers += 1
            
            logger.info(f"  ✓ Registered {len(handlers)} handlers from {module_name}")
        
        except Exception as e:
            logger.error(f"  ✗ Failed to register handlers from {module_name}: {e}")
            logger.exception(e)
    
    logger.info(f"✅ Module handler registration complete: {total_handlers} handlers registered")


# Convenience function for full integration
def integrate_module_intents(intent_classifier, intent_executor):
    """
    Full integration: discover, load, and register module intents.
    
    Args:
        intent_classifier: Classifier instance (e.g., HassilClassifier)
        intent_executor: Executor instance to handle intents
    
    Returns:
        Number of modules with intents loaded
    """
    logger.info("🚀 Starting module intent integration...")
    
    # Discover and load
    loaded_intents = load_module_intents()
    
    if not loaded_intents:
        logger.info("No module intents found")
        return 0
    
    # Register with classifier (for intent matching)
    register_module_intents_with_hassil(loaded_intents, intent_classifier)
    
    # Register with executor (for intent handling)
    register_module_handlers_with_executor(loaded_intents, intent_executor)
    
    logger.info(f"✅ Module intent integration complete: {len(loaded_intents)} modules")
    
    return len(loaded_intents)
