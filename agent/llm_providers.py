"""
LLM Provider Factory with support for OpenAI, Gemini, and vLLM
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

class LLMProviderFactory:
    """Factory for creating LLM instances with provider switching"""
    
    def __init__(self):
        self.providers = {
            LLMProvider.OPENAI: self._create_openai,
        }
        
    def _create_openai(self, **kwargs) -> BaseChatModel:
        """Create OpenAI LLM instance"""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")
            
        model = kwargs.get("model", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
        temperature = kwargs.get("temperature", 0.1)
        
        return ChatOpenAI(
            api_key=api_key,
            model=model,
            temperature=temperature,
            streaming=True
        )
    
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
            LLMProvider.VLLM: False,    # Placeholder
        }
    
    def get_default_provider(self) -> str:
        """Get the default provider based on availability"""
        available = self.get_available_providers()
        
        if available[LLMProvider.OPENAI]:
            return LLMProvider.OPENAI
        else:
            raise ValueError("No LLM provider configured. Set OPENAI_API_KEY")