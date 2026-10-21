"""A small hand-built graph for the read-tool tests, modelled on corpus scenario S1.

Built only through store.py helpers plus direct Cypher for the record nodes, exactly as
docs/ARCHITECTURE.md describes the graph, so the tests do not depend on the write gate.

Contents: billing-api changes owner (Sana Petrov -> Luca Ostrander, ADR-0009 and a chat message);
a 4-hop DEPENDS_ON chain ending in ledger-db plus a diamond; a SUPERSEDES chain (ADR-0011 over
ADR-0004); WORKED_ON counts with a tie; a refused rumour, a refused stale claim and a refused
personal-data write; two findings, a handoff between agents and a handoff between teams.
"""

from __future__ import annotations

from honest_memory.store import Store, epoch

PEOPLE = {  # name -> (team, role)
    "Sana Petrov": ("Payments", "Backend Engineer"),
    "Mateo Sandoval": ("Payments", "Payments Lead"),
    "Luca Ostrander": ("Platform", "Site Reliability Engineer"),
    "Selma Marsh": ("Platform", "Platform Lead"),
    "Pavel Hollis": ("Platform", "Backend Engineer"),
    "Priya Farrow": ("Support", "Support Lead"),
    "Aiko Rademacher": ("Growth", "Product Analyst"),
}
LEADS = {"Payments": "Mateo Sandoval", "Platform": "Selma Marsh", "Support": "Priya Farrow"}  # Growth has none
SYSTEMS = ["billing-api", "invoice-renderer", "ledger-db", "auth", "notifications", "status-page", "reporting",
           "webhooks"]  # webhooks: no owner, no dependents

EPISODES = {  # id -> (channel, ts, author, title)
    "DOC-01": ("doc", "2026-08-17T12:00:00Z", "Selma Marsh", "Service catalog"),
    "DOC-02": ("doc", "2026-08-17T12:30:00Z", "Selma Marsh", "Team roster"),
    "RB-05": ("doc", "2026-08-19T14:10:00Z", "Sana Petrov", "Runbook: billing-api"),
    "TCK-0033": ("ticket", "2026-08-26T16:00:00Z", "Luca Ostrander", "Rotate auth signing keys"),
    "ADR-0004": ("doc", "2026-08-24T15:00:00Z", "Mateo Sandoval", "ADR-0004: Render invoices synchronously"),
    "ADR-0011": ("doc", "2026-09-08T15:15:00Z", "Pavel Hollis",
                 "ADR-0011: Render invoices asynchronously through invoice-renderer"),
    "PM-03": ("doc", "2026-09-09T14:30:00Z", "Mateo Sandoval", "Postmortem: invoice rendering timeouts"),
    "ADR-0009": ("doc", "2026-09-22T15:28:00Z", "Selma Marsh",
                 "ADR-0009: Transfer billing-api ownership from Sana Petrov to Luca Ostrander"),
    "msg-0148": ("chat", "2026-09-22T17:31:00Z", "Luca Ostrander", "#platform"),
    "msg-0174": ("chat", "2026-09-29T16:27:00Z", "Aiko Rademacher", "#general"),
    "msg-0181": ("chat", "2026-09-30T14:00:00Z", "Priya Farrow", "#support"),
    "TCK-0044": ("ticket", "2026-09-03T15:46:00Z", "Priya Farrow", "Customer cannot receive paper invoices"),
    "TCK-0076": ("ticket", "2026-09-15T14:34:00Z", "Priya Farrow", "Customer reports duplicate invoice emails"),
    "TCK-0077": ("ticket", "2026-09-15T16:34:00Z", "Priya Farrow",
                 "Handoff: duplicate invoice emails (Support to Platform)"),
}

T_ADR9 = epoch("2026-09-22T15:28:00Z")


