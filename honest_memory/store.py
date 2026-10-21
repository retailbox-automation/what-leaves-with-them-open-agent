"""FalkorDB access: one graph per tenant, timed queries, low-level fact writes.

Only the write gate should call create_fact / close_fact; everything else reads.
Labels and edge types in the Cypher below come from schema.py, never from callers.
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from falkordb import FalkorDB

from .schema import ENTITY_KEYS, HISTORY_EDGE, INDEXES, PREDICATES, RECORD_EDGES, RECORD_KEYS, Predicate, graph_name


def _key(label: str) -> str:
    """Identifying property of a label; KeyError for anything that is not in the schema."""
    return ENTITY_KEYS.get(label) or RECORD_KEYS[label]


def _object_label(p: Predicate, object_label: str | None) -> str:
    label = object_label or p.object
    if label not in p.object_labels:
        raise ValueError(f"{p.name} cannot point at {label!r}; allowed: {p.object_labels}")
    return label


def connect() -> FalkorDB:
    """Connect from FALKORDB_* env vars. A socket timeout is always set: FalkorDB Cloud free
    speaks plain RESP only, and without a timeout a TLS attempt hangs forever (spike Q1)."""
    return FalkorDB(
        host=os.environ.get("FALKORDB_HOST", "localhost"),
        port=int(os.environ.get("FALKORDB_PORT", "6379")),
        username=os.environ.get("FALKORDB_USERNAME") or None,
        password=os.environ.get("FALKORDB_PASSWORD") or None,
        ssl=os.environ.get("FALKORDB_TLS", "0") == "1",
        socket_timeout=float(os.environ.get("FALKORDB_SOCKET_TIMEOUT", "10")),
        socket_connect_timeout=float(os.environ.get("FALKORDB_SOCKET_TIMEOUT", "10")),
    )


def epoch(ts: str | datetime) -> int:
    """ISO-8601 (with Z) or datetime -> integer seconds, the form stored on edges."""
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return int(ts.timestamp())


@dataclass
class TraceRow:
    query: str
    params: dict
    ms: float
    rows: int
    graph: str = ""  # empty for the tenant's graph, else the scratch graph the query ran on


@dataclass
class Scratch:
    """A temporary graph opened by Store.scratch; its queries land in the owning store's trace."""
    store: "Store"
    graph: object
    name: str

    def query(self, query: str, params: dict | None = None) -> list[list]:
        return self.store._run(self.graph.query, query, params, graph=self.name)


