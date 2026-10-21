"""The honest-memory paths: the corpus goes through the write gate into a fresh tenant graph, then every question is
answered twice, once by the deterministic router (no model) and once by the LLM agent with the tool catalogue.

The tenant name is unique per run and is dropped at the end, so a run never touches another graph.
"""

from __future__ import annotations

import os
import time
import uuid

from falkordb import FalkorDB

from honest_memory import answer as A
from honest_memory.agent import Agent, AgentConfig, AgentRun
from honest_memory.ingest import ingest_corpus
from honest_memory.store import Store

from .common import BASE_URL, Corpus, falkordb_version

WRITE_TOOLS = ("remember", "handoff")


def _tool_trace(run: AgentRun) -> list[dict]:
    return [{"tool": a.tool, "args": a.args, "outcome": a.outcome, "rows": len(a.rows),
             "sources": [s.id for s in a.sources], "ms": round(sum(t.ms for t in a.trace), 1)} for a in run.answers]


def _evidence(run: AgentRun) -> str:
    """Everything the answer may rest on: the rows, paths, sources and receipts the tools returned."""
    return "\n".join(A.allowed_text(a) for a in run.answers)


def _row(run: AgentRun) -> dict:
    cited = list(dict.fromkeys(src.id for a in run.answers for src in a.sources))
    out = {"answer": run.sentence, "cited": cited, "mode": run.mode, "rule": run.rule, "outcome": run.outcome,
           "sentence_source": run.sentence_source, "check": run.check, "rejected": run.rejected,
           "trace": _tool_trace(run), "evidence": _evidence(run), "errors": run.errors, "ms": run.ms}
    if any(a.tool in WRITE_TOOLS for a in run.answers):
        out["errors"] = run.errors + ["the agent called a write tool"]
    return out


def _connect(host: str, port: int, tries: int = 5) -> FalkorDB:
    """The FalkorDB here is shared with other work and can be busy for a while: a generous socket timeout, and a retry."""
    for attempt in range(tries):
        try:
            return FalkorDB(host=host, port=port, socket_timeout=120, socket_connect_timeout=30)
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(10 * (attempt + 1))


class Honest:
    """One tenant graph on one FalkorDB, filled through the gate, queried by the router and by the agent."""

    def __init__(self, corpus: Corpus, host: str, port: int, model: str, base_url: str = BASE_URL,
                 keep: bool = False, reasoning: str | None = None):
        self.corpus, self.keep = corpus, keep
        self.db = _connect(host, port)
        self.tenant = f"bench_{uuid.uuid4().hex[:10]}"
        self.store = Store(self.db, self.tenant)
        self.router = Agent(self.store, name="agent:bench-router", config=AgentConfig(model=None))
        self.agent = Agent(self.store, name="agent:bench-llm", config=AgentConfig(base_url=base_url, model=model,
                                                                                  api_key=os.environ.get("HM_LLM_API_KEY", "ollama"),
                                                                                  reasoning_effort=reasoning))
        self.ingest_summary: dict = {}

    @property
    def version(self) -> str:
        return falkordb_version(self.db)

    def __enter__(self) -> "Honest":
        self.ingest_summary = ingest_corpus(self.store, self.corpus.dir, reset=True)
        return self

    def __exit__(self, *exc) -> None:
        if not self.keep:
            self.store.drop()

    def ask_router(self, question: str) -> dict:
        return _row(self.router.ask(question))

    def ask_agent(self, question: str) -> dict:
        return _row(self.agent.ask(question))
