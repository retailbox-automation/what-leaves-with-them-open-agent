# Design sketch (written before the hackathon start)

This note is the pre-start sketch that rule 8 allows ("discuss ideas, take notes, sketch your graph model, diagrams"). It was drafted on 2026-10-11 and translated on 2026-10-14 before 18:31 UTC. It contains no code. Everything else in the repository is built during the event.

## Flow

```
source (chat / ticket / doc of a fictional company, public benchmark)
   -> extractor (LLM proposes candidate facts; a proposal only)
   -> WRITE GATE (code: provenance? conflict? PII / disallowed? duplicate?) -> GateDecision node with a receipt
   -> FalkorDB: one graph per tenant, temporal edges (valid_at / invalid_at), vector index
   <- Search API / MCP tools: Cypher (path to source, "what changed since", "who knows about X"),
      algorithms (shortest path, PageRank, community detection)
   <- agents (support / ops / company brain) share one tenant graph; handoff through Task / Handoff nodes
```

## Graph model (draft)

Nodes: `Person, Team, Project, System, Document, Decision, Task, Episode` (source chunk), `GateDecision`.

Edges: `OWNS, MEMBER_OF, WORKED_ON, USES, DECIDED, SUPERSEDES, MENTIONS, DERIVED_FROM (-> Episode), HANDOFF_TO`.

Fact edges carry `valid_at, invalid_at, source_id, confidence, gate_id`.

Isolation: one FalkorDB graph per tenant.

## Open questions for the first spike

1. Current vs closed facts in multi-hop paths: filter on `invalid_at IS NULL`, or keep current and closed facts structurally apart? (FalkorDB team, Discord #questions, 2026-10-14: an edge-property filter inside `[*1..6]` is checked per expanded edge and does not use an index.)
2. Vector index and graph filter in one Cypher query.
3. Which algorithms ship in FalkorDB 6.0.2 for agent tools.
4. Bytes per fact against the 100 MB FalkorDB Cloud free tier.
5. Whether the Graphiti FalkorDB driver issues getzep/graphiti#1934 and #1892 reproduce on FalkorDB 6.0.2.
