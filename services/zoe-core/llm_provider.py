"""
LLM Provider Abstraction Layer

ARCHITECTURE (2025-11-17):
  User Request → RouteLLM (routing logic) → LiteLLM Gateway (execution) → [Local Models | Cloud APIs]
  
  LiteLLM Gateway: http://zoe-litellm:8001/v1/chat/completions
    - Unified OpenAI-compatible API
    - Automatic fallbacks (hermes3 → qwen → gemma → cloud)
    - Redis-backed caching (10min TTL)
    - Load balancing across workers
    - Usage tracking
  
Supported providers:
  - LiteLLM (PRIMARY - unified gateway for all models)
  - llama.cpp (LEGACY - direct access if needed)
"""
import httpx
import json
import logging
import platform
from typing import Optional, AsyncGenerator
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """Abstract base class for LLM providers"""
    
    @abstractmethod
    async def generate(self, prompt: str, model: str = "auto", **kwargs) -> str:
        """Generate a response from the LLM"""
        pass
    
    @abstractmethod
    async def generate_stream(self, prompt: str, **kwargs) -> AsyncGenerator[str, None]:
        """Stream tokens from the LLM"""
        pass


class LiteLLMProvider(LLMProvider):
    """
    LiteLLM Gateway Provider - PRIMARY PROVIDER
    
    Architecture:
      - Unified gateway for ALL models (local + cloud)
      - OpenAI-compatible API endpoint
      - Automatic fallbacks configured in minimal_config.yaml
      - Redis-backed caching (10min TTL)
      - Load balancing across workers
      - Usage tracking and monitoring
    
    Benefits:
      - Single endpoint for all models
      - Zero code changes to switch models
      - Built-in reliability (fallbacks, retries)
      - Cost optimization (caching)
    
    Configured Models (minimal_config.yaml):
      Local:  hermes3-8b, gemma-2-2b, qwen2.5-7b (via zoe-llamacpp)
      Cloud:  gpt-4o-mini, claude-3-5-sonnet (if API keys set)
    """
    
    def __init__(self, base_url: str = "http://zoe-litellm:8001"):
        self.base_url = base_url
        logger.info("🎯 Using LiteLLM Gateway (unified API for all models)")
    
    async def generate(self, prompt: str, model: str = "auto", **kwargs) -> str:
        """Generate via LiteLLM gateway (OpenAI-compatible)"""
        try:
            # If model is "auto", LiteLLM uses routing strategy from config
            # Otherwise, use specific model name from minimal_config.yaml
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json={
                        "model": model if model != "auto" else "hermes3-8b",  # Default to fast model
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "temperature": kwargs.get("temperature", 0.7),
                        "max_tokens": kwargs.get("max_tokens", 512)
                    },
                    headers={"Authorization": "Bearer sk-f3320300bb32df8f176495bb888ba7c8f87a0d01c2371b50f767b9ead154175f"}
                )
                result = response.json()
                return result["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"❌ LiteLLM generation error: {e}")
            raise
    
    async def generate_stream(self, prompt: str, **kwargs) -> AsyncGenerator[str, None]:
        """Stream tokens via LiteLLM gateway"""
        try:
            model = kwargs.get("model", "hermes3-8b")
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": True,
                        "temperature": kwargs.get("temperature", 0.7),
                        "max_tokens": kwargs.get("max_tokens", 512)
                    },
                    headers={"Authorization": "Bearer sk-f3320300bb32df8f176495bb888ba7c8f87a0d01c2371b50f767b9ead154175f"}
                ) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                token_data = json.loads(data)
                                if "choices" in token_data and len(token_data["choices"]) > 0:
                                    delta = token_data["choices"][0].get("delta", {})
                                    if "content" in delta:
                                        yield delta["content"]
                            except:
                                continue
        except Exception as e:
            logger.error(f"❌ LiteLLM streaming error: {e}")
            raise