class Builder:
    def __init__(self, store: Store):
        self.s = store
        self.n = 0
        self.gate: dict[str, str] = {}

    def receipt(self, gid, outcome, reasons, ts, predicate, subject, obj, cites=(), about=(), claim=None):
        props = {"id": gid, "outcome": outcome, "reasons": list(reasons), "ts": epoch(ts), "predicate": predicate,
                 "subject": subject, "object": obj, "proposer": "extractor:rules"}
        if claim:
            props["claim_episode"] = claim
        self.s.rw("CREATE (g:GateDecision) SET g = $p", {"p": props})
        if claim and claim not in cites:  # where a refused claim was read, when it is not one of its sources
            self.s.rw("MATCH (g:GateDecision {id: $g}), (e:Episode {id: $e}) CREATE (g)-[:CLAIMED_IN]->(e)",
                      {"g": gid, "e": claim})
        for ep in cites:
            self.s.rw("MATCH (g:GateDecision {id: $g}), (e:Episode {id: $e}) CREATE (g)-[:CITES]->(e)", {"g": gid, "e": ep})
        for label, key, name in about:
            self.s.rw(f"MATCH (g:GateDecision {{id: $g}}), (n:{label} {{{key}: $n}}) CREATE (g)-[:ABOUT]->(n)",
                      {"g": gid, "n": name})

    def fact(self, predicate, subject, obj, valid_at, sources, label_s, label_o, props=None, gid=None, outcome="ACCEPT"):
        self.n += 1
        gid = gid or f"gd-{self.n:04d}"
        key_s = "id" if label_s == "Decision" else "name"
        key_o = "id" if label_o == "Decision" else "name"
        self.s.create_fact(predicate, subject, obj, {"valid_at": epoch(valid_at), "source_ids": list(sources),
                                                     "gate_id": gid, **(props or {})})
        self.receipt(gid, outcome, [], valid_at, predicate, subject, obj, cites=[x for x in sources if x in EPISODES],
                     about=[(label_s, key_s, subject), (label_o, key_o, obj)])
        return gid


