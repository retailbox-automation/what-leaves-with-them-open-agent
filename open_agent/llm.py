"""The model behind both agents: any OpenAI-compatible endpoint, chosen by environment only.

  OA_LLM_BASE_URL       e.g. http://localhost:11434/v1 (Ollama) or http://127.0.0.1:11435/v1 (Meterless Nightshift)
  OA_LLM_MODEL          e.g. nemotron-3-nano:30b-a3b-q4_K_M; unset means no model (the agents use their
                        deterministic paths)
  OA_LLM_API_KEY        or OA_LLM_API_KEY_FILE (a file holding the key); default "local"
  OA_LLM_TIMEOUT        seconds per call, default 120
  OA_LLM_REASONING      optional reasoning_effort to send (e.g. "none")

Every call goes into the trace with the endpoint, the model asked for and the model that answered, latency,
token usage, the response headers and any fields outside the OpenAI schema (where a provider puts its receipt).
The API key is never written anywhere.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

DEFAULT_BASE_URL = "http://localhost:11434/v1"
_SECRET_HEADER = re.compile(r"authorization|cookie|api[-_]?key|token|secret", re.I)
_RECEIPT_HINT = re.compile(r"receipt|meterless|nightshift|routed", re.I)


@dataclass
class LLMConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str | None = None
    api_key: str = "local"
    timeout: float = 120.0
    reasoning: str | None = None

    @classmethod
    def from_env(cls) -> "LLMConfig":
        key = os.environ.get("OA_LLM_API_KEY")
        if not key and os.environ.get("OA_LLM_API_KEY_FILE"):
            key = Path(os.environ["OA_LLM_API_KEY_FILE"]).expanduser().read_text(encoding="utf-8").strip()
        return cls(base_url=os.environ.get("OA_LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
                   model=os.environ.get("OA_LLM_MODEL") or None, api_key=key or "local",
                   timeout=float(os.environ.get("OA_LLM_TIMEOUT", "120")),
                   reasoning=os.environ.get("OA_LLM_REASONING") or None)


def provider_receipt(headers: dict, extra: dict):
    """The provider's receipt, wherever it is: a body field or a header whose name says so. None when absent."""
    for k, v in (extra or {}).items():
        if _RECEIPT_HINT.search(k):
            return v
    for k, v in (headers or {}).items():
        if _RECEIPT_HINT.search(k):
            return v
    return None


class TracedClient:
    """Looks like OpenAI().chat.completions to honest_memory.agent.Agent, and records each call in the trace."""

    def __init__(self, cfg: LLMConfig, trace, agent: str, client=None):
        self.cfg, self.trace, self.agent = cfg, trace, agent
        if client is None:
            from openai import OpenAI

            client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=cfg.timeout, max_retries=0)
        self._client = client
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.purpose = "tool_loop"   # what the next call is for, shown in the trace

    def create(self, **kwargs):
        import openai

        if self.cfg.reasoning and "reasoning_effort" not in kwargs:
            kwargs["reasoning_effort"] = self.cfg.reasoning
        t0 = time.perf_counter()
        common = {"endpoint": self.cfg.base_url, "model": kwargs.get("model"), "purpose": self.purpose,
                  "messages": len(kwargs.get("messages") or []), "tools_offered": len(kwargs.get("tools") or [])}
        try:
            raw = self._client.chat.completions.with_raw_response.create(**kwargs)
            completion = raw.parse()
        except openai.OpenAIError as exc:
            self.trace.event(self.agent, "llm_call", status="error", seconds=round(time.perf_counter() - t0, 3),
                             error=f"{type(exc).__name__}: {str(exc)[:200]}", **common)
            raise
        headers = {k: v for k, v in raw.headers.items() if not _SECRET_HEADER.search(k)}
        extra = dict(completion.model_extra or {})
        msg = completion.choices[0].message if completion.choices else None
        usage = completion.usage.model_dump() if completion.usage else None
        self.trace.event(self.agent, "llm_call", seconds=round(time.perf_counter() - t0, 3), **common,
                         response_model=completion.model, response_id=completion.id, usage=usage,
                         tool_calls=[c.function.name for c in (msg.tool_calls or [])] if msg else [],
                         provider_receipt=provider_receipt(headers, extra), response_extra=extra,
                         response_headers=headers)
        return completion

    def ask_json(self, purpose: str, system: str, user: dict) -> tuple[dict | None, str]:
        """One call that must answer with a JSON object. Returns (object, "") or (None, why)."""
        import openai

        self.purpose = purpose
        try:
            completion = self.create(model=self.cfg.model, temperature=0,
                                     messages=[{"role": "system", "content": system},
                                               {"role": "user", "content": json.dumps(user, ensure_ascii=False,
                                                                                      default=str)}])
        except openai.OpenAIError as exc:
            return None, f"model unavailable ({type(exc).__name__})"
        finally:
            self.purpose = "tool_loop"
        content = completion.choices[0].message.content if completion.choices else ""
        obj = parse_json_object(content or "")
        return (obj, "") if obj is not None else (None, f"model reply was not a JSON object: {(content or '')[:120]!r}")


def parse_json_object(text: str) -> dict | None:
    """The first JSON object in a reply, after any <think> block or code fence."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
    return None
