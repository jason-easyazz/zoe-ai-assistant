"""
Retry Utility
=============

Provides decorators and utilities for retry logic with exponential backoff.
"""

import asyncio
import functools
import logging
import random
from dataclasses import dataclass, field
from typing import (
    Callable, TypeVar, Optional, Union, Type, Tuple, Any, List
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryError(Exception):
    """Raised when all retry attempts have been exhausted."""
    
    def __init__(
        self,
        message: str,
        attempts: int,
        last_exception: Optional[Exception] = None
    ):
        super().__init__(message)
        self.attempts = attempts
        self.last_exception = last_exception


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    
    # Number of retry attempts (not including initial attempt)
    max_retries: int = 3
    
    # Base delay in seconds between retries
    base_delay: float = 1.0
    
    # Maximum delay between retries
    max_delay: float = 60.0
    
    # Exponential backoff multiplier
    backoff_multiplier: float = 2.0
    
    # Add random jitter to prevent thundering herd
    jitter: bool = True
    jitter_factor: float = 0.1
    
    # Exceptions to retry on (empty = all exceptions)
    retry_on: Tuple[Type[Exception], ...] = field(default_factory=tuple)
    
    # Exceptions to never retry on
    no_retry_on: Tuple[Type[Exception], ...] = field(default_factory=tuple)
    
    # Optional callback when retry occurs
    on_retry: Optional[Callable[[Exception, int, float], None]] = None
    
    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay for a given attempt number."""
        delay = min(
            self.base_delay * (self.backoff_multiplier ** attempt),
            self.max_delay
        )
        
        if self.jitter:
            jitter_range = delay * self.jitter_factor
            delay += random.uniform(-jitter_range, jitter_range)
        
        return max(0, delay)
    
    def should_retry(self, exception: Exception) -> bool:
        """Check if we should retry on this exception."""
        # Never retry on these
        if self.no_retry_on and isinstance(exception, self.no_retry_on):
            return False
        
        # If retry_on is specified, only retry on those
        if self.retry_on:
            return isinstance(exception, self.retry_on)
        
        # By default, retry on all exceptions
        return True


# Default configurations for common scenarios
DEFAULT_CONFIG = RetryConfig()

AGGRESSIVE_CONFIG = RetryConfig(
    max_retries=5,
    base_delay=0.5,
    max_delay=30.0
)

GENTLE_CONFIG = RetryConfig(
    max_retries=2,
    base_delay=2.0,
    max_delay=10.0
)

NETWORK_CONFIG = RetryConfig(
    max_retries=3,
    base_delay=1.0,
    max_delay=30.0,
    retry_on=(
        ConnectionError,
        TimeoutError,
        OSError
    )
)


def retry(
    config: Optional[RetryConfig] = None,
    max_retries: Optional[int] = None,
    base_delay: Optional[float] = None,
    retry_on: Optional[Tuple[Type[Exception], ...]] = None,
    no_retry_on: Optional[Tuple[Type[Exception], ...]] = None,
    on_retry: Optional[Callable[[Exception, int, float], None]] = None
):
    """
    Decorator for adding retry logic to functions.
    
    Can be used with or without parentheses:
        @retry
        async def my_function(): ...
        
        @retry(max_retries=5)
        async def my_function(): ...
    
    Args:
        config: Full RetryConfig object
        max_retries: Override max retries
        base_delay: Override base delay
        retry_on: Tuple of exception types to retry on
        no_retry_on: Tuple of exception types to never retry on
        on_retry: Callback when retry occurs
    
    Returns:
        Decorated function with retry logic
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        # Build configuration
        cfg = config or RetryConfig()
        
        if max_retries is not None:
            cfg = RetryConfig(
                max_retries=max_retries,
                base_delay=cfg.base_delay,
                max_delay=cfg.max_delay,
                backoff_multiplier=cfg.backoff_multiplier,
                jitter=cfg.jitter,
                jitter_factor=cfg.jitter_factor,
                retry_on=cfg.retry_on,
                no_retry_on=cfg.no_retry_on,
                on_retry=cfg.on_retry
            )
        
        if base_delay is not None:
            cfg.base_delay = base_delay
        
        if retry_on is not None:
            cfg.retry_on = retry_on
        
        if no_retry_on is not None:
            cfg.no_retry_on = no_retry_on
        
        if on_retry is not None:
            cfg.on_retry = on_retry
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(cfg.max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    
                    # Check if we should retry
                    if attempt >= cfg.max_retries or not cfg.should_retry(e):
                        logger.error(
                            f"{func.__name__} failed after {attempt + 1} attempts: {e}"
                        )
                        raise
                    
                    # Calculate delay
                    delay = cfg.calculate_delay(attempt)
                    
                    logger.warning(
                        f"{func.__name__} attempt {attempt + 1} failed: {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    
                    # Call retry callback if provided
                    if cfg.on_retry:
                        try:
                            cfg.on_retry(e, attempt + 1, delay)
                        except Exception:
                            pass
                    
                    await asyncio.sleep(delay)
            
            # Should not reach here, but handle it
            raise RetryError(
                f"{func.__name__} failed after {cfg.max_retries + 1} attempts",
                attempts=cfg.max_retries + 1,
                last_exception=last_exception
            )
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(cfg.max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    
                    if attempt >= cfg.max_retries or not cfg.should_retry(e):
                        logger.error(
                            f"{func.__name__} failed after {attempt + 1} attempts: {e}"
                        )
                        raise
                    
                    delay = cfg.calculate_delay(attempt)
                    
                    logger.warning(
                        f"{func.__name__} attempt {attempt + 1} failed: {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    
                    if cfg.on_retry:
                        try:
                            cfg.on_retry(e, attempt + 1, delay)
                        except Exception:
                            pass
                    
                    import time
                    time.sleep(delay)
            
            raise RetryError(
                f"{func.__name__} failed after {cfg.max_retries + 1} attempts",
                attempts=cfg.max_retries + 1,
                last_exception=last_exception
            )
        
        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    # Handle both @retry and @retry() syntax
    if callable(config):
        # Called without parentheses: @retry
        func = config
        config = None
        return decorator(func)
    
    return decorator


async def retry_async(
    func: Callable[..., T],
    *args,
    config: Optional[RetryConfig] = None,
    **kwargs
) -> T:
    """
    Retry a single async function call.
    
    Usage:
        result = await retry_async(some_function, arg1, arg2, config=my_config)
    """
    cfg = config or DEFAULT_CONFIG
    last_exception = None
    
    for attempt in range(cfg.max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            
            if attempt >= cfg.max_retries or not cfg.should_retry(e):
                raise
            
            delay = cfg.calculate_delay(attempt)
            
            logger.warning(
                f"Retry attempt {attempt + 1} for {func.__name__}: {e}. "
                f"Waiting {delay:.2f}s..."
            )
            
            await asyncio.sleep(delay)
    
    raise RetryError(
        f"Failed after {cfg.max_retries + 1} attempts",
        attempts=cfg.max_retries + 1,
        last_exception=last_exception
    )


class RetryContext:
    """
    Context manager for retry logic.
    
    Usage:
        async with RetryContext(max_retries=3) as ctx:
            while ctx.should_continue:
                try:
                    result = await risky_operation()
                    break
                except Exception as e:
                    if not await ctx.handle_error(e):
                        raise
    """
    
    def __init__(self, config: Optional[RetryConfig] = None, **kwargs):
        self.config = config or RetryConfig(**kwargs)
        self.attempt = 0
        self.last_error: Optional[Exception] = None
        self._should_continue = True
    
    @property
    def should_continue(self) -> bool:
        return self._should_continue
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
    
    async def handle_error(self, error: Exception) -> bool:
        """
        Handle an error and determine if we should retry.
        
        Returns True if we should retry, False if we should give up.
        """
        self.last_error = error
        self.attempt += 1
        
        if self.attempt > self.config.max_retries:
            self._should_continue = False
            return False
        
        if not self.config.should_retry(error):
            self._should_continue = False
            return False
        
        delay = self.config.calculate_delay(self.attempt - 1)
        
        logger.warning(
            f"RetryContext attempt {self.attempt}: {error}. "
            f"Waiting {delay:.2f}s..."
        )
        
        await asyncio.sleep(delay)
        return True

