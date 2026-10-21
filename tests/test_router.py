"""The deterministic router against the seed-42 corpus questions.

Routing is pure (no database). The answer check ingests the seed-42 corpus through the extractor and the
write gate into a tenant on every engine, runs the routed tool and looks for the gold answer's names and
sources in the result.
"""

import json
import os
import re

import pytest

from honest_memory import answer as A
from honest_memory import tools as T
from honest_memory.corpus.generator import build
from honest_memory.router import route
from tests.conftest import PORTS, make_store

QUESTIONS = build(42).questions

# What a person would call the right tool for each question shape, written independently of the router.
RIGHT_TOOLS = [
    (r"^Who currently owns", {"current_owner"}),
    (r"^Who owned .* on ", {"owner_at"}),
    (r"^What changed in system ownership since", {"what_changed"}),
    (r"^Which ADRs were accepted since", {"what_changed"}),
    (r"^Which decision replaced", {"decision_trail"}),
    (r"^Who knows the most about|^Rank the people", {"who_knows"}),
    (r"^Where is it recorded that .* owns", {"current_owner", "why"}),
    (r"^Where was .* ownership recorded before", {"history", "owner_at"}),
    (r"^Where is the supersession", {"decision_trail", "why"}),
    (r"^Where is it recorded which team", {"facts_about"}),
    (r"^Is .* the current owner", {"why", "current_owner"}),
    (r"^According to the .* runbook", {"history", "current_owner"}),
    (r"^What is the (phone number|mailing address)", {"why"}),
    (r"^Who received the .* incident", {"recall"}),
    (r"^How many independent sources", {"current_owner", "why"}),
    (r"^Which team is", {"facts_about"}),
    (r"^Who leads the project", {"facts_about"}),
    (r"^What depends on", {"impact"}),
    (r"^Who decided ADR", {"facts_about", "decision_trail"}),
    # N5, a departure
    (r"^What leaves with|^What does .* own, lead or belong to|^Which decisions of .* govern", {"what_leaves"}),
    (r"rest only on records|^What does only .* know", {"single_source"}),
    (r"connected to without|becomes an island", {"without"}),
    (r"bridge between teams|only person linking", {"bridges"}),
    (r"^Who could take over", {"successors"}),
    (r"handoff plan", {"handoff_plan"}),
    (r"^Did .* promise", {"promises"}),
    (r"^Who owns .* now\?", {"current_owner"}),
]


def right_tools(question: str) -> set[str]:
    return next(tools for pat, tools in RIGHT_TOOLS if re.search(pat, question))


def test_every_question_shape_has_a_right_tool():
    for q in QUESTIONS:
        assert right_tools(q["question"])


def test_router_picks_the_right_tool_for_every_corpus_question():
    wrong = [(q["id"], q["question"], route(q["question"])) for q in QUESTIONS
             if (r := route(q["question"])) is None or r.tool not in right_tools(q["question"])]
    assert not wrong, wrong


@pytest.mark.parametrize("question,tool,args", [
    ("Who owns billing-api?", "current_owner", {"system": "billing-api"}),
    ("who is the owner of auth", "current_owner", {"system": "auth"}),
    ("What breaks if ledger-db goes down?", "impact", {"system": "ledger-db"}),
    ("What depends on ledger-db, 2 hops out?", "impact", {"system": "ledger-db", "depth": 2}),
    ("Who should I call about billing-api?", "escalation_path", {"system": "billing-api"}),
    ("How is Luca Ostrander connected to status-page?", "path_between", {"a": "Luca Ostrander", "b": "status-page"}),
    ("What did we conclude about billing-api?", "recall", {"entity": "billing-api"}),
    ("Why does the memory think Luca Ostrander owns billing-api?", "why",
     {"subject": "Luca Ostrander", "predicate": "OWNS", "object": "billing-api"}),
    ("What changed since 2026-09-20?", "what_changed", {"since": "2026-09-20"}),
    ("Who owns billing-api as of 2026-09-01?", "owner_at", {"system": "billing-api", "date": "2026-09-01"}),
    ("What do we know about Selma Marsh?", "facts_about", {"entity": "Selma Marsh"}),
    ("What do we lose if Oskar Alvarez leaves, 2 hops out?", "what_leaves", {"person": "Oskar Alvarez", "depth": 2}),
    ("What leaves with Oskar Alvarez as of 2026-10-01?", "what_leaves",
     {"person": "Oskar Alvarez", "as_of": "2026-10-01"}),
    ("Who are the top 5 bridges between teams?", "bridges", {"top": 5}),
    ("What did Oskar Alvarez promise Fernhill Outfitters since 2026-09-01?", "promises",
     {"person": "Oskar Alvarez", "to": "Fernhill Outfitters", "since": "2026-09-01"}),
    ("Did Lars Thorne promise anything?", "promises", {"person": "Lars Thorne"}),
    ("Who could replace Sana Petrov?", "successors", {"person": "Sana Petrov"}),
    ("What rests only on records written by Sana Petrov as of 2026-09-10?", "single_source",
     {"person": "Sana Petrov", "as_of": "2026-09-10"}),
])
def test_router_handles_other_phrasings(question, tool, args):
    r = route(question)
    assert r is not None and (r.tool, r.args) == (tool, args)


def test_router_returns_none_when_no_tool_fits():
    for q in ("What is the weather in Northfield?", "Write me a poem", "", "hello"):
        assert route(q) is None


