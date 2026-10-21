"""N5, "what leaves with them": the departure tools of the catalogue.

A manager gets a resignation and a list of the systems the person owns. These tools answer the harder question,
each from human-written Cypher over the same graph and the same write gate as the rest of the catalogue:

    what_leaves(person, depth)   what they own, lead and belong to (depth 1); at depth 2-4 the decisions they made
                                 that govern systems other people own, and what depends on those systems
    single_source(person)        live decisions and systems whose every source record this person wrote
    without(person)              what nobody is connected to any more once this person is gone (algo.WCC on a
                                 temporary projection graph without them)
    bridges(top)                 who links teams (algo.betweenness on a temporary projection, edges both ways)
    successors(person)           who has worked on each orphaned item or next to it: candidates, never decisions
    handoff_plan(person, ...)    one list combining the above, with an action per line; propose=true writes each
                                 line as a proposed Handoff through the gate (a person approves; nothing is sent)
    promises(person, since, to)  promises in the records since a date, or an honest not_in_graph naming the
                                 channels searched

The same check works before a vacation, parental leave or a team move. The framing is a handoff, never
surveillance: betweenness in bridges is the only per-person score, and it measures paths, not worth.

Time: with `as_of` (whole UTC day) a tool reads the facts in force that day and only the sources dated by then,
like impact(as_of). Without it, it reads the present, plus what the person held until a recorded last day (the
gate's CLOSE receipts), so the question still has an answer after they have gone.
"""

from __future__ import annotations

import re
import time
from itertools import combinations

from . import answer as A
from . import tools as T
from .answer import Answer, iso_ts

FAR = 2 ** 40                                   # "now" for interval filters: after every stored timestamp
PROJECTION_LABELS = ("Person", "Team", "System", "Decision")
BRIDGE_LABELS = ("Person", "Team", "System")
ESTIMATE_RUNS = 8                               # sampled betweenness runs averaged when the engine is not exact
PROMISE = re.compile(r"\bpromis|\bcommit(?:ted|ment)\b|\bguarantee|\bagreed to\b|\bwill call\b|\bcall(?: \w+)? back\b",
                     re.I)
CHANNELS = ("chat", "ticket", "doc")


def _t(when) -> int:
    return when.end if when else FAR


def _when_args(args: dict, when) -> dict:
    return {**args, **({"as_of": when.label} if when else {})}


# --- the person's departure, as recorded ----------------------------------------------------------------------------

_PERSON = "MATCH (p:Person {name: $person}) RETURN p.notice_at, p.last_day, p.notice_source"

# Receipts of the gate's CLOSE path for this person: what a last-day record ended.
_ENDS = ("MATCH (g:GateDecision) WHERE g.outcome = 'CLOSE' AND g.subject = $person "
         "OPTIONAL MATCH (g)-[:CITES]->(e:Episode) "
         "RETURN g.id, g.ts, g.predicate, g.object, collect(e.id) ORDER BY g.ts, g.id")


def departure_of(store, person: str, when=None) -> dict:
    """Notice and last day (properties a resignation record set on the Person) and the CLOSE receipts. With an
    as_of date, ended facts are read by interval instead, so `ended` is empty."""
    notice_at, last_day, notice_source = (store.ro(_PERSON, {"person": person}) or [[None, None, None]])[0]
    ends = [] if when else store.ro(_ENDS, {"person": person})
    return {"notice_at": notice_at, "last_day": last_day, "notice_source": notice_source,
            "ended": [r[0] for r in ends], "end_sources": list(dict.fromkeys(i for r in ends for i in r[4]))}


# --- depth 1: what the person holds ------------------------------------------------------------------------------------

# Current role facts, plus closed ones that were in force at $t or ended by this person's last-day record.
_HELD = (
    "MATCH (me:Person {name: $person})-[r:OWNS|LEADS|MEMBER_OF]->(x) WHERE r.valid_at <= $t "
    "RETURN type(r) AS rel, labels(x)[0] AS label, coalesce(x.name, x.id) AS item, r.valid_at AS since, "
    "null AS until, r.source_ids AS src, null AS closed_by "
    "UNION "
    "MATCH (me:Person {name: $person})-[h:WAS]->(x) "
    "WHERE h.predicate IN ['OWNS', 'LEADS', 'MEMBER_OF'] AND h.valid_at <= $t "
    "AND (h.invalid_at > $t OR h.closed_by IN $ended) "
    "RETURN h.predicate AS rel, labels(x)[0] AS label, coalesce(x.name, x.id) AS item, h.valid_at AS since, "
    "h.invalid_at AS until, h.source_ids AS src, h.closed_by AS closed_by")

_REL_ORDER = {"OWNS": 0, "LEADS": 1, "MEMBER_OF": 2}


def held(store, person: str, t: int, ended: list[str]) -> list[dict]:
    rows = []
    for rel, label, item, since, until, src, closed_by in store.ro(_HELD, {"person": person, "t": t, "ended": ended}):
        state = "current" if until is None or until > t else "ended"
        rows.append({"kind": "held", "hops": 1, "item": item, "item_kind": label.lower(), "relation": rel,
                     "state": state, "since": iso_ts(since), "until": iso_ts(until) or None,
                     "source_ids": src or [], "closed_by": closed_by if state == "ended" else None})
    return sorted(rows, key=lambda r: (_REL_ORDER[r["relation"]], r["item"]))


def _held_path(person: str, r: dict) -> list[str]:
    return [person, r["relation"] if r["state"] == "current" else "WAS", r["item"]]


# --- depth 2-4: decisions that govern other people's systems, and what depends on those ------------------------------

# Decisions superseded by $t. Passed to the queries below as a list: filtering them inside one query with
# OPTIONAL MATCH + count() + WITH ... WHERE makes FalkorDB 6.0.2 drop later rows (spike/with_where_repro.py).
_REPLACED = "MATCH (:Decision)-[sup:SUPERSEDES]->(d:Decision) WHERE sup.valid_at <= $t RETURN collect(DISTINCT d.id)"

