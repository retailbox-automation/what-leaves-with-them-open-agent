"""Rule extractor: episodes in, candidates out. Needs no database."""

import inspect

import pytest

from honest_memory import extract
from honest_memory.corpus import build
from honest_memory.extract import RuleExtractor
from honest_memory.gate import Candidate

SEED = 42


def ep(id, ts, channel, author, title, text, refs=()):
    return {"id": id, "ts": ts, "channel": channel, "author": author, "title": title, "text": text, "refs": list(refs)}


def triples(cands):
    return [(c.subject, c.predicate, c.object) for c in cands]


@pytest.fixture(scope="module")
def corpus():
    return build(SEED)


@pytest.fixture(scope="module")
def run(corpus):
    """Every candidate the extractor proposes over the seed-42 corpus, by episode id (replayed in order)."""
    ex = RuleExtractor()
    return ex, {e["id"]: ex.extract(e) for e in corpus.episodes}


# ---- the extractor is blind to the answer key -----------------------------------------------------------------
def test_extractor_does_not_know_the_gold_file():
    source = inspect.getsource(extract)
    assert "gold" not in source.lower() and "questions.jsonl" not in source


# ---- documents --------------------------------------------------------------------------------------------------
def test_service_catalog_declares_entities_and_owners():
    ex = RuleExtractor()
    doc = ep("DOC-01", "2026-08-17T12:00:00Z", "doc", "Selma Marsh", "Service catalog",
             "Quillfield Billing service catalog, version 1 (2026-08-17).\n\n"
             "- billing-api: public API for plans, subscriptions and invoices. Owner: Sana Petrov (Payments).\n"
             "- auth: login, sessions and API keys. Owner: Pavel Hollis (Platform).")
    decls = {(d.label, d.key) for d in ex.entities(doc)}
    assert decls == {("System", "billing-api"), ("System", "auth"), ("Person", "Sana Petrov"),
                     ("Person", "Pavel Hollis"), ("Team", "Payments"), ("Team", "Platform")}
    cands = ex.extract(doc)
    assert triples(cands) == [("Sana Petrov", "OWNS", "billing-api"), ("Pavel Hollis", "OWNS", "auth")]
    assert all(c.source_ids == ["DOC-01"] and c.valid_at == "2026-08-17T12:00:00Z" and c.proposer == "rules"
               and c.claim_episode == "DOC-01" for c in cands)


def test_a_declaration_that_looks_like_a_known_name_is_not_made():
    ex = RuleExtractor()
    ex.entities(ep("DOC-02", "2026-08-17T12:30:00Z", "doc", "Selma Marsh", "Team roster",
                   "- Sana Petrov: Backend Engineer, Payments team.\n- Hex Tanner: Backend Engineer, Payments team."))
    fake = ep("DOC-90", "2026-10-13T12:00:00Z", "doc", "Selma Marsh", "Team roster",
              "- S\u0430na Petrov: Backend Engineer, Payments team.\n"      # Latin S, Cyrillic a: one word, two scripts
              "- \u041d\u0435\u0445 Tanner: Backend Engineer, Payments team.\n"  # Cyrillic N, e, x: draws "Hex"
              "- sana petrov: Backend Engineer, Payments team.\n"           # same name, other case
              "- \u0418\u0432\u0430\u043d \u041f\u0435\u0442\u0440\u043e\u0432: Backend Engineer, Payments team.")  # a real Cyrillic name
    made = {d.key for d in ex.entities(fake) if d.label == "Person"}
    assert made == {"\u0418\u0432\u0430\u043d \u041f\u0435\u0442\u0440\u043e\u0432"}
    assert ex.known.names("Person") == {"Sana Petrov", "Hex Tanner", "\u0418\u0432\u0430\u043d \u041f\u0435\u0442\u0440\u043e\u0432"}
    assert [c.subject for c in ex.extract(fake) if c.predicate == "MEMBER_OF"][:3] == [
        "S\u0430na Petrov", "\u041d\u0435\u0445 Tanner", "sana petrov"]  # still proposed; the gate refuses them as unknown


