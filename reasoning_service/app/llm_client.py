"""LLM client abstraction. Default provider is local Ollama (zero API keys);
ANTHROPIC/OPENAI are optional hosted overrides selected via LLM_PROVIDER.
Every client exposes the same complete(system, user) -> str surface so the
extraction/classification code doesn't need to know which provider it's
talking to.
"""

import logging
from typing import Protocol

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def ensure_model_available(self) -> None:
        """Blocking pull so a fresh `docker compose up` doesn't need a manual
        `ollama pull` step. No-ops quickly if the model is already present
        (e.g. on the persisted ollama-data volume from a prior run).

        Also does a throwaway /api/chat call to force Ollama to load the
        model into the inference runtime before the consumer loop starts.
        Pulling weights to disk doesn't load them — the first real request
        pays that cold-start cost otherwise, and on this box loading
        llama3.2:1b alone measured ~120s, which blew through complete()'s
        timeout and crashed the service mid-request."""
        logger.info("Ensuring Ollama model %r is pulled (this may take a while on first run)...", self.model)
        with httpx.Client(timeout=1800.0) as client:
            resp = client.post(
                f"{self.base_url}/api/pull",
                json={"model": self.model, "stream": False},
            )
            resp.raise_for_status()
        logger.info("Ollama model %r pulled. Warming up (loading into memory)...", self.model)
        with httpx.Client(timeout=600.0) as client:
            resp = client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                },
            )
            resp.raise_for_status()
        logger.info("Ollama model %r ready.", self.model)

    def complete(self, system: str, user: str) -> str:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "stream": False,
                    "options": {"temperature": 0.2},
                },
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]


class AnthropicClient:
    def __init__(self, api_key: str, model: str) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def complete(self, system: str, user: str) -> str:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text


class OpenAIClient:
    def __init__(self, api_key: str, model: str) -> None:
        import openai

        self._client = openai.OpenAI(api_key=api_key)
        self.model = model

    def complete(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "ollama":
        client = OllamaClient(settings.ollama_base_url, settings.ollama_model)
        client.ensure_model_available()
        return client
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
        return AnthropicClient(settings.anthropic_api_key, settings.anthropic_model)
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set")
        return OpenAIClient(settings.openai_api_key, settings.openai_model)
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
