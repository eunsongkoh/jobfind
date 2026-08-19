import logging
import os
import time
from collections import deque
from typing import Protocol

import httpx
from google import genai
from google.genai import types
from openai import OpenAI

from ..config import ScoringConfig
from ..retry import retry_call, status_code_of

logger = logging.getLogger(__name__)

# Retryable: rate limiting and transient server errors
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_MAX_RETRIES = 5
_RETRY_BASE_DELAY_SECONDS = 2.0
_RETRY_MAX_DELAY_SECONDS = 60.0
_REQUESTS_PER_MINUTE = 14

# Groq limits for the currently-configured fallback model (openai/gpt-oss-120b):
# 30 RPM / 8,000 TPM. TPM is the binding constraint given gpt-oss's heavy internal
# reasoning-token overhead — adjust if the fallback model changes.
_GROQ_REQUESTS_PER_MINUTE = 30
_GROQ_TOKENS_PER_MINUTE = 8000
# ~2,600 observed in production (real profile + job description prompts, not a
# trivial test prompt) — used only until real usage samples take over via the
# moving average in _TokenRateLimiter.
_GROQ_INITIAL_TOKEN_ESTIMATE = 2600


def _is_retryable(error: Exception) -> bool:
    if isinstance(error, httpx.TransportError):
        return True
    return status_code_of(error) in _RETRYABLE_STATUS_CODES


def _is_gemini_daily_quota_exhausted(error: Exception) -> bool:
    # Google's free-tier request-count quota (as opposed to a transient
    # per-minute rate limit) — e.g. "Quota exceeded for metric:
    # generativelanguage.googleapis.com/generate_content_free_tier_requests".
    return "free_tier_requests" in str(error).lower()


def _is_groq_daily_quota_exhausted(error: Exception) -> bool:
    # Groq's tokens/requests-per-day quota, e.g. "Rate limit reached ... on
    # tokens per day (TPD): Limit 200000, Used 199179 ... retry in 10m19s" —
    # far longer than our retry backoff can bridge.
    message = str(error).lower()
    return "tokens per day (tpd)" in message or "requests per day (rpd)" in message


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


class _TokenRateLimiter:
    """Blocks until issuing another request keeps the caller under
    `requests_per_minute` AND `tokens_per_minute` in any trailing 60s window.
    Uses an exponential moving average of each call's *actual* token usage
    (read back from the response) to estimate the next call's cost — this
    tracks real throughput closely instead of assuming worst-case max_tokens
    every time, which would throttle far more aggressively than necessary."""

    def __init__(self, requests_per_minute: int, tokens_per_minute: int, initial_estimate: int):
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        self._avg_tokens = initial_estimate
        self._events: deque[tuple[float, int]] = deque()

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0][0] >= 60:
            self._events.popleft()

    def wait(self) -> None:
        now = time.monotonic()
        self._prune(now)
        while self._events and (
            len(self._events) >= self.requests_per_minute
            or sum(tokens for _, tokens in self._events) + self._avg_tokens > self.tokens_per_minute
        ):
            sleep_for = 60 - (now - self._events[0][0])
            if sleep_for > 0:
                logger.info("groq rate limit throttle: sleeping %.1fs", sleep_for)
                time.sleep(sleep_for)
            now = time.monotonic()
            self._prune(now)

    def record(self, tokens: int | None) -> None:
        used = tokens if tokens is not None else self._avg_tokens
        self._events.append((time.monotonic(), used))
        if tokens is not None:
            self._avg_tokens = int(0.3 * tokens + 0.7 * self._avg_tokens)


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
        def _call():
            self._rate_limiter.wait()
            return self.client.interactions.create(**kwargs)

        return retry_call(
            _call,
            is_retryable=_is_retryable,
            is_daily_quota_exhausted=_is_gemini_daily_quota_exhausted,
            label="scoring call",
            max_retries=_MAX_RETRIES,
            base_delay=_RETRY_BASE_DELAY_SECONDS,
            max_delay=_RETRY_MAX_DELAY_SECONDS,
        )


class OpenAICompatibleProvider:
    def __init__(self, api_key: str, model: str, base_url: str):
        # max_retries=0: the openai SDK otherwise retries 429s internally before we
        # ever see them, which bypasses our rate limiter's pacing entirely.
        self.client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        self.model = model
        self._rate_limiter = _TokenRateLimiter(
            _GROQ_REQUESTS_PER_MINUTE, _GROQ_TOKENS_PER_MINUTE, _GROQ_INITIAL_TOKEN_ESTIMATE
        )

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
        def _call():
            self._rate_limiter.wait()
            completion = self.client.chat.completions.create(**kwargs)
            usage = getattr(completion, "usage", None)
            self._rate_limiter.record(getattr(usage, "total_tokens", None) if usage else None)
            return completion

        return retry_call(
            _call,
            is_retryable=_is_retryable,
            is_daily_quota_exhausted=_is_groq_daily_quota_exhausted,
            label="fallback scoring call",
            max_retries=_MAX_RETRIES,
            base_delay=_RETRY_BASE_DELAY_SECONDS,
            max_delay=_RETRY_MAX_DELAY_SECONDS,
        )


class FallbackProvider:
    """Tries `primary` first; only calls `secondary` once primary's own
    retries are exhausted (e.g. Gemini's daily/RPM quota). Once primary fails
    once, it's assumed exhausted for the rest of this provider's lifetime
    (one pipeline run) — a daily quota won't clear mid-run, so retrying
    primary on every subsequent call would just burn time for no benefit."""

    def __init__(self, primary: LLMProvider, secondary: LLMProvider):
        self.primary = primary
        self.secondary = secondary
        self._primary_exhausted = False

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 200,
        response_format: dict | None = None,
    ) -> str:
        if not self._primary_exhausted:
            try:
                return self.primary.complete(
                    system_prompt,
                    user_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                )
            except Exception as error:
                logger.warning("primary provider exhausted (%s), falling back for remainder of run", error)
                self._primary_exhausted = True

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
