"""Provider-agnostic OpenAI-compatible LLM configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LLMProviderConfig:
    """Resolved credentials and base URL for OpenAI-compatible APIs."""

    api_key: str
    base_url: Optional[str] = None
    provider: str = "openai"


_DEFAULT_EMBEDDING_OPENAI = "text-embedding-3-small"
_DEFAULT_EMBEDDING_OPENROUTER = "openai/text-embedding-3-small"


def _looks_like_chat_model(model: str) -> bool:
    lower = model.lower()
    if "embed" in lower:
        return False
    chat_markers = (
        "llama",
        "mistral",
        "phi-",
        "gemma",
        "qwen",
        "gpt-4",
        "gpt-3",
        "claude",
        "mixtral",
    )
    return any(marker in lower for marker in chat_markers)


def get_embedding_provider_config() -> LLMProviderConfig:
    """
    Resolve credentials for embedding APIs.

    Groq has no embedding endpoint, so prefer OpenRouter or OpenAI even when
    GROQ_API_KEY is set for chat.
    """
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_EMBEDDING_BASE_URL") or os.getenv("OPENAI_BASE_URL")

    if openrouter_key:
        return LLMProviderConfig(
            api_key=openrouter_key,
            base_url=base_url or "https://openrouter.ai/api/v1",
            provider="openrouter",
        )
    if openai_key:
        return LLMProviderConfig(
            api_key=openai_key,
            base_url=base_url,
            provider="openai",
        )
    raise ValueError(
        "RAG embeddings require OPENROUTER_API_KEY or OPENAI_API_KEY. "
        "Groq does not provide embedding models."
    )


def resolve_embedding_model(
    provider: LLMProviderConfig,
    explicit: Optional[str] = None,
) -> str:
    """Pick a valid embedding model for the configured provider."""
    model = (explicit or os.getenv("OPENAI_EMBEDDING_MODEL") or "").strip()
    if not model or _looks_like_chat_model(model):
        if provider.provider == "openrouter":
            return _DEFAULT_EMBEDDING_OPENROUTER
        return _DEFAULT_EMBEDDING_OPENAI
    if provider.provider == "openrouter" and "/" not in model:
        return f"openai/{model}"
    return model


def get_llm_provider_config() -> LLMProviderConfig:
    """
    Resolve provider config from environment variables.

    Supported setups:
    - OpenAI:
        OPENAI_API_KEY=...
    - Groq:
        GROQ_API_KEY=...
    - OpenRouter:
        OPENROUTER_API_KEY=...
        OPENAI_BASE_URL=https://openrouter.ai/api/v1   (recommended)
    """
    groq_key = os.getenv("GROQ_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")

    if groq_key:
        return LLMProviderConfig(
            api_key=groq_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1"),
            provider="groq",
        )
    if openai_key:
        return LLMProviderConfig(
            api_key=openai_key,
            base_url=os.getenv("OPENAI_BASE_URL"),
            provider="openai",
        )
    if openrouter_key:
        return LLMProviderConfig(
            api_key=openrouter_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
            provider="openrouter",
        )
    raise ValueError(
        "No LLM API key found. Set GROQ_API_KEY (Groq), OPENAI_API_KEY (OpenAI), or "
        "OPENROUTER_API_KEY (OpenRouter)."
    )
