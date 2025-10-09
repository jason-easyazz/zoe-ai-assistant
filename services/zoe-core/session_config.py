"""
Session Management Configuration
Centralized configuration for session management system
"""

import os
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

@dataclass
class SessionConfig:
    """Configuration for session management"""
    
    # Database configuration
    db_path: str = "data/zoe.db"
    
    # Session timeout configuration
    default_timeout: int = 3600  # 1 hour
    max_timeout: int = 86400     # 24 hours
    min_timeout: int = 300       # 5 minutes
    
    # Cleanup configuration
    cleanup_interval: int = 300  # 5 minutes
    max_sessions_per_user: int = 10
    
    # Security configuration
    require_https: bool = False
    secure_cookies: bool = False
    session_header: str = "X-Session-ID"
    
    # Middleware configuration
    protected_paths: List[str] = None
    excluded_paths: List[str] = None
    auto_update_activity: bool = True
    
    # Logging configuration
    log_level: str = "INFO"
    log_requests: bool = True
    
    def __post_init__(self):
        """Set default values after initialization"""
        if self.protected_paths is None:
            self.protected_paths = [
                "/api/chat",
                "/developer",
                "/tasks",
                "/memory",
                "/settings",
                "/admin"
            ]
        
        if self.excluded_paths is None:
            self.excluded_paths = [
                "/docs",
                "/redoc",
                "/openapi.json",
                "/health",
                "/sessions/create",
                "/sessions/stats",
                "/sessions/validate",
                "/static",
                "/favicon.ico"
            ]

def load_session_config() -> SessionConfig:
    """
    Load session configuration from environment variables
    
    Returns:
        SessionConfig object with loaded settings
    """
    config = SessionConfig()
    
    # Override with environment variables if present
    config.db_path = os.getenv("SESSION_DB_PATH", config.db_path)
    config.default_timeout = int(os.getenv("SESSION_DEFAULT_TIMEOUT", config.default_timeout))
    config.max_timeout = int(os.getenv("SESSION_MAX_TIMEOUT", config.max_timeout))
    config.min_timeout = int(os.getenv("SESSION_MIN_TIMEOUT", config.min_timeout))
    config.cleanup_interval = int(os.getenv("SESSION_CLEANUP_INTERVAL", config.cleanup_interval))
    config.max_sessions_per_user = int(os.getenv("SESSION_MAX_PER_USER", config.max_sessions_per_user))
    config.require_https = os.getenv("SESSION_REQUIRE_HTTPS", "false").lower() == "true"
    config.secure_cookies = os.getenv("SESSION_SECURE_COOKIES", "false").lower() == "true"
    config.session_header = os.getenv("SESSION_HEADER", config.session_header)
    config.auto_update_activity = os.getenv("SESSION_AUTO_UPDATE", "true").lower() == "true"
    config.log_level = os.getenv("SESSION_LOG_LEVEL", config.log_level)
    config.log_requests = os.getenv("SESSION_LOG_REQUESTS", "true").lower() == "true"
    
    # Parse protected paths from environment
    protected_env = os.getenv("SESSION_PROTECTED_PATHS")
    if protected_env:
        config.protected_paths = [path.strip() for path in protected_env.split(",")]
    
    # Parse excluded paths from environment
    excluded_env = os.getenv("SESSION_EXCLUDED_PATHS")
    if excluded_env:
        config.excluded_paths = [path.strip() for path in excluded_env.split(",")]
    
    return config

def validate_session_config(config: SessionConfig) -> List[str]:
    """
    Validate session configuration
    
    Args:
        config: SessionConfig to validate
        
    Returns:
        List of validation errors (empty if valid)
    """
    errors = []
    
    # Validate timeout values
    if config.min_timeout <= 0:
        errors.append("min_timeout must be positive")
    
    if config.default_timeout < config.min_timeout:
        errors.append("default_timeout must be >= min_timeout")
    
    if config.max_timeout < config.default_timeout:
        errors.append("max_timeout must be >= default_timeout")
    
    # Validate database path
    if not config.db_path:
        errors.append("db_path cannot be empty")
    
    # Validate cleanup interval
    if config.cleanup_interval <= 0:
        errors.append("cleanup_interval must be positive")
    
    # Validate max sessions per user
    if config.max_sessions_per_user <= 0:
        errors.append("max_sessions_per_user must be positive")
    
    # Validate log level
    valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if config.log_level.upper() not in valid_log_levels:
        errors.append(f"log_level must be one of: {valid_log_levels}")
    
    return errors

def get_session_config() -> SessionConfig:
    """
    Get validated session configuration
    
    Returns:
        Validated SessionConfig object
        
    Raises:
        ValueError: If configuration is invalid
    """
    config = load_session_config()
    errors = validate_session_config(config)
    
    if errors:
        raise ValueError(f"Invalid session configuration: {'; '.join(errors)}")
    
    return config

# Default configuration instance
DEFAULT_CONFIG = SessionConfig()

# Environment-specific configurations
DEVELOPMENT_CONFIG = SessionConfig(
    default_timeout=7200,  # 2 hours for development
    log_level="DEBUG",
    log_requests=True
)

PRODUCTION_CONFIG = SessionConfig(
    default_timeout=1800,  # 30 minutes for production
    require_https=True,
    secure_cookies=True,
    log_level="WARNING",
    log_requests=False
)

TESTING_CONFIG = SessionConfig(
    db_path=":memory:",
    default_timeout=60,
    cleanup_interval=10,
    log_level="DEBUG"
)

def get_environment_config() -> SessionConfig:
    """
    Get configuration based on environment
    
    Returns:
        Environment-appropriate SessionConfig
    """
    env = os.getenv("ENVIRONMENT", "development").lower()
    
    if env == "production":
        return PRODUCTION_CONFIG
    elif env == "testing":
        return TESTING_CONFIG
    else:
        return DEVELOPMENT_CONFIG
