"""
LLM Provider Factory with support for OpenAI, Gemini, and vLLM
"""
import os
from typing import Optional, Dict, Any
import logging

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
try:
    from langchain_google_genai import ChatGoogleGenerativeAI
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    
try:
    from langchain_community.llms import VLLMOpenAI
    VLLM_AVAILABLE = True
except ImportError:
    VLLM_AVAILABLE = False

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
            LLMProvider.GEMINI: self._create_gemini,
            LLMProvider.VLLM: self._create_vllm
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
    
    def _create_gemini(self, **kwargs) -> BaseChatModel:
        """Create Google Gemini LLM instance"""
        if not GEMINI_AVAILABLE:
            raise ValueError("langchain_google_genai not installed. Install with: pip install langchain-google-genai")
            
        api_key = os.getenv("GOOGLE_GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_GEMINI_API_KEY not set")
            
        model = kwargs.get("model", os.getenv("GEMINI_MODEL", "gemini-1.5-flash"))
        temperature = kwargs.get("temperature", 0.1)
        
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            google_api_key=api_key,
            model=model,
            temperature=temperature,
            streaming=True,
            convert_system_message_to_human=True
        )
    
    def _create_vllm(self, **kwargs) -> BaseChatModel:
        """Create vLLM instance (self-hosted)"""
        if not VLLM_AVAILABLE:
            raise ValueError("langchain_community not installed with vLLM support")
            
        vllm_url = os.getenv("VLLM_URL", "http://vllm:8001/v1")
        model = kwargs.get("model", os.getenv("VLLM_MODEL", "deepseek-ai/deepseek-coder-7b-instruct-v1.5"))
        temperature = kwargs.get("temperature", 0.1)
        
        from langchain_community.llms import VLLMOpenAI
        return VLLMOpenAI(
            openai_api_base=vllm_url,
            model_name=model,
            temperature=temperature,
            streaming=True,
            openai_api_key="dummy"  # vLLM doesn't need a real key
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
            LLMProvider.GEMINI: GEMINI_AVAILABLE and bool(os.getenv("GOOGLE_GEMINI_API_KEY")),
            LLMProvider.VLLM: VLLM_AVAILABLE and bool(os.getenv("VLLM_URL"))
        }
    
    def get_default_provider(self) -> str:
        """Get the default provider based on availability"""
        available = self.get_available_providers()
        
        # Priority order
        if available[LLMProvider.OPENAI]:
            return LLMProvider.OPENAI
        elif available[LLMProvider.GEMINI]:
            return LLMProvider.GEMINI
        elif available[LLMProvider.VLLM]:
            return LLMProvider.VLLM
        else:
            raise ValueError("No LLM provider configured. Set OPENAI_API_KEY, GOOGLE_GEMINI_API_KEY, or VLLM_URL")