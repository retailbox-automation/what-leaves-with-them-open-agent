"""CLOSE path holes found by the red team (redteam/attacks.jsonl, E01-E06). The fixture and helpers are those of
test_gate_close.py: Ana owns billing-api, is in Payments and leads Ledger Hardening; DOC-20 is her last-day record."""

import pytest

from honest_memory.gate import submit_end
from honest_memory.store import epoch
from tests.test_gate import typed_edges
from tests.test_gate_close import LAST_DAY, end, history, store  # noqa: F401  (store is a fixture)


def plant(store, id_, text, ts=LAST_DAY, title="", author="Cy", channel="doc"):
    store.merge_entity("Episode", id_, {"ts": epoch(ts), "channel": channel, "author": author, "title": title, "text": text})


def test_a_document_that_asserts_the_fact_does_not_end_it(store):
    # DOC-01 (the catalog), DOC-06 (the roster) and DOC-08 (the charter) say Ana owns, belongs to, leads: none says it stopped
    for predicate, obj, source in (("OWNS", "billing-api", "DOC-01"), ("MEMBER_OF", "Payments", "DOC-06"),
                                   ("LEADS", "Ledger Hardening", "DOC-08")):
        res = end(store, predicate, obj, sources=(source,))
        assert (res.outcome, res.reasons, res.written) == ("REJECT", ["ungrounded"], False), source
    assert typed_edges(store, "OWNS") == typed_edges(store, "MEMBER_OF") == typed_edges(store, "LEADS") == 1


def test_the_end_cue_must_be_on_the_line_that_names_both_ends(store):
    plant(store, "DOC-30", "Ana no longer reviews code.\nbilling-api is owned by Ana.")      # the cue is on another line
    assert end(store, sources=("DOC-30",)).reasons == ["ungrounded"]
    plant(store, "DOC-31", "Ana has stepped down from owning billing-api.")
    assert end(store, sources=("DOC-31",)).outcome == "CLOSE"


def test_a_title_does_not_stand_in_for_the_person_on_an_end(store):
    plant(store, "DOC-32", "Last day: Ana.\nThis record ends the roles below.\n- Bo no longer owns billing-api.",
          title="Last day: Ana")
    res = end(store, sources=("DOC-32",))
    assert (res.outcome, res.reasons) == ("REJECT", ["ungrounded"])
    assert typed_edges(store, "OWNS") == 1
    plant(store, "DOC-33", "Last day: Ana.\nThis record ends the roles below.\n- Ana no longer owns billing-api.",
          title="Last day: Ana")
    assert end(store, sources=("DOC-33",)).outcome == "CLOSE"


def test_an_end_cannot_be_dated_after_the_record_that_states_it(store):
    for at in ("2030-01-01T00:00:00Z", "2026-10-09T17:00:00Z"):                              # years ahead, one day ahead
        res = end(store, at=at)
        assert (res.outcome, res.reasons, res.written) == ("REJECT", ["valid_after_source"], False)
    assert end(store, at="2026-10-08T17:00:00Z").outcome == "CLOSE"                         # the record's own time


def test_an_end_cannot_be_backdated_far_before_the_record(store):
    res = end(store, at="2026-09-01T00:00:00Z")                                              # after the fact began, 5 weeks early
    assert (res.outcome, res.reasons, res.written) == ("REJECT", ["end_before_source"], False)
    assert typed_edges(store, "OWNS") == 1 and history(store, "OWNS", "billing-api") == []
    assert end(store, at="2026-10-07T18:00:00Z").outcome == "CLOSE"                         # a record filed a day late is fine


def test_the_end_checks_use_the_sources_that_state_it_not_the_others(store):
    # DOC-22 (2026-11-02) is cited as well but does not state the end: it must not make a later end date acceptable
    res = submit_end(store, "Ana", "OWNS", "billing-api", ["DOC-20", "DOC-22"], "2026-11-02T10:00:00Z")
    assert (res.outcome, res.reasons) == ("REJECT", ["valid_after_source"])
