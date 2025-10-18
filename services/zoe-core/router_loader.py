"""
Auto-discovery Router Loader
Automatically discovers and registers all routers in the routers/ directory
"""
import os
import importlib
import logging
from pathlib import Path
from typing import List, Tuple
from fastapi import APIRouter

logger = logging.getLogger(__name__)

class RouterLoader:
    """Auto-discover and load FastAPI routers"""
    
    def __init__(self, routers_dir: str = "routers"):
        self.routers_dir = routers_dir
        self.loaded_routers: List[Tuple[str, APIRouter]] = []
        
    def discover_routers(self) -> List[Tuple[str, APIRouter]]:
        """
        Discover all routers in the routers directory
        Returns list of (module_name, router) tuples
        """
        routers = []
        routers_path = Path(self.routers_dir)
        
        if not routers_path.exists():
            logger.warning(f"Routers directory '{self.routers_dir}' not found")
            return routers
        
        # Get all .py files in routers directory
        router_files = [
            f for f in routers_path.glob("*.py") 
            if f.is_file() and not f.name.startswith("_")
        ]
        
        for router_file in router_files:
            module_name = router_file.stem
            
            try:
                # Import the module
                module = importlib.import_module(f"{self.routers_dir}.{module_name}")
                
                # Look for 'router' attribute
                if hasattr(module, 'router') and isinstance(module.router, APIRouter):
                    routers.append((module_name, module.router))
                    logger.info(f"✅ Loaded router: {module_name}")
                
                # Some routers have multiple routers (e.g., widget_builder has user_layout_router)
                for attr_name in dir(module):
                    if attr_name.endswith('_router') and attr_name != 'router':
                        attr = getattr(module, attr_name)
                        if isinstance(attr, APIRouter):
                            routers.append((f"{module_name}.{attr_name}", attr))
                            logger.info(f"✅ Loaded additional router: {module_name}.{attr_name}")
                            
            except Exception as e:
                logger.error(f"❌ Failed to load router {module_name}: {e}")
                # Don't fail the entire app, just skip this router
                continue
        
        self.loaded_routers = routers
        return routers
    
    def get_router_count(self) -> int:
        """Get the number of loaded routers"""
        return len(self.loaded_routers)
    
    def get_router_names(self) -> List[str]:
        """Get list of loaded router names"""
        return [name for name, _ in self.loaded_routers]


def load_all_routers() -> List[APIRouter]:
    """
    Convenience function to load all routers
    Returns list of APIRouter instances
    """
    loader = RouterLoader()
    routers = loader.discover_routers()
    
    logger.info(f"📦 Loaded {len(routers)} routers: {', '.join(loader.get_router_names())}")
    
    return [router for _, router in routers]



