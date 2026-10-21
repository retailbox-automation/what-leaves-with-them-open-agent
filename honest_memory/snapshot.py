"""The company graph as the panel draws it: people, teams, systems, decisions and projects, and the facts between them.

    company_graph(store, as_of=None) -> dict
    episode(store, id) -> dict | None     one source record, for the hover cards and the handoff answers

Without `as_of`: every current fact (a typed edge), and apart from them, in `ended`, the facts a last-day record
ended without a successor (WAS edges closed by a CLOSE receipt). The departure tools read the same two sets, so the
drawing and their answers agree about the person who is leaving.
With `as_of` (YYYY-MM-DD, the whole UTC day): the facts in force at the end of that day, whether they are current
now or were closed later; `ended` is empty.

Records (Episode, GateDecision, Agent, Finding, Handoff) are not drawn. Every query is read-only and traced.
A line of a record that looks like personal data (gate.detect_pii) is withheld from `episode`.
"""

from __future__ import annotations

from collections import Counter

from .answer import iso_ts
from .schema import ENTITY_KEYS, HISTORY_EDGE, PREDICATES
from .store import Store

LABELS = ("Person", "Team", "System", "Decision", "Project")
EDGE_TYPES = tuple(PREDICATES)  # OWNS, MEMBER_OF, LEADS, WORKED_ON, DECIDED, SUPERSEDES, DEPENDS_ON (+ CONSTRAINS)
# Node properties the panel shows on hover, and the name each gets in the answer (a System's `kind`, integration
# or runbook, must not overwrite the node kind); anything else stays in the graph.
SHOWN = {"Person": {"notice_at": "notice_at", "last_day": "last_day", "notice_source": "notice_source"}, "Team": {},
         "System": {"kind": "system_kind", "purpose": "purpose"}, "Decision": {"title": "title", "status": "status"},
         "Project": {"goal": "goal"}}

NODES = " UNION ALL ".join(
    f"MATCH (n:{label}) RETURN '{label}' AS label, n.{ENTITY_KEYS[label]} AS key, properties(n) AS props"
    for label in LABELS)

_TYPES = "|".join(EDGE_TYPES)
_COLUMNS = ("labels(a)[0] AS a_label, coalesce(a.name, a.id) AS a, {rel} AS rel, labels(b)[0] AS b_label, "
            "coalesce(b.name, b.id) AS b, {e}.valid_at AS since, {e}.source_ids AS src, {e}.count AS n")
CURRENT = f"MATCH (a)-[r:{_TYPES}]->(b) RETURN {_COLUMNS.format(rel='type(r)', e='r')}"

# Ended without a successor: the WAS edge's closing receipt is a CLOSE (a last-day record), not a SUPERSEDE.
ENDED = (f"MATCH (a)-[h:{HISTORY_EDGE}]->(b) WITH a, h, b "
         "MATCH (g:GateDecision {id: h.closed_by}) WHERE g.outcome = 'CLOSE' "
         "OPTIONAL MATCH (g)-[:CITES]->(e:Episode) "
         "RETURN labels(a)[0], coalesce(a.name, a.id), h.predicate, labels(b)[0], coalesce(b.name, b.id), "
         "h.valid_at, h.source_ids, h.count, h.invalid_at, h.closed_by, collect(e.id)")

AS_OF = (f"MATCH (a)-[r:{_TYPES}]->(b) WHERE r.valid_at <= $t "
         f"RETURN {_COLUMNS.format(rel='type(r)', e='r')} "
         "UNION ALL "
         f"MATCH (a)-[h:{HISTORY_EDGE}]->(b) WHERE h.valid_at <= $t AND h.invalid_at > $t "
         f"RETURN {_COLUMNS.format(rel='h.predicate', e='h')}")


def _edge(row) -> dict | None:
    a_label, a, rel, b_label, b, valid_at, source_ids, count = row[:8]
    if a_label not in LABELS or b_label not in LABELS or a is None or b is None:
        return None
    edge = {"source": a, "target": b, "type": rel, "since": iso_ts(valid_at), "source_ids": list(source_ids or [])}
    if count:
        edge["count"] = count
    return edge