# A decision the person made, not superseded by $t, that CONSTRAINS a system; then every system that depends on
# that system within {k} more hops (0 hops: the constrained system itself).
_DECIDED_REACH = (
    "MATCH (me:Person {{name: $person}})-[dec:DECIDED]->(d:Decision)-[c:CONSTRAINS]->(x:System) "
    "WHERE dec.valid_at <= $t AND c.valid_at <= $t AND NOT d.id IN $replaced "
    "MATCH up = (s:System)-[:DEPENDS_ON*0..{k}]->(x) "
    "RETURN d.id, x.name, s.name, length(up), [n IN nodes(up) | n.name], [r IN relationships(up) | r.valid_at], "
    "[r IN relationships(up) | r.source_ids], dec.source_ids + c.source_ids")

# A system the person owns (or owned until their recorded last day), and what depends on it within {k} hops.
_OWNED_REACH = (
    "MATCH (me:Person {{name: $person}})-[h:OWNS|WAS]->(x:System) "
    "WHERE h.valid_at <= $t AND (type(h) = 'OWNS' OR (h.predicate = 'OWNS' AND (h.invalid_at > $t OR h.closed_by IN $ended))) "
    "MATCH up = (s:System)-[:DEPENDS_ON*1..{k}]->(x) "
    "RETURN null, x.name, s.name, length(up), [n IN nodes(up) | n.name], [r IN relationships(up) | r.valid_at], "
    "[r IN relationships(up) | r.source_ids], h.source_ids")

# Who owned each system at $t, and their team at $t (current edges, or closed ones in force then).
_OWNER_AT = (
    "UNWIND $names AS n MATCH (p:Person)-[r:OWNS]->(:System {name: n}) WHERE r.valid_at <= $t "
    "RETURN n AS system, p.name AS owner, r.source_ids AS src "
    "UNION "
    "UNWIND $names AS n MATCH (p:Person)-[r:WAS]->(:System {name: n}) "
    "WHERE r.predicate = 'OWNS' AND r.valid_at <= $t AND r.invalid_at > $t "
    "RETURN n AS system, p.name AS owner, r.source_ids AS src")
_TEAM_AT = (
    "UNWIND $names AS n MATCH (p:Person {name: n})-[r:MEMBER_OF]->(t:Team) WHERE r.valid_at <= $t "
    "RETURN n AS person, t.name AS team "
    "UNION "
    "UNWIND $names AS n MATCH (p:Person {name: n})-[r:WAS]->(t:Team) "
    "WHERE r.predicate = 'MEMBER_OF' AND r.valid_at <= $t AND (r.invalid_at > $t OR r.closed_by IN $ended) "
    "RETURN n AS person, t.name AS team")
_DECIDERS = ("UNWIND $ids AS i MATCH (p:Person)-[r:DECIDED]->(:Decision {id: i}) WHERE r.valid_at <= $t "
             "RETURN i, collect(p.name)")


def replaced_by(store, t: int) -> list[str]:
    return store.ro(_REPLACED, {"t": t})[0][0]


def owners_at(store, systems, t: int) -> dict[str, tuple[str, list]]:
    return {s: (o, src or []) for s, o, src in store.ro(_OWNER_AT, {"names": sorted(systems), "t": t})}


def teams_at(store, people, t: int, ended=()) -> dict[str, str]:
    return dict(store.ro(_TEAM_AT, {"names": sorted(p for p in people if p), "t": t, "ended": list(ended)}))


def reach(store, person: str, depth: int, t: int, ended: list[str], exclude: set[str]) -> list[dict]:
    """Systems at risk 2..depth hops from the person, one row per system along its shortest path (ties: the
    lowest decision id), skipping what the person holds or owns. Dependency edges count only if they existed by $t
    (filtered here: FalkorDB 6.0.x rejects all(...) over a variable-length relationship)."""
    if depth < 2:
        return []
    found = store.ro(_DECIDED_REACH.format(k=depth - 2), {"person": person, "t": t, "replaced": replaced_by(store, t)})
    found += store.ro(_OWNED_REACH.format(k=depth - 1), {"person": person, "t": t, "ended": ended})
    best: dict[str, tuple] = {}
    for decision, start, item, hops, chain, valid, dep_src, why in found:
        if item in exclude or any(v is None or v > t for v in valid):
            continue
        total = hops + (2 if decision else 1)
        key = (total, decision or "")
        if total <= depth and (item not in best or key < best[item][0]):
            best[item] = (key, decision, start, chain, [i for lst in dep_src for i in (lst or [])], why or [])
    if not best:
        return []
    owners = owners_at(store, best, t)
    teams = teams_at(store, {o for o, _ in owners.values()} | {person}, t, ended)
    co = dict(store.ro(_DECIDERS, {"ids": sorted({b[1] for b in best.values() if b[1]}), "t": t}))
    mine = teams.get(person)
    rows = []
    for item, ((total, _), decision, start, chain, dep_src, why) in best.items():
        owner, owner_src = owners.get(item, (None, []))
        if owner == person:
            continue
        others = sorted(x for x in co.get(decision, []) if x != person)
        team = teams.get(owner)
        if decision:
            head = f"{decision} ({'decided with ' + ', '.join(others) if others else 'decided by ' + person + ' alone'})"
            risk = f"{head} governs {start}" + (f", and {item} depends on {start}" if item != start else "")
            path = [person, "DECIDED", decision, "CONSTRAINS", start]
        else:
            risk = f"{item} depends on {start}, which {person} owns"
            path = [person, "OWNS", start]
        for name in reversed(chain[:-1]):
            path += ["<-DEPENDS_ON", name]
        if owner:
            path += ["<-OWNS", owner] + (["MEMBER_OF", team] if team else [])
        rows.append({"kind": "at_risk", "hops": total, "item": item, "item_kind": "system", "via": start,
                     "decision": decision, "co_deciders": others, "owner": owner, "owner_team": team,
                     "other_team": bool(team and mine and team != mine), "why": risk,
                     "source_ids": list(dict.fromkeys(list(why) + dep_src + list(owner_src))), "path": path})
    return sorted(rows, key=lambda r: (r["hops"], r["item"]))