def build_fixture(store: Store) -> dict[str, str]:
    """Create the graph in an empty tenant; returns ids the tests assert on."""
    s = store
    s.ensure_schema()
    b = Builder(s)
    for team in ("Payments", "Platform", "Support", "Growth"):
        s.merge_entity("Team", team)
    for name in PEOPLE:
        s.merge_entity("Person", name)
    for name in SYSTEMS:
        s.merge_entity("System", name)
    for adr, status in (("ADR-0004", "superseded"), ("ADR-0009", "accepted"), ("ADR-0011", "accepted")):
        s.merge_entity("Decision", adr, {"status": status})
    for agent in ("agent:support", "agent:ops"):
        s.merge_entity("Agent", agent)
    for eid, (channel, ts, author, title) in EPISODES.items():
        s.rw("CREATE (e:Episode) SET e = $p", {"p": {"id": eid, "ts": epoch(ts), "channel": channel, "author": author,
                                                     "title": title, "text": title}})

    t0 = "2026-08-17T12:00:00Z"
    for name, (team, role) in PEOPLE.items():
        b.fact("MEMBER_OF", name, team, t0, ["DOC-02"], "Person", "Team", {"role": role})
    for team, lead in LEADS.items():
        b.fact("LEADS", lead, team, t0, ["DOC-02"], "Person", "Team")

    # billing-api: Sana owned it from the catalog; ADR-0009 and a chat message moved it to Luca.
    b.gate["sana_owns"] = b.fact("OWNS", "Sana Petrov", "billing-api", t0, ["DOC-01", "RB-05"], "Person", "System")
    s.close_fact("OWNS", "Sana Petrov", "billing-api", T_ADR9, "gd-0900")
    b.gate["luca_owns"] = b.fact("OWNS", "Luca Ostrander", "billing-api", "2026-09-22T15:28:00Z",
                                 ["ADR-0009", "msg-0148"], "Person", "System", gid="gd-0900", outcome="SUPERSEDE")
    for system, owner, sources in (("invoice-renderer", "Mateo Sandoval", ["DOC-01"]), ("ledger-db", "Mateo Sandoval", ["DOC-01"]),
                                   ("auth", "Pavel Hollis", ["DOC-01", "TCK-0033"]), ("notifications", "Selma Marsh", ["DOC-01"]),
                                   ("status-page", "Priya Farrow", ["DOC-01"]), ("reporting", "Aiko Rademacher", ["DOC-01"])):
        b.fact("OWNS", owner, system, t0, sources, "Person", "System")

    # a 4-hop chain (status-page > notifications > invoice-renderer > billing-api > ledger-db) and a diamond
    for up, down, since, src in (("status-page", "notifications", t0, "DOC-01"), ("notifications", "invoice-renderer", t0, "DOC-01"),
                                 ("invoice-renderer", "billing-api", t0, "DOC-01"), ("billing-api", "ledger-db", t0, "DOC-01"),
                                 ("reporting", "billing-api", "2026-09-17T15:50:00Z", "ADR-0009"),  # added later
                                 ("reporting", "ledger-db", t0, "DOC-01")):
        b.fact("DEPENDS_ON", up, down, since, [src], "System", "System", {"weight": 1})

    for person, system, count in (("Mateo Sandoval", "ledger-db", 10), ("Pavel Hollis", "ledger-db", 7),
                                  ("Sana Petrov", "ledger-db", 5), ("Luca Ostrander", "ledger-db", 3),
                                  ("Selma Marsh", "ledger-db", 2), ("Priya Farrow", "ledger-db", 1),
                                  ("Luca Ostrander", "billing-api", 6), ("Sana Petrov", "billing-api", 4),
                                  ("Pavel Hollis", "billing-api", 4)):
        b.fact("WORKED_ON", person, system, "2026-08-20T13:00:00Z", [f"msg-9{i:03d}" for i in range(count)],
               "Person", "System", {"count": count})

    for person, adr in (("Mateo Sandoval", "ADR-0004"), ("Pavel Hollis", "ADR-0011"), ("Selma Marsh", "ADR-0009")):
        ts = EPISODES[adr][1]
        b.fact("DECIDED", person, adr, ts, [adr], "Person", "Decision")
    b.fact("SUPERSEDES", "ADR-0011", "ADR-0004", EPISODES["ADR-0011"][1], ["ADR-0011", "PM-03"], "Decision", "Decision")

    # three refused writes, each with a receipt
    b.receipt("gd-0100", "REJECT", ["no_source", "conflicts_current"], "2026-09-29T16:27:00Z", "OWNS", "Growth",
              "billing-api", about=[("Team", "name", "Growth"), ("System", "name", "billing-api")], claim="msg-0174")
    b.receipt("gd-0101", "REJECT", ["older_than_current"], "2026-09-30T14:00:00Z", "OWNS", "Sana Petrov", "billing-api",
              cites=["RB-05"], about=[("Person", "name", "Sana Petrov"), ("System", "name", "billing-api")],
              claim="msg-0181")
    b.receipt("gd-0102", "REJECT", ["pii"], "2026-09-03T15:46:00Z", "HAS_PHONE", "Customer in TCK-0044", "+1-555-0159",
              cites=["TCK-0044"])

    # memory written by agents: two findings about billing-api (one predates the ownership change) and two handoffs
    for fid, ts, text, session, srcs in (
            ("f-001", "2026-09-10T10:00:00Z", "billing-api is owned by Sana Petrov per the service catalog.", "s0", ["DOC-01"]),
            ("f-002", "2026-09-25T10:00:00Z", "A customer asked who owns billing-api; per ADR-0009 it is Luca Ostrander.",
             "s1", ["ADR-0009"])):
        s.rw("MATCH (a:Agent {name: 'agent:support'}), (n:System {name: 'billing-api'}) "
             "CREATE (f:Finding {id: $id, text: $text, ts: $ts, session: $session}) "
             "CREATE (a)-[:WROTE]->(f) CREATE (f)-[:ABOUT]->(n)",
             {"id": fid, "text": text, "ts": epoch(ts), "session": session})
        for src in srcs:
            s.rw("MATCH (f:Finding {id: $f}), (e:Episode {id: $e}) CREATE (f)-[:DERIVED_FROM]->(e)", {"f": fid, "e": src})
    s.rw("MATCH (a:Agent {name: 'agent:support'}), (b:Agent {name: 'agent:ops'}), (e:Episode {id: 'TCK-0076'}) "
         "CREATE (h:Handoff {id: 'h-001', topic: 'billing-api escalations', note: 'Escalate billing-api tickets to ops.', "
         "ts: $ts}) CREATE (h)-[:FROM]->(a) CREATE (h)-[:TO]->(b) CREATE (h)-[:CITES]->(e)",
         {"ts": epoch("2026-09-25T10:05:00Z")})
    s.rw("MATCH (t:Team {name: 'Support'}), (p:Person {name: 'Selma Marsh'}), (e1:Episode {id: 'TCK-0077'}), "
         "(e2:Episode {id: 'TCK-0076'}), (r:System {name: 'invoice-renderer'}) "
         "CREATE (h:Handoff {id: 'h-002', topic: 'duplicate invoice emails', note: 'Platform on-call takes it.', ts: $ts}) "
         "CREATE (h)-[:FROM]->(t) CREATE (h)-[:TO]->(p) CREATE (h)-[:CITES]->(e1) CREATE (h)-[:CITES]->(e2) "
         "CREATE (e2)-[:MENTIONS]->(r)", {"ts": epoch("2026-09-15T16:34:00Z")})
    return b.gate
