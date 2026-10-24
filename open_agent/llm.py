"""The model behind both agents. Meterless Nightshift is the primary provider; the choice is environment only.

  OA_LLM_PROVIDER       nightshift (default) | ollama | none
                        nightshift: each agent calls its own NVIDIA Nemotron model through Nightshift on this Mac
                          (open_agent/nightshift.py: Investigator -> app wlwt-investigator, lane nemotron-lightning;
                          Verifier -> app wlwt-verifier, lane nemotron-nano), with a signed receipt per call. If
                          Nightshift is down or answers with another model, the run fails and says so: there is no
                          fallback to another provider.
                        ollama: an explicit development override, any OpenAI-compatible endpoint (OA_LLM_BASE_URL,
                          OA_LLM_MODEL required, OA_VERIFIER_MODEL optional). Never chosen on its own.
                        none: no model; the agents use their deterministic paths (the result says so).
  OA_LLM_TIMEOUT        seconds per call (default 180 for Nightshift, 120 for ollama)
  OA_LLM_MAX_TOKENS     Nightshift: tokens per call including the model's thinking (default 6144)
  OA_LLM_BASE_URL, OA_LLM_MODEL, OA_LLM_API_KEY or OA_LLM_API_KEY_FILE, OA_LLM_REASONING, OA_VERIFIER_MODEL
                        ollama only (OA_LLM_MODEL with the default provider is refused, so a leftover setting cannot
                        silently pick the provider)

Every call goes into the trace. A Nightshift call carries the receipt: receipt_id, requested_model, routed_model,
the app and its lane, source_policy, cost_usd, from_cache, latency and tokens. An ollama call carries the endpoint,
the model asked for and the model that answered, latency, tokens, the response headers and any fields outside the
OpenAI schema. No key or token is ever written anywhere.
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


PROVIDERS = ("nightshift", "ollama", "none")


def MAX_TOKENS() -> int:
    """Token budget per Nightshift call (thinking included): OA_LLM_MAX_TOKENS, default 6144."""
    return int(os.environ.get("OA_LLM_MAX_TOKENS", "6144"))


class ProviderDown(RuntimeError):
    """The primary provider (Nightshift) did not answer. Not an OpenAIError on purpose: no agent turns it into a
    fallback, the run stops with this message."""


class ProviderConfigError(ValueError):
    """The provider settings contradict each other (e.g. OA_LLM_MODEL with the default provider)."""


@dataclass
class LLMConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str | None = None
    api_key: str = "local"
    timeout: float = 120.0
    reasoning: str | None = None
    provider: str = "ollama"         # "nightshift" or "ollama" (any OpenAI-compatible endpoint)
    scope: str | None = None         # Nightshift meterless-scope: one per run, shared by both agents

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """The OpenAI-compatible endpoint settings (OA_LLM_PROVIDER=ollama)."""
        key = os.environ.get("OA_LLM_API_KEY")
        if not key and os.environ.get("OA_LLM_API_KEY_FILE"):
            key = Path(os.environ["OA_LLM_API_KEY_FILE"]).expanduser().read_text(encoding="utf-8").strip()
        return cls(base_url=os.environ.get("OA_LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
                   model=os.environ.get("OA_LLM_MODEL") or None, api_key=key or "local",
                   timeout=float(os.environ.get("OA_LLM_TIMEOUT", "120")),
                   reasoning=os.environ.get("OA_LLM_REASONING") or None)


def provider_name() -> str:
    name = (os.environ.get("OA_LLM_PROVIDER") or "nightshift").strip().lower()
    if name == "openai":
        name = "ollama"
    if name not in PROVIDERS:
        raise ProviderConfigError(f"OA_LLM_PROVIDER={name!r}; known: {', '.join(PROVIDERS)}")
    return name


def resolve(run_id: str) -> LLMConfig | None:
    """The run's model settings from the environment; None for OA_LLM_PROVIDER=none. Raises ProviderConfigError."""
    name = provider_name()
    if name == "none":
        return None
    if name == "ollama":
        cfg = LLMConfig.from_env()
        if not cfg.model:
            raise ProviderConfigError("OA_LLM_PROVIDER=ollama needs OA_LLM_MODEL (and OA_LLM_BASE_URL unless it is "
                                      f"{DEFAULT_BASE_URL})")
        return cfg
    from . import nightshift as N

    if os.environ.get("OA_LLM_MODEL") or os.environ.get("OA_VERIFIER_MODEL"):
        raise ProviderConfigError("OA_LLM_MODEL / OA_VERIFIER_MODEL apply to OA_LLM_PROVIDER=ollama only; with "
                                  "Nightshift (the default) each agent's Nemotron model comes from its lane "
                                  "(NIGHTSHIFT_<AGENT>_LANE). Unset them, or set OA_LLM_PROVIDER=ollama.")
    return LLMConfig(base_url=N.base_url(), model=N.route_for("investigator").model, api_key="",
                     timeout=float(os.environ.get("OA_LLM_TIMEOUT", N.TIMEOUT_S)),
                     reasoning=os.environ.get("OA_LLM_REASONING") or None, provider="nightshift",
                     scope=f"wlwt-{run_id}")