def what_leaves(store, a: dict, ctx) -> Answer:
    person, depth, when = a["person"].name, a["depth"], a.get("as_of")
    args = _when_args({"person": person, "depth": depth}, when)
    t, info = _t(when), departure_of(store, person, when)
    hold = held(store, person, t, info["ended"])
    owned_now = {r["item"] for r in hold if r["relation"] == "OWNS"}
    risk = reach(store, person, depth, t, info["ended"], {r["item"] for r in hold} | owned_now)
    if not hold and not risk:
        return T._not_in_graph("what_leaves", args, f"{person} exists, but the graph records nothing they own, lead "
                               "or belong to" + (f", nor a decision of theirs that governs a system within {depth} hops"
                                                 if depth > 1 else "") + (f" as of {when.label}" if when else "") + ".")
    records = {r["item"]: r for r in lone(store, person, t, info["ended"]) if r["item_kind"] == "decision"}
    for r in risk:  # whose records stand behind the decision on this path, as of the same moment
        if r["decision"] in records:
            r["records_by"] = records[r["decision"]]["authors"]
            r["only_theirs"] = records[r["decision"]]["single_source"]
    by_hops: dict[str, int] = {}
    for r in hold + risk:
        by_hops[str(r["hops"])] = by_hops.get(str(r["hops"]), 0) + 1
    summary = {"kind": "summary", "person": person, "depth": depth, "as_of": when.label if when else None,
               "depth_1": [r["item"] for r in hold], "depth_n": [r["item"] for r in hold + risk], "by_hops": by_hops,
               "teams_reached": sorted({r["owner_team"] for r in risk if r["owner_team"]}),
               "decisions_only_theirs": sorted(d for d, r in records.items() if r["single_source"]),
               "last_day": info["last_day"]}
    paths = [_held_path(person, r) for r in hold] + [r.pop("path") for r in risk]
    ids = [i for r in hold + risk for i in r["source_ids"]] + info["end_sources"]
    receipts = T._receipts(store, [r["closed_by"] for r in hold if r["closed_by"]])
    return T._finish(Answer(A.ANSWERED_WITH_PATH, "what_leaves", args, [summary] + hold + risk, paths,
                            T._sources(store, ids), receipts))


# --- single source --------------------------------------------------------------------------------------------------

# Every live decision the person made, with the authors of every source record behind any fact about it.
_LONE_DECISIONS = (
    "MATCH (me:Person {name: $person})-[mine:DECIDED]->(d:Decision) "
    "WHERE mine.valid_at <= $t AND NOT d.id IN $replaced "
    "MATCH (x)-[r:DECIDED|CONSTRAINS|SUPERSEDES]-(d) WHERE r.valid_at <= $t "
    "UNWIND r.source_ids AS sid "
    "MATCH (e:Episode {id: sid}) WHERE e.ts <= $t "
    "RETURN d.id, d.title, collect(DISTINCT e.author), collect(DISTINCT e.id) ORDER BY d.id")

# Every system the person owns, owned until their last day, or worked on, with the authors of every source record
# behind any fact about that system (ownership, work, dependencies, constraints).
_LONE_SYSTEMS = (
    "MATCH (me:Person {name: $person})-[w:OWNS|WORKED_ON|WAS]->(s:System) "
    "WHERE w.valid_at <= $t AND (type(w) <> 'WAS' OR (w.predicate = 'OWNS' AND (w.invalid_at > $t OR w.closed_by IN $ended))) "
    "WITH s, collect(DISTINCT CASE WHEN type(w) = 'WAS' THEN 'OWNS' ELSE type(w) END) AS rels "
    "MATCH (s)-[r:OWNS|WORKED_ON|DEPENDS_ON|CONSTRAINS|WAS]-(x) "
    "WHERE r.valid_at <= $t AND (type(r) <> 'WAS' OR r.invalid_at > $t OR r.closed_by IN $ended) "
    "UNWIND r.source_ids AS sid "
    "MATCH (e:Episode {id: sid}) WHERE e.ts <= $t "
    "RETURN s.name, s.kind, rels, collect(DISTINCT e.author), collect(DISTINCT e.id) ORDER BY s.name")


def lone(store, person: str, t: int, ended: list[str]) -> list[dict]:
    """Decisions and systems checked for a single author; `single_source` marks those whose authors are only them."""
    rows = []
    for did, title, authors, eps in store.ro(_LONE_DECISIONS, {"person": person, "t": t,
                                                               "replaced": replaced_by(store, t)}):
        rows.append({"kind": "checked", "item": did, "item_kind": "decision", "title": title, "relation": "DECIDED",
                     "single_source": sorted(authors) == [person], "authors": sorted(authors), "episodes": sorted(eps)})
    for name, kind, rels, authors, eps in store.ro(_LONE_SYSTEMS, {"person": person, "t": t, "ended": ended}):
        rows.append({"kind": "checked", "item": name, "item_kind": "system", "system_kind": kind or "service",
                     "relation": "OWNS" if "OWNS" in rels else "WORKED_ON",
                     "single_source": sorted(authors) == [person], "authors": sorted(authors), "episodes": sorted(eps)})
    return sorted(rows, key=lambda r: (not r["single_source"], r["item_kind"] != "decision", r["item"]))


def single_source(store, a: dict, ctx) -> Answer:
    person, when = a["person"].name, a.get("as_of")
    args = _when_args({"person": person}, when)
    t, info = _t(when), departure_of(store, person, when)
    rows = lone(store, person, t, info["ended"])
    single = [r for r in rows if r["single_source"]]
    paths = [[person, r["relation"], r["item"]] for r in single]
    ids = [i for r in single for i in r["episodes"]]
    summary = {"kind": "summary", "person": person, "as_of": when.label if when else None, "checked": len(rows),
               "single_source": len(single), "decisions": sum(r["item_kind"] == "decision" for r in single),
               "systems": sum(r["item_kind"] == "system" for r in single)}
    if not single:
        note = (f"Nothing {person} decided, owns or worked on rests only on records they wrote"
                + (f" as of {when.label}" if when else "") + f": {len(rows)} items checked, each has another author.")
        return T._finish(Answer(A.NOT_IN_GRAPH, "single_source", args, [summary] + rows, [], [], note=note))
    return T._finish(Answer(A.ANSWERED_WITH_PATH, "single_source", args, [summary] + rows, paths,
                            T._sources(store, ids)))


# --- projections: temporary graphs for the algorithms ----------------------------------------------------------------

