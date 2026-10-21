# Use the memory from Claude Code or Cursor (MCP)

`honest_memory.mcp_server` is an MCP stdio server. It exposes the fixed catalogue of 21 tools: `current_owner`, `owner_at`, `history`, `escalation_path`, `impact`, `who_knows`, `decision_trail`, `why`, `what_changed`, `path_between`, `recall`, `facts_about`, `remember`, `handoff`, and the departure tools `what_leaves`, `single_source`, `without`, `bridges`, `successors`, `handoff_plan`, `promises`. The model picks a tool and fills arguments; it never writes Cypher, every name is resolved against the graph first, and every answer carries its path.

## Set up

```bash
pip install -e ".[mcp]"                 # needs Python 3.12 and a FalkorDB (local or Cloud)
docker run --rm -p 6379:6379 falkordb/falkordb:latest
python -m honest_memory.corpus --seed 42 --out data/corpus
python -m honest_memory.ingest --corpus data/corpus/42 --tenant demo --reset
```

## Claude Code

Project file `.mcp.json` (or `claude mcp add honest-memory -e HM_TENANT=demo -- python -m honest_memory.mcp_server`):

```json
{
  "mcpServers": {
    "honest-memory": {
      "command": "python",
      "args": ["-m", "honest_memory.mcp_server"],
      "env": {
        "HM_TENANT": "demo",
        "HM_AGENT": "agent:editor",
        "FALKORDB_HOST": "localhost",
        "FALKORDB_PORT": "6379"
      }
    }
  }
}
```

## Cursor

`.cursor/mcp.json`, same block as above:

```json
{
  "mcpServers": {
    "honest-memory": {
      "command": "python",
      "args": ["-m", "honest_memory.mcp_server"],
      "env": { "HM_TENANT": "demo", "HM_AGENT": "agent:cursor", "FALKORDB_HOST": "localhost", "FALKORDB_PORT": "6379" }
    }
  }
}
```

Use the interpreter of the environment where the package is installed (`/path/to/.venv/bin/python`) if `python` on the PATH is not that one.

## Environment

| Variable | Meaning | Default |
|---|---|---|
| `HM_TENANT` | tenant graph (`hm_<tenant>`) | `demo` |
| `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_USERNAME`, `FALKORDB_PASSWORD`, `FALKORDB_TLS` | connection, see `store.connect` (FalkorDB Cloud free speaks plain RESP: leave `FALKORDB_TLS` at 0) | localhost:6379 |
| `HM_AGENT` | identity stamped on `remember` and `handoff` (`agent:<name>`) | `agent:mcp` |
| `HM_SESSION` | session id stamped on writes | one random id per server process |
| `HM_MCP_READONLY` | `1` hides `remember` and `handoff` | off |

Keep the password in your shell or a secret store and pass it through the client's `env`; do not commit it.

## Try it

Ask in the editor: "Who owns billing-api and what did it replace?", "What depends on ledger-db within 4 hops, and who must be told?", "Is Growth the current owner of billing-api?", "What changed in system ownership since 2026-09-23?". The last two show a refused claim with its receipt.

Each result is JSON: `outcome` (`answered_with_path`, `answered_from_memory`, `not_in_graph`, `unknown_entity`, or for writes `accepted`, `confirmed`, `superseded`, `refused`), `rows`, `paths` (node names alternating with edge types; a leading `<-` on an edge means it was walked backwards), `sources`, `receipts`, `trace` and a `sentence` built from the rows. `not_in_graph` means the entity exists but nothing connects the way the question assumed; it is not the same as "nothing is affected".

Notes: the server uses the official `mcp` SDK 2.x (`MCPServer`, the renamed `FastMCP`). A bad argument comes back as a tool error with a plain message; a tenant with no graph says how to ingest one.
