import os
from typing import Protocol

import requests

from ..config import ScoringConfig


class LLMProvider(Protocol):
    def complete(self, system_prompt: str, user_prompt: str, *, temperature: float, max_tokens: int) -> str: ...


class OpenRouterProvider:
    """OpenAI-compatible chat completions against OpenRouter.

    This is the only place in the codebase that knows about OpenRouter. Swapping
    providers later means adding a new class here (implementing the same
    `complete()` signature) and adding one branch to get_provider() below.
    """

    def __init__(self, api_key: str, model: str, base_url: str = "https://openrouter.ai/api/v1"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def complete(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.0, max_tokens: int = 200) -> str:
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        resp.raise_for_status()
        # Some free-tier reasoning models return content: null (e.g. they spent
        # max_tokens on hidden reasoning and never emitted a final answer) without
        # the request itself failing — coerce to "" so this always honors its str
        # return type and downstream parsing can fail closed instead of crashing.
        return resp.json()["choices"][0]["message"].get("content") or ""


def get_provider(config: ScoringConfig) -> LLMProvider:
    if config.provider == "openrouter":
        return OpenRouterProvider(
            api_key=os.environ["OPENROUTER_API_KEY"],
            model=config.model,
            base_url=config.api_base,
        )
    raise ValueError(f"unknown scoring provider '{config.provider}'")