_P_NODES = ("MATCH (n:Person) RETURN 'Person' AS label, n.name AS key UNION ALL "
            "MATCH (n:Team) RETURN 'Team' AS label, n.name AS key UNION ALL "
            "MATCH (n:System) RETURN 'System' AS label, n.name AS key UNION ALL "
            "MATCH (n:Decision) RETURN 'Decision' AS label, n.id AS key")
# Current facts between people, teams, systems and decisions (no DEPENDS_ON: an island is something no person
# is connected to; a dependency between two systems keeps no knowledge alive).
_P_LINKS = ("MATCH (p:Person)-[r:OWNS|WORKED_ON|MEMBER_OF|LEADS|DECIDED]->(x) "
            "RETURN 'Person' AS a_label, p.name AS a, labels(x)[0] AS b_label, coalesce(x.name, x.id) AS b, type(r) AS rel "
            "UNION ALL "
            "MATCH (d:Decision)-[r:CONSTRAINS]->(s:System) "
            "RETURN 'Decision' AS a_label, d.id AS a, 'System' AS b_label, s.name AS b, type(r) AS rel")
_WCC = ("CALL algo.WCC({nodeLabels: ['Person', 'Team', 'System', 'Decision'], relationshipTypes: ['LINK']}) "
        "YIELD node, componentId RETURN labels(node)[0], node.key, componentId")
_BETWEENNESS = ("CALL algo.betweenness({nodeLabels: ['Person', 'Team', 'System'], relationshipTypes: ['LINK'], "
                "samplingSize: $n, samplingSeed: $seed}) YIELD node, score RETURN labels(node)[0], node.key, score")


def project(scratch, nodes: list[list], links: list[list], both_ways: bool) -> tuple[int, int]:
    """Write nodes (label, key) and one LINK per linked pair into a scratch graph; both ways when an algorithm
    follows edge direction (algo.betweenness does, on 4.22 and 6.0.x). Labels come from PROJECTION_LABELS only."""
    by_label: dict[str, list[str]] = {}
    for label, key in nodes:
        if label in PROJECTION_LABELS and key is not None:
            by_label.setdefault(label, []).append(key)
    for label, keys in by_label.items():
        scratch.query(f"UNWIND $keys AS k CREATE (:{label} {{key: k}})", {"keys": sorted(set(keys))})
    pairs: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for la, a, lb, b in links:
        if la in by_label and lb in by_label:
            (la, a), (lb, b) = sorted([(la, a), (lb, b)])
            pairs.setdefault((la, lb), set()).add((a, b))
    back = " CREATE (b)-[:LINK]->(a)" if both_ways else ""
    for (la, lb), group in sorted(pairs.items()):
        scratch.query(f"UNWIND $pairs AS e MATCH (a:{la} {{key: e[0]}}), (b:{lb} {{key: e[1]}}) "
                      f"CREATE (a)-[:LINK]->(b){back}", {"pairs": sorted([list(p) for p in group])})
    return sum(len(v) for v in by_label.values()), sum(len(v) for v in pairs.values())


# --- without: what nobody is connected to once the person is gone ----------------------------------------------------

_MINE = ("MATCH (me:Person {name: $person})-[r:OWNS|WORKED_ON|MEMBER_OF|LEADS|DECIDED]->(x) "
         "RETURN type(r) AS rel, labels(x)[0] AS label, coalesce(x.name, x.id) AS item, r.source_ids AS src, "
         "coalesce(r.count, 0) AS n, 'current' AS state "
         "UNION ALL "
         "MATCH (me:Person {name: $person})-[h:WAS]->(x) WHERE h.closed_by IN $ended "
         "RETURN h.predicate AS rel, labels(x)[0] AS label, coalesce(x.name, x.id) AS item, h.source_ids AS src, "
         "0 AS n, 'ended' AS state")


def islands_without(store, person: str) -> dict:
    """Copy the current facts without the person into a scratch graph (not GRAPH.COPY: the copy is a projection),
    run algo.WCC there, and keep the components with no Person. The scratch graph is always deleted."""
    t0 = time.perf_counter()
    info = departure_of(store, person)
    nodes = [r for r in store.ro(_P_NODES) if not (r[0] == "Person" and r[1] == person)]
    links = [r for r in store.ro(_P_LINKS) if r[0] in PROJECTION_LABELS and r[2] in PROJECTION_LABELS
             and not (r[0] == "Person" and r[1] == person)]
    mine = store.ro(_MINE, {"person": person, "ended": info["ended"]})
    with store.scratch("without") as scratch:
        n_nodes, n_links = project(scratch, nodes, [r[:4] for r in links], both_ways=False)
        components: dict[int, list[tuple[str, str]]] = {}
        for label, key, comp in scratch.query(_WCC):
            components.setdefault(comp, []).append((label, key))
    wall_ms = round((time.perf_counter() - t0) * 1000, 1)
    touched: dict[str, list[dict]] = {}
    for rel, label, item, src, count, state in sorted(mine, key=lambda r: (r[2], r[0], r[5])):
        touched.setdefault(item, []).append({"relation": rel, "item": item, "item_kind": label.lower(), "state": state,
                                             "count": count or None, "source_ids": src or []})
    islands, before = [], []
    for members in components.values():
        if any(label == "Person" for label, _ in members):
            continue
        names = sorted(key for _, key in members)
        through = [c for n in names for c in touched.get(n, [])]
        (islands if through else before).append({"items": names, "kinds": {k: l.lower() for l, k in members},
                                                 "connected_through": through})
    return {"islands": sorted(islands, key=lambda r: r["items"]), "already": before, "nodes": n_nodes,
            "links": n_links, "components": len(components), "wall_ms": wall_ms, "info": info}