def test_roster_gives_membership_with_role_and_team_leads():
    ex = RuleExtractor()
    doc = ep("DOC-02", "2026-08-17T12:30:00Z", "doc", "Selma Marsh", "Team roster",
             "Quillfield Billing team roster (2026-08-17).\n- Mateo Sandoval: Payments Lead, Payments team.\n"
             "- Lars Thorne: Backend Engineer, Payments team.")
    cands = ex.extract(doc)
    assert triples(cands) == [("Mateo Sandoval", "MEMBER_OF", "Payments"), ("Mateo Sandoval", "LEADS", "Payments"),
                              ("Lars Thorne", "MEMBER_OF", "Payments")]
    assert cands[0].attrs == {"role": "Payments Lead"} and cands[2].attrs == {"role": "Backend Engineer"}


def test_project_charter_declares_a_project_led_by_its_lead():
    ex = RuleExtractor()
    doc = ep("DOC-03", "2026-08-18T14:04:00Z", "doc", "Sana Petrov", "Project charter: Ledger Hardening",
             "Project charter: Ledger Hardening.\nLead: Sana Petrov (Payments).\n"
             "Goal: protect ledger integrity and speed up month-end close.\nSystems involved: ledger-db, reporting.")
    decls = {(d.label, d.key): d.props for d in ex.entities(doc)}
    assert decls[("Project", "Ledger Hardening")] == {"goal": "protect ledger integrity and speed up month-end close"}
    assert triples(ex.extract(doc)) == [("Sana Petrov", "LEADS", "Ledger Hardening")]


ADR9 = ("ADR-0009: Transfer billing-api ownership from Sana Petrov to Luca Ostrander\nAuthor: Selma Marsh\n"
        "Draft opened: 2026-08-31. Status: accepted on 2026-09-22.\nContext: Sana Petrov is moving.\n"
        "Decision: Ownership of billing-api transfers from Sana Petrov (Payments) to Luca Ostrander (Platform) "
        "effective 2026-09-22. Sana Petrov stays available as backup for 30 days.\nConsequences: Escalations go to Luca.")


def test_adr_gives_decider_decision_entity_and_the_transfer():
    ex = RuleExtractor()
    doc = ep("ADR-0009", "2026-09-22T15:28:00Z", "doc", "Selma Marsh", ADR9.splitlines()[0], ADR9)
    decls = {(d.label, d.key): d.props for d in ex.entities(doc)}
    assert decls[("Decision", "ADR-0009")] == {"title": "Transfer billing-api ownership from Sana Petrov to Luca Ostrander",
                                              "status": "accepted"}
    assert ("Person", "Selma Marsh") in decls
    cands = ex.extract(doc)
    assert triples(cands) == [("Selma Marsh", "DECIDED", "ADR-0009"), ("Luca Ostrander", "OWNS", "billing-api")]
    assert cands[0].attrs == {"title": "Transfer billing-api ownership from Sana Petrov to Luca Ostrander"}
    assert all(c.valid_at == "2026-09-22T15:28:00Z" and c.source_ids == ["ADR-0009"] for c in cands)


