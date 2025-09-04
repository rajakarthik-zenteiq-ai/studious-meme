"""
LLM Provider Factory with support for OpenAI, Gemini, and vLLM (with optional LMCache)
"""
import os
from typing import Optional, Dict, Any
import logging

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

class LLMProvider:
    """Enum for LLM providers"""
    OPENAI = "openai"
    GEMINI = "gemini"
    VLLM = "vllm"

class LLM:
    """Factory for creating LLM instances with provider switching"""
    
    def __init__(self):
        self.providers = {
            LLMProvider.OPENAI: self._create_openai,
            LLMProvider.VLLM: self._create_vllm,
        }
        # Attempt LMCache optional import
        self._lmcache_available = False
        try:
            import lmcache  # noqa: F401
            self._lmcache_available = True
        except Exception:
            pass

    def _create_openai(self, **kwargs) -> BaseChatModel:
        """Create OpenAI LLM instance"""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")
            
        model = kwargs.get("model", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
        temperature = kwargs.get("temperature", 0.1)
        max_tokens = kwargs.get("max_tokens")
        return ChatOpenAI(
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=True
        )
    
    def _create_vllm(self, **kwargs) -> BaseChatModel:
        """Create a vLLM-backed model with optional LMCache layer.
        Expects VLLM_ENDPOINT=http(s)://host:port and VLLM_MODEL name.
        If LMCache is available and LMCACHE_ENABLED=true it will wrap the HTTP calls.
        """
        endpoint = os.getenv("VLLM_ENDPOINT")
        model_name = kwargs.get("model", os.getenv("VLLM_MODEL", "meta-llama/Llama-3-8b-instruct"))
        if not endpoint:
            raise ValueError("VLLM_ENDPOINT not set for vLLM provider")
        # Use generic OpenAI-compatible interface if vLLM exposes OpenAI API; else placeholder
        from langchain_openai import ChatOpenAI as OpenAICompat
        openai_base = endpoint.rstrip('/')
        api_key = os.getenv("VLLM_API_KEY", "dummy-key")  # vLLM may ignore
        llm = OpenAICompat(
            api_key=api_key,
            base_url=openai_base,
            model=model_name,
            temperature=kwargs.get("temperature", 0.1),
            max_tokens=kwargs.get("max_tokens"),
            streaming=True
        )
        # LMCache integration stub
        if self._lmcache_available and os.getenv("LMCACHE_ENABLED", "false").lower() == "true":
            try:
                from lmcache import cache
                # Basic HTTP cache configuration
                cache_ttl = int(os.getenv("LMCACHE_TTL", "300"))
                cache.max_age = cache_ttl
                logger.info(f"LMCache enabled for vLLM with ttl={cache_ttl}s")
            except Exception as e:
                logger.warning(f"Failed to enable LMCache: {e}")
        return llm

    def get_llm(self, provider: str, **kwargs) -> BaseChatModel:
        """Get LLM instance for the specified provider"""
        if provider not in self.providers:
            raise ValueError(f"Unknown provider: {provider}. Available: {list(self.providers.keys())}")
            
        try:
            llm = self.providers[provider](**kwargs)
            logger.info(f"Created LLM instance for provider: {provider}")
            return llm
        except Exception as e:
            logger.error(f"Failed to create LLM for provider {provider}: {e}")
            raise
    
    def get_available_providers(self) -> Dict[str, bool]:
        """Check which providers are available based on environment variables"""
        return {
            LLMProvider.OPENAI: bool(os.getenv("OPENAI_API_KEY")),
            LLMProvider.GEMINI: False,  # Placeholder
            LLMProvider.VLLM: bool(os.getenv("VLLM_ENDPOINT")),
        }
    
    def get_default_provider(self) -> str:
        """Get the default provider based on availability"""
        available = self.get_available_providers()
        
        if available[LLMProvider.OPENAI]:
            return LLMProvider.OPENAI
        if available[LLMProvider.VLLM]:
            return LLMProvider.VLLM
        raise ValueError("No LLM provider configured. Set OPENAI_API_KEY or VLLM_ENDPOINT")