def without(store, a: dict, ctx) -> Answer:
    person = a["person"].name
    args = {"person": person}
    res = islands_without(store, person)
    summary = {"kind": "summary", "person": person, "algorithm": "algo.WCC", "nodes": res["nodes"],
               "links": res["links"], "components": res["components"], "islands": len(res["islands"]),
               "already_unconnected": len(res["already"]), "wall_ms": res["wall_ms"]}
    rows = [summary] + [{"kind": "island", **i} for i in res["islands"]]
    paths = [[person, c["relation"] if c["state"] == "current" else "WAS", c["item"]]
             for i in res["islands"] for c in i["connected_through"]]
    ids = [s for i in res["islands"] for c in i["connected_through"] for s in c["source_ids"]]
    if not res["islands"]:
        note = (f"Without {person}, every system, team and decision is still connected to someone in the graph "
                f"({res['components']} components over {res['nodes']} nodes, {res['wall_ms']} ms).")
        return T._finish(Answer(A.NOT_IN_GRAPH, "without", args, rows, [], [], note=note))
    return T._finish(Answer(A.ANSWERED_WITH_PATH, "without", args, rows, paths, T._sources(store, ids)))


# --- bridges: who links teams ------------------------------------------------------------------------------------------

# The teams a person links: their own, and the team of the owner of every system they own or work on.
_TEAM_LINKS = (
    "MATCH (p:Person)-[:MEMBER_OF]->(t:Team) "
    "RETURN p.name AS person, t.name AS team, 'MEMBER_OF' AS rel, null AS system, null AS owner "
    "UNION "
    "MATCH (p:Person)-[r:OWNS|WORKED_ON]->(s:System)<-[:OWNS]-(o:Person)-[:MEMBER_OF]->(t:Team) "
    "RETURN p.name AS person, t.name AS team, type(r) AS rel, s.name AS system, o.name AS owner")


def betweenness(store) -> dict:
    """algo.betweenness over people, teams and systems, in a scratch graph with every link written both ways.
    samplingSize covers every node; two seeds that agree mean the engine computed it exactly (6.0.x does). When
    they differ (4.22 samples sources), the mean of ESTIMATE_RUNS seeded runs is reported, marked as an estimate."""
    nodes = [r for r in store.ro(_P_NODES) if r[0] in BRIDGE_LABELS]
    links = [r[:4] for r in store.ro(_P_LINKS) if r[0] in BRIDGE_LABELS and r[2] in BRIDGE_LABELS]
    with store.scratch("bridges") as scratch:
        n_nodes, n_links = project(scratch, nodes, links, both_ways=True)

        def run(seed):
            return {(label, key): score for label, key, score in
                    scratch.query(_BETWEENNESS, {"n": max(n_nodes, 1), "seed": seed})}

        first, second = run(1), run(2)
        exact = first == second
        runs = [first, second] + ([] if exact else [run(seed) for seed in range(3, ESTIMATE_RUNS + 1)])
    scores = {k: sum(r.get(k, 0.0) for r in runs) / len(runs) for k in first}
    return {"scores": scores, "exact": exact, "runs": 1 if exact else len(runs), "nodes": n_nodes, "links": n_links}


def team_links(store) -> dict[str, dict[str, list]]:
    """person -> team -> the evidence (relation, system, owner) of the first link found."""
    out: dict[str, dict[str, list]] = {}
    for person, team, rel, system, owner in sorted(store.ro(_TEAM_LINKS), key=lambda r: tuple(str(x) for x in r)):
        out.setdefault(person, {}).setdefault(team, [rel, system, owner])
    return out


def bridges(store, a: dict, ctx) -> Answer:
    top = a["top"]
    args = {"top": top}
    res = betweenness(store)
    people = {key: score for (label, key), score in res["scores"].items() if label == "Person"}
    if not people:
        return T._not_in_graph("bridges", args, "The graph holds no people to rank.")
    links = team_links(store)
    pairs: dict[tuple[str, str], list[str]] = {}
    for person, teams in links.items():
        for pair in combinations(sorted(teams), 2):
            pairs.setdefault(pair, []).append(person)
    only = {pair: who[0] for pair, who in sorted(pairs.items()) if len(who) == 1}
    ranked = sorted(people, key=lambda p: (-round(people[p], 6), p))[:top]
    summary = {"kind": "summary", "algorithm": "algo.betweenness", "exact": res["exact"], "runs": res["runs"],
               "nodes": res["nodes"], "links": res["links"],
               "projection": "people, teams and systems; every current link written both ways"}
    rows, paths = [summary], []
    for rank, person in enumerate(ranked, 1):
        teams = links.get(person, {})
        rows.append({"kind": "bridge", "rank": rank, "person": person, "score": round(people[person], 2),
                     "teams": sorted(teams), "only_link": [list(p) for p, who in only.items() if who == person]})
        for team, (rel, system, owner) in sorted(teams.items()):
            paths.append([person, "MEMBER_OF", team] if rel == "MEMBER_OF"
                         else [person, rel, system, "<-OWNS", owner, "MEMBER_OF", team])
    rows += [{"kind": "only_link", "teams": list(pair), "person": who} for pair, who in only.items()]
    note = ("Betweenness is the only per-person score this memory computes: how many shortest paths between people, "
            "teams and systems pass through someone. It shows where knowledge would be cut off, not anyone's worth."
            + ("" if res["exact"] else f" This engine samples sources, so the scores are the mean of {res['runs']} "
               "seeded runs: estimates."))
    return T._finish(Answer(A.ANSWERED_WITH_PATH, "bridges", args, rows, paths, [], note=note))


# --- successors: candidates for what would be orphaned -------------------------------------------------------------------

_NEAR = ("UNWIND $items AS item MATCH (x:System {name: item}) "
         "OPTIONAL MATCH (x)-[:DEPENDS_ON]->(down:System) OPTIONAL MATCH (up:System)-[:DEPENDS_ON]->(x) "
         "RETURN item, collect(DISTINCT down.name), collect(DISTINCT up.name)")
_GOVERNS = ("UNWIND $items AS item MATCH (:Decision {id: item})-[:CONSTRAINS]->(s:System) "
            "RETURN item, collect(s.name)")
_WORKERS = ("UNWIND $systems AS sys MATCH (c:Person)-[w:WORKED_ON]->(:System {name: sys}) WHERE c.name <> $person "
            "OPTIONAL MATCH (c)-[:MEMBER_OF]->(t:Team) "
            "RETURN sys, c.name, t.name, coalesce(w.count, size(w.source_ids)), w.source_ids")


