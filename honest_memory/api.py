"""HTTP API over the tool catalogue.

    GET  /health                          FalkorDB ping and version
    GET  /v1/{tenant}/tools               every tool with its JSON schema
    POST /v1/{tenant}/tools/{name}        JSON arguments -> Answer
    POST /v1/{tenant}/ask                 {question, agent?}: the model if HM_LLM_MODEL is set, else the router
    GET  /v1/{tenant}/graph               people, teams, systems, decisions, projects and the facts between them (?as_of=)
    GET  /v1/{tenant}/receipts            gate receipts, newest first (?outcome=&limit=)
    POST /v1/{tenant}/episodes            one source record -> the extractor and the write gate
    GET  /v1/{tenant}/episodes/{id}       one source record (lines that look like personal data withheld)

Env: FALKORDB_* (see store.connect), HM_LLM_* (see agent.py), HM_RATE_LIMIT_PER_MIN (default 120 per client
IP; HM_TRUST_PROXY=1 reads X-Forwarded-For), HM_WRITE_TOKEN (when set, writes need `Authorization: Bearer`),
HM_WEB_DIR (static panel, default ./web when it exists).
Run: uvicorn honest_memory.api:app
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import snapshot
from . import tools as T
from .agent import Agent, AgentConfig
from .schema import graph_name
from .store import Store, connect

AGENT_NAME = re.compile(r"^agent:[a-z0-9][a-z0-9_-]{0,63}$")
SESSION = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
GATE_OUTCOMES = {"accept": "ACCEPT", "accepted": "ACCEPT", "confirm": "CONFIRM", "confirmed": "CONFIRM",
                 "supersede": "SUPERSEDE", "superseded": "SUPERSEDE", "reject": "REJECT", "refused": "REJECT"}
CHANNELS = ("chat", "ticket", "doc")


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    agent: str | None = None
    session: str | None = None


class EpisodeBody(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    ts: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")  # ISO-8601, UTC
    channel: str
    author: str = Field(min_length=1, max_length=120)
    title: str = Field(default="", max_length=300)
    text: str = Field(min_length=1, max_length=20000)
    refs: list[str] = Field(default_factory=list, max_length=20)


class RateLimiter:
    """Sliding one-minute window per client IP. Generous on purpose: judges share one NAT."""

    def __init__(self, per_minute: int):
        self.per_minute, self.hits, self.lock = per_minute, defaultdict(deque), threading.Lock()

    def check(self, client: str) -> int:
        """0 when allowed, else seconds until the next request would be."""
        now = time.monotonic()
        with self.lock:
            q = self.hits[client]
            while q and now - q[0] >= 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return max(1, int(60 - (now - q[0])) + 1)
            q.append(now)
            return 0


def falkordb_version(db) -> str | None:
    """'6.0.2' / '4.22.0' from the graph module, or None when MODULE LIST is not allowed."""
    try:
        for mod in db.connection.execute_command("MODULE", "LIST"):
            info = dict(zip(mod[::2], mod[1::2]))
            if info.get(b"name", info.get("name")) in (b"graph", "graph"):
                v = int(info.get(b"ver", info.get("ver")))
                return f"{v // 10000}.{v // 100 % 100}.{v % 100}"
    except Exception:
        return None
    return None


def _jsonable(value: Any):
    import dataclasses
    import json

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    return json.loads(json.dumps(value, default=lambda o: dataclasses.asdict(o) if dataclasses.is_dataclass(o) else str(o)))


def create_app(db=None, *, rate_limit: int | None = None, web_dir: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="honest-memory", version="0.0.1",
                  description="Agent memory on FalkorDB: a fixed tool catalogue, answers with paths, a write gate.")
    app.state.db = db
    app.state.db_lock = threading.Lock()
    limiter = RateLimiter(rate_limit or int(os.environ.get("HM_RATE_LIMIT_PER_MIN", "120")))
    trust_proxy = os.environ.get("HM_TRUST_PROXY") == "1"

    def database():
        with app.state.db_lock:
            if app.state.db is None:
                app.state.db = connect()
            return app.state.db

    def store_for(tenant: str) -> Store:
        try:
            name = graph_name(tenant)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        db_ = database()
        if name not in db_.list_graphs():
            raise HTTPException(404, f"tenant {tenant!r} has no graph")
        return Store(db_, tenant)

    def require_write(request: Request) -> None:
        token = os.environ.get("HM_WRITE_TOKEN")
        if token and request.headers.get("authorization") != f"Bearer {token}":
            raise HTTPException(401, "writes need Authorization: Bearer <token>")

    def identity(request: Request, default_agent: str = "agent:api") -> tuple[str, str]:
        agent = request.headers.get("x-hm-agent", default_agent)
        session = request.headers.get("x-hm-session", "api")
        if not AGENT_NAME.match(agent) or not SESSION.match(session):
            raise HTTPException(422, "X-HM-Agent must look like agent:name and X-HM-Session like s-1")
        return agent, session

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        if request.url.path.startswith("/v1/"):
            # The last entry is the one our own proxy appended; earlier entries come from the client and can be forged.
            fwd = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
            client = fwd if trust_proxy and fwd else (request.client.host if request.client else "unknown")
            wait = limiter.check(client)
            if wait:
                return JSONResponse({"error": "rate limit exceeded", "retry_after_seconds": wait}, 429,
                                    headers={"Retry-After": str(wait)})
        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, exc.status_code)

    @app.exception_handler(T.ArgError)
    async def arg_error(_: Request, exc: T.ArgError):
        return JSONResponse({"error": str(exc)}, 422)

    @app.exception_handler(T.GateUnavailable)
    async def gate_error(_: Request, exc: T.GateUnavailable):
        return JSONResponse({"error": str(exc)}, 501)

    @app.exception_handler(Exception)
    async def server_error(_: Request, exc: Exception):
        kind = type(exc).__name__
        status = 503 if kind in ("ConnectionError", "TimeoutError", "BusyLoadingError", "ResponseError") else 500
        return JSONResponse({"error": f"{kind} while talking to the graph" if status == 503 else "internal error"}, status)

    @app.get("/health")
    def health():
        cfg = AgentConfig.from_env()
        try:
            db_ = database()
            db_.connection.ping()
            falkor = {"ping": True, "version": falkordb_version(db_)}
        except Exception as exc:
            return JSONResponse({"status": "down", "falkordb": {"ping": False, "error": type(exc).__name__}}, 503)
        try:
            from . import gate  # noqa: F401
            gate_ok = True
        except ImportError:
            gate_ok = False
        return {"status": "ok", "falkordb": falkor, "tools": len(T.TOOLS), "write_gate": gate_ok,
                "llm": {"configured": bool(cfg.model), "model": cfg.model}}

    @app.get("/v1/{tenant}/tools")
    def list_tools(tenant: str):
        store_for(tenant)
        return {"tenant": tenant, "tools": T.tool_schemas()}

    @app.post("/v1/{tenant}/tools/{name}")
    def run_tool(tenant: str, name: str, request: Request, args: dict[str, Any] | None = None):
        tool = T.TOOLS.get(name)
        if tool is None:
            raise HTTPException(404, f"unknown tool {name!r}; tools: {', '.join(T.TOOLS)}")
        if tool.writes_with(args):  # handoff_plan writes only with propose=true
            require_write(request)
        store = store_for(tenant)
        agent, session = identity(request)
        return T.call(store, name, args or {}, T.Context(agent=agent, session=session)).to_dict()

    @app.post("/v1/{tenant}/ask")
    def ask(tenant: str, body: AskBody, request: Request):
        store = store_for(tenant)
        name = body.agent or "agent:api"
        if not AGENT_NAME.match(name) or (body.session and not SESSION.match(body.session)):
            raise HTTPException(422, "agent must look like agent:name and session like s-1")
        agent = Agent(store, name=name, role=name.split(":", 1)[1], session=body.session, config=AgentConfig.from_env())
        return agent.ask(body.question).to_dict()

    @app.get("/v1/{tenant}/graph")
    def graph(tenant: str, as_of: str | None = None):
        store = store_for(tenant)
        return snapshot.company_graph(store, T.parse_when(as_of) if as_of else None)

    @app.get("/v1/{tenant}/episodes/{episode_id}")
    def get_episode(tenant: str, episode_id: str):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", episode_id):
            raise HTTPException(422, "episode id must be letters, digits, '.', '_' or '-'")
        found = snapshot.episode(store_for(tenant), episode_id)
        if found is None:
            raise HTTPException(404, f"no episode {episode_id!r}")
        return found

    @app.get("/v1/{tenant}/receipts")
    def receipts(tenant: str, outcome: str | None = None, limit: int = 50):
        store = store_for(tenant)
        if not 1 <= limit <= 200:
            raise HTTPException(422, "limit must be 1 to 200")
        gate_outcome = None
        if outcome:
            gate_outcome = GATE_OUTCOMES.get(outcome.lower())
            if gate_outcome is None:
                raise HTTPException(422, f"outcome must be one of {', '.join(sorted(set(GATE_OUTCOMES)))}")
        rows = store.ro("MATCH (g:GateDecision) WHERE $o IS NULL OR g.outcome = $o "
                        "OPTIONAL MATCH (g)-[:CITES]->(e:Episode) "
                        f"RETURN {T._RECEIPT_COLUMNS} ORDER BY g.ts DESC, g.id LIMIT $limit", {"o": gate_outcome, "limit": limit})
        return {"tenant": tenant, "outcome": gate_outcome, "receipts": [T._receipt(r).__dict__ for r in rows]}

    @app.post("/v1/{tenant}/episodes")
    def add_episode(tenant: str, body: EpisodeBody, request: Request):
        require_write(request)
        if body.channel not in CHANNELS:
            raise HTTPException(422, f"channel must be one of {', '.join(CHANNELS)}")
        store = store_for(tenant)
        try:
            from . import ingest
        except ImportError as exc:
            raise T.GateUnavailable("ingest needs honest_memory.ingest, which is not installed") from exc
        try:
            result = ingest.ingest_episode(store, body.model_dump(), T.get_extractor())
        except ValueError as exc:  # an episode dated in the future, or a time that does not exist
            raise HTTPException(422, str(exc)) from exc
        return {"tenant": tenant, "episode": body.id, "result": _jsonable(result)}

    folder = Path(web_dir or os.environ.get("HM_WEB_DIR") or Path(__file__).resolve().parent.parent / "web")
    if folder.is_dir():  # mounted last, so /health and /v1 win
        app.mount("/", StaticFiles(directory=folder, html=True), name="web")
    return app


app = create_app()