def for_agent(cfg: LLMConfig | None, agent: str) -> LLMConfig | None:
    """The settings one agent uses: on Nightshift, its own Nemotron model; on ollama, OA_VERIFIER_MODEL may give the
    Verifier another model at the same endpoint."""
    from dataclasses import replace

    if cfg is None:
        return None
    if cfg.provider == "nightshift":
        from . import nightshift as N

        return replace(cfg, model=N.route_for(agent.lower()).model)
    if agent.lower() == "verifier" and os.environ.get("OA_VERIFIER_MODEL"):
        return replace(cfg, model=os.environ["OA_VERIFIER_MODEL"])
    return cfg


def preflight(cfg: LLMConfig | None) -> dict | None:
    """Before a live run on Nightshift: the token is readable and the engine answers /healthz. Raises ProviderDown."""
    if cfg is None or cfg.provider != "nightshift":
        return None
    import urllib.error
    import urllib.request

    from . import nightshift as N

    root = re.sub(r"/v1$", "", cfg.base_url)
    try:
        N._token()
        with urllib.request.urlopen(root + "/healthz", timeout=5) as resp:
            status = resp.status
    except (N.NightshiftUnavailable, urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise ProviderDown(f"Meterless Nightshift is not answering at {cfg.base_url} ({exc}). Start it with "
                           "scripts/nightshift_headless.sh start, or run --sample (no model). Another provider is "
                           "used only when asked for: OA_LLM_PROVIDER=ollama.") from exc
    routes = {a: N.route_for(a) for a in ("investigator", "verifier")}
    return {"healthz": status, "routes": {a: {"app": r.app, "lane": r.lane, "model": r.model} for a, r in routes.items()}}


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


class NightshiftClient(TracedClient):
    """The same face as TracedClient, sending each call through Meterless Nightshift for one agent.

    The agent's route (app id, lane, Nemotron model) comes from open_agent.nightshift.route_for. Each call's receipt
    is written into the trace and kept in `receipts` (for the per-run receipt export). Nightshift down raises
    ProviderDown and a reply under another model or pin raises nightshift.RoutingMismatch: neither is an OpenAIError,
    so neither agent turns it into a fallback."""

    def __init__(self, cfg: LLMConfig, trace, agent: str):
        from . import nightshift as N

        self.N = N
        self.route = N.route_for(agent.lower())
        self.receipts: list = []
        super().__init__(cfg, trace, agent, client=object())

    @property
    def receipt_ids(self) -> list[str]:
        return [r.receipt_id for r in self.receipts if r.receipt_id]

    def create(self, **kwargs):
        N, route = self.N, self.route
        t0 = time.perf_counter()
        common = {"provider": "nightshift", "endpoint": self.cfg.base_url, "app": route.app, "lane": route.lane,
                  "model": kwargs.get("model"), "purpose": self.purpose,
                  "messages": len(kwargs.get("messages") or []), "tools_offered": len(kwargs.get("tools") or [])}

        def failed(exc: Exception) -> None:
            self.trace.event(self.agent, "llm_call", status="error", seconds=round(time.perf_counter() - t0, 3),
                             error=f"{type(exc).__name__}: {str(exc)[:300]}", **common)

        if kwargs.get("model") != route.model:
            exc = N.RoutingMismatch(f"{self.agent} asked for {kwargs.get('model')!r}; its Nightshift model is "
                                    f"{route.model!r} (lane {route.lane})")
            failed(exc)
            raise exc
        extra = {k: kwargs[k] for k in ("max_tokens", "response_format", "stop", "seed") if kwargs.get(k)}
        # The Nemotron lanes think before they answer and Nightshift cannot switch that off from outside; its local
        # default of 2,048 tokens ran out inside the thinking on the cite prompt (an empty reply). 16,384-token slots.
        extra.setdefault("max_tokens", MAX_TOKENS())
        effort = kwargs.get("reasoning_effort") or self.cfg.reasoning
        if effort:
            extra["reasoning_effort"] = effort
        try:
            reply = N.chat(kwargs.get("messages") or [], agent=route.agent, scope=self.cfg.scope or N.new_scope(),
                           tools=kwargs.get("tools"), tool_choice=kwargs.get("tool_choice"),
                           temperature=kwargs.get("temperature", 0), timeout=self.cfg.timeout, extra=extra)
        except N.NightshiftUnavailable as exc:
            if "empty_response" in str(exc):   # Nightshift answered: the model produced no text. Not "down".
                self.trace.event(self.agent, "llm_call", status="warning", seconds=round(time.perf_counter() - t0, 3),
                                 error=f"empty reply: {str(exc)[:300]}", **common)
                return SimpleNamespace(choices=[SimpleNamespace(message=N._ns({"role": "assistant", "content": "",
                                                                                 "tool_calls": None}),
                                                                finish_reason="empty")], model=None, id=None)
            failed(exc)
            raise ProviderDown(f"Meterless Nightshift did not answer the {self.agent} (app {route.app}, lane "
                               f"{route.lane}, model {route.model}) at {self.cfg.base_url}: {exc}. Start it or the "
                               "lane with scripts/nightshift_headless.sh, or run --sample (no model). Another "
                               "provider is used only when asked for: OA_LLM_PROVIDER=ollama.") from exc
        except N.RoutingMismatch as exc:
            failed(exc)
            raise
        r = reply.receipt
        self.receipts.append(r)
        msg = dict(reply.message)
        usage = r.usage or {}
        self.trace.event(self.agent, "llm_call", seconds=round(time.perf_counter() - t0, 3), **common,
                         receipt_id=r.receipt_id, decision_id=r.decision_id, requested_model=r.requested_model,
                         routed_model=r.routed_model, response_model=r.routed_model, lanes=r.lanes,
                         source_policy=r.source_policy, cost_usd=r.cost_usd, naive_usd=r.naive_usd,
                         from_cache=r.from_cache, cache_status=r.cache_status, privacy=r.privacy, signed=r.signed,
                         latency_ms=r.latency_ms, engine_ms=usage.get("latency_ms"),
                         usage={"prompt_tokens": usage.get("input_tokens"),
                                "completion_tokens": usage.get("output_tokens"),
                                "total_tokens": usage.get("total_tokens")},
                         finish_reason=reply.finish_reason,
                         tool_calls=[(c.get("function") or {}).get("name") for c in (msg.get("tool_calls") or [])],
                         provider_receipt=r.receipt_id)
        msg.setdefault("content", None)
        msg["tool_calls"] = msg.get("tool_calls") or None
        return SimpleNamespace(choices=[SimpleNamespace(message=N._ns(msg), finish_reason=reply.finish_reason)],
                               model=r.routed_model, id=r.receipt_id)


def make_client(cfg: LLMConfig | None, trace, agent: str, client=None) -> TracedClient | None:
    """The traced client for one agent, or None when there is no model."""
    if not cfg or not cfg.model:
        return None
    if cfg.provider == "nightshift":
        return NightshiftClient(cfg, trace, agent)
    return TracedClient(cfg, trace, agent, client)


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