def orphaned(store, person: str, t: int, ended: list[str]) -> list[dict]:
    """What would be left without the person: systems they own (or owned until their last day), what they lead,
    and the decisions and systems that rest only on their records."""
    hold = held(store, person, t, ended)
    items = [{"item": r["item"], "item_kind": r["item_kind"], "why": [f"{r['relation'].lower()} ({r['state']})"],
              "source_ids": r["source_ids"], "path": _held_path(person, r)}
             for r in hold if r["relation"] in ("OWNS", "LEADS")]
    seen = {r["item"]: r for r in items}
    for r in lone(store, person, t, ended):
        if not r["single_source"]:
            continue
        if r["item"] in seen:
            seen[r["item"]]["why"].append("single source")
        else:
            seen[r["item"]] = {"item": r["item"], "item_kind": r["item_kind"], "why": ["single source"],
                               "source_ids": r["episodes"], "path": [person, r["relation"], r["item"]]}
            items.append(seen[r["item"]])
    return items


def candidates(store, person: str, items: list[dict], limit: int = 3) -> dict[str, dict]:
    """Per item: people ranked by work on the item, then on its neighbours (dependencies either way for a system,
    the systems a decision governs). Candidates only; nobody is named the successor here."""
    systems = [i["item"] for i in items if i["item_kind"] == "system"]
    decisions = [i["item"] for i in items if i["item_kind"] == "decision"]
    near: dict[str, dict[str, str]] = {}
    for item, down, up in store.ro(_NEAR, {"items": systems}):
        near[item] = {**{d: "<-DEPENDS_ON" for d in down if d}, **{u: "DEPENDS_ON" for u in up if u}}
    for item, governed in store.ro(_GOVERNS, {"items": decisions}):
        near[item] = {s: "<-CONSTRAINS" for s in governed}
    every = sorted(set(systems) | {s for n in near.values() for s in n})
    work: dict[str, list] = {}
    for sys, who, team, count, src in store.ro(_WORKERS, {"systems": every, "person": person}):
        work.setdefault(sys, []).append((who, team, count, src or []))
    out = {}
    for i in items:
        item = i["item"]
        direct = [] if i["item_kind"] == "decision" else [item]
        tally: dict[str, dict] = {}
        for sys in direct + sorted(near.get(item, {})):
            for who, team, count, src in work.get(sys, []):
                c = tally.setdefault(who, {"person": who, "team": team, "on_item": 0, "nearby": 0, "via": [],
                                           "status": "candidate", "source_ids": []})
                if sys == item:
                    c["on_item"] += count
                else:
                    c["nearby"] += count
                    c["via"].append(sys)
                c["source_ids"] += src[:3]
        ranked = sorted(tally.values(), key=lambda c: (-c["on_item"], -c["nearby"], c["person"]))[:limit]
        out[item] = {"candidates": ranked, "near": near.get(item, {})}
    return out


def _candidate_path(c: dict, item: str, near: dict[str, str]) -> list[str]:
    if c["on_item"]:
        return [c["person"], "WORKED_ON", item]
    via = c["via"][0]
    return [c["person"], "WORKED_ON", via, near[via], item]


def successors(store, a: dict, ctx) -> Answer:
    person = a["person"].name
    args = {"person": person}
    info = departure_of(store, person)
    items = orphaned(store, person, FAR, info["ended"])
    if not items:
        return T._not_in_graph("successors", args, f"{person} owns, leads and alone decided nothing that the graph "
                               "would leave without anyone.")
    cand = candidates(store, person, items)
    rows, paths, ids = [], [], []
    for i in items:
        c = cand[i["item"]]
        row = {"kind": "orphaned", **{k: v for k, v in i.items() if k != "path"}, "candidates": c["candidates"],
               "note": "" if c["candidates"] else "no one in the graph has worked on this"}
        if not c["candidates"] and i["item_kind"] in ("team", "project"):
            row["note"] = "a lead role: the graph does not rank people for it"
        rows.append(row)
        paths += [_candidate_path(x, i["item"], c["near"]) for x in c["candidates"]]
        ids += i["source_ids"] + [s for x in c["candidates"] for s in x["source_ids"]]
    note = "Candidates, not decisions: a person chooses who takes over."
    return T._finish(Answer(A.ANSWERED_WITH_PATH, "successors", args, rows, paths, T._sources(store, ids), note=note))


# --- handoff_plan: one list, an action per line ------------------------------------------------------------------------------

def _action(person: str, line: dict, by: str) -> str:
    item, names = line["item"], [c["person"] for c in line["candidates"]]
    if line["item_kind"] == "decision":
        review = f" and review it with {', '.join(line['review_with'])}" if line["review_with"] else ""
        hold = f"; candidates to hold it: {', '.join(names)}" if names else ""
        return f"Ask {person} to write down why {item} was decided{review} {by}{hold}."
    if line["item_kind"] in ("team", "project"):
        return f"Name a new lead for {item} {by}."
    if len(names) == 1:
        return f"Ask {names[0]} (candidate) to take over {item}; {person} walks them through it {by}."
    if names:
        return (f"Ask one of {', '.join(names[:-1])} or {names[-1]} (candidates) to take over {item}; "
                f"{person} walks them through it {by}.")
    return (f"No one in the graph has worked on {item}: {person} writes down how it works, and a lead names someone "
            f"to pair with them {by}.")


_EPISODES = "UNWIND $ids AS i MATCH (e:Episode {id: i}) RETURN e.id, e.author, e.ts"
_INTEGRATIONS = "MATCH (s:System) WHERE s.name IN $names AND s.kind = 'integration' RETURN s.name ORDER BY s.name"


