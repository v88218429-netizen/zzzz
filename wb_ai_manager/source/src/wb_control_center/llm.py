from __future__ import annotations

import httpx

from .config import Settings


class LLMClient:
    """Optional reasoning layer.

    rules/none: no model at all; agents still make deterministic decisions.
    ollama: local model over localhost, no cloud API key.
    anthropic/openai: optional cloud upgrade.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def provider(self) -> str:
        return self.settings.llm_provider.lower().strip() or "rules"

    @property
    def enabled(self) -> bool:
        return self.provider in {"anthropic", "openai", "ollama"}

    @property
    def label(self) -> str:
        if self.provider in {"rules", "none"}:
            return "rules-only (без LLM/API)"
        if self.provider == "ollama":
            return f"ollama local ({self.settings.ollama_model})"
        return self.provider

    async def complete(self, system: str, user: str, max_tokens: int = 1000) -> str:
        provider = self.provider
        if provider in {"none", "rules"}:
            return ""
        if provider == "ollama":
            url = self.settings.ollama_base_url.rstrip("/") + "/api/chat"
            payload = {
                "model": self.settings.ollama_model or self.settings.llm_model or "qwen3:4b-instruct",
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "options": {"num_predict": max_tokens, "temperature": 0.1},
            }
            async with httpx.AsyncClient(timeout=180) as client:
                r = await client.post(url, json=payload)
                r.raise_for_status()
                data = r.json()
            return str(((data or {}).get("message") or {}).get("content") or "").strip()
        if provider == "anthropic":
            if not self.settings.anthropic_api_key:
                return ""
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:
                raise RuntimeError("Install cloud extras: pip install -e '.[cloud-llm]'") from exc
            client = AsyncAnthropic(api_key=self.settings.anthropic_api_key)
            model = self.settings.llm_model or "claude-sonnet-4-5"
            msg = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return "\n".join(getattr(x, "text", "") for x in msg.content if getattr(x, "text", ""))
        if provider == "openai":
            if not self.settings.openai_api_key:
                return ""
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise RuntimeError("Install cloud extras: pip install -e '.[cloud-llm]'") from exc
            client = AsyncOpenAI(api_key=self.settings.openai_api_key)
            model = self.settings.llm_model or "gpt-5"
            resp = await client.responses.create(
                model=model,
                instructions=system,
                input=user,
                max_output_tokens=max_tokens,
            )
            return getattr(resp, "output_text", "") or ""
        return ""
