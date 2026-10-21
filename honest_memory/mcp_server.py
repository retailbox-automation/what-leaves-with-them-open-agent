"""MCP stdio server: the tool catalogue for any MCP client (editors, agents).

Every tool of honest_memory.tools is exposed with the JSON schema from tool_schemas(), so the client sees
exactly what the HTTP API and the OpenAI-format tools see. The model picks a tool and fills arguments; it
never writes Cypher, and writes go through the write gate.

Env: HM_TENANT (default "demo"), FALKORDB_* (see store.connect), HM_AGENT (identity stamped on writes, default
agent:mcp), HM_SESSION (default: one random id per server process), HM_MCP_READONLY=1 to hide remember/handoff and refuse handoff_plan with propose=true.

    python -m honest_memory.mcp_server
"""

from __future__ import annotations

import inspect
import os
import threading
import uuid
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools.base import Tool as McpTool

from . import tools as T
from .schema import graph_name
from .store import Store, connect

INSTRUCTIONS = (
    "Questions about a company's systems, owners, decisions and history, answered from a FalkorDB graph. "
    "Every answer is one of: answered_with_path (rows plus the hops), answered_from_memory (a finding with its "
    "date), not_in_graph (the entity exists but nothing connects the way the question assumed: that is not the "
    "same as 'nothing is affected'), unknown_entity (no such name; no query ran), or for writes accepted, "
    "confirmed, superseded, refused with a receipt. Use the exact names from the question. Quote the sentence and "
    "show the paths; do not add names, dates or ids that the rows do not contain.")


class Memory:
    """Holds the FalkorDB connection for one tenant and runs tool calls as one identity."""

    def __init__(self, tenant: str, db=None, agent: str | None = None, session: str | None = None,
                 readonly: bool = False):
        graph_name(tenant)  # fail at startup on a bad tenant name
        self.tenant, self._db, self._lock, self.readonly = tenant, db, threading.Lock(), readonly
        self.ctx = T.Context(agent=agent or os.environ.get("HM_AGENT", "agent:mcp"),
                             session=session or os.environ.get("HM_SESSION") or f"mcp-{uuid.uuid4().hex[:8]}")

    def run(self, name: str, args: dict) -> dict:
        with self._lock:
            if self._db is None:
                self._db = connect()
        if graph_name(self.tenant) not in self._db.list_graphs():
            raise ToolError(f"tenant {self.tenant!r} has no graph in this FalkorDB; ingest a corpus first "
                            "(python -m honest_memory.ingest --corpus data/corpus/42 --tenant " + self.tenant + ")")
        if self.readonly and T.TOOLS[name].writes_with(args):
            raise ToolError(f"this server is read-only; {name} with these arguments would write")
        try:
            return T.call(Store(self._db, self.tenant), name, args, self.ctx).to_dict()
        except (T.ArgError, T.GateUnavailable) as exc:
            raise ToolError(str(exc)) from exc


def _entry(tool: T.Tool, memory: Memory) -> McpTool:
    """One MCP tool whose published schema is ours. The function takes whatever the client sends; tools.call
    does the real validation, so a bad call comes back as a tool error with a plain message."""
    params = [inspect.Parameter(a.name, inspect.Parameter.KEYWORD_ONLY, annotation=Any,
                                default=inspect.Parameter.empty if a.required else None) for a in tool.args]

    def fn(**kwargs: Any) -> dict[str, Any]:
        return memory.run(tool.name, {k: v for k, v in kwargs.items() if v is not None})

    fn.__name__, fn.__doc__ = tool.name, tool.description
    fn.__signature__ = inspect.Signature(params, return_annotation=dict[str, Any])  # type: ignore[attr-defined]
    entry = McpTool.from_function(fn, name=tool.name, description=tool.description, structured_output=False)
    return entry.model_copy(update={"parameters": tool.parameters()})


def build_server(tenant: str | None = None, db=None, readonly: bool | None = None) -> MCPServer:
    if readonly is None:
        readonly = os.environ.get("HM_MCP_READONLY") == "1"
    memory = Memory(tenant or os.environ.get("HM_TENANT", "demo"), db, readonly=readonly)
    entries = [_entry(t, memory) for t in T.TOOLS.values() if not (readonly and t.writes)]
    server = MCPServer("honest-memory", instructions=INSTRUCTIONS, version="0.0.1", tools=entries)
    server.memory = memory  # type: ignore[attr-defined]
    return server


def main() -> None:
    build_server().run()  # stdio


if __name__ == "__main__":
    main()
