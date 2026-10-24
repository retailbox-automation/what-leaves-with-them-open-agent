"""An agent over the tool catalogue: the model picks tools and fills arguments, then writes one sentence.

The model sits behind any OpenAI-compatible chat-completions endpoint (a local Ollama by default).
It never writes Cypher and it never decides what is stored: tools resolve names against the graph,
writes go through the gate, and the sentence it writes is checked against the rows before anyone
reads it. When the check fails, or no model is configured, a sentence built from the rows is used.

Env: HM_LLM_BASE_URL (default http://localhost:11434/v1), HM_LLM_MODEL, HM_LLM_API_KEY (default "ollama").
HM_LLM_PROVIDER=nightshift sends the calls to Meterless Nightshift instead (open_agent/nightshift.py: the agent's
pinned Nemotron model, a per-request token, a receipt per call); HM_NIGHTSHIFT_AGENT picks the route (investigator).
Before the model answers, findings kept in the H-MEM engine that match the question are put in front of it
(open_agent/hmem_bridge.memory_context); HM_MEMORY_CONTEXT=off turns that off.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field

from . import answer as A
from . import tools as T
from .answer import Answer, check_sentence, template_sentence
from .router import route
from .store import Store

DEFAULT_BASE_URL = "http://localhost:11434/v1"
# Chosen on five corpus questions against the ingested corpus (call the right tool, sentence passes the check):
# gemma4 26b 5/5 and 5/5, warm 2.1 s per question over 15 runs, all correct; qwen3.6 35b 5/5, 3.6 s warm;
# qwen3.8 27b 5/5, 65 s cold; nemotron-3-nano 4/5, 158 s. Re-run: python -m honest_memory.agent compare.
DEFAULT_MODEL = "gemma4:26b-a4b-it-qat"
MAX_CALLS = 4

SYSTEM_PROMPT = """You are {name}, a {role} agent answering from a company memory graph. You know nothing about \
the company except what the tools return.
- Pick a tool and fill its arguments with the exact names the user wrote. Never guess or correct a name.
- Call at most {max_calls} tools in total. Use the fewest that answer the question.
- If a tool returns unknown_entity or not_in_graph, say exactly that. Do not substitute another name and do not \
claim an effect that the rows do not show.
- When you have the rows, reply with ONE plain sentence that uses only names, dates and record ids that appear in \
the tool results. Never invent systems, people, ids or dates.
- If a row marks a fact as ended (ended, until, last day, closed), say it ended and when. Never say that someone \
owns, leads or belongs to something that has ended.
- When the rows list items, name every item they list, with its owner when the row gives one. Do not shorten the \
list.
- Name only the relations the rows show: if a row says a decision governs one system, do not attach other systems \
to it.
- Write to the memory (remember, handoff) only when the user asks you to.
When someone leaves (or goes on leave, or moves team), these tools answer what goes with them:
- what_leaves: what the person owns, leads and belongs to (depth 1), and at depth 2-4 the decisions they made that \
govern systems other people own; ask for depth 1 and depth 4 to show the difference.
- single_source: decisions and systems whose every source record this one person wrote.
- without: what nobody would be connected to without this person.
- bridges: who links teams; the only per-person score, about paths, not worth.
- successors: who worked on each orphaned item or next to it; say "candidates", never "the successor".
- handoff_plan: one list with an action per line; set propose=true only when the user asks to write the handoffs.
- promises: promises in the written records since a date; phone calls are not recorded, so none found means \
not in the graph."""

# How findings recalled from H-MEM are put in front of the model (the [MEMORY CONTEXT] block follows).
MEMORY_PREFIX = ("Earlier findings kept in memory (they may be out of date; confirm with a tool before you rely on "
                 "one):\n")

NO_TOOL = ("I have no tool for that question. I can answer who owns a system now or on a date, its ownership history, "
           "whom to call, what depends on it, who worked on it, how a decision evolved, why the memory holds or "
           "refused a fact, what changed since a date, how two things connect, what agents remembered, and what "
           "leaves with a person who goes: what they hold, what rests only on them, who links teams, who could take "
           "over, a handoff plan, and what they promised in writing.")


class LLMUnavailable(RuntimeError):
    """The configured endpoint could not be reached or did not answer in time."""


@dataclass
class AgentConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str | None = None
    api_key: str = "ollama"
    max_calls: int = MAX_CALLS
    timeout: float = 180.0
    reasoning_effort: str | None = None     # e.g. "none" turns thinking off on models that think
    provider: str = "openai"                # "nightshift": open_agent.nightshift, receipts and pinned Nemotron
    nightshift_agent: str = "investigator"

    @classmethod
    def from_env(cls, default_model: str | None = None) -> "AgentConfig":
        provider = os.environ.get("HM_LLM_PROVIDER", "openai").strip().lower()
        ns_agent = os.environ.get("HM_NIGHTSHIFT_AGENT", "investigator")
        if provider == "nightshift" and not (os.environ.get("HM_LLM_MODEL") or default_model):
            from open_agent.nightshift import route_for

            default_model = route_for(ns_agent).model
        return cls(base_url=os.environ.get("HM_LLM_BASE_URL", DEFAULT_BASE_URL),
                   model=os.environ.get("HM_LLM_MODEL") or default_model,
                   api_key=os.environ.get("HM_LLM_API_KEY", "ollama"),
                   reasoning_effort=os.environ.get("HM_LLM_REASONING") or None,
                   provider=provider, nightshift_agent=ns_agent)


@dataclass
class AgentRun:
    question: str
    agent: str
    session: str
    mode: str                                   # "llm" or "router"
    answers: list[Answer] = field(default_factory=list)
    sentence: str = ""
    sentence_source: str = "template"           # "model" (passed the check) or "template" (built from the rows)
    check: str = "ok"                           # why the model's sentence was rejected, else "ok"
    rejected: str = ""                          # the model's sentence when it was rejected
    rule: str = ""                              # router rule that fired
    errors: list[str] = field(default_factory=list)
    ms: float = 0.0
    memory_ids: list[str] = field(default_factory=list)   # H-MEM memories put in front of the model

    @property
    def primary(self) -> Answer | None:
        useful = [a for a in self.answers if a.outcome not in (A.UNKNOWN_ENTITY, A.NOT_IN_GRAPH)]
        return (useful or self.answers or [None])[-1]

    @property
    def outcome(self) -> str:
        return self.primary.outcome if self.primary else "no_tool"

    @property
    def trace(self) -> list[A.ToolTrace]:
        return [t for a in self.answers for t in a.trace]

    def to_dict(self) -> dict:
        return {"question": self.question, "agent": self.agent, "session": self.session, "mode": self.mode,
                "outcome": self.outcome, "sentence": self.sentence, "sentence_source": self.sentence_source,
                "check": self.check, "rejected": self.rejected, "rule": self.rule, "errors": self.errors,
                "ms": self.ms, "memory_ids": self.memory_ids, "answers": [a.to_dict() for a in self.answers]}


def combined(answers: list[Answer]) -> Answer:
    """One view over several tool calls, for checking a sentence that draws on all of them."""
    out = Answer(answers[-1].outcome, ",".join(a.tool for a in answers), {"calls": [a.args for a in answers]})
    for a in answers:
        out.rows += a.rows
        out.paths += a.paths
        out.note = (out.note + " " + a.note).strip()
        out.sources += [s for s in a.sources if s.id not in {x.id for x in out.sources}]
        out.receipts += [r for r in a.receipts if r.id not in {x.id for x in out.receipts}]
    return out


def model_view(ans: Answer) -> str:
    """The tool result as the model sees it: rows, paths as text, sources; long id lists shortened."""
    def short(v):
        if isinstance(v, list) and len(v) > 4 and all(isinstance(x, str) for x in v):
            return v[:4] + [f"... +{len(v) - 4} more"]
        return v

    view = {"outcome": ans.outcome, "rows": [{k: short(v) for k, v in row.items()} for row in ans.rows[:15]],
            "paths": [A.render_path(p) for p in ans.paths[:8]],
            "sources": [{"id": s.id, "title": s.title, "date": s.ts[:10]} for s in ans.sources[:8]],
            "receipts": [{"id": r.id, "outcome": r.outcome, "reasons": r.reasons} for r in ans.receipts[:5]]}
    if ans.note:
        view["note"] = ans.note
    return json.dumps(view, ensure_ascii=False, default=str)


def _strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


class Agent:
    """One named agent with a role and a session. `ask` uses the model when one is configured, else the router."""

    def __init__(self, store: Store, name: str = "agent:support", role: str = "support", session: str | None = None,
                 config: AgentConfig | None = None, client=None, ctx_at: str | None = None):
        self.store, self.name, self.role = store, name, role
        self.session = session or f"s-{uuid.uuid4().hex[:8]}"
        self.config = config or AgentConfig.from_env()
        self._client = client
        self.ctx = T.Context(agent=name, session=self.session, at=ctx_at)
        self._names: set[str] | None = None

    # -- helpers --------------------------------------------------------------------------

    @property
    def uses_llm(self) -> bool:
        return bool(self.config.model)

    @property
    def client(self):
        if self._client is None and self.config.provider == "nightshift":
            from open_agent.nightshift import openai_client

            self._client = openai_client(self.config.nightshift_agent, timeout=self.config.timeout)
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self.config.base_url, api_key=self.config.api_key, timeout=self.config.timeout,
                                  max_retries=0)
        return self._client

    def names(self, refresh: bool = False) -> set[str]:
        """Every entity name in the graph (for the sentence check); refreshed after writes."""
        if self._names is None or refresh:
            self._names = T.all_names(self.store)
        return self._names

    def tool(self, name: str, args: dict | None = None) -> Answer:
        """Call one tool as this agent (what a model's tool call does, without the model)."""
        ans = T.call(self.store, name, args or {}, self.ctx)
        if T.TOOLS[name].writes and ans.outcome in (A.ACCEPTED, A.SUPERSEDED, A.CONFIRMED):
            self._names = None
        return ans

    # -- asking ---------------------------------------------------------------------------

    def ask(self, question: str) -> AgentRun:
        t0 = time.perf_counter()
        if self.uses_llm:
            try:
                run = self._ask_model(question)
            except LLMUnavailable as exc:
                run = self._ask_router(question)
                run.errors.append(f"model unavailable, used the router: {exc}")
        else:
            run = self._ask_router(question)
        run.ms = round((time.perf_counter() - t0) * 1000, 1)
        return run

    def _ask_router(self, question: str) -> AgentRun:
        run = AgentRun(question, self.name, self.session, "router")
        r = route(question)
        if r is None:
            run.sentence, run.rule = NO_TOOL, "no_rule"
            return run
        run.rule = r.rule
        try:
            run.answers.append(self.tool(r.tool, r.args))
        except T.ArgError as exc:
            run.errors.append(str(exc))
            run.sentence = f"The router chose {r.tool} but the arguments were refused: {exc}"
            return run
        run.sentence = run.answers[0].sentence
        return run

    def _ask_model(self, question: str) -> AgentRun:
        run = AgentRun(question, self.name, self.session, "llm")
        cfg = self.config
        messages = [{"role": "system", "content": SYSTEM_PROMPT.format(name=self.name, role=self.role, max_calls=cfg.max_calls)},
                    *self._memory_messages(run, question),
                    {"role": "user", "content": question}]
        tools = T.openai_tools()
        text, spent = "", 0
        while True:
            force_text = spent >= cfg.max_calls
            msg = self._complete(messages, None if force_text else tools, force_text)
            calls = [] if force_text else (msg.tool_calls or [])
            if not calls:
                text = _strip_thinking(msg.content or "")
                break
            ids = [c.id or f"call_{spent + i}" for i, c in enumerate(calls)]
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [{"id": cid, "type": "function",
                                             "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                                            for cid, c in zip(ids, calls)]})
            for cid, c in zip(ids, calls):
                spent += 1
                messages.append({"role": "tool", "tool_call_id": cid, "content": self._run_call(run, c, spent)})
        return self._finish_model_run(run, text)

    def _memory_messages(self, run: AgentRun, question: str) -> list[dict]:
        """Findings the write gate accepted and H-MEM holds that match the question, as one system message."""
        if os.environ.get("HM_MEMORY_CONTEXT", "on").strip().lower() == "off" or T.memory_backend()[0] != "hmem":
            return []
        try:
            from open_agent.hmem_bridge import memory_context

            context, hits = memory_context(self.store, question)
        except Exception as exc:  # no memory is not a reason to refuse the question
            run.errors.append(f"H-MEM context unavailable: {type(exc).__name__}: {str(exc)[:160]}")
            return []
        if not context:
            return []
        run.memory_ids = [h["memory"]["id"] for h in hits]
        return [{"role": "system", "content": MEMORY_PREFIX + context}]

    def _complete(self, messages: list, tools: list | None, force_text: bool):
        kwargs = {"model": self.config.model, "messages": messages, "temperature": 0}
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        if self.config.reasoning_effort:
            kwargs["reasoning_effort"] = self.config.reasoning_effort
        import openai

        try:
            return self.client.chat.completions.create(**kwargs).choices[0].message
        except openai.OpenAIError as exc:  # refused, timed out, unknown model, no tool support: the router takes over
            raise LLMUnavailable(f"{type(exc).__name__}: {str(exc)[:200]}") from exc

    def _run_call(self, run: AgentRun, call, spent: int) -> str:
        if spent > self.config.max_calls:
            return json.dumps({"error": f"call budget of {self.config.max_calls} tools is used up; answer now"})
        try:
            args = json.loads(call.function.arguments or "{}")
            ans = self.tool(call.function.name, args)
        except json.JSONDecodeError:
            run.errors.append(f"{call.function.name}: arguments were not valid JSON")
            return json.dumps({"error": "arguments were not valid JSON"})
        except T.ArgError as exc:
            run.errors.append(f"{call.function.name}: {exc}")
            return json.dumps({"error": str(exc)})
        except T.GateUnavailable as exc:
            run.errors.append(str(exc))
            return json.dumps({"error": str(exc)})
        run.answers.append(ans)
        return model_view(ans)

    def _finish_model_run(self, run: AgentRun, text: str) -> AgentRun:
        if not run.answers:  # a model that answers without a tool has nothing to ground the sentence in
            fallback = self._ask_router(run.question)
            fallback.mode, fallback.rule = "llm", f"{fallback.rule} (model called no tool)"
            fallback.errors += run.errors
            return fallback
        ok, why = (check_sentence(text, combined(run.answers), self.names(), asked=run.question) if text
                   else (False, "the model wrote no sentence"))
        if ok:
            run.sentence, run.sentence_source = text, "model"
            return run
        run.check, run.rejected = why, text
        sentences = list(dict.fromkeys(template_sentence(a) for a in run.answers))
        run.sentence = " ".join(sentences)
        return run


# --- command line: ask one question, or compare models on corpus questions -------------------

COMPARE_QUESTIONS = (  # (question, acceptable first tools)
    ("Who currently owns billing-api?", {"current_owner"}),
    ("Who owned billing-api on 2026-09-09?", {"owner_at", "history"}),
    ("What changed in system ownership since 2026-09-15?", {"what_changed"}),
    ("Is Growth the current owner of billing-api?", {"why", "current_owner"}),
    ("Who received the duplicate invoice emails incident from Support?", {"recall"}),
)


def compare(store: Store, models: list[str], base_url: str, api_key: str, effort: str | None = None) -> list[dict]:
    """Run COMPARE_QUESTIONS through each model; report whether it called the right tool and how fast."""
    results = []
    for model in models:
        cfg = AgentConfig(base_url=base_url, model=model, api_key=api_key, reasoning_effort=effort)
        for question, right in COMPARE_QUESTIONS:
            run = Agent(store, config=cfg).ask(question)
            first = run.answers[0].tool if run.answers and run.mode == "llm" and not run.rule else None
            results.append({"model": model, "question": question, "first_tool": first, "right_tool": first in right,
                            "sentence_ok": run.sentence_source == "model", "calls": len(run.answers),
                            "seconds": round(run.ms / 1000, 1), "errors": run.errors, "check": run.check})
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m honest_memory.agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ask = sub.add_parser("ask", help="ask one question (the model when HM_LLM_MODEL is set, else the router)")
    ask.add_argument("question")
    ask.add_argument("--tenant", default=os.environ.get("HM_TENANT", "demo"))
    ask.add_argument("--agent", default="agent:support")
    cmp = sub.add_parser("compare", help="compare models on five corpus questions")
    cmp.add_argument("--tenant", default=os.environ.get("HM_TENANT", "demo"))
    cmp.add_argument("--models", required=True, help="comma-separated model names")
    cmp.add_argument("--effort", default=None, help="reasoning_effort to send, e.g. none")
    args = ap.parse_args(argv)

    from .store import connect

    store = Store(connect(), args.tenant)
    if args.cmd == "ask":
        run = Agent(store, name=args.agent, config=AgentConfig.from_env()).ask(args.question)
        print(json.dumps(run.to_dict(), indent=2, ensure_ascii=False, default=str))
        return 0
    base = os.environ.get("HM_LLM_BASE_URL", DEFAULT_BASE_URL)
    for row in compare(store, args.models.split(","), base, os.environ.get("HM_LLM_API_KEY", "ollama"), args.effort):
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
