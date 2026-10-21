"""The MCP server: tool listing and calls in-process, and once over a real stdio subprocess."""

import json
import os
import sys

import pytest

anyio = pytest.importorskip("anyio")
pytest.importorskip("mcp")
from mcp.client import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

from honest_memory import tools as T  # noqa: E402
from honest_memory.mcp_server import build_server  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def with_client(server, fn):
    async def run():
        async with Client(server) as client:
            return await fn(client)

    return anyio.run(run)


def payload(result) -> dict:
    return json.loads(result.content[0].text)


def test_every_tool_is_listed_with_our_schema(fx_store):
    server = build_server(fx_store.tenant, db=fx_store.db)
    listing = with_client(server, lambda c: c.list_tools())
    tools = {t.name: t for t in listing.tools}
    assert list(tools) == list(T.TOOLS) and len(tools) == 21  # 14 + the 7 departure tools (N5)
    ours = {s["name"]: s for s in T.tool_schemas()}
    for name, tool in tools.items():
        assert tool.description == ours[name]["description"]
        assert tool.input_schema == ours[name]["parameters"]
    depth = tools["impact"].input_schema["properties"]["depth"]
    assert (depth["minimum"], depth["maximum"]) == (1, 4)
    assert tools["current_owner"].input_schema["required"] == ["system"]


def test_a_call_returns_the_answer_with_its_path(fx_store):
    server = build_server(fx_store.tenant, db=fx_store.db)
    res = with_client(server, lambda c: c.call_tool("current_owner", {"system": "billing-api"}))
    assert not res.is_error
    body = payload(res)
    assert body["outcome"] == "answered_with_path" and body["rows"][0]["owner"] == "Luca Ostrander"
    assert body["paths"][0][:3] == ["Luca Ostrander", "OWNS", "billing-api"] and body["trace"][0]["tool"] == "current_owner"


def test_unknown_entity_is_an_answer_and_bad_arguments_are_a_tool_error(fx_store):
    server = build_server(fx_store.tenant, db=fx_store.db)

    async def calls(c):
        return (await c.call_tool("current_owner", {"system": "payment-service"}),
                await c.call_tool("impact", {"system": "ledger-db", "depth": 9}),
                await c.call_tool("current_owner", {}))

    unknown, bad_depth, missing = with_client(server, calls)
    assert not unknown.is_error and payload(unknown)["outcome"] == "unknown_entity"
    assert bad_depth.is_error and "depth must be an integer from 1 to 4" in bad_depth.content[0].text
    assert missing.is_error


def test_writes_are_stamped_with_the_server_identity_and_can_be_switched_off(fx_store):
    from honest_memory.mcp_server import Memory

    server = build_server(fx_store.tenant, db=fx_store.db)
    server.memory.ctx.agent, server.memory.ctx.session = "agent:editor", "mcp-test"
    res = with_client(server, lambda c: c.call_tool("remember", {
        "text": "Asked in the editor who owns billing-api; per ADR-0009 it is Luca Ostrander.",
        "about": ["billing-api"], "source_ids": ["ADR-0009"]}))
    assert payload(res)["outcome"] == "accepted"
    row = fx_store.ro("MATCH (a:Agent)-[:WROTE]->(f:Finding {session: 'mcp-test'}) RETURN a.name")
    assert row == [["agent:editor"]]
    readonly = build_server(fx_store.tenant, db=fx_store.db, readonly=True)
    names = [t.name for t in with_client(readonly, lambda c: c.list_tools()).tools]
    assert "remember" not in names and "handoff" not in names and len(names) == 19
    refused = with_client(readonly, lambda c: c.call_tool("handoff_plan", {"person": "Mateo Sandoval", "propose": True}))
    assert refused.is_error and "read-only" in refused.content[0].text
    plan = with_client(readonly, lambda c: c.call_tool("handoff_plan", {"person": "Mateo Sandoval"}))
    assert not plan.is_error and payload(plan)["outcome"] == "answered_with_path"
    assert Memory(fx_store.tenant).ctx.session.startswith("mcp-")


def test_a_tenant_without_a_graph_says_how_to_create_it(fx_store):
    server = build_server("no_such_tenant_here", db=fx_store.db)
    res = with_client(server, lambda c: c.call_tool("current_owner", {"system": "auth"}))
    assert res.is_error and "ingest" in res.content[0].text


def test_a_bad_tenant_name_fails_at_startup():
    with pytest.raises(ValueError):
        build_server("a b}) //")


def test_stdio_server_started_the_way_a_client_config_starts_it(fx_store, port):
    env = {**os.environ, "HM_TENANT": fx_store.tenant, "FALKORDB_PORT": str(port), "FALKORDB_HOST": "localhost",
           "HM_AGENT": "agent:stdio-test", "PYTHONPATH": ROOT}
    params = StdioServerParameters(command=sys.executable, args=["-m", "honest_memory.mcp_server"], env=env, cwd=ROOT)

    async def run(c):
        listing = await c.list_tools()
        res = await c.call_tool("who_knows", {"system": "ledger-db", "limit": 2})
        return len(listing.tools), payload(res)

    n, body = with_client(params, run)
    assert n == 21 and [r["person"] for r in body["rows"]] == ["Mateo Sandoval", "Pavel Hollis"]
