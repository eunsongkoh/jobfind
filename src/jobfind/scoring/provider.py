import logging
import os
import random
import time
from collections import deque
from typing import Protocol

import httpx
from google import genai
from google.genai import types
from openai import OpenAI

from ..config import ScoringConfig

logger = logging.getLogger(__name__)

# Retryable: rate limiting and transient server errors
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_MAX_RETRIES = 5
_RETRY_BASE_DELAY_SECONDS = 2.0
_RETRY_MAX_DELAY_SECONDS = 60.0
_REQUESTS_PER_MINUTE = 14


def _status_code_of(error: Exception) -> int | None:
    return getattr(error, "status_code", None) or getattr(error, "code", None)


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, httpx.TransportError):
        return True
    return _status_code_of(error) in _RETRYABLE_STATUS_CODES


class LLMProvider(Protocol):
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float,
        max_tokens: int,
        response_format: dict | None = None,
    ) -> str: ...


class _RateLimiter:
    """Blocks until issuing another request keeps the caller under
    `requests_per_minute` in any trailing 60s window."""

    def __init__(self, requests_per_minute: int):
        self.requests_per_minute = requests_per_minute
        self._request_times: deque[float] = deque()

    def wait(self) -> None:
        now = time.monotonic()
        while self._request_times and now - self._request_times[0] >= 60:
            self._request_times.popleft()

        if len(self._request_times) >= self.requests_per_minute:
            sleep_for = 60 - (now - self._request_times[0])
            if sleep_for > 0:
                logger.info("rate limit throttle: sleeping %.1fs", sleep_for)
                time.sleep(sleep_for)
            now = time.monotonic()
            while self._request_times and now - self._request_times[0] >= 60:
                self._request_times.popleft()

        self._request_times.append(time.monotonic())


def _retry_delay_seconds(error: Exception, attempt: int, base_delay: float, max_delay: float) -> float:
    headers = getattr(error, "headers", None)
    retry_after = headers.get("Retry-After") if headers is not None else None
    if retry_after is not None:
        try:
            return min(float(retry_after), max_delay)
        except ValueError:
            pass

    # Full jitter: uniform in [0, backoff] rather than backoff * [0.5, 1.5],
    # so many jobs retrying at once after a shared 429 don't stay correlated.
    backoff = min(base_delay * (2**attempt), max_delay)
    return random.uniform(0, backoff)


class GoogleAIProvider:
    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["http_options"] = types.HttpOptions(base_url=base_url)
        self.client = genai.Client(**client_kwargs)
        self.model = model
        self._rate_limiter = _RateLimiter(_REQUESTS_PER_MINUTE)

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 200,
        response_format: dict | None = None,
    ) -> str:
        kwargs = {
            "model": self.model,
            "system_instruction": system_prompt,
            "input": user_prompt,
            "generation_config": {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
                "thinking_level": "minimal",
            },
        }
        if response_format is not None:
            kwargs["response_format"] = {
                "type": "text",
                "mime_type": "application/json",
                "schema": response_format,
            }

        interaction = self._create_with_retry(kwargs)
        return interaction.output_text or ""

    def _create_with_retry(self, kwargs: dict):
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            self._rate_limiter.wait()
            try:
                return self.client.interactions.create(**kwargs)
            except Exception as error:
                if not _is_retryable(error) or attempt == _MAX_RETRIES:
                    raise
                delay = _retry_delay_seconds(error, attempt, _RETRY_BASE_DELAY_SECONDS, _RETRY_MAX_DELAY_SECONDS)
                logger.warning(
                    "scoring call failed (%s), retrying in %.1fs [attempt %d/%d]",
                    _status_code_of(error) or type(error).__name__,
                    delay,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                last_error = error
                time.sleep(delay)
        # Unreachable: the loop above always returns or raises.
        raise last_error


class OpenAICompatibleProvider:
    def __init__(self, api_key: str, model: str, base_url: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 200,
        response_format: dict | None = None,
    ) -> str:
        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "score_response", "schema": response_format},
            }

        completion = self._create_with_retry(kwargs)
        return completion.choices[0].message.content or ""

    def _create_with_retry(self, kwargs: dict):
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as error:
                if not _is_retryable(error) or attempt == _MAX_RETRIES:
                    raise
                delay = _retry_delay_seconds(error, attempt, _RETRY_BASE_DELAY_SECONDS, _RETRY_MAX_DELAY_SECONDS)
                logger.warning(
                    "fallback scoring call failed (%s), retrying in %.1fs [attempt %d/%d]",
                    _status_code_of(error) or type(error).__name__,
                    delay,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                last_error = error
                time.sleep(delay)
        # Unreachable: the loop above always returns or raises.
        raise last_error


class FallbackProvider:
    """Tries `primary` first; only calls `secondary` once primary's own
    retries are exhausted (e.g. Gemini's daily/RPM quota)."""

    def __init__(self, primary: LLMProvider, secondary: LLMProvider):
        self.primary = primary
        self.secondary = secondary

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 200,
        response_format: dict | None = None,
    ) -> str:
        try:
            return self.primary.complete(
                system_prompt,
                user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
        except Exception as error:
            logger.warning("primary provider exhausted (%s), falling back", error)
            return self.secondary.complete(
                system_prompt,
                user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )


def get_provider(config: ScoringConfig) -> LLMProvider:
    if config.provider != "google":
        raise ValueError(f"unknown scoring provider '{config.provider}'")

    provider: LLMProvider = GoogleAIProvider(
        api_key=os.environ["GOOGLE_AI_API_KEY"],
        model=config.model,
        base_url=config.api_base,
    )
    if config.fallback_provider == "groq":
        fallback = OpenAICompatibleProvider(
            api_key=config.fallback_api_key,
            model=config.fallback_model,
            base_url=config.fallback_api_base,
        )
        return FallbackProvider(provider, fallback)
    return provider
