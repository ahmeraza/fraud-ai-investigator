"""
app/llm/client.py
──────────────────
Unified LLM client — Gemini Flash primary, Groq fallback.

Design:
  - Single interface: callers never know which provider responded
  - Structured JSON output: every response is parsed and validated
  - Automatic fallback: Groq activates if Gemini fails or rate-limits
  - Exponential backoff: 2s → 4s between retries per provider
  - Full logging: every call logged with provider, latency, outcome

Free tier limits (both $0/month):
  Gemini Flash: 15 req/min, 1500 req/day
  Groq Llama3:  30 req/min
"""

from __future__ import annotations

import json
import time
from typing import Optional

from google import genai
from groq import Groq

from app.core.config import get_settings
from app.core.logging import get_logger

logger   = get_logger(__name__)
settings = get_settings()


class LLMResponse:
    """
    Wraps a raw LLM text response with metadata.
    Provides consistent interface regardless of which provider responded.
    """

    def __init__(self, content: str, provider: str, latency_ms: float, model: str) -> None:
        self.content    = content
        self.provider   = provider
        self.latency_ms = latency_ms
        self.model      = model

    def parse_json(self) -> dict:
        """
        Parse response as JSON.
        Handles markdown code fences (```json ... ```) that some models add.
        Raises ValueError if content is not valid JSON.
        """
        text = self.content.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            text  = "\n".join(lines[1:-1]).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(
                f"JSON parse failed | provider={self.provider} "
                f"| error={e} | content_preview={text[:200]}"
            )
            raise ValueError(f"LLM response is not valid JSON: {e}") from e

    def __repr__(self) -> str:
        return (
            f"LLMResponse(provider={self.provider}, "
            f"model={self.model}, latency={self.latency_ms:.0f}ms)"
        )


class GeminiProvider:
    """
    Google Gemini 2.5 Flash provider.
    Temperature 0.1 for consistent, deterministic JSON output.
    """

    def __init__(self) -> None:
        if not settings.has_gemini_key:
            raise RuntimeError("GEMINI_API_KEY not set in .env")
        self._client = genai.Client(api_key=settings.gemini_api_key)
        logger.info(f"GeminiProvider ready | model={settings.gemini_model}")

    def complete(self, prompt: str, system_prompt: str) -> LLMResponse:
        start       = time.monotonic()
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        
        response = self._client.models.generate_content(
            model=settings.gemini_model,
            contents=full_prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.1,        # low = consistent JSON output
                max_output_tokens=1024,
            ),
        )

        latency_ms = (time.monotonic() - start) * 1000
        logger.debug(f"Gemini response | latency={latency_ms:.0f}ms")

        return LLMResponse(
            content    = response.text,
            provider   = "gemini",
            latency_ms = latency_ms,
            model      = settings.gemini_model,
        )


class GroqProvider:
    """
    Groq Llama 3 70B provider.
    Used as fallback when Gemini fails or hits rate limits.
    """

    def __init__(self) -> None:
        if not settings.has_groq_key:
            raise RuntimeError("GROQ_API_KEY not set in .env")
        self._client = Groq(api_key=settings.groq_api_key)
        logger.info(f"GroqProvider ready | model={settings.groq_model}")

    def complete(self, prompt: str, system_prompt: str) -> LLMResponse:
        start    = time.monotonic()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = self._client.chat.completions.create(
            model       = settings.groq_model,
            messages    = messages,
            temperature = 0.1,
            max_tokens  = 1024,
        )

        latency_ms = (time.monotonic() - start) * 1000
        logger.debug(f"Groq response | latency={latency_ms:.0f}ms")

        return LLMResponse(
            content    = response.choices[0].message.content,
            provider   = "groq",
            latency_ms = latency_ms,
            model      = settings.groq_model,
        )


class LLMClient:
    """
    Unified client with automatic provider fallback.

    Order: Gemini → Groq (if Gemini fails).
    Each provider retried up to max_retries times with exponential backoff.

    Usage:
        client   = LLMClient()
        response = client.complete(prompt, system_prompt)
        data     = response.parse_json()
    """

    def __init__(self, max_retries: int = 2) -> None:
        self._max_retries = max_retries
        self._gemini: Optional[GeminiProvider] = None
        self._groq:   Optional[GroqProvider]   = None

        if settings.has_gemini_key:
            try:
                self._gemini = GeminiProvider()
            except Exception as e:
                logger.warning(f"Gemini init failed: {e}")

        if settings.has_groq_key:
            try:
                self._groq = GroqProvider()
            except Exception as e:
                logger.warning(f"Groq init failed: {e}")

        if not self._gemini and not self._groq:
            raise RuntimeError(
                "No LLM providers available. "
                "Add GEMINI_API_KEY or GROQ_API_KEY to .env"
            )

    def complete(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        """Send a prompt — tries Gemini first, falls back to Groq."""
        providers = []
        if self._gemini:
            providers.append(("gemini", self._gemini))
        if self._groq:
            providers.append(("groq", self._groq))

        last_error: Optional[Exception] = None

        for provider_name, provider in providers:
            for attempt in range(1, self._max_retries + 1):
                try:
                    logger.info(
                        f"LLM call | provider={provider_name} "
                        f"| attempt={attempt}/{self._max_retries}"
                    )
                    response = provider.complete(prompt, system_prompt)
                    logger.info(
                        f"LLM success | provider={provider_name} "
                        f"| latency={response.latency_ms:.0f}ms"
                    )
                    return response

                except Exception as e:
                    last_error = e
                    wait = 2 ** attempt  # 2s, then 4s
                    logger.warning(
                        f"LLM failed | provider={provider_name} "
                        f"| attempt={attempt} | error={e} | retry_in={wait}s"
                    )
                    if attempt < self._max_retries:
                        time.sleep(wait)

            logger.warning(f"Provider {provider_name} exhausted — trying fallback")

        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    @property
    def available_providers(self) -> list[str]:
        return (
            (["gemini"] if self._gemini else []) +
            (["groq"]   if self._groq   else [])
        )


# ── Singleton ─────────────────────────────────────────────────────────────────
_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Return the shared LLMClient singleton."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
