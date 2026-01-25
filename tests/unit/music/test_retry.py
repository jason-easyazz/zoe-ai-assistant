"""
Unit Tests for Retry Utility
============================

Tests the retry decorator and configuration.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch

import sys
sys.path.insert(0, '/home/zoe/assistant/services/zoe-core')

from utils.retry import (
    retry,
    RetryConfig,
    RetryError,
    retry_async,
    RetryContext,
    DEFAULT_CONFIG,
    NETWORK_CONFIG
)


class TestRetryConfig:
    """Tests for RetryConfig."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = RetryConfig()
        
        assert config.max_retries == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0
        assert config.backoff_multiplier == 2.0
        assert config.jitter is True
    
    def test_calculate_delay_exponential(self):
        """Test exponential backoff calculation."""
        config = RetryConfig(
            base_delay=1.0,
            backoff_multiplier=2.0,
            jitter=False
        )
        
        assert config.calculate_delay(0) == 1.0
        assert config.calculate_delay(1) == 2.0
        assert config.calculate_delay(2) == 4.0
        assert config.calculate_delay(3) == 8.0
    
    def test_calculate_delay_respects_max(self):
        """Test that delay is capped at max_delay."""
        config = RetryConfig(
            base_delay=10.0,
            max_delay=20.0,
            backoff_multiplier=10.0,
            jitter=False
        )
        
        # 10 * 10^2 = 1000, but should be capped at 20
        assert config.calculate_delay(2) == 20.0
    
    def test_should_retry_default(self):
        """Test should_retry with default config."""
        config = RetryConfig()
        
        # Should retry on any exception by default
        assert config.should_retry(ValueError()) is True
        assert config.should_retry(ConnectionError()) is True
    
    def test_should_retry_with_retry_on(self):
        """Test should_retry with specific exceptions."""
        config = RetryConfig(
            retry_on=(ConnectionError, TimeoutError)
        )
        
        assert config.should_retry(ConnectionError()) is True
        assert config.should_retry(TimeoutError()) is True
        assert config.should_retry(ValueError()) is False
    
    def test_should_retry_with_no_retry_on(self):
        """Test should_retry with excluded exceptions."""
        config = RetryConfig(
            no_retry_on=(ValueError,)
        )
        
        assert config.should_retry(ConnectionError()) is True
        assert config.should_retry(ValueError()) is False


class TestRetryDecorator:
    """Tests for the @retry decorator."""
    
    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        """Test successful function doesn't retry."""
        call_count = 0
        
        @retry(max_retries=3)
        async def successful_func():
            nonlocal call_count
            call_count += 1
            return "success"
        
        result = await successful_func()
        
        assert result == "success"
        assert call_count == 1
    
    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        """Test function retries then succeeds."""
        call_count = 0
        
        @retry(max_retries=3, base_delay=0.01)
        async def eventually_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Not yet")
            return "success"
        
        result = await eventually_succeeds()
        
        assert result == "success"
        assert call_count == 3
    
    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        """Test exception raised after max retries."""
        call_count = 0
        
        @retry(max_retries=2, base_delay=0.01)
        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise ValueError("Always fails")
        
        with pytest.raises(ValueError, match="Always fails"):
            await always_fails()
        
        assert call_count == 3  # Initial + 2 retries
    
    @pytest.mark.asyncio
    async def test_no_retry_on_excluded_exception(self):
        """Test no retry on excluded exception type."""
        call_count = 0
        
        @retry(
            max_retries=3,
            base_delay=0.01,
            no_retry_on=(ValueError,)
        )
        async def fails_with_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("No retry for me")
        
        with pytest.raises(ValueError):
            await fails_with_value_error()
        
        assert call_count == 1  # No retries
    
    @pytest.mark.asyncio
    async def test_on_retry_callback(self):
        """Test on_retry callback is called."""
        callback_calls = []
        
        def on_retry(exc, attempt, delay):
            callback_calls.append({
                "exception": str(exc),
                "attempt": attempt,
                "delay": delay
            })
        
        @retry(max_retries=2, base_delay=0.01, on_retry=on_retry)
        async def fails_twice():
            if len(callback_calls) < 2:
                raise RuntimeError("Fail")
            return "success"
        
        result = await fails_twice()
        
        assert result == "success"
        assert len(callback_calls) == 2
        assert callback_calls[0]["attempt"] == 1
        assert callback_calls[1]["attempt"] == 2


class TestRetryAsync:
    """Tests for the retry_async function."""
    
    @pytest.mark.asyncio
    async def test_retry_async_success(self):
        """Test retry_async with successful function."""
        async def my_func(x, y):
            return x + y
        
        result = await retry_async(my_func, 2, 3)
        
        assert result == 5
    
    @pytest.mark.asyncio
    async def test_retry_async_with_config(self):
        """Test retry_async with custom config."""
        call_count = 0
        
        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError()
            return "ok"
        
        config = RetryConfig(max_retries=3, base_delay=0.01)
        result = await retry_async(flaky_func, config=config)
        
        assert result == "ok"
        assert call_count == 2


class TestRetryContext:
    """Tests for the RetryContext context manager."""
    
    @pytest.mark.asyncio
    async def test_retry_context_success(self):
        """Test RetryContext with successful operation."""
        attempts = 0
        
        async with RetryContext(max_retries=3, base_delay=0.01) as ctx:
            while ctx.should_continue:
                try:
                    attempts += 1
                    if attempts < 3:
                        raise ConnectionError()
                    result = "success"
                    break
                except Exception as e:
                    if not await ctx.handle_error(e):
                        raise
        
        assert result == "success"
        assert attempts == 3
    
    @pytest.mark.asyncio
    async def test_retry_context_exhausted(self):
        """Test RetryContext when retries exhausted."""
        attempts = 0
        
        async with RetryContext(max_retries=2, base_delay=0.01) as ctx:
            while ctx.should_continue:
                try:
                    attempts += 1
                    raise ValueError("Always fails")
                except Exception as e:
                    if not await ctx.handle_error(e):
                        break
        
        assert attempts == 3  # Initial + 2 retries
        assert ctx.last_error is not None


class TestNetworkConfig:
    """Tests for NETWORK_CONFIG preset."""
    
    def test_network_config_retries_on_network_errors(self):
        """Test NETWORK_CONFIG retries on network errors."""
        assert NETWORK_CONFIG.should_retry(ConnectionError()) is True
        assert NETWORK_CONFIG.should_retry(TimeoutError()) is True
        assert NETWORK_CONFIG.should_retry(OSError()) is True
    
    def test_network_config_no_retry_on_other_errors(self):
        """Test NETWORK_CONFIG doesn't retry on other errors."""
        assert NETWORK_CONFIG.should_retry(ValueError()) is False
        assert NETWORK_CONFIG.should_retry(KeyError()) is False