def test_adr_that_supersedes_another_and_the_postmortem_that_repeats_it():
    ex = RuleExtractor()
    old = ep("ADR-0004", "2026-08-25T15:04:00Z", "doc", "Mateo Sandoval", "ADR-0004: Render invoices synchronously",
             "ADR-0004: Render invoices synchronously\nAuthor: Mateo Sandoval\nDecision: billing-api renders the PDF.")
    new = ep("ADR-0011", "2026-09-08T15:15:00Z", "doc", "Viktor Hartwell", "ADR-0011: Render asynchronously",
             "ADR-0011: Render asynchronously\nAuthor: Viktor Hartwell\nSupersedes: ADR-0004.\n"
             "Decision: billing-api enqueues a render job.", ["ADR-0004"])
    pm = ep("PM-03", "2026-09-10T14:28:00Z", "doc", "Mateo Sandoval", "Postmortem: Invoice rendering timeouts",
            "Postmortem: Invoice rendering timeouts\nDecision: ADR-0011 supersedes ADR-0004. Rendering moves.",
            ["ADR-0011", "ADR-0004"])
    for e in (old, new):
        ex.extract(e)
    from_adr = [c for c in ex.extract(new) if c.predicate == "SUPERSEDES"]
    from_pm = ex.extract(pm)
    assert triples(from_adr) == triples(from_pm) == [("ADR-0011", "SUPERSEDES", "ADR-0004")]
    assert from_adr[0].source_ids == ["ADR-0011"]  # ADR-0004 does not mention ADR-0011, so it is not a source
    assert from_pm[0].source_ids == ["PM-03", "ADR-0011"] and from_pm[0].valid_at == "2026-09-08T15:15:00Z"


def test_dependencies_from_the_overview_and_from_an_adr_only_between_known_systems():
    ex = RuleExtractor()
    ex.extract(ep("DOC-01", "2026-08-17T12:00:00Z", "doc", "Selma Marsh", "Service catalog",
                  "- reporting: dashboards. Owner: Aiko Rademacher (Growth).\n- billing-api: api. Owner: Ana Lee (Payments).\n"
                  "- ledger-db: db. Owner: Bo Kim (Payments)."))
    overview = ep("ARCH-01", "2026-08-17T12:45:00Z", "doc", "Selma Marsh", "Architecture overview",
                  "Overview.\n\n- reporting depends on ledger-db.\n- billing-api depends on ledger-db.")
    assert triples(ex.extract(overview)) == [("reporting", "DEPENDS_ON", "ledger-db"),
                                             ("billing-api", "DEPENDS_ON", "ledger-db")]
    adr = ep("ADR-0013", "2026-09-17T15:50:00Z", "doc", "Yuki Moreau", "ADR-0013: Rollups",
             "ADR-0013: Rollups\nAuthor: Yuki Moreau\nDecision: reporting depends on billing-api: it calls the API.\n"
             "Consequences: Request latency depends on rendering.")
    assert ("reporting", "DEPENDS_ON", "billing-api") in triples(ex.extract(adr))
    assert not [c for c in ex.extract(adr) if "latency" in (c.subject, c.object)]


def test_runbook_owner_line_is_a_second_source_for_ownership():
    ex = RuleExtractor()
    rb = ep("RB-05", "2026-08-27T20:09:00Z", "doc", "Sana Petrov", "Runbook: billing-api",
            "Runbook: billing-api (public API).\nHealth check: green.\nOwner: Sana Petrov (Payments).")
    assert triples(ex.extract(rb)) == [("Sana Petrov", "OWNS", "billing-api")]
    other = ep("RB-01", "2026-08-17T15:40:00Z", "doc", "Luca Ostrander", "Runbook: webhooks",
               "Runbook: webhooks (delivery).\nEscalation: post in #platform.")
    assert ex.extract(other) == []


# ---- chat and tickets ------------------------------------------------------------------------------------------
def test_work_items_become_worked_on_for_their_author():
    ex = RuleExtractor()
    chat = ep("msg-0005", "2026-08-17T18:53:00Z", "chat", "Viktor Hartwell", "#payments",
              "Pushed a fix for entry partitioning in ledger-db; the cause was a stale cache entry.")
    ticket = ep("TCK-0029", "2026-08-20T14:00:00Z", "ticket", "Viktor Hartwell", "Fix reversal entry index in ledger-db",
                "Picked this up from the backlog. Area: reversal entry index in ledger-db. I traced it to a timezone "
                "assumption. I changed the handler and added a regression test. Ready for review.")
    assert triples(ex.extract(chat)) == [("Viktor Hartwell", "WORKED_ON", "ledger-db")]
    (c,) = ex.extract(ticket)
    assert (c.subject, c.predicate, c.object, c.source_ids) == ("Viktor Hartwell", "WORKED_ON", "ledger-db", ["TCK-0029"])