@dataclass
class Store:
    db: FalkorDB
    tenant: str
    trace: list[TraceRow] = field(default_factory=list)

    def __post_init__(self):
        self.graph = self.db.select_graph(graph_name(self.tenant))

    def ro(self, query: str, params: dict | None = None) -> list[list]:
        return self._run(self.graph.ro_query, query, params)

    def rw(self, query: str, params: dict | None = None) -> list[list]:
        return self._run(self.graph.query, query, params)

    def _run(self, fn, query, params, graph: str = ""):
        t0 = time.perf_counter()
        rows = fn(query, params or {}).result_set
        self.trace.append(TraceRow(query, params or {}, round((time.perf_counter() - t0) * 1000, 2), len(rows), graph))
        return rows

    @contextmanager
    def scratch(self, purpose: str):
        """A temporary graph next to the tenant's own, hm_<tenant>__<purpose>_<random>, for projections that an
        algorithm needs (written edges both ways, or with one person left out). Always deleted on exit, also when
        a query inside fails; its queries are traced like the tenant's."""
        name = f"{graph_name(self.tenant)}__{purpose}_{uuid.uuid4().hex[:10]}"
        graph = self.db.select_graph(name)
        try:
            yield Scratch(self, graph, name)
        finally:
            try:
                graph.delete()
            except Exception as exc:  # nothing was ever written to it
                if "empty key" not in str(exc).lower() and "invalid graph" not in str(exc).lower():
                    raise

    def ensure_schema(self) -> None:
        existing = {(r[0], key) for r in self.rw("CALL db.indexes() YIELD label, properties RETURN label, properties")
                    for key in r[1]}
        for label, key in INDEXES:
            if (label, key) not in existing:
                self.rw(f"CREATE INDEX FOR (n:{label}) ON (n.{key})")

    def drop(self) -> None:
        try:
            self.graph.delete()
        except Exception as exc:  # a graph that was never written does not exist yet
            if "empty key" not in str(exc).lower() and "invalid graph" not in str(exc).lower():
                raise
        self.graph = self.db.select_graph(graph_name(self.tenant))

    # --- entities and records -------------------------------------------------------------

    def merge_entity(self, label: str, key_value: str, props: dict | None = None) -> None:
        key = _key(label)
        self.rw(f"MERGE (n:{label} {{{key}: $k}}) SET n += $props", {"k": key_value, "props": props or {}})

    def find_entity(self, label: str, key_value: str) -> bool:
        key = ENTITY_KEYS[label]
        return bool(self.ro(f"MATCH (n:{label} {{{key}: $k}}) RETURN 1 LIMIT 1", {"k": key_value}))

    def entity_names(self, label: str) -> list[str]:
        key = ENTITY_KEYS[label]
        return [r[0] for r in self.ro(f"MATCH (n:{label}) RETURN n.{key} ORDER BY n.{key}")]

    def records(self, label: str, ids: list[str]) -> dict[str, dict]:
        """Properties of the nodes of one label whose key is in `ids`, by key. Missing ids are absent."""
        key = _key(label)
        if not ids:
            return {}
        rows = self.ro(f"MATCH (n:{label}) WHERE n.{key} IN $ids RETURN n.{key}, properties(n)", {"ids": list(ids)})
        return {r[0]: r[1] for r in rows}

    def link(self, src_label: str, src_key: str, rel: str, dst_label: str, dst_keys: list[str]) -> None:
        """MERGE (src)-[:rel]->(dst) for every dst key that exists. Record edges only; safe to repeat."""
        if rel not in RECORD_EDGES:
            raise ValueError(f"not a record edge: {rel!r}")
        sk, dk = _key(src_label), _key(dst_label)
        if not dst_keys:
            return
        self.rw(
            f"MATCH (a:{src_label} {{{sk}: $a}}) UNWIND $keys AS k MATCH (b:{dst_label} {{{dk}: k}}) "
            f"MERGE (a)-[:{rel}]->(b)",
            {"a": src_key, "keys": list(dst_keys)},
        )

    def next_sequence(self, name: str) -> int:
        """Per-tenant counter (1, 2, 3, ...), atomic in one query; the gate numbers its receipts with it."""
        rows = self.rw("MERGE (c:Seq {name: $n}) SET c.value = coalesce(c.value, 0) + 1 RETURN c.value", {"n": name})
        return rows[0][0]

    # --- facts (gate only) ----------------------------------------------------------------
    # Every helper takes an optional object_label for predicates whose object may carry more than one
    # label (LEADS: Team or Project); it defaults to the predicate's own object label.

    def current_facts(self, predicate: str, subject: str | None = None, obj: str | None = None,
                      object_label: str | None = None) -> list[dict]:
        """Current edges of one predicate, filtered by either end."""
        p = PREDICATES[predicate]
        olabel = _object_label(p, object_label)
        sk, ok = ENTITY_KEYS[p.subject], ENTITY_KEYS[olabel]
        rows = self.ro(
            f"MATCH (s:{p.subject})-[r:{p.name}]->(o:{olabel}) "
            f"WHERE ($s IS NULL OR s.{sk} = $s) AND ($o IS NULL OR o.{ok} = $o) "
            "RETURN s.%s, o.%s, properties(r)" % (sk, ok),
            {"s": subject, "o": obj},
        )
        return [{"subject": r[0], "object": r[1], **r[2]} for r in rows]

    def create_fact(self, predicate: str, subject: str, obj: str, props: dict, object_label: str | None = None) -> None:
        p = PREDICATES[predicate]
        olabel = _object_label(p, object_label)
        sk, ok = ENTITY_KEYS[p.subject], ENTITY_KEYS[olabel]
        self.rw(
            f"MATCH (s:{p.subject} {{{sk}: $s}}), (o:{olabel} {{{ok}: $o}}) "
            f"CREATE (s)-[r:{p.name}]->(o) SET r = $props",
            {"s": subject, "o": obj, "props": props},
        )

    def add_fact_source(self, predicate: str, subject: str, obj: str, source_ids: list[str],
                        object_label: str | None = None) -> None:
        p = PREDICATES[predicate]
        olabel = _object_label(p, object_label)
        sk, ok = ENTITY_KEYS[p.subject], ENTITY_KEYS[olabel]
        self.rw(
            f"MATCH (s:{p.subject} {{{sk}: $s}})-[r:{p.name}]->(o:{olabel} {{{ok}: $o}}) "
            "SET r.source_ids = r.source_ids + [x IN $ids WHERE NOT x IN r.source_ids]",
            {"s": subject, "o": obj, "ids": source_ids},
        )

    def set_fact_props(self, predicate: str, subject: str, obj: str, props: dict,
                       object_label: str | None = None) -> None:
        """Overwrite some properties of a current edge (the gate keeps WORKED_ON.count in step with its sources)."""
        p = PREDICATES[predicate]
        olabel = _object_label(p, object_label)
        sk, ok = ENTITY_KEYS[p.subject], ENTITY_KEYS[olabel]
        self.rw(
            f"MATCH (s:{p.subject} {{{sk}: $s}})-[r:{p.name}]->(o:{olabel} {{{ok}: $o}}) SET r += $props",
            {"s": subject, "o": obj, "props": props},
        )

    def close_fact(self, predicate: str, subject: str, obj: str, invalid_at: int, closed_by: str,
                   object_label: str | None = None) -> int:
        """Move a current edge into history: delete the typed edge, write WAS with its properties."""
        p = PREDICATES[predicate]
        olabel = _object_label(p, object_label)
        sk, ok = ENTITY_KEYS[p.subject], ENTITY_KEYS[olabel]
        rows = self.rw(
            f"MATCH (s:{p.subject} {{{sk}: $s}})-[r:{p.name}]->(o:{olabel} {{{ok}: $o}}) "
            f"WITH s, o, r, properties(r) AS old "
            f"CREATE (s)-[h:{HISTORY_EDGE}]->(o) SET h = old, h.predicate = $pred, "
            "h.invalid_at = $inv, h.closed_by = $by "
            "DELETE r RETURN count(h)",
            {"s": subject, "o": obj, "pred": predicate, "inv": invalid_at, "by": closed_by},
        )
        return rows[0][0] if rows else 0
