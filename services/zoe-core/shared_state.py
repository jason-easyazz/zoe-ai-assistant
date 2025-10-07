"""
Shared State Management
In-memory key-value store for sharing information between different parts of the system.
"""
import threading
from typing import Any, Dict

class SharedStateManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(SharedStateManager, cls).__new__(cls)
                    cls._instance._state = {}
        return cls._instance

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._state.get(key, default)

    def set(self, key: str, value: Any):
        with self._lock:
            self._state[key] = value

    def get_all(self) -> Dict[str, Any]:
        with self._lock:
            return self._state.copy()

shared_state = SharedStateManager()


