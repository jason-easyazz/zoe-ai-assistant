"""
LLM Provider Abstraction Layer
Supports: vLLM (Jetson), Ollama (Raspberry Pi)
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


class VLLMProvider(LLMProvider):
    """
    vLLM Provider for Jetson Orin NX
    Production-ready with streaming support
    """
    
    def __init__(self, base_url: str = "http://zoe-vllm:11434"):
        self.base_url = base_url
        logger.info("🚀 Using vLLM provider (production mode)")
    
    async def generate(self, prompt: str, model: str = "auto", **kwargs) -> str:
        """Generate with vLLM (routing handled server-side)"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "context": kwargs.get("context", {})
                    }
                )
                result = response.json()
                return result["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"❌ vLLM generation error: {e}")
            raise
    
    async def generate_stream(self, prompt: str, **kwargs) -> AsyncGenerator[str, None]:
        """Stream tokens for voice UX"""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": True,
                        "context": kwargs.get("context", {})
                    }
                ) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                token_data = json.loads(data)
                                yield token_data.get("token", "")
                            except:
                                continue
        except Exception as e:
            logger.error(f"❌ vLLM streaming error: {e}")
            raise


class OllamaProvider(LLMProvider):
    """
    Ollama Provider for Raspberry Pi
    Fallback/compatibility mode
    """
    
    def __init__(self, base_url: str = "http://zoe-ollama:11434"):
        self.base_url = base_url
        logger.info("🐧 Using Ollama provider (compatibility mode)")
    
    async def generate(self, prompt: str, model: str = "qwen2.5:7b", **kwargs) -> str:
        """Generate with Ollama"""
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
            logger.error(f"❌ Ollama generation error: {e}")
            raise
    
    async def generate_stream(self, prompt: str, **kwargs) -> AsyncGenerator[str, None]:
        """Stream tokens from Ollama"""
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
            logger.error(f"❌ Ollama streaming error: {e}")
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
    Get LLM provider based on hardware detection or force specific provider
    
    Args:
        force_provider: Optional["vllm", "ollama"] to override detection
    
    Returns:
        LLMProvider instance
    """
    global _provider_instance
    
    if _provider_instance is not None:
        return _provider_instance
    
    if force_provider:
        provider_type = force_provider
    else:
        hardware = detect_hardware()
        if hardware == "jetson":
            provider_type = "vllm"
        else:
            provider_type = "ollama"
            logger.warning(
                f"⚠️ Hardware detection: {hardware}, defaulting to Ollama. "
                f"For Jetson, use vLLM for best performance."
            )
    
    if provider_type == "vllm":
        _provider_instance = VLLMProvider()
    elif provider_type == "ollama":
        _provider_instance = OllamaProvider()
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")
    
    return _provider_instance


# For backward compatibility
async def call_ollama_direct(prompt: str, model: str = "auto", **kwargs) -> str:
    """Legacy function - routes to current provider"""
    provider = get_llm_provider()
    return await provider.generate(prompt, model, **kwargs)


async def call_ollama_streaming(prompt: str, **kwargs) -> AsyncGenerator[str, None]:
    """Legacy function - routes to current provider"""
    provider = get_llm_provider()
    async for token in provider.generate_stream(prompt, **kwargs):
        yield token


