"""The tool catalogue: what an agent may ask the memory.

Each tool is a typed signature plus human-written, parameterised Cypher. A model picks a tool and
fills arguments; it never writes a query. Every argument that names something is resolved against
the graph first (case-insensitive exact match); a name that does not resolve returns
`unknown_entity` and the main query never runs. An empty traversal returns `not_in_graph`, which
is not the same as "nothing is affected". Paths come back from the query that produced the rows.

Reads use store.ro. Writes (remember, handoff) go through the write gate (honest_memory.gate),
imported lazily, so the read tools work without it.

FalkorDB 4.22 and 6.0.2 both reject `*1..$depth`; the depth is a validated 1..4 integer formatted
into the pattern, never a string from the model.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from . import answer as A
from .answer import Answer, Receipt, Source, ToolTrace, iso_ts
from .schema import ENTITY_KEYS, FORBIDDEN_PREDICATES, PREDICATES, RECORD_KEYS
from .store import Store, epoch

KEYS = {**ENTITY_KEYS, **RECORD_KEYS}
ANY_ENTITY = ("System", "Person", "Team", "Decision", "Project", "Agent")
PREDICATE_NAMES = tuple(PREDICATES)
MAX_DEPTH = 4
MAX_SOURCES = 12
FACT_TYPES = "|".join(PREDICATES)               # relationship types of current facts, from the schema
FACT_TYPE_LIST = "[" + ", ".join(f"'{p}'" for p in PREDICATES) + "]"
GATE_OK = ("ACCEPT", "CONFIRM", "SUPERSEDE")
AGENT_ID = re.compile(r"^agent:[a-z0-9][a-z0-9_-]{0,63}$")


class ArgError(ValueError):
    """The call is malformed: wrong type, out of bounds, unknown argument."""


class GateUnavailable(RuntimeError):
    """A write tool was called but honest_memory.gate is not importable."""


# --- arguments -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Arg:
    name: str
    kind: str                       # entity | entities | date | int | text | enum | ids | bool
    description: str
    required: bool = True
    labels: tuple[str, ...] = ()    # entity kinds: the labels a name may resolve to
    low: int = 1
    high: int = 1000                # int bounds, or max length for text and max items for lists
    default: Any = None
    choices: tuple[str, ...] = ()

    def schema(self) -> dict:
        s: dict[str, Any]
        if self.kind == "entity":
            s = {"type": "string", "minLength": 1, "maxLength": 200,
                 "description": f"{self.description} Resolved against the graph ({'/'.join(self.labels)}); "
                                "an unknown name returns unknown_entity."}
        elif self.kind == "entities":
            s = {"type": "array", "minItems": 1, "maxItems": self.high,
                 "items": {"type": "string", "minLength": 1, "maxLength": 200},
                 "description": f"{self.description} Each is resolved against the graph ({'/'.join(self.labels)})."}
        elif self.kind == "date":
            s = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})?)?$",
                 "description": self.description}
        elif self.kind == "int":
            s = {"type": "integer", "minimum": self.low, "maximum": self.high, "description": self.description}
        elif self.kind == "enum":
            s = {"type": "string", "enum": list(self.choices), "description": self.description}
        elif self.kind == "bool":
            s = {"type": "boolean", "description": self.description}
        elif self.kind == "ids":
            s = {"type": "array", "maxItems": self.high, "items": {"type": "string", "minLength": 1, "maxLength": 64},
                 "description": self.description}
        else:
            s = {"type": "string", "minLength": self.low, "maxLength": self.high, "description": self.description}
        if self.default is not None:
            s["default"] = self.default
        return s


@dataclass
class Context:
    """Who is calling. Write tools stamp this identity; the model never chooses it."""
    agent: str = "agent:unknown"
    session: str = "session-0"
    at: str | None = None           # ISO timestamp for writes; now when None


@dataclass(frozen=True)
class Entity:
    label: str
    name: str                       # canonical value of the label's key property

    @property
    def key(self) -> str:
        return KEYS[self.label]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: tuple[Arg, ...]
    fn: Callable[[Store, dict, Context], Answer]
    writes: bool = False
    write_flag: str | None = None   # a boolean argument that turns this read tool into a write (handoff_plan.propose)

    def writes_with(self, args: dict | None) -> bool:
        """Whether a call with these arguments writes: always for a write tool, else when its write flag is set."""
        return self.writes or bool(self.write_flag and (args or {}).get(self.write_flag) in (True, "true", "True", 1))

    def parameters(self) -> dict:
        return {"type": "object", "additionalProperties": False,
                "properties": {a.name: a.schema() for a in self.args},
                "required": [a.name for a in self.args if a.required]}

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters(),
                "writes": self.writes}

    def openai(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters()}}


# --- time ----------------------------------------------------------------------------------

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class When:
    start: int
    end: int
    label: str


def parse_when(value: str) -> When:
    """'2026-09-22' covers that whole UTC day; a datetime is one instant."""
    value = value.strip()
    try:
        if _DATE_ONLY.match(value):
            start = epoch(value + "T00:00:00Z")
            return When(start, start + 86399, value)
        t = epoch(value.replace(" ", "T"))
        return When(t, t, value)
    except ValueError as exc:
        raise ArgError(f"not a date or ISO datetime: {value!r}") from exc


def _epoch_of(value) -> int:
    """A stored timestamp (epoch seconds or ISO text) as epoch seconds; 0 when missing."""
    if value is None or value == "":
        return 0
    return int(value) if isinstance(value, (int, float)) else epoch(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- resolving names -----------------------------------------------------------------------

def resolve(store: Store, name: str, labels: tuple[str, ...]) -> Entity | None:
    """Exact match first (uses the index), then case-insensitive; the first label wins a tie."""
    for exact in (True, False):
        subs = []
        for label in labels:
            key = KEYS[label]
            cond = f"n.{key} = $n" if exact else f"toLower(n.{key}) = $n"
            subs.append(f"MATCH (n:{label}) WHERE {cond} RETURN '{label}' AS label, n.{key} AS name")
        rows = store.ro(" UNION ".join(subs), {"n": name if exact else name.lower()})
        if rows:
            label, canonical = min(rows, key=lambda r: labels.index(r[0]))
            return Entity(label, canonical)
    return None


def entity_names(store: Store) -> dict[str, list[str]]:
    """Every entity name in the graph by label, for routing and for the sentence check."""
    subs = [f"MATCH (n:{label}) RETURN '{label}' AS label, n.{key} AS name" for label, key in ENTITY_KEYS.items()]
    out: dict[str, list[str]] = {label: [] for label in ENTITY_KEYS}
    for label, name in store.ro(" UNION ".join(subs)):
        if name is not None:
            out[label].append(name)
    return out


def all_names(store: Store) -> set[str]:
    return {n for names in entity_names(store).values() for n in names}


def _unknown(store: Store, tool: str, args: dict, arg: Arg, value: str) -> Answer:
    kind = arg.labels[0].lower() if len(arg.labels) == 1 else "entity"
    note = f"There is no {kind} named '{value}' in this memory, so no query was run."
    subs = [f"MATCH (n:{label}) RETURN n.{KEYS[label]} AS name" for label in arg.labels]
    names = [r[0] for r in store.ro(" UNION ".join(subs)) if r[0]]
    close = difflib.get_close_matches(value, names, n=3, cutoff=0.75)
    if close:
        note += f" Closest names: {', '.join(close)} (a hint only; nothing was assumed)."
    return Answer(A.UNKNOWN_ENTITY, tool, args, note=note, sentence=note)


# --- evidence helpers ----------------------------------------------------------------------

def _sources(store: Store, ids: list[str]) -> list[Source]:
    ids = list(dict.fromkeys(i for i in ids if i))[:MAX_SOURCES]
    if not ids:
        return []
    rows = store.ro("UNWIND $ids AS i MATCH (e:Episode {id: i}) RETURN e.id, e.title, e.ts, e.channel, e.author",
                    {"ids": ids})
    found = {r[0]: Source(r[0], r[1] or "", iso_ts(r[2]), r[3] or "", r[4] or "") for r in rows}
    return [found[i] for i in ids if i in found]


def _redact(reasons: list[str], predicate: str | None) -> bool:
    return "pii" in reasons or predicate in FORBIDDEN_PREDICATES


def _receipt(r: list) -> Receipt:
    gid, outcome, reasons, ts, pred, subj, obj, cites, claim = r
    reasons = reasons or []
    if _redact(reasons, pred):  # the memory must not echo the personal data it refused to store
        subj, obj = "[personal data]", "[personal data]"
    return Receipt(gid, outcome or "", reasons, iso_ts(ts), pred or "", subj or "", obj or "",
                   sorted(c for c in cites if c), claim or "")


_RECEIPT_COLUMNS = ("g.id, g.outcome, g.reasons, g.ts, g.predicate, g.subject, g.object, "
                    "collect(DISTINCT e.id), g.claim_episode")


def _receipts(store: Store, ids: list[str]) -> list[Receipt]:
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:
        return []
    rows = store.ro("UNWIND $ids AS i MATCH (g:GateDecision {id: i}) OPTIONAL MATCH (g)-[:CITES]->(e:Episode) "
                    f"RETURN {_RECEIPT_COLUMNS}", {"ids": ids})
    return [_receipt(r) for r in rows]


def _hops(names: list, types: list, starts: list, ids: list) -> list[str]:
    """Rebuild a path from nodes(path)/relationships(path); '<-' marks a hop walked backwards."""
    path = [names[0]]
    for i, t in enumerate(types):
        path += [t if starts[i] == ids[i] else "<-" + t, names[i + 1]]
    return path


def _fixed(names: list, types: list, arrows: str) -> list[str]:
    """Same, for a pattern whose directions are known: arrows is '>' or '<' per hop."""
    path = [names[0]]
    for t, arrow, name in zip(types, arrows, names[1:]):
        path += [t if arrow == ">" else "<-" + t, name]
    return path


def _finish(ans: Answer) -> Answer:
    ans.sentence = A.template_sentence(ans)
    return ans


def _not_in_graph(tool: str, args: dict, note: str, sources: list[Source] | None = None) -> Answer:
    return _finish(Answer(A.NOT_IN_GRAPH, tool, args, sources=sources or [], note=note))


# --- read tools ----------------------------------------------------------------------------

def current_owner(store: Store, a: dict, ctx: Context) -> Answer:
    system = a["system"].name
    rows = store.ro(
        "MATCH (p:Person)-[r:OWNS]->(s:System {name: $system}) "
        "OPTIONAL MATCH (p)-[:MEMBER_OF]->(t:Team) "
        "OPTIONAL MATCH prov = (s)<-[:ABOUT]-(g:GateDecision)-[:CITES]->(e:Episode) "
        "WHERE g.predicate = 'OWNS' AND g.subject = p.name AND g.outcome IN $ok AND e.id IN r.source_ids "
        "RETURN p.name, t.name, r.valid_at, r.source_ids, r.gate_id, "
        "[n IN nodes(prov) | coalesce(n.name, n.id)], [x IN relationships(prov) | type(x)] "
        "ORDER BY e.id, g.ts", {"system": system, "ok": list(GATE_OK)})
    args = {"system": system}
    if not rows:
        return _no_current_owner(store, system, args)
    owner, team, since, source_ids, gate_id = rows[0][:5]
    prev = store.ro(
        "MATCH (q:Person)-[h:WAS]->(s:System {name: $system}) WHERE h.predicate = 'OWNS' "
        "OPTIONAL MATCH (q)-[:MEMBER_OF]->(t:Team) "
        "RETURN q.name, t.name, h.invalid_at ORDER BY h.invalid_at DESC LIMIT 1", {"system": system})
    row = {"owner": owner, "team": team, "system": system, "since": iso_ts(since), "previous_owner": None,
           "previous_team": None, "previous_until": None, "source_ids": source_ids or [], "gate_id": gate_id}
    if prev:
        row.update(previous_owner=prev[0][0], previous_team=prev[0][1], previous_until=iso_ts(prev[0][2]))
    fact = [owner, "OWNS", system]
    prov = [r for r in rows if r[5]]
    paths = [fact + _fixed(r[5], r[6], "<>")[1:] for r in prov] or [fact]
    # the receipt that wrote the fact, then the ones that confirmed it with further sources
    receipts = _receipts(store, [gate_id] + [r[5][1] for r in prov])
    return _finish(Answer(A.ANSWERED_WITH_PATH, "current_owner", args, [row], paths,
                          _sources(store, row["source_ids"]), receipts))


# The most recent closed ownership of a system and the receipt that closed it (a SUPERSEDE or a CLOSE).
_LAST_OWNER = (
    "MATCH (q:Person)-[h:WAS]->(s:System {name: $system}) WHERE h.predicate = 'OWNS' "
    "OPTIONAL MATCH (g:GateDecision {id: h.closed_by}) "
    "RETURN q.name, h.valid_at, h.invalid_at, h.source_ids, h.closed_by, g.outcome "
    "ORDER BY h.invalid_at DESC LIMIT 1")


def _no_current_owner(store: Store, system: str, args: dict) -> Answer:
    """No current OWNS edge. If history holds one, say who owned it last, until when, and which receipt ended it,
    instead of guessing a successor."""
    last = store.ro(_LAST_OWNER, {"system": system})
    if not last:
        return _not_in_graph("current_owner", args, f"{system} exists, but the graph records no current owner "
                             "for it. That may mean ownership is not modelled, not that nobody owns it.")
    who, since, until, source_ids, closed_by, outcome = last[0]
    receipts = _receipts(store, [closed_by])
    ended_by = receipts[0].cites if receipts else []
    how = (f"closed by {closed_by}" + (f", citing {', '.join(ended_by)}" if ended_by else "")) if closed_by else "closed"
    note = (f"{system} has no current owner. Last owner: {who} until {iso_ts(until)[:10]} ({how}). "
            "Nobody has been recorded as taking it over.")
    row = {"owner": None, "system": system, "last_owner": who, "last_since": iso_ts(since), "last_until": iso_ts(until),
           "closed_by": closed_by, "closed_outcome": outcome, "ended_by": ended_by, "source_ids": source_ids or []}
    return _finish(Answer(A.NOT_IN_GRAPH, "current_owner", args, [row], [[who, "WAS", system]],
                          _sources(store, list(source_ids or []) + ended_by), receipts, note=note))


_OWNERS_AT = (
    "MATCH (p:Person)-[r:OWNS]->(s:System {name: $system}) WHERE r.valid_at <= $end "
    "RETURN p.name AS owner, s.name AS system, r.valid_at AS v, null AS inv, r.source_ids AS src, "
    "r.gate_id AS gate, type(r) AS edge, null AS closed_by "
    "UNION "
    "MATCH (p:Person)-[r:WAS]->(s:System {name: $system}) "
    "WHERE r.predicate = 'OWNS' AND r.valid_at <= $end AND r.invalid_at > $start "
    "RETURN p.name AS owner, s.name AS system, r.valid_at AS v, r.invalid_at AS inv, r.source_ids AS src, "
    "r.gate_id AS gate, type(r) AS edge, r.closed_by AS closed_by")


def _owner_rows(rows: list[list]) -> tuple[list[dict], list[list[str]]]:
    rows = sorted(rows, key=lambda r: r[2])
    out = [{"owner": r[0], "system": r[1], "from": iso_ts(r[2]), "to": iso_ts(r[3]) or None,
            "state": "current" if r[3] is None else "closed", "source_ids": r[4] or [], "gate_id": r[5],
            "closed_by": r[7]} for r in rows]
    return out, [[r[0], r[6], r[1]] for r in rows]


def owner_at(store: Store, a: dict, ctx: Context) -> Answer:
    system, when = a["system"].name, a["date"]
    args = {"system": system, "date": when.label}
    rows = store.ro(_OWNERS_AT, {"system": system, "start": when.start, "end": when.end})
    if not rows:
        return _not_in_graph("owner_at", args, f"{system} exists, but the graph records no owner for it on "
                             f"{when.label}; the earliest recorded ownership may start later.")
    out, paths = _owner_rows(rows)
    ids = [i for r in out for i in r["source_ids"]]
    return _finish(Answer(A.ANSWERED_WITH_PATH, "owner_at", args, out, paths, _sources(store, ids)))


def history(store: Store, a: dict, ctx: Context) -> Answer:
    system = a["system"].name
    args = {"system": system}
    rows = store.ro(_OWNERS_AT, {"system": system, "start": 0, "end": 2 ** 40})
    if not rows:
        return _not_in_graph("history", args, f"{system} exists, but the graph records no owner for it at any time.")
    out, paths = _owner_rows(rows)
    ids = [i for r in out for i in r["source_ids"]]
    return _finish(Answer(A.ANSWERED_WITH_PATH, "history", args, out, paths, _sources(store, ids)))


def escalation_path(store: Store, a: dict, ctx: Context) -> Answer:
    system = a["system"].name
    args = {"system": system}
    rows = store.ro(
        "MATCH (s:System {name: $system})<-[:OWNS]-(o:Person) "
        "OPTIONAL MATCH full = (s)<-[:OWNS]-(o)-[:MEMBER_OF]->(t:Team)<-[:LEADS]-(l:Person) "
        "OPTIONAL MATCH part = (s)<-[:OWNS]-(o)-[:MEMBER_OF]->(t2:Team) "
        "RETURN o.name, [n IN nodes(full) | n.name], [x IN relationships(full) | type(x)], "
        "[n IN nodes(part) | n.name], [x IN relationships(part) | type(x)]", {"system": system})
    if not rows:
        return _not_in_graph("escalation_path", args, f"{system} exists, but the graph records no owner to call.")
    owner, full_n, full_t, part_n, part_t = rows[0]
    if full_n:
        path, row = _fixed(full_n, full_t, "<><"), {"system": system, "owner": owner, "team": full_n[2], "lead": full_n[3]}
    elif part_n:
        path, row = _fixed(part_n, part_t, "<>"), {"system": system, "owner": owner, "team": part_n[2], "lead": None}
    else:
        path, row = [system, "<-OWNS", owner], {"system": system, "owner": owner, "team": None, "lead": None}
    return _finish(Answer(A.ANSWERED_WITH_PATH, "escalation_path", args, [row], [path]))


_IMPACT = (
    "MATCH path = (u:System)-[:DEPENDS_ON*1..{depth}]->(s:System {{name: $system}}) "
    "WITH u, path ORDER BY length(path) "
    "WITH u, collect(path)[0] AS best "
    "OPTIONAL MATCH (o:Person)-[ro:OWNS]->(u) "
    "OPTIONAL MATCH (o)-[:MEMBER_OF]->(t:Team) "
    "RETURN u.name, length(best), [n IN nodes(best) | n.name], [x IN relationships(best) | type(x)], o.name, t.name, "
    "[x IN relationships(best) | x.source_ids], ro.source_ids "
    "ORDER BY length(best), u.name LIMIT 200")

_DEPENDENCY_DOCS = "MATCH ()-[r:DEPENDS_ON]->() UNWIND r.source_ids AS sid RETURN DISTINCT sid ORDER BY sid LIMIT 6"


_IMPACT_PATHS = (
    "MATCH path = (u:System)-[:DEPENDS_ON*1..{depth}]->(s:System {{name: $system}}) "
    "RETURN u.name, length(path), [n IN nodes(path) | n.name], [x IN relationships(path) | type(x)], "
    "[x IN relationships(path) | x.valid_at], [x IN relationships(path) | x.source_ids] "
    "ORDER BY length(path), u.name LIMIT 1000")

_OWNERS_AS_OF = (
    "UNWIND $names AS n MATCH (p:Person)-[r:OWNS]->(s:System {name: n}) WHERE r.valid_at <= $t "
    "RETURN n AS system, p.name AS owner, r.valid_at AS v, r.source_ids AS src "
    "UNION "
    "UNWIND $names AS n MATCH (p:Person)-[r:WAS]->(s:System {name: n}) "
    "WHERE r.predicate = 'OWNS' AND r.valid_at <= $t AND r.invalid_at > $t "
    "RETURN n AS system, p.name AS owner, r.valid_at AS v, r.source_ids AS src")


def _impact_as_of(store: Store, system: str, depth: int, when: When) -> list[list]:
    """Dependents as of a date, in the shape of the present-day query: only edges that already existed count,
    the shortest such path is kept per dependent, and owners are those of that date. Filtering happens here
    because FalkorDB 6.0.x does not take an all(...) filter over a variable-length relationship."""
    best: dict[str, list] = {}
    for name, hops, names, types, valid, edge_src in store.ro(_IMPACT_PATHS.format(depth=depth), {"system": system}):
        if name not in best and all(v is not None and v <= when.end for v in valid):
            best[name] = [name, hops, names, types]
            best[name].append(edge_src)
    if not best:
        return []
    held = {r[0]: r for r in sorted(store.ro(_OWNERS_AS_OF, {"names": list(best), "t": when.end}), key=lambda r: r[2])}
    team = dict(store.ro("UNWIND $owners AS o MATCH (p:Person {name: o}) OPTIONAL MATCH (p)-[:MEMBER_OF]->(t:Team) "
                         "RETURN o, t.name", {"owners": sorted({r[1] for r in held.values()})}))
    out = []
    for name, hops, names, types, edge_src in best.values():
        owner = held.get(name)
        out.append([name, hops, names, types, owner[1] if owner else None, team.get(owner[1]) if owner else None,
                    edge_src, owner[3] if owner else None])
    return out


def impact(store: Store, a: dict, ctx: Context) -> Answer:
    system, depth, when = a["system"].name, a["depth"], a.get("as_of")
    args = {"system": system, "depth": depth, **({"as_of": when.label} if when else {})}
    rows = (_impact_as_of(store, system, depth, when) if when
            else store.ro(_IMPACT.format(depth=depth), {"system": system}))
    if not rows:
        docs = [r[0] for r in store.ro(_DEPENDENCY_DOCS)]
        basis = f" The dependencies the graph holds come from {', '.join(docs)}." if docs else ""
        return _not_in_graph("impact", args, f"No system depends on {system} within {depth} hop"
                             f"{'s' if depth != 1 else ''}{f' as of {when.label}' if when else ''} in the graph. "
                             f"That means no dependency is recorded, which is not the same as nothing breaking.{basis}",
                             _sources(store, docs))
    out = [{"system": r[0], "hops": r[1], "owner": r[4], "owner_team": r[5],
            "source_ids": list(dict.fromkeys([i for lst in r[6] for i in (lst or [])] + list(r[7] or [])))} for r in rows]
    paths = [_fixed(r[2], r[3], ">" * len(r[3])) for r in rows]
    return _finish(Answer(A.ANSWERED_WITH_PATH, "impact", args, out, paths,
                          _sources(store, [i for r in out for i in r["source_ids"]])))


def who_knows(store: Store, a: dict, ctx: Context) -> Answer:
    system, limit = a["system"].name, a["limit"]
    args = {"system": system, "limit": limit}
    rows = store.ro(
        "MATCH (p:Person)-[w:WORKED_ON]->(s:System {name: $system}) "
        "WITH p, w, coalesce(w.count, size(w.source_ids)) AS n "
        "RETURN p.name, n, w.valid_at, w.source_ids ORDER BY n DESC, p.name LIMIT $limit",
        {"system": system, "limit": limit})
    if not rows:
        return _not_in_graph("who_knows", args, f"{system} exists, but the graph records no one working on it.")
    out = [{"person": r[0], "count": r[1], "rank": i, "since": iso_ts(r[2]), "source_ids": r[3] or []}
           for i, r in enumerate(rows, 1)]
    paths = [[r["person"], "WORKED_ON", system] for r in out]
    return _finish(Answer(A.ANSWERED_WITH_PATH, "who_knows", args, out, paths, _sources(store, out[0]["source_ids"])))


_TRAIL = (
    "MATCH (d:Decision {id: $decision}) "
    "OPTIONAL MATCH newer = (n:Decision)-[:SUPERSEDES*1..6]->(d) "
    "OPTIONAL MATCH older = (d)-[:SUPERSEDES*1..6]->(o:Decision) "
    "RETURN [x IN nodes(newer) | x.id], [x IN relationships(newer) | x.source_ids], "
    "[x IN nodes(older) | x.id], [x IN relationships(older) | x.source_ids]")

_DECISION_ROWS = (
    "UNWIND $ids AS i MATCH (d:Decision {id: i}) "
    "OPTIONAL MATCH (p:Person)-[dec:DECIDED]->(d) OPTIONAL MATCH (e:Episode {id: i}) "
    "RETURN d.id, d.status, p.name, e.title, e.ts, dec.source_ids")


_TRAIL_RANK = {"replaces": 0, "queried": 1, "replaced_by": 2}


def _trail_status(stored, relation: str, distance: int, newest: int) -> str:
    if stored:
        return stored
    if relation == "replaces":
        return "superseded"
    if relation == "queried":
        return "superseded" if newest else "accepted"
    return "accepted" if distance == newest else "superseded"


def decision_trail(store: Store, a: dict, ctx: Context) -> Answer:
    decision = a["decision"].name
    args = {"decision": decision}
    position: dict[str, tuple[str, int]] = {decision: ("queried", 0)}
    paths: list[list[str]] = []
    edge_sources: list[str] = []
    for newer_ids, newer_src, older_ids, older_src in store.ro(_TRAIL, {"decision": decision}):
        for i, did in enumerate(reversed(newer_ids or [])):
            position.setdefault(did, ("replaced_by", i))
        for i, did in enumerate(older_ids or []):
            position.setdefault(did, ("replaces", i))
        for ids, src in ((newer_ids, newer_src), (older_ids, older_src)):
            if ids and len(ids) > 1:
                path = [ids[0]] + [x for d in ids[1:] for x in ("SUPERSEDES", d)]
                if path not in paths:
                    paths.append(path)
                edge_sources += [s for lst in src for s in (lst or [])]
    newest = max((k for rel, k in position.values() if rel == "replaced_by"), default=0)
    details = {r[0]: r for r in store.ro(_DECISION_ROWS, {"ids": list(position)})}
    out = []
    for did, (rel, k) in position.items():
        _, status, who, title, ts, _src = details.get(did, (did, None, None, None, None, None))
        out.append({"decision": did, "title": title, "status": _trail_status(status, rel, k, newest),
                    "decided_by": who, "accepted_at": iso_ts(ts), "relation": rel, "distance": k})
    out.sort(key=lambda r: (_TRAIL_RANK[r["relation"]], -r["distance"] if r["relation"] == "replaces" else r["distance"]))
    decided = [[r["decided_by"], "DECIDED", r["decision"]] for r in out if r["decided_by"]]
    note = "" if paths else f"No supersession is recorded for {decision}."
    return _finish(Answer(A.ANSWERED_WITH_PATH, "decision_trail", args, out, paths + decided,
                          _sources(store, list(position) + edge_sources), note=note))


def _receipt_row(r: Receipt) -> dict:
    return {"kind": "receipt", "receipt": r.id, "outcome": r.outcome, "reasons": r.reasons, "at": r.ts,
            "subject": r.subject, "predicate": r.predicate, "object": r.object, "cited": r.cites,
            "claimed_in": r.claim or None}


_WHY_BY_NAME = (
    "MATCH (g:GateDecision) WHERE toLower(g.subject) = toLower($subject) "
    "AND ($predicate IS NULL OR g.predicate = $predicate) "
    "AND ($object IS NULL OR toLower(g.object) = toLower($object)) "
    "OPTIONAL MATCH (g)-[:CITES]->(e:Episode) RETURN " + _RECEIPT_COLUMNS + " ORDER BY g.ts")

_WHY_BY_EPISODE = (
    "MATCH (g:GateDecision)-[:CITES|CLAIMED_IN]->(c:Episode {id: $subject}) "
    "WHERE $predicate IS NULL OR g.predicate = $predicate "
    "OPTIONAL MATCH (g)-[:CITES]->(e:Episode) RETURN " + _RECEIPT_COLUMNS + " ORDER BY g.ts")


def _current_for(store: Store, predicate: str, subject: Entity, obj: Entity | None) -> list[dict]:
    """What the memory holds now for this slot: the current holder of a one-per-object predicate
    (who owns X), the current value of a one-per-subject one (which team is P in), else the pair."""
    p = PREDICATES[predicate]
    s_name = subject.name if subject.label == p.subject else None
    if obj is not None and obj.label == p.object:
        o_name = obj.name
    else:
        o_name = subject.name if subject.label == p.object else None
    if p.functional == "object" and o_name:
        s_name = None
    elif p.functional == "subject" and s_name:
        o_name = None
    if s_name is None and o_name is None:
        return []
    return store.current_facts(predicate, s_name, o_name)


def why(store: Store, a: dict, ctx: Context) -> Answer:
    subject, obj, predicate = a["subject"], a.get("object"), a.get("predicate")
    args = {"subject": subject.name, "predicate": predicate, "object": obj.name if obj else None}
    params = {"subject": subject.name, "predicate": predicate, "object": obj.name if obj else None}
    receipts = [_receipt(r) for r in store.ro(_WHY_BY_EPISODE if subject.label == "Episode" else _WHY_BY_NAME, params)]
    rows = [_receipt_row(r) for r in receipts]
    paths = [[r.id, "CITES", c] for r in receipts for c in r.cites]
    paths += [[r.id, "CLAIMED_IN", r.claim] for r in receipts if r.claim and r.claim not in r.cites]
    if predicate in PREDICATES and subject.label != "Episode":
        for f in _current_for(store, predicate, subject, obj):
            rows.append({"kind": "current", "subject": f["subject"], "predicate": predicate, "object": f["object"],
                         "since": iso_ts(f.get("valid_at")), "source_ids": f.get("source_ids") or []})
            paths.append([f["subject"], predicate, f["object"]])
    if not rows:
        return _not_in_graph("why", args, f"The memory holds no receipt and no current fact for "
                             f"{' '.join(str(x) for x in (subject.name, predicate, obj.name if obj else None) if x)}.")
    ids = ([c for r in receipts for c in r.cites + [r.claim]]
           + [i for r in rows if r["kind"] == "current" for i in r["source_ids"]])
    return _finish(Answer(A.ANSWERED_WITH_PATH, "why", args, rows, paths, _sources(store, ids), receipts))


def what_changed(store: Store, a: dict, ctx: Context) -> Answer:
    when, predicate, limit = a["since"], a.get("predicate"), a["limit"]
    args = {"since": when.label, "predicate": predicate, "limit": limit}
    types = predicate or FACT_TYPES
    params = {"t": when.end, "pred": predicate, "limit": limit}
    rows: list[dict] = []
    paths: list[list[str]] = []
    for s, ty, o, at, src, gate in store.ro(
            f"MATCH (s)-[r:{types}]->(o) WHERE r.valid_at > $t "
            "RETURN coalesce(s.name, s.id), type(r), coalesce(o.name, o.id), r.valid_at, r.source_ids, r.gate_id "
            "ORDER BY r.valid_at LIMIT $limit", params):
        rows.append({"change": "learned", "predicate": ty, "subject": s, "object": o, "at": iso_ts(at),
                     "source_ids": src or [], "gate_id": gate})
        paths.append([s, ty, o])
    closers = []
    for s, ty, o, at, src, gate in store.ro(
            "MATCH (s)-[h:WAS]->(o) WHERE h.invalid_at > $t AND ($pred IS NULL OR h.predicate = $pred) "
            "RETURN coalesce(s.name, s.id), h.predicate, coalesce(o.name, o.id), h.invalid_at, h.source_ids, "
            "h.closed_by ORDER BY h.invalid_at LIMIT $limit", params):
        rows.append({"change": "closed", "predicate": ty, "subject": s, "object": o, "at": iso_ts(at),
                     "source_ids": src or [], "gate_id": gate})
        paths.append([s, "WAS", o])
        closers.append(gate)
    closing = {r.id: r for r in _receipts(store, closers)}
    for r in rows:
        if r["change"] == "closed" and r["gate_id"] in closing:  # why it was closed: the receipt and its sources
            r["closed_by"] = closing[r["gate_id"]].outcome
            r["source_ids"] = list(dict.fromkeys(r["source_ids"] + closing[r["gate_id"]].cites))
    refused = [_receipt(r) for r in store.ro(
        "MATCH (g:GateDecision) WHERE g.outcome = 'REJECT' AND g.ts > $t AND ($pred IS NULL OR g.predicate = $pred) "
        "OPTIONAL MATCH (g)-[:CITES]->(e:Episode) RETURN " + _RECEIPT_COLUMNS + " ORDER BY g.ts LIMIT $limit", params)]
    refused_rows = [{"change": "refused", "predicate": r.predicate, "subject": r.subject, "object": r.object,
                     "at": r.ts, "source_ids": r.cites, "gate_id": r.id, "reasons": r.reasons,
                     "claimed_in": r.claim or None} for r in refused]
    rows.sort(key=lambda r: r["at"])
    if not rows:
        what = f"{predicate} " if predicate else ""
        note = f"The memory records no {what}change since {when.label}."
        if refused_rows:
            claims = "; ".join(f"{r['subject']} {r['predicate']} {r['object']} ({', '.join(r['reasons']) or 'refused'})"
                               for r in refused_rows[:3])
            note += f" {len(refused_rows)} claim{'s were' if len(refused_rows) != 1 else ' was'} refused, not stored: {claims}."
        claims = [r["claimed_in"] for r in refused_rows if r["claimed_in"]]
        ans = Answer(A.NOT_IN_GRAPH, "what_changed", args, refused_rows, [], _sources(store, claims), refused, note=note)
        return _finish(ans)
    rows += refused_rows
    ids = [i for r in rows for i in r["source_ids"] + [r.get("claimed_in")]]
    closed_receipts = [closing[g] for g in dict.fromkeys(closers) if g in closing]
    return _finish(Answer(A.ANSWERED_WITH_PATH, "what_changed", args, rows, paths, _sources(store, ids),
                          refused + closed_receipts))


def path_between(store: Store, a: dict, ctx: Context) -> Answer:
    x, y, max_hops = a["a"], a["b"], a["max_hops"]
    if x == y:
        raise ArgError("a and b must be different entities")
    args = {"a": x.name, "b": y.name, "max_hops": max_hops}
    rows = store.ro(
        f"MATCH (a:{x.label} {{{x.key}: $a}}), (b:{y.label} {{{y.key}: $b}}) "
        f"CALL algo.SPpaths({{sourceNode: a, targetNode: b, relTypes: {FACT_TYPE_LIST}, relDirection: 'both', "
        "pathCount: 1, maxLen: $max}) YIELD path, pathWeight "
        "RETURN [n IN nodes(path) | coalesce(n.name, n.id)], [x IN relationships(path) | type(x)], "
        "[x IN relationships(path) | id(startNode(x))], [n IN nodes(path) | id(n)]",
        {"a": x.name, "b": y.name, "max": max_hops})
    if not rows:
        return _not_in_graph("path_between", args, f"No path of up to {max_hops} hops joins {x.name} and {y.name} "
                             "over current facts. That may mean the connection is not modelled.")
    path = _hops(*rows[0])
    row = {"from": x.name, "to": y.name, "hops": len(rows[0][1])}
    return _finish(Answer(A.ANSWERED_WITH_PATH, "path_between", args, [row], [path]))


_FINDINGS = (
    "MATCH (f:Finding)-[:ABOUT]->(n:{label} {{{key}: $k}}) "
    "OPTIONAL MATCH (w:Agent)-[:WROTE]->(f) OPTIONAL MATCH (f)-[:DERIVED_FROM]->(e) "
    "RETURN f.id, f.text, f.ts, f.session, w.name, collect(e.id) ORDER BY f.ts DESC LIMIT $limit")

# When each fact about the entity was closed; counted in Python, because FalkorDB 6.0.2 loses the
# match of an OPTIONAL MATCH ... WHERE h.invalid_at > f.ts that follows an aggregating WITH.
_CLOSED = "MATCH (x)-[h:WAS]-(n:{label} {{{key}: $k}}) RETURN h.invalid_at"

_HANDOFFS = (
    "MATCH (h:Handoff) OPTIONAL MATCH (h)-[:FROM]->(f) OPTIONAL MATCH (h)-[:TO]->(t) "
    "OPTIONAL MATCH (t)-[:MEMBER_OF]->(tt:Team) OPTIONAL MATCH (f)-[:MEMBER_OF]->(ft:Team) "
    "OPTIONAL MATCH (h)-[:CITES]->(e:Episode) "
    "WITH h, coalesce(f.name, f.id) AS sender, coalesce(t.name, t.id) AS receiver, tt.name AS team, "
    "ft.name AS sender_team, collect(e.id) AS cites "
    "WHERE sender = $k OR receiver = $k OR team = $k OR sender_team = $k "
    "OR toLower(h.topic) CONTAINS toLower($k) OR toLower(h.note) CONTAINS toLower($k) "
    "RETURN h.id, h.topic, h.note, h.ts, sender, receiver, team, cites ORDER BY h.ts DESC LIMIT $limit")

_HANDOFFS_VIA_EPISODE = (
    "MATCH (h:Handoff)-[:CITES]->(:Episode)-[:MENTIONS]->(n:{label} {{{key}: $k}}) "
    "OPTIONAL MATCH (h)-[:FROM]->(f) OPTIONAL MATCH (h)-[:TO]->(t) OPTIONAL MATCH (t)-[:MEMBER_OF]->(tt:Team) "
    "OPTIONAL MATCH (h)-[:CITES]->(e:Episode) "
    "WITH DISTINCT h, coalesce(f.name, f.id) AS sender, coalesce(t.name, t.id) AS receiver, tt.name AS team, "
    "collect(e.id) AS cites "
    "RETURN h.id, h.topic, h.note, h.ts, sender, receiver, team, cites ORDER BY h.ts DESC LIMIT $limit")


def recall(store: Store, a: dict, ctx: Context) -> Answer:
    ent, limit = a["entity"], a["limit"]
    args = {"entity": ent.name, "limit": limit}
    params = {"k": ent.name, "limit": limit}
    rows: list[dict] = []
    paths: list[list[str]] = []
    findings = store.ro(_FINDINGS.format(label=ent.label, key=ent.key), params)
    closed_at = [_epoch_of(r[0]) for r in store.ro(_CLOSED.format(label=ent.label, key=ent.key), params)] if findings else []
    for fid, text, ts, session, agent, src in findings:
        rows.append({"kind": "finding", "id": fid, "text": text, "at": iso_ts(ts), "agent": agent,
                     "session": session, "source_ids": src,
                     "closed_since": sum(1 for c in closed_at if c > _epoch_of(ts))})
        head = [agent, "WROTE", fid] if agent else [fid]
        paths += [head + ["ABOUT", ent.name] + (["DERIVED_FROM", s] if s else []) for s in (src or [None])]
    seen: set[str] = set()
    handoffs = store.ro(_HANDOFFS, params) + store.ro(_HANDOFFS_VIA_EPISODE.format(label=ent.label, key=ent.key), params)
    for hid, topic, note, ts, frm, to, to_team, src in handoffs:
        if hid in seen:
            continue
        seen.add(hid)
        rows.append({"kind": "handoff", "id": hid, "topic": topic, "note": note, "at": iso_ts(ts), "from": frm,
                     "to": to, "to_team": to_team, "source_ids": src})
        paths += [[hid, "CITES", s] for s in src]
    if not rows:
        return _not_in_graph("recall", args, f"No agent has recorded a finding or a handoff about {ent.name}.")
    rows.sort(key=lambda r: r["at"], reverse=True)
    ids = [i for r in rows for i in r["source_ids"]]
    return _finish(Answer(A.ANSWERED_FROM_MEMORY, "recall", args, rows[:limit], paths, _sources(store, ids)))


def facts_about(store: Store, a: dict, ctx: Context) -> Answer:
    ent, predicate = a["entity"], a.get("predicate")
    args = {"entity": ent.name, "predicate": predicate}
    rows = store.ro(
        f"MATCH (n:{ent.label} {{{ent.key}: $k}})-[r:{predicate or FACT_TYPES}]-(m) "
        "RETURN type(r), startNode(r) = n, coalesce(m.name, m.id), r.valid_at, r.source_ids "
        "ORDER BY type(r), r.valid_at, coalesce(m.name, m.id) LIMIT 100", {"k": ent.name})
    if not rows:
        what = f"{predicate} fact" if predicate else "current fact"
        return _not_in_graph("facts_about", args, f"{ent.name} exists, but the graph holds no {what} about it.")
    out = [{"entity": ent.name, "predicate": r[0], "direction": "out" if r[1] else "in", "other": r[2],
            "since": iso_ts(r[3]), "source_ids": r[4] or []} for r in rows]
    paths = [[ent.name, r["predicate"] if r["direction"] == "out" else "<-" + r["predicate"], r["other"]] for r in out]
    ids = [i for r in out for i in r["source_ids"]]
    return _finish(Answer(A.ANSWERED_WITH_PATH, "facts_about", args, out, paths, _sources(store, ids)))


# --- write tools (through the write gate) --------------------------------------------------

def _gate():
    try:
        from . import gate
    except ImportError as exc:
        raise GateUnavailable("write tools need honest_memory.gate, which is not installed") from exc
    return gate


def get_extractor():
    """The extractor the API and the example hand to ingest_episode (rules; the gate decides)."""
    try:
        from . import extract
    except ImportError as exc:
        raise GateUnavailable("ingest needs honest_memory.extract, which is not installed") from exc
    for name in ("default_extractor", "RuleExtractor", "RulesExtractor", "Extractor"):
        if hasattr(extract, name):
            return getattr(extract, name)()
    raise GateUnavailable("honest_memory.extract exposes no default_extractor()")


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


def _write_answer(store: Store, tool: str, args: dict, result, source_ids: list[str]) -> Answer:
    outcome = str(getattr(result.outcome, "value", result.outcome)).upper()
    reasons = [str(getattr(r, "value", r)) for r in (result.reasons or [])]
    if outcome == "REJECT" and "pii" in reasons:  # do not hand the refused personal data back in the answer
        args = {k: "[redacted: personal data]" if k in ("text", "topic", "note") and v else v for k, v in args.items()}
    receipts = _receipts(store, [result.receipt_id]) or [Receipt(result.receipt_id, outcome, reasons)]
    note = f"Refused: {', '.join(reasons)}." if outcome == "REJECT" else ""
    rows = [{"written": _jsonable(result.written), "closed": _jsonable(result.closed),
             "record_id": getattr(result, "record_id", None)}]
    return _finish(Answer(A.GATE_OUTCOMES[outcome], tool, args, rows, [], _sources(store, source_ids), receipts, note=note))


def remember(store: Store, a: dict, ctx: Context) -> Answer:
    about = [e.name for e in a["about"]]
    args = {"text": a["text"], "about": about, "source_ids": a["source_ids"]}
    result = _gate().submit_finding(store, ctx.agent, ctx.session, a["text"], about, a["source_ids"],
                                    ctx.at or _now_iso())
    return _write_answer(store, "remember", args, result, a["source_ids"])


def handoff(store: Store, a: dict, ctx: Context) -> Answer:
    to = a["to"]
    args = {"to": to if isinstance(to, str) else to.name, "topic": a["topic"], "note": a.get("note") or "",
            "source_ids": a["source_ids"]}
    result = _gate().submit_handoff(store, from_=ctx.agent, to=args["to"], topic=a["topic"], note=args["note"],
                                    source_ids=a["source_ids"], at=ctx.at or _now_iso())
    return _write_answer(store, "handoff", args, result, a["source_ids"])


# --- the catalogue -------------------------------------------------------------------------

SYSTEM = Arg("system", "entity", "Name of the system, e.g. billing-api.", labels=("System",))
ID_LIST = "Episode ids (e.g. ADR-0009, msg-0148, TCK-0077) that support this."

TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("current_owner", "Who owns a system now, since when, who owned it before, and the path back to the "
         "documents that say so.", (SYSTEM,), current_owner),
    Tool("owner_at", "Who owned a system on a given date (a whole UTC day, or an instant if a time is given).",
         (SYSTEM, Arg("date", "date", "ISO date YYYY-MM-DD, or an ISO datetime for one instant.")), owner_at),
    Tool("history", "Every owner of a system in order, with start, end and the documents behind each change.",
         (SYSTEM,), history),
    Tool("escalation_path", "Whom to call about a system: its owner, the owner's team and the team lead.",
         (SYSTEM,), escalation_path),
    Tool("impact", "What depends on a system, up to a number of hops upstream, and who owns each dependent.",
         (SYSTEM, Arg("depth", "int", "How many dependency hops to follow (1-4).", required=False, low=1,
                      high=MAX_DEPTH, default=MAX_DEPTH),
          Arg("as_of", "date", "Only dependencies and owners that existed by this date (whole UTC day).",
              required=False)), impact),
    Tool("who_knows", "Who has worked on a system, ranked by how many work items they touched.",
         (SYSTEM, Arg("limit", "int", "How many people to return (1-50).", required=False, low=1, high=50,
                      default=10)), who_knows),
    Tool("decision_trail", "How a decision evolved: what it replaced, what replaced it, who decided each, and the "
         "documents behind it.", (Arg("decision", "entity", "Decision id, e.g. ADR-0004.", labels=("Decision",)),),
         decision_trail),
    Tool("why", "Why the memory believes or refused a fact: the gate receipts for it and what the memory holds now. "
         "The subject may be an entity or a record id (a ticket whose data was refused).",
         (Arg("subject", "entity", "Who or what the fact is about, or a record id such as TCK-0044.",
              labels=ANY_ENTITY + ("Episode",)),
          Arg("predicate", "enum", "Relationship, e.g. OWNS. Personal-data predicates show refusals.",
              required=False, choices=PREDICATE_NAMES + tuple(sorted(FORBIDDEN_PREDICATES))),
          Arg("object", "entity", "The other end of the fact.", required=False, labels=ANY_ENTITY)), why),
    Tool("what_changed", "What the memory learned, closed or refused since a date (strictly after the end of that "
         "day; give a datetime for an instant).",
         (Arg("since", "date", "ISO date YYYY-MM-DD or ISO datetime."),
          Arg("predicate", "enum", "Only this kind of fact: OWNS for ownership, DECIDED for accepted ADRs.",
              required=False, choices=PREDICATE_NAMES),
          Arg("limit", "int", "Most rows per kind (1-200).", required=False, low=1, high=200, default=50)),
         what_changed),
    Tool("path_between", "How two things are connected: the shortest path over current facts.",
         (Arg("a", "entity", "First entity.", labels=ANY_ENTITY), Arg("b", "entity", "Second entity.", labels=ANY_ENTITY),
          Arg("max_hops", "int", "Longest path to accept (1-8).", required=False, low=1, high=8, default=6)),
         path_between),
    Tool("recall", "What agents concluded or handed over before about an entity, with the dates.",
         (Arg("entity", "entity", "The entity to recall.", labels=ANY_ENTITY),
          Arg("limit", "int", "Most items to return (1-20).", required=False, low=1, high=20, default=5)), recall),
    Tool("facts_about", "Every current fact about an entity: team membership, ownership, decisions, work.",
         (Arg("entity", "entity", "The entity to describe.", labels=ANY_ENTITY),
          Arg("predicate", "enum", "Only this relationship, e.g. MEMBER_OF.", required=False,
              choices=PREDICATE_NAMES)), facts_about),
    Tool("remember", "Write a finding to the shared memory. The write gate decides whether it is kept.",
         (Arg("text", "text", "What was concluded, one or two sentences.", low=3, high=1000),
          Arg("about", "entities", "Entities the finding is about.", labels=ANY_ENTITY, high=5),
          Arg("source_ids", "ids", ID_LIST, required=False, high=10)), remember, writes=True),
    Tool("handoff", "Hand work to another agent or person. The write gate records it.",
         (Arg("to", "entity", "agent:<name>, or a person or team name.", labels=("Person", "Team", "Agent")),
          Arg("topic", "text", "What is being handed over.", low=3, high=200),
          Arg("note", "text", "Context for the receiver.", required=False, low=0, high=1000),
          Arg("source_ids", "ids", ID_LIST, required=False, high=10)), handoff, writes=True),
)}


# N5, the departure tools (what_leaves, single_source, without, bridges, successors, handoff_plan, promises),
# live in departure.py, which adds them to TOOLS when it loads. Imported here, after everything it uses from this
# module exists, so importing either module first gives the same catalogue.
from . import departure as _departure  # noqa: E402,F401


def tool_schemas() -> list[dict]:
    """JSON description of every tool, for MCP and the HTTP API."""
    return [t.schema() for t in TOOLS.values()]


def openai_tools(include_writes: bool = True) -> list[dict]:
    """The catalogue in OpenAI tool-calling format."""
    return [t.openai() for t in TOOLS.values() if include_writes or not t.writes]


# --- calling -------------------------------------------------------------------------------

def _coerce(arg: Arg, value: Any) -> Any:
    k = arg.kind
    if k == "bool":
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        if not isinstance(value, bool):
            raise ArgError(f"{arg.name} must be true or false")
        return value
    if k == "int":
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int) or not arg.low <= value <= arg.high:
            raise ArgError(f"{arg.name} must be an integer from {arg.low} to {arg.high}")
        return value
    if k in ("entities", "ids"):
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
            raise ArgError(f"{arg.name} must be a list of non-empty strings")
        if len(value) > arg.high or (k == "entities" and not value):
            raise ArgError(f"{arg.name} must have {1 if k == 'entities' else 0} to {arg.high} items")
        if any(len(v) > 200 for v in value):
            raise ArgError(f"{arg.name} has an item longer than 200 characters")
        return [v.strip() for v in value]
    if not isinstance(value, str):
        raise ArgError(f"{arg.name} must be a string")
    value = value.strip()
    if k == "enum":
        value = value.upper()
        if value not in arg.choices:
            raise ArgError(f"{arg.name} must be one of {', '.join(arg.choices)}")
        return value
    if k == "date":
        return parse_when(value)
    limit = 200 if k == "entity" else arg.high
    if not arg.low <= len(value) <= limit:
        raise ArgError(f"{arg.name} must be {arg.low} to {limit} characters")
    return value


def _validate(tool: Tool, args: dict) -> dict:
    if not isinstance(args, dict):
        raise ArgError("arguments must be a JSON object")
    known = {a.name for a in tool.args}
    if extra := sorted(set(args) - known):
        raise ArgError(f"unexpected argument(s) for {tool.name}: {', '.join(extra)}")
    out: dict[str, Any] = {}
    for arg in tool.args:
        value = args.get(arg.name)
        if value is None or value == "" and arg.kind != "text":
            if arg.required:
                raise ArgError(f"missing required argument: {arg.name}")
            out[arg.name] = [] if arg.kind == "ids" else arg.default
            continue
        out[arg.name] = _coerce(arg, value)
    return out


def _resolve_all(store: Store, tool: Tool, clean: dict, raw: dict) -> dict | Answer:
    """Replace every name with its Entity, or return the unknown_entity answer for the first miss."""
    out = dict(clean)
    for arg in tool.args:
        value = clean.get(arg.name)
        if value is None or arg.kind not in ("entity", "entities"):
            continue
        found = []
        for name in value if arg.kind == "entities" else [value]:
            if tool.name == "handoff" and AGENT_ID.match(name):
                found.append(name)      # an agent id: the gate creates the Agent on first use
                continue
            ent = resolve(store, name, arg.labels)
            if ent is None:
                return _unknown(store, tool.name, {k: v for k, v in raw.items() if v is not None}, arg, name)
            found.append(ent)
        out[arg.name] = found if arg.kind == "entities" else found[0]
    return out


def _trace(tool: str, args: dict, store: Store, mark: int, ans: Answer) -> ToolTrace:
    ran = store.trace[mark:]
    queries = [re.sub(r"\s+", " ", t.query).strip() for t in ran]
    return ToolTrace(tool, args, round(sum(t.ms for t in ran), 2), len(ans.rows), queries)


def call(store: Store, name: str, args: dict | None = None, ctx: Context | None = None) -> Answer:
    """Validate, resolve names, run one tool. Raises ArgError for a malformed call."""
    tool = TOOLS.get(name)
    if tool is None:
        raise ArgError(f"unknown tool {name!r}; tools: {', '.join(TOOLS)}")
    raw = args or {}
    mark = len(store.trace)
    clean = _validate(tool, raw)
    resolved = _resolve_all(store, tool, clean, raw)
    ans = resolved if isinstance(resolved, Answer) else tool.fn(store, resolved, ctx or Context())
    shown = ans.args if tool.writes and ans.args else {k: v for k, v in raw.items() if v is not None}
    ans.trace = [_trace(name, shown, store, mark, ans)]
    return ans