def questions_for(store, person: str, info: dict, items: list[dict], cand: dict, governs: dict, owners: dict,
                  by: str) -> list[dict]:
    """What to ask the person before they go: one question for every item that rests only on their records, one for
    every orphaned item nobody else has worked on, and one for promises the records cannot hold. Each says why we
    ask and which records exist so far. Plain text; nothing is written."""
    lone_rows = {r["item"]: r for r in lone(store, person, FAR, info["ended"]) if r["single_source"]}
    meta = {i: (author, ts) for i, author, ts in store.ro(_EPISODES, {"ids": sorted({e for r in lone_rows.values()
                                                                                       for e in r["episodes"]})})}
    notice = info["notice_at"] or FAR
    out = []
    for item in [i["item"] for i in items if i["item"] in lone_rows] + [i for i in lone_rows if i not in
                                                                        {x["item"] for x in items}]:
        r = lone_rows[item]
        answered = [e for e in r["episodes"] if meta.get(e, (None, 0))[0] == person and meta[e][1] > notice]
        alone = not (cand.get(item) or {}).get("candidates")
        if r["item_kind"] == "decision":
            governed = ", ".join(f"{s} (owner {owners[s][0]})" if s in owners else s for s in governs.get(item, []))
            ask = f"Why was {item} decided, and what breaks in {governed or 'the systems it governs'} if it is undone?"
            why = f"Every record behind {item} was written by {person}"
        else:
            ask = f"How is {item} run day to day, and where are the steps written down?"
            why = f"Every record about {item} was written by {person}"
            if alone:
                ask += f" Nobody else in the graph has worked on it: who should learn it {by}?"
        if answered:
            ask = f"Who besides {person} has read {', '.join(answered)} and can confirm what it says about {item}?"
            why += f"; {person} answered in {', '.join(answered)}, but no one else has confirmed it yet"
        out.append({"kind": "question", "item": item, "item_kind": r["item_kind"], "question": ask,
                    "why": why + ".", "sources_so_far": r["episodes"], "answered_in": answered,
                    "status": "answered by the leaver, waiting for a second person" if answered else "open"})
    for i in items:
        if i["item"] not in lone_rows and not cand[i["item"]]["candidates"] and i["item_kind"] == "system":
            out.append({"kind": "question", "item": i["item"], "item_kind": i["item_kind"],
                        "question": f"Who besides you has worked on {i['item']}, even informally? If no one, who "
                                    f"should learn it {by}?",
                        "why": f"No one else in the graph has worked on {i['item']}.",
                        "sources_so_far": i["source_ids"], "answered_in": [], "status": "open"})
    search = search_promises(store, person, info)
    if not search["found"]:
        integrations = [r[0] for r in store.ro(_INTEGRATIONS, {"names": [i["item"] for i in items]})]
        out.append({"kind": "question", "item": "promises", "item_kind": "gap",
                    "question": "Did you promise a customer or partner anything that is not written down, on a call "
                                f"or in a meeting{', including for ' + ', '.join(integrations) if integrations else ''}? "
                                f"If so, write it in a ticket {by}.",
                    "why": f"No record written by or naming {person} since {search['since']} records a promise "
                           f"({search['records']} records searched); calls and meetings are not recorded.",
                    "sources_so_far": [], "answered_in": [], "status": "open"})
    for n, q in enumerate(out, 1):
        q["n"] = n
    return out


def handoff_plan(store, a: dict, ctx) -> Answer:
    person, when, propose = a["person"].name, a.get("last_day"), bool(a.get("propose"))
    info = departure_of(store, person)
    last_day = when.label if when else info["last_day"]
    args = {"person": person, "last_day": last_day, "propose": propose}
    items = orphaned(store, person, FAR, info["ended"])
    if not items:
        return T._not_in_graph("handoff_plan", args, f"{person} owns, leads and alone decided nothing that would be "
                               "left without anyone; there is nothing to hand over.")
    cand = candidates(store, person, items)
    islands = {n for i in islands_without(store, person)["islands"] for n in i["items"]}
    # the strongest path for a decision: who decided it, what it governs, who owns that and in which team
    decision_path = {}
    for r in reach(store, person, 2, FAR, info["ended"], set()):
        if r["decision"]:
            decision_path.setdefault(r["decision"], r["path"])
    governs = dict(store.ro(_GOVERNS, {"items": [i["item"] for i in items if i["item_kind"] == "decision"]}))
    owners = owners_at(store, {s for v in governs.values() for s in v}, FAR)
    team = teams_at(store, [person], FAR, info["ended"]).get(person)
    by = f"before {last_day}" if last_day else "before the last day"
    rows, paths, ids, receipts = [], [], [], []
    for n, i in enumerate(items, 1):
        reasons = list(i["why"]) + (["nobody else is connected to it"] if i["item"] in islands else [])
        governed = governs.get(i["item"], [])
        review = sorted({owners[s][0] for s in governed if s in owners and owners[s][0] != person})
        if governed:
            reasons.append("governs " + ", ".join(f"{s} (owner {owners[s][0]})" if s in owners else s for s in governed))
        line = {"kind": "line", "line": n, "item": i["item"], "item_kind": i["item_kind"], "risk": reasons,
                "source_ids": i["source_ids"], "candidates": [c["person"] for c in cand[i["item"]]["candidates"]],
                "review_with": review, "status": "proposed" if propose else "plan"}
        line["action"] = _action(person, {**line, "candidates": cand[i["item"]]["candidates"]}, by)
        paths.append(decision_path.get(i["item"], i["path"]))
        if propose:
            to = line["candidates"][0] if line["candidates"] else team
            if to:
                res = T._gate().submit_handoff(store, from_=person, to=to, topic=f"Handoff: {i['item']}",
                                               note=line["action"], source_ids=i["source_ids"][:10],
                                               at=ctx.at or T._now_iso(), status="proposed")
                line.update(handoff=res.record_id, to=to, receipt=res.receipt_id, gate=res.outcome)
                receipts.append(res.receipt_id)
            else:
                line.update(handoff=None, to=None, gate="not written: no candidate and no team to hand it to")
        rows.append(line)
        ids += i["source_ids"]
    questions = questions_for(store, person, info, items, cand, governs, owners, by)
    summary = {"kind": "summary", "person": person, "last_day": last_day, "lines": len(rows),
               "proposed": sum(1 for r in rows if r.get("handoff")), "questions": len(questions),
               "notice_source": info["notice_source"]}
    note = ("A plan, read-only: nothing was written or sent." if not propose else
            "Each line was written as a proposed Handoff through the write gate; a person approves it. Nothing was sent.")
    return T._finish(Answer(A.ANSWERED_WITH_PATH, "handoff_plan", args, [summary] + rows + questions, paths,
                            T._sources(store, ids + ([info["notice_source"]] if info["notice_source"] else [])),
                            T._receipts(store, receipts), note=note))


# --- promises: what was written down, and only that ------------------------------------------------------------------------