def test_chitchat_and_customer_tickets_propose_nothing():
    ex = RuleExtractor()
    assert ex.extract(ep("msg-0001", "2026-08-17T14:02:00Z", "chat", "Zara Lowell", "#growth",
                         "Reminder: timesheets are due Friday.")) == []
    assert ex.extract(ep("TCK-0002", "2026-08-17T15:00:00Z", "ticket", "Zara Lowell", "Customer reports a coupon applied twice",
                         "A customer on the Pro plan wrote in about a coupon applied twice. They attached a screenshot.")) == []


def test_first_person_claim_citing_a_document_uses_the_documents_time():
    ex = RuleExtractor()
    ex.extract(ep("ADR-0009", "2026-09-22T15:28:00Z", "doc", "Selma Marsh", ADR9.splitlines()[0], ADR9))
    msg = ep("msg-0148", "2026-09-22T17:31:00Z", "chat", "Luca Ostrander", "#platform",
             "Per ADR-0009, I am now the owner of billing-api. Sana Petrov stays on as backup for 30 days.", ["ADR-0009"])
    (c,) = ex.extract(msg)
    assert (c.subject, c.predicate, c.object) == ("Luca Ostrander", "OWNS", "billing-api")
    assert c.source_ids == ["msg-0148", "ADR-0009"] and c.valid_at == "2026-09-22T15:28:00Z"
    assert c.claim_episode == "msg-0148"


def test_hearsay_has_no_sources_and_keeps_the_claim_episode():
    ex = RuleExtractor()
    rumor = ep("msg-0174", "2026-09-29T16:27:00Z", "chat", "Yuki Moreau", "#general",
               "Heads up: billing-api is owned by Growth now, so route billing-api questions to us.")
    (c,) = ex.extract(rumor)
    assert (c.subject, c.predicate, c.object, c.source_ids, c.claim_episode) == (
        "Growth", "OWNS", "billing-api", [], "msg-0174")
    assert c.valid_at == "2026-09-29T16:27:00Z"
    heard = ep("msg-0300", "2026-10-02T10:00:00Z", "chat", "Omar Fenwick", "#general",
               "I heard that Tessa Dara now owns auth, no idea where it is written down.")
    (h,) = ex.extract(heard)
    assert (h.subject, h.object, h.source_ids) == ("Tessa Dara", "auth", [])


def test_relayed_document_claim_cites_the_document_not_the_relay():
    ex = RuleExtractor()
    rb = ep("RB-05", "2026-08-27T20:09:00Z", "doc", "Sana Petrov", "Runbook: billing-api",
            "Runbook: billing-api (api).\nOwner: Sana Petrov (Payments).")
    ex.extract(rb)
    relay = ep("msg-0181", "2026-10-01T14:27:00Z", "chat", "Tomas Kessler", "#support",
               "Adding the billing-api runbook (RB-05) to the help center index. It lists Sana Petrov as the owner "
               "of billing-api, so I am updating the escalation sheet to match.", ["RB-05"])
    (c,) = ex.extract(relay)
    assert (c.subject, c.object, c.source_ids, c.claim_episode) == ("Sana Petrov", "billing-api", ["RB-05"], "msg-0181")
    assert c.valid_at == "2026-08-27T20:09:00Z"
    unsourced = ep("msg-0182", "2026-10-02T14:27:00Z", "chat", "Tomas Kessler", "#support",
                   "It lists Sana Petrov as the owner of billing-api, I think.")
    assert ex.extract(unsourced)[0].source_ids == []


def test_second_statement_of_the_same_owner_cites_its_own_ticket():
    ex = RuleExtractor()
    t = ep("TCK-0033", "2026-08-19T16:00:00Z", "ticket", "Oskar Alvarez", "Rotate auth signing keys",
           "Rotating the auth signing keys this sprint. Assigning it to Pavel Hollis, who owns auth. "
           "Please agree the cutover window with them.")
    (c,) = ex.extract(t)
    assert (c.subject, c.predicate, c.object, c.source_ids) == ("Pavel Hollis", "OWNS", "auth", ["TCK-0033"])