class LlamaCppProvider(LLMProvider):
    """
    llama.cpp Provider - LEGACY/DIRECT ACCESS
    Use LiteLLMProvider instead for production
    """
    
    def __init__(self, base_url: str = "http://zoe-llamacpp:11434"):
        self.base_url = base_url
        logger.warning("⚠️ Using direct llama.cpp provider (consider LiteLLM gateway instead)")
    
    async def generate(self, prompt: str, model: str = "auto", **kwargs) -> str:
        """Generate with llama.cpp (OpenAI-compatible API)"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "temperature": kwargs.get("temperature", 0.7),
                        "max_tokens": kwargs.get("max_tokens", 512)
                    }
                )
                result = response.json()
                return result["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"❌ llama.cpp generation error: {e}")
            raise
    
    async def generate_stream(self, prompt: str, **kwargs) -> AsyncGenerator[str, None]:
        """Stream tokens from llama.cpp"""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": True,
                        "temperature": kwargs.get("temperature", 0.7),
                        "max_tokens": kwargs.get("max_tokens", 512)
                    }
                ) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                token_data = json.loads(data)
                                if "choices" in token_data and len(token_data["choices"]) > 0:
                                    delta = token_data["choices"][0].get("delta", {})
                                    if "content" in delta:
                                        yield delta["content"]
                            except:
                                continue
        except Exception as e:
            logger.error(f"❌ llama.cpp streaming error: {e}")
            raise


class OllamaProvider(LLMProvider):
    """
    Generic Ollama-compatible Provider
    Works with Ollama, LM Studio, or any Ollama API-compatible server
    """
    
    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        logger.info("🔧 Using Ollama-compatible provider")
    
    async def generate(self, prompt: str, model: str = "qwen2.5:7b", **kwargs) -> str:
        """Generate with Ollama-compatible API"""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": kwargs.get("temperature", 0.7),
                            "num_ctx": kwargs.get("max_tokens", 2048)
                        }
                    }
                )
                result = response.json()
                return result.get("response", "")
        except Exception as e:
            logger.error(f"❌ Generation error: {e}")
            raise
    
    async def generate_stream(self, prompt: str, **kwargs) -> AsyncGenerator[str, None]:
        """Stream tokens from Ollama-compatible API"""
        try:
            model = kwargs.get("model", "qwen2.5:7b")
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": True,
                        "options": {
                            "temperature": kwargs.get("temperature", 0.7)
                        }
                    }
                ) as response:
                    async for line in response.aiter_lines():
                        if line:
                            try:
                                data = json.loads(line)
                                if "response" in data:
                                    yield data["response"]
                                if data.get("done", False):
                                    break
                            except:
                                continue
        except Exception as e:
            logger.error(f"❌ Streaming error: {e}")
            raise


def detect_hardware() -> str:
    """
    Detect hardware platform
    Returns: "jetson" or "raspberry_pi" or "unknown"
    """
    try:
        # Check for Jetson
        with open("/etc/nv_tegra_release", "r") as f:
            if "tegra" in f.read().lower():
                return "jetson"
    except FileNotFoundError:
        pass
    
    # Check for Raspberry Pi
    try:
        with open("/proc/cpuinfo", "r") as f:
            cpuinfo = f.read().lower()
            if "raspberry pi" in cpuinfo or "bcm" in cpuinfo:
                return "raspberry_pi"
    except:
        pass
    
    return "unknown"


# Singleton instance
_provider_instance: Optional[LLMProvider] = None


def get_llm_provider(force_provider: Optional[str] = None) -> LLMProvider:
    """
    Get LLM provider - ALWAYS returns LiteLLM Gateway by default (2025-11-17)
    
    ARCHITECTURE DECISION:
      - LiteLLM Gateway is the PRIMARY and RECOMMENDED provider
      - Provides unified API for all models (local + cloud)
      - Handles fallbacks, caching, load balancing automatically
      - To use direct providers, set LLM_PROVIDER env var or force_provider
    
    Args:
        force_provider: Optional["litellm", "llamacpp", "ollama"] to override default
    
    Returns:
        LLMProvider instance (default: LiteLLMProvider)
    """
    global _provider_instance
    
    if _provider_instance is not None:
        return _provider_instance
    
    # Check environment variable for provider override
    import os
    env_provider = os.getenv("LLM_PROVIDER", "litellm").lower()  # Default to litellm
    
    if force_provider:
        provider_type = force_provider.lower()
    elif env_provider in ["litellm", "llamacpp", "ollama"]:
        provider_type = env_provider
    else:
        # ALWAYS default to LiteLLM Gateway (production standard)
        provider_type = "litellm"
        logger.info("🎯 Using LiteLLM Gateway (production default)")
    
    # Create provider instance
    if provider_type == "litellm":
        _provider_instance = LiteLLMProvider()
    elif provider_type == "llamacpp":
        _provider_instance = LlamaCppProvider()
        logger.warning("⚠️ Direct llamacpp access - consider using LiteLLM gateway")
    elif provider_type == "ollama":
        _provider_instance = OllamaProvider()
        logger.warning("⚠️ Ollama compatibility mode - consider using LiteLLM gateway")
    else:
        raise ValueError(f"Unknown provider type: {provider_type}. Use: litellm, llamacpp, ollama")
    
    return _provider_instance


# For backward compatibility with legacy code
async def call_ollama_direct(prompt: str, model: str = "auto", **kwargs) -> str:
    """Legacy function name - routes to current LLM provider"""
    provider = get_llm_provider()
    return await provider.generate(prompt, model, **kwargs)


async def call_ollama_streaming(prompt: str, **kwargs) -> AsyncGenerator[str, None]:
    """Legacy function name - routes to current LLM provider"""
    provider = get_llm_provider()
    async for token in provider.generate_stream(prompt, **kwargs):
        yield token


# Modern generic function names
async def generate_text(prompt: str, model: str = "auto", **kwargs) -> str:
    """Generate text from LLM"""
    provider = get_llm_provider()
    return await provider.generate(prompt, model, **kwargs)


async def generate_text_stream(prompt: str, **kwargs) -> AsyncGenerator[str, None]:
    """Stream tokens from LLM"""
    provider = get_llm_provider()
    async for token in provider.generate_stream(prompt, **kwargs):
        yield token