_RECORDS_SINCE = (
    "MATCH (e:Episode) WHERE e.ts > $since AND e.author = $person "
    "RETURN e.id AS id, e.ts AS ts, e.channel AS channel, e.author AS author, e.title AS title, e.text AS text "
    "UNION "
    "MATCH (e:Episode)-[:MENTIONS]->(:Person {name: $person}) WHERE e.ts > $since "
    "RETURN e.id AS id, e.ts AS ts, e.channel AS channel, e.author AS author, e.title AS title, e.text AS text")


def _snippet(text: str, match: re.Match) -> str:
    start = max(0, text.rfind("\n", 0, match.start()) + 1)
    end = text.find("\n", match.end())
    line = text[start:end if end >= 0 else len(text)][:200]
    try:
        from .gate import detect_pii
    except ImportError:
        return "[text withheld]"
    return "[redacted: personal data]" if detect_pii(line) else line


def search_promises(store, person: str, info: dict, when=None, to: str = "") -> dict:
    """Records written by or naming the person after `when` (default: their resignation), and the promises in them."""
    if when:
        since, since_label, basis = when.end, when.label, ""
    elif info["notice_at"]:
        since, since_label = info["notice_at"], iso_ts(info["notice_at"])[:10]
        basis = f" (their resignation, {info['notice_source']})"
    else:
        since, since_label, basis = 0, "the start of the records", ""
    records = store.ro(_RECORDS_SINCE, {"person": person, "since": since})
    by_channel = {c: 0 for c in CHANNELS}
    found = []
    for eid, ts, channel, author, title, text in sorted(records, key=lambda r: (r[1], r[0])):
        by_channel[channel] = by_channel.get(channel, 0) + 1
        m = PROMISE.search(text or "")
        if m and (not to or to.lower() in (text or "").lower()):
            found.append({"kind": "promise", "episode": eid, "at": iso_ts(ts), "channel": channel, "author": author,
                          "title": title, "line": _snippet(text, m)})
    return {"since": since_label, "basis": basis, "records": len(records), "by_channel": by_channel, "found": found}


def promises(store, a: dict, ctx) -> Answer:
    person, when, to = a["person"].name, a.get("since"), (a.get("to") or "").strip()
    info = departure_of(store, person)
    res = search_promises(store, person, info, when, to)
    since_label, basis, records, by_channel, found = (res["since"], res["basis"], res["records"], res["by_channel"],
                                                      res["found"])
    args = {"person": person, "since": since_label, **({"to": to} if to else {})}
    paths = [[person, "<-MENTIONS", r["episode"]] if r["author"] != person else [r["episode"]] for r in found]
    searched = {"kind": "searched", "person": person, "since": since_label, "records": records,
                "by_channel": by_channel, "not_received": ["phone calls", "meetings", "email outside tickets"]}
    sources = T._sources(store, [info["notice_source"]] if info["notice_source"] else [])
    if not found:
        counts = ", ".join(f"{n} {c} record{'s' if n != 1 else ''}" for c, n in by_channel.items())
        note = (f"Not in the graph: no record written by or naming {person} after {since_label}{basis} records a "
                f"promise{' involving ' + to if to else ''}. Searched {records} records ({counts}). Phone calls "
                "and meetings are not channels this memory receives, so an unwritten promise cannot be in it.")
        return T._finish(Answer(A.NOT_IN_GRAPH, "promises", args, [searched], [], sources, note=note))
    return T._finish(Answer(A.ANSWERED_WITH_PATH, "promises", args, found + [searched], paths,
                            T._sources(store, [r["episode"] for r in found]) + sources))


# --- the catalogue entries ---------------------------------------------------------------------------------------------

PERSON = T.Arg("person", "entity", "Full name of the person, e.g. Oskar Alvarez.", labels=("Person",))
AS_OF = T.Arg("as_of", "date", "Read the memory as it stood on this date (whole UTC day).", required=False)

DEPARTURE_TOOLS = (
    T.Tool("what_leaves", "What leaves with a person: at depth 1 what they own, lead and belong to; at depth 2-4 also "
           "the decisions they made that govern systems other people own, and what depends on those systems, each "
           "with its path and sources. Also works before a vacation, leave or team move.",
           (PERSON, T.Arg("depth", "int", "How many hops to walk from the person (1-4).", required=False, low=1,
                          high=T.MAX_DEPTH, default=T.MAX_DEPTH), AS_OF), what_leaves),
    T.Tool("single_source", "Live decisions and systems where every source record was written by this one person, "
           "with the records; items with a second author are listed as checked.", (PERSON, AS_OF), single_source),
    T.Tool("without", "What-if: what nobody in the graph would be connected to without this person (islands), and "
           "through which of their facts it was connected. Runs connected components on a temporary copy.",
           (PERSON,), without),
    T.Tool("bridges", "Who links teams: betweenness over people, teams and systems, with the teams each person "
           "links and the team pairs only one person links. The only per-person score in this memory.",
           (T.Arg("top", "int", "How many people to return (1-10).", required=False, low=1, high=10, default=3),),
           bridges),
    T.Tool("successors", "For each item a person leaves orphaned, the people who worked on it or next to it: "
           "candidates only, never a decision.", (PERSON,), successors),
    T.Tool("handoff_plan", "One handoff list for a person: each item at risk with why (path), sources, candidates "
           "and a suggested action. Read-only; propose=true writes each line as a proposed Handoff through the gate.",
           (PERSON, T.Arg("last_day", "date", "Their last day (default: the one their resignation record gives).",
                          required=False),
            T.Arg("propose", "bool", "Write each line as a proposed Handoff (a person approves; nothing is sent).",
                  required=False, default=False)), handoff_plan, write_flag="propose"),
    T.Tool("promises", "Promises a person made in the written records since a date (default: their resignation), "
           "optionally involving someone. Phone calls and meetings are not recorded; none found is not_in_graph.",
           (PERSON, T.Arg("since", "date", "Only records after this date (whole UTC day).", required=False),
            T.Arg("to", "text", "Who the promise would be to, e.g. a customer name.", required=False, low=0,
                  high=200)), promises),
)

T.TOOLS.update({t.name: t for t in DEPARTURE_TOOLS})