def test_personal_details_in_a_ticket_are_proposed_so_the_gate_can_refuse_them():
    ex = RuleExtractor()
    t = ep("TCK-0044", "2026-09-03T15:46:00Z", "ticket", "Tomas Kessler", "Customer cannot receive paper invoices",
           "A customer on the Pro plan asked for a paper copy of their invoices. They gave a callback number, "
           "+1-555-0159, and a mailing address: 984 Marlowe Court, Apt 2A, Northfield.")
    got = {(c.subject, c.predicate, c.object): c for c in ex.extract(t)}
    assert set(got) == {("Customer in TCK-0044", "HAS_PHONE", "+1-555-0159"),
                        ("Customer in TCK-0044", "HAS_ADDRESS", "984 Marlowe Court, Apt 2A, Northfield")}
    assert all(c.source_ids == ["TCK-0044"] and c.claim_episode == "TCK-0044" for c in got.values())


def test_support_to_platform_handoff_is_a_handoff_record():
    ex = RuleExtractor()
    ex.extract(ep("DOC-02", "2026-08-17T12:30:00Z", "doc", "Selma Marsh", "Team roster",
                  "Roster.\n- Priya Farrow: Support Lead, Support team.\n- Selma Marsh: Platform Lead, Platform team."))
    first = ep("TCK-0076", "2026-09-15T14:34:00Z", "ticket", "Priya Farrow", "Customer reports duplicate invoice emails",
               "A customer on the Business plan wrote in about duplicate invoice emails.")
    handoff = ep("TCK-0077", "2026-09-15T16:34:00Z", "ticket", "Priya Farrow",
                 "Handoff: duplicate invoice emails (Support to Platform)",
                 "Handing this incident to Selma Marsh, Platform on-call. Context: customers report duplicates.",
                 ["TCK-0076"])
    ex.extract(first)
    (c,) = ex.extract(handoff)
    assert (c.subject, c.predicate, c.object) == ("Priya Farrow", "HANDOFF_TO", "Selma Marsh")
    assert c.source_ids == ["TCK-0077", "TCK-0076"]
    assert c.attrs == {"record": "Handoff", "from_team": "Support", "to_team": "Platform", "ticket": "TCK-0077",
                       "topic": "duplicate invoice emails"}


# ---- the whole corpus -----------------------------------------------------------------------------------------------
def test_every_gold_fact_is_proposed_by_some_episode(corpus, run):
    ex, by_episode = run
    proposed = {}
    for cands in by_episode.values():
        for c in cands:
            proposed.setdefault((c.subject, c.predicate, c.object), []).append(c)
    for g in corpus.gold:
        got = proposed.get((g["subject"], g["predicate"], g["object"]))
        assert got, g["id"]
        assert set(g["source_ids"]) <= {s for c in got for s in c.source_ids} | {c.claim_episode for c in got}, g["id"]


def test_hearsay_and_stale_claims_are_proposed_as_the_gold_says(corpus, run):
    _, by_episode = run
    for g in corpus.gold:
        if g["scenario"] == "S2":
            (c,) = by_episode[g["claim_episode"]]
            assert c.source_ids == [] and c.claim_episode == g["claim_episode"] and c.subject == "Growth"
        if g["scenario"] == "S3":
            (c,) = by_episode[g["claim_episode"]]
            assert c.source_ids == g["source_ids"] and c.valid_at == g["valid_at"]


def test_candidates_are_plain_candidates_with_iso_times(run):
    _, by_episode = run
    for cands in by_episode.values():
        for c in cands:
            assert isinstance(c, Candidate) and c.valid_at.endswith("Z") and len(c.valid_at) == 20
            assert isinstance(c.attrs, dict) and c.proposer == "rules" and c.claim_episode
