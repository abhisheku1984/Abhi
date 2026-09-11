"""LLM adapters for prompt enhancement, story writing and the command assistant.

The platform never requires an LLM: the Prompt Engine and Story Engine have
deterministic implementations that run offline. These adapters upgrade them
when a local or hosted model is available.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.adapters.base import (
    AdapterStatus,
    LLMAdapter,
    ProgressCallback,
)
from app.core.errors import ErrorCode


class OllamaLLMAdapter(LLMAdapter):
    key = "ollama"
    name = "Ollama (local LLM server)"
    capability = "llm"
    capabilities = ("llm", "prompt_enhance", "story")
    version = "latest"
    license = "Varies by model (e.g. Llama 3.1 Community, Mistral Apache-2.0)"
    source_url = "https://ollama.com"
    description = "Free local LLM server. Install Ollama and pull a model to enable AI writing features."
    quality = "high"
    ram_gb = 8.0
    local = True
    provider = "ollama"
    base_url = "http://127.0.0.1:11434"
    default_model = "llama3.1"

    def status(self, refresh: bool = False) -> AdapterStatus:
        st = super().status(refresh=refresh)
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=2.0)
            models = [m.get("name", "") for m in (r.json().get("models") or [])]
            if not models:
                return AdapterStatus(
                    self.key, self.name, False, ErrorCode.MODEL_NOT_AVAILABLE,
                    "Ollama server is running but has no models installed.",
                    f"ollama pull {self.default_model}", device="cpu", requires=[],
                    missing_requirements=[], hardware={},
                )
            st.available = True
            st.message = f"Available ({', '.join(models[:3])})"
        except Exception:
            return AdapterStatus(
                self.key, self.name, False, ErrorCode.EXTERNAL_PROVIDER_REQUIRED,
                "Ollama server is not reachable at http://127.0.0.1:11434.",
                "Install Ollama from https://ollama.com, run `ollama serve`, then "
                f"`ollama pull {self.default_model}`. No API key or cost.",
                device="cpu", requires=[], missing_requirements=[], hardware={},
            )
        return st

    def generate(self, req, progress: ProgressCallback):  # type: ignore[override]
        text = self.complete("You are a helpful assistant.", req.prompt, temperature=0.7)
        from app.adapters.base import GenerationResult

        return GenerationResult(files=[], meta={"text": text}, model_id=self.key)

    def complete(self, system: str, user: str, max_tokens: int = 1200, temperature: float = 0.7) -> str:
        self.ensure_ready()
        r = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.default_model,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=300,
        )
        r.raise_for_status()
        return (r.json().get("message") or {}).get("content", "")


class OpenAILLMAdapter(LLMAdapter):
    key = "openai_llm"
    name = "OpenAI GPT (API)"
    capability = "llm"
    capabilities = ("llm", "prompt_enhance", "story")
    provider = "openai"
    local = False
    quality = "reference"
    description = "Hosted LLM for prompt enhancement and story writing. Requires an API key (paid)."

    def generate(self, req, progress: ProgressCallback):  # type: ignore[override]
        text = self.complete("You are a helpful assistant.", req.prompt)
        from app.adapters.base import GenerationResult

        return GenerationResult(files=[], meta={"text": text}, model_id=self.key)

    def complete(self, system: str, user: str, max_tokens: int = 1200, temperature: float = 0.7) -> str:
        self.ensure_ready()
        r = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key()}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=180,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def best_llm() -> LLMAdapter | None:
    """Return the first available LLM adapter, or None (offline mode)."""
    from app.adapters.registry import registry

    for adapter in registry.by_capability("llm"):
        if isinstance(adapter, LLMAdapter) and adapter.status().available:
            return adapter
    return None


def llm_json(prompt: str, system: str = "You output strict JSON only.") -> dict[str, Any] | None:
    """Ask an available LLM for JSON; return None when offline or on parse failure."""
    llm = best_llm()
    if not llm:
        return None
    try:
        raw = llm.complete(system, prompt, max_tokens=2000, temperature=0.8)
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
    except Exception:
        return None
    return None
