"""Dependency validation utility"""
import sys
import importlib
import json

def check_dependencies():
    """Check all required dependencies are available"""
    
    results = {
        "python_version": sys.version,
        "path": sys.path,
        "modules": {},
        "errors": []
    }
    
    # Required modules
    required = {
        "fastapi": "0.104.1",
        "uvicorn": "0.24.0",
        "pydantic": "2.5.0",
        "httpx": "0.25.2",
        "python-multipart": "0.0.6"
    }
    
    for module, version in required.items():
        try:
            mod = importlib.import_module(module)
            actual_version = getattr(mod, "__version__", "unknown")
            results["modules"][module] = {
                "required": version,
                "actual": actual_version,
                "status": "ok"
            }
        except ImportError as e:
            results["modules"][module] = {
                "required": version,
                "actual": None,
                "status": "missing",
                "error": str(e)
            }
            results["errors"].append(f"{module}: {e}")
    
    return results

if __name__ == "__main__":
    results = check_dependencies()
    print(json.dumps(results, indent=2))
    
    if results["errors"]:
        print(f"\n❌ {len(results['errors'])} dependencies missing!")
        sys.exit(1)
    else:
        print("\n✅ All dependencies satisfied!")
        sys.exit(0)