def test_router_passes_names_through_untouched():
    """A made-up name reaches the tool as written; the tool, not the router, says it is unknown."""
    r = route("Who owns payment-service?")
    assert r.tool == "current_owner" and r.args == {"system": "payment-service"}


# --- against the graph --------------------------------------------------------------------

def gold_names(answer_text: str, names: set[str]) -> set[str]:
    return {n for n in names if re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", answer_text)} | set(
        re.findall(r"(?<![\w-])(?:ADR-\d{4}|TCK-\d{4}|msg-\d{4}|RB-\d{2}|PM-\d{2}|DOC-\d{2})(?![\w-])", answer_text))


def evaluate(store) -> dict:
    names = T.all_names(store)
    rows = []
    for q in QUESTIONS:
        r = route(q["question"])
        ans = T.call(store, r.tool, r.args) if r else None
        blob = (A.allowed_text(ans) + ans.sentence) if ans else ""
        want_names = gold_names(q["answer"], names)
        rows.append({
            "id": q["id"], "type": q["type"], "question": q["question"], "tool": r.tool if r else None,
            "outcome": ans.outcome if ans else None,
            "right_tool": bool(r) and r.tool in right_tools(q["question"]),
            "names": all(n in blob for n in want_names), "missing_names": sorted(n for n in want_names if n not in blob),
            "sources": all(s in blob for s in q["source_ids"]),
            "missing_sources": [s for s in q["source_ids"] if s not in blob],
            "phone_leak": "555-01" in blob,
        })
    n = len(rows)
    return {"rows": rows, "n": n,
            "right_tool": sum(r["right_tool"] for r in rows), "names": sum(r["names"] for r in rows),
            "sources": sum(r["sources"] for r in rows),
            "all": sum(r["right_tool"] and r["names"] and r["sources"] for r in rows)}


@pytest.fixture(scope="module", params=PORTS, ids=lambda p: f"falkordb:{p}")
def gold_store(request, tmp_path_factory):
    from honest_memory.corpus.generator import write_corpus
    from honest_memory.ingest import ingest_corpus

    out = write_corpus(build(42), tmp_path_factory.mktemp("corpus") / "corpus")
    s = make_store(request.param, f"gold{request.param}")
    s.summary = ingest_corpus(s, out)
    s.trace.clear()
    yield s
    s.drop()


def test_gold_questions_answered_with_the_gold_names(gold_store):
    result = evaluate(gold_store)
    failures = [r for r in result["rows"] if not (r["right_tool"] and r["names"] and r["sources"])]
    summary = (f"{result['all']}/{result['n']} fully answered; right tool {result['right_tool']}, gold names "
               f"{result['names']}, gold sources {result['sources']}")
    print(summary)
    for f in failures:
        print("  FAIL", f["id"], f["tool"], f["outcome"], f["question"], f["missing_names"], f["missing_sources"])
    assert result["right_tool"] == result["n"], summary
    assert not any(r["phone_leak"] for r in result["rows"])
    assert result["all"] >= result["n"] - 2, summary
    n5 = [r for r in result["rows"] if r["id"] in {q["id"] for q in QUESTIONS if q["scenario"] == "N5"}]
    assert len(n5) == 16 and all(r["right_tool"] and r["names"] and r["sources"] for r in n5), \
        [(r["id"], r["missing_names"], r["missing_sources"]) for r in n5 if not (r["names"] and r["sources"])]


def test_adversarial_questions_are_answered_honestly(gold_store):
    rumour = T.call(gold_store, "why", {"subject": "Growth", "predicate": "OWNS", "object": "billing-api"})
    assert [r["reasons"] for r in rumour.rows if r["kind"] == "receipt"] == [["no_source", "conflicts_current"]]
    current = next(r for r in rumour.rows if r["kind"] == "current")
    assert current["since"].startswith("2026-09-22")
    pii = T.call(gold_store, "why", {"subject": "TCK-0044", "predicate": "HAS_PHONE"})
    assert pii.rows[0]["reasons"] == ["pii"] and pii.rows[0]["subject"] == "[personal data]"
    after = T.call(gold_store, "what_changed", {"since": "2026-09-23", "predicate": "OWNS"})
    # corpus 0.3.0: the only ownership changes after 2026-09-23 are the two the leaver's last day ends; the rumour
    # that Growth owns billing-api is still refused, not a change
    assert {(r["change"], r["subject"], r["object"]) for r in after.rows if r["change"] != "refused"} == {
        ("closed", "Oskar Alvarez", "fernhill-sso"), ("closed", "Oskar Alvarez", "oncall-runbook")}
    assert all(r["closed_by"] == "CLOSE" for r in after.rows if r["change"] == "closed")
    assert ("refused", "Growth", "billing-api") in {(r["change"], r["subject"], r["object"]) for r in after.rows}


if __name__ == "__main__":  # python tests/test_router.py [port]  -> the accuracy report
    import sys

    sys.path.insert(0, ".")
    import tempfile

    from honest_memory.corpus.generator import write_corpus
    from honest_memory.ingest import ingest_corpus

    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORTS[0]
    st = make_store(port, f"report{os.getpid()}")
    try:
        ingest_corpus(st, write_corpus(build(42), __import__("pathlib").Path(tempfile.mkdtemp()) / "corpus"))
        res = evaluate(st)
        print(json.dumps({k: v for k, v in res.items() if k != "rows"}))
        for row in res["rows"]:
            if not (row["right_tool"] and row["names"] and row["sources"]):
                print(row)
    finally:
        st.drop()
