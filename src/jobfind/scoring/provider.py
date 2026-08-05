import os
from typing import Protocol

from google import genai
from google.genai import types

from ..config import ScoringConfig


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


class GoogleAIProvider:
    """Chat completions against Google AI Studio's Gemini API, via the
    google-genai SDK's Interactions API (`client.interactions.create()`).
    `response_format` is plain JSON Schema (e.g.
    `ScoreResponse.model_json_schema()`) — the Interactions API accepts it
    directly, no translation needed.

    NOTE: the free tier's terms let Google use prompts/outputs sent here —
    the full candidate profile and every job description — to improve their
    products. See README.md's "Gemini free-tier data sharing" section.
    """

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["http_options"] = types.HttpOptions(base_url=base_url)
        self.client = genai.Client(**client_kwargs)
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
            "system_instruction": system_prompt,
            "input": user_prompt,
            "generation_config": {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
                # Thinking tokens count against max_output_tokens — the same
                # failure mode we hit with a reasoning model on another
                # provider (budget spent on hidden reasoning, empty response
                # left over). This is a single-step scoring call, not a task
                # that benefits from extended reasoning, so keep it minimal.
                "thinking_level": "minimal",
            },
        }
        if response_format is not None:
            kwargs["response_format"] = {
                "type": "text",
                "mime_type": "application/json",
                "schema": response_format,
            }

        interaction = self.client.interactions.create(**kwargs)
        # Empty if the interaction didn't complete normally (blocked, failed,
        # cut off before any output) so callers fail closed instead of
        # crashing on a missing/None value.
        return interaction.output_text or ""


def get_provider(config: ScoringConfig) -> LLMProvider:
    if config.provider == "google":
        return GoogleAIProvider(
            api_key=os.environ["GOOGLE_AI_API_KEY"],
            model=config.model,
            base_url=config.api_base,
        )
    raise ValueError(f"unknown scoring provider '{config.provider}'")