def _teams(nodes: dict[str, dict], edges: list[dict]) -> None:
    """Where each node sits: a person in their team, a system with its owner's team, a decision with the team
    most of its deciders belong to, a project with its lead's team. Nodes with no such link get team None."""
    member = {e["source"]: e["target"] for e in edges if e["type"] == "MEMBER_OF"}
    for key, n in nodes.items():
        if n["kind"] == "team":
            n["team"] = key
        elif n["kind"] == "person":
            n["team"] = member.get(key)
    owner_team = {e["target"]: member.get(e["source"]) for e in edges if e["type"] == "OWNS"}
    lead_team = {e["target"]: member.get(e["source"]) for e in edges if e["type"] == "LEADS"}
    deciders: dict[str, Counter] = {}
    for e in edges:
        if e["type"] == "DECIDED" and member.get(e["source"]):
            deciders.setdefault(e["target"], Counter())[member[e["source"]]] += 1
    for key, n in nodes.items():
        if n["kind"] == "system":
            n["team"] = owner_team.get(key)
        elif n["kind"] == "project":
            n["team"] = lead_team.get(key)
        elif n["kind"] == "decision":
            votes = deciders.get(key)
            n["team"] = min(votes, key=lambda t: (-votes[t], t)) if votes else None


def company_graph(store: Store, as_of=None) -> dict:
    """Nodes and edges for the panel. `as_of` is a tools.When (or None for the present)."""
    first = len(store.trace)
    nodes: dict[str, dict] = {}
    for label, key, props in store.ro(NODES):
        if key is None:
            continue
        node = {"id": key, "label": key, "kind": label.lower(), "team": None}
        for prop, name in SHOWN[label].items():
            if props.get(prop) is not None:
                node[name] = iso_ts(props[prop]) if prop == "notice_at" else props[prop]
        nodes.setdefault(key, node)  # keys are unique across these labels in this model; the first one wins

    if as_of is None:
        edges = [e for e in map(_edge, store.ro(CURRENT)) if e]
        ended = []
        for row in store.ro(ENDED):
            e = _edge(row)
            if e:
                e.update(until=iso_ts(row[8]), closed_by=row[9], ended_by=[i for i in row[10] if i])
                ended.append(e)
    else:
        edges, ended = [e for e in map(_edge, store.ro(AS_OF, {"t": as_of.end})) if e], []

    edges = [e for e in edges if e["source"] in nodes and e["target"] in nodes]
    ended = [e for e in ended if e["source"] in nodes and e["target"] in nodes]
    _teams(nodes, edges + ended)
    leaving = [{"person": n["id"], "notice_at": n.get("notice_at"), "last_day": n.get("last_day"),
                "notice_source": n.get("notice_source")} for n in nodes.values() if n.get("last_day")]
    order = {label.lower(): i for i, label in enumerate(LABELS)}
    return {
        "tenant": store.tenant,
        "as_of": as_of.label if as_of else None,
        "nodes": sorted(nodes.values(), key=lambda n: (order[n["kind"]], n["id"])),
        "edges": sorted(edges, key=lambda e: (e["type"], e["source"], e["target"])),
        "ended": sorted(ended, key=lambda e: (e["type"], e["source"], e["target"])),
        "leaving": sorted(leaving, key=lambda r: r["person"]),
        "trace": [{"query": t.query, "ms": t.ms, "rows": t.rows} for t in store.trace[first:]],
    }


EPISODE = "MATCH (e:Episode {id: $id}) RETURN e.id, e.ts, e.channel, e.author, e.title, e.text"
WITHHELD = "[withheld: looks like personal data]"


def episode(store: Store, episode_id: str) -> dict | None:
    rows = store.ro(EPISODE, {"id": episode_id})
    if not rows:
        return None
    from .gate import detect_pii

    eid, ts, channel, author, title, text = rows[0]
    lines = [WITHHELD if detect_pii(line) else line for line in (text or "").splitlines()]
    return {"id": eid, "ts": iso_ts(ts), "channel": channel or "", "author": author or "", "title": title or "",
            "text": "\n".join(lines)}
