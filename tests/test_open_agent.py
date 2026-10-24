"""open_agent: Investigator <-> Verifier over the three sources, with a fake Zetaris and a fake model.

The local sample engine (SQLite over sample_data/sources/) stands in for Zetaris; FakeOpenAI stands in for the
model and answers by prompt. Graph tests run on every FalkorDB in HM_TEST_PORTS, like the rest of the suite.
"""

from __future__ import annotations

import json
import os

import httpx
import openai
import pytest
from openai.types.chat import ChatCompletion

from honest_memory.corpus import build, write_corpus
from honest_memory.extract import RuleExtractor
from honest_memory.ingest import ingest_corpus, ingest_episodes, read_episodes
from open_agent import agents as AG
from open_agent import run as R
from open_agent.llm import LLMConfig, TracedClient, parse_json_object, provider_receipt
from open_agent.sources import (INGEST, LocalSample, SourceUnavailable, ZetarisSource, by_id, episode, mentions,
                                read_all, sqlite_text)
from open_agent.trace import Trace
from tests.conftest import PORTS, make_store

STALE = "Per ADR-0009, I am now the owner of billing-api."
EDITED = "Per ADR-0009, the billing-api transfer is on hold. Sana Petrov remains the owner of billing-api."
ADR_LINE = ("Decision: Ownership of billing-api transfers from Sana Petrov (Payments) to Luca Ostrander (Platform) "
            "effective 2026-09-22.")
QUESTION = "Who owns billing-api now?"


@pytest.fixture(scope="module")
def corpus_dir(tmp_path_factory):
    return write_corpus(build(42), tmp_path_factory.mktemp("corpus") / "corpus")


@pytest.fixture(scope="module", params=PORTS, ids=lambda p: f"falkordb:{p}")
def graph(request, tmp_path_factory):
    """One graph per engine, built from the local sample tables exactly as `run` builds it."""
    store = make_store(request.param, f"oa{request.param}")
    episodes = read_all(LocalSample(), Trace("t", log_dir=tmp_path_factory.mktemp("logs"), echo=False))
    ingest_episodes(store, episodes, reset=True, extractor=RuleExtractor())
    store.port = request.param
    yield store
    store.drop()


@pytest.fixture
def trace(tmp_path):
    return Trace("test", log_dir=tmp_path, echo=False)


# --- sources -------------------------------------------------------------------------------

def test_sqlite_rewrite_only_touches_the_explode():
    assert sqlite_text(INGEST["chat"]) == INGEST["chat"]
    assert sqlite_text(INGEST["ticket"]) == INGEST["ticket"]
    assert sqlite_text(INGEST["doc"]).endswith("FROM wlwt_docs.docs_raw d")
    assert "LATERAL" not in sqlite_text(by_id("doc", "ADR-0009"))


def test_sample_tables_hold_the_corpus_field_for_field(corpus_dir, trace):
    corpus = {e["id"]: e for e in read_episodes(corpus_dir)}
    got = read_all(LocalSample(), trace)
    assert len(got) == len(corpus) == 403
    assert all(e == {**corpus[e["id"]], "title": corpus[e["id"]].get("title", "")} for e in got)
    assert [e["rows"] for e in trace.events] == [225, 127, 51]
    assert {e["endpoint"] for e in trace.events} == {"local-sample"}


def test_zetaris_csv_row_is_normalised():
    # what Zetaris returns for a tickets-CSV row: ts inferred as a timestamp, an empty refs cell as null
    row = {"id": "TCK-0001", "ts": "2026-08-17 16:43:00.0", "author": "A", "title": "T", "text": "x", "refs": None}
    assert episode(row, "ticket") == {"id": "TCK-0001", "ts": "2026-08-17T16:43:00Z", "channel": "ticket",
                                      "author": "A", "title": "T", "text": "x", "refs": []}
    assert episode({"id": "m", "ts": "2026-08-17T14:02:00Z", "refs": "A B"}, "chat")["refs"] == ["A", "B"]


def test_point_reads_and_the_absence_query_with_a_control():
    src = LocalSample()
    assert src.sql(by_id("doc", "ADR-0009"))[0][0]["id"] == "ADR-0009"
    assert src.sql(by_id("chat", "msg-0148"))[0][0]["text"].startswith(STALE)
    assert src.sql(by_id("doc", "ADR-9999"))[0] == []
    assert src.sql(mentions("payroll-export"))[0] == []
    assert len(src.sql(mentions("fernhill-sso"))[0]) == 8      # must-light: the same query finds a known term
    assert src.sql(mentions("Fernhill-SSO"))[0] == []          # case-sensitive, like Zetaris


def test_local_tamper_and_restore():
    src = LocalSample()
    original = src.chat_text("msg-0148")
    src.set_chat_text("msg-0148", EDITED)
    assert src.sql(by_id("chat", "msg-0148"))[0][0]["text"] == EDITED
    src.set_chat_text("msg-0148", original)
    assert src.chat_text("msg-0148") == original
    assert src.chat_text("TCK-0001") is None                     # tamper works on chat records only


def test_zetaris_retries_then_raises_source_unavailable(monkeypatch):
    calls = []

    class Down:
        def __init__(self, timeout):
            calls.append(timeout)
            raise RuntimeError("/api/auth/login: HTTP 502 bad gateway")

    monkeypatch.setattr("zetaris_spike.zclient.Zetaris", Down)
    monkeypatch.setattr("open_agent.sources.time.sleep", lambda s: None)
    with pytest.raises(SourceUnavailable, match="zetaris: RuntimeError"):
        ZetarisSource(timeout=7, retries=1).sql("SELECT 1")
    assert calls == [7, 7]


def test_ingest_through_the_sample_tables_equals_the_corpus_ingest(corpus_dir, graph, trace):
    other = make_store(graph.port, f"oacmp{graph.port}")
    try:
        want = ingest_corpus(other, corpus_dir, reset=True)
    finally:
        other.drop()
    again = make_store(graph.port, f"oacmp2{graph.port}")
    try:
        got = ingest_episodes(again, read_all(LocalSample(), trace), reset=True)
    finally:
        again.drop()
    assert (got["outcomes"], got["reasons"], got["episodes"]) == (want["outcomes"], want["reasons"], want["episodes"])


# --- the loop without a model (SAMPLE_MODE) ------------------------------------------------

def test_router_loop_verifies_both_citations(graph, trace):
    out = R.investigate(QUESTION, graph, LocalSample(), trace, None, sample=True)
    assert out["status"] == "verified" and out["rounds"] == 1 and out["investigator_path"] == "router"
    assert {c["id"] for c in out["citations"]} == {"ADR-0009", "msg-0148"}
    assert all(c["verdict"] == "skipped: no model in SAMPLE_MODE" and c["quote_found"] for c in out["citations"])
    assert out["write_gate"]["outcome"] == "accepted" and set(out["write_gate"]["source_ids"]) == {"ADR-0009", "msg-0148"}
    rereads = [e for e in trace.events if e["step"] == "reread"]
    assert [e["sql"] for e in rereads] == [by_id("doc", "ADR-0009"), by_id("chat", "msg-0148")]


def test_router_tamper_is_caught_revised_and_restored(graph, trace):
    src = LocalSample()
    original = R.tamper(src, "msg-0148", EDITED, trace)
    try:
        out = R.investigate(QUESTION, graph, src, trace, None, sample=True)
    finally:
        assert R.restore(src, "msg-0148", original, trace)
    assert out["rounds"] == 2 and out["revisions"] == 1 and out["status"] == "verified"
    first = {c["id"]: c for c in out["rounds_detail"][0]["checks"]}
    assert first["msg-0148"]["reason"] == "quote_not_in_record" and first["ADR-0009"]["accepted"]
    assert [c["id"] for c in out["citations"]] == ["ADR-0009"]
    assert out["dropped_citations"][0]["id"] == "msg-0148"
    assert out["dropped_citations"][0]["text_at_source_now"] == EDITED
    assert "msg-0148 is no longer cited" in out["answer"] and "Sources: ADR-0009." in out["answer"]
    assert out["write_gate"]["source_ids"] == ["ADR-0009"]
    steps = [(e["agent"], e["step"]) for e in trace.events]
    assert steps.index(("Verifier", "feedback")) < steps.index(("Investigator", "revise"))


def test_absence_is_checked_in_all_three_sources(graph, trace):
    out = R.investigate("Who owns payroll-export?", graph, LocalSample(), trace, None, sample=True)
    assert out["status"] == "absence_confirmed" and out["citations"] == []
    assert out["absence_checks"][0] == {"term": "payroll-export", "ids": [], "accepted": True, "states_absence": True,
                                        "reason": "absent from all three systems"}
    assert out["write_gate"]["written"] is False


class MentionsFound(LocalSample):
    """The sources do mention the name the memory does not know."""
    def sql(self, statement):
        if "instr(" in statement:
            return [{"source": "chat", "id": "msg-0001"}], 0.0
        return super().sql(statement)


def test_absence_contradicted_by_the_sources_is_revised(graph, trace):
    out = R.investigate("Who owns payroll-export?", graph, MentionsFound(), trace, None, sample=True)
    assert out["rounds"] == 2
    assert out["rounds_detail"][0]["accepted"] is False
    assert "msg-0001" in out["answer"] and out["status"] == "absence_confirmed"


class Unreadable(LocalSample):
    def sql(self, statement):
        if "WHERE s.id" in statement:
            raise SourceUnavailable("zetaris: TimeoutError: timed out")
        return super().sql(statement)


def test_unreadable_source_ends_unverified_without_a_revision_loop(graph, trace):
    out = R.investigate(QUESTION, graph, Unreadable(), trace, None, sample=True)
    assert out["status"] == "unverified" and out["rounds"] == 1
    assert {c["reason"] for c in out["citations"]} == {"unreadable"}
    assert out["write_gate"] == {"written": False,
                                 "reason": "the write gate keeps a finding only with a verified source; nothing written"}
    assert any(e["step"] == "reread" and e["status"] == "error" for e in trace.events)


def test_quote_shape_rules(graph, trace):
    inv = AG.Investigator(graph, trace)
    draft = inv.investigate(QUESTION)
    whole = LocalSample().sql(by_id("doc", "ADR-0009"))[0][0]["text"]
    draft.citations = [AG.Citation("ADR-0009", "doc", "billing-api transfers"),
                       AG.Citation("ADR-0009", "doc", whole),
                       AG.Citation("ADR-0009", "doc", ADR_LINE.replace("Luca", "Lucas")),
                       AG.Citation("ADR-9999", "doc", ADR_LINE)]
    rv = AG.Verifier(LocalSample(), trace, sample=True).review(draft)
    assert [c.reason for c in rv.checks] == ["quote_too_short", "quote_not_a_sentence", "quote_not_in_record",
                                             "record_missing"]
    assert not rv.accepted and rv.needs_revision


# --- the loop with a model (fake) ----------------------------------------------------------

def completion(content=None, tool_calls=None) -> dict:
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [{"id": f"t{i}", "type": "function",
                              "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(tool_calls)]
    return {"id": "cmpl-1", "object": "chat.completion", "created": 0, "model": "fake-nemotron",
            "choices": [{"index": 0, "finish_reason": "stop", "message": msg}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "receipt": {"id": "rcpt-123", "routed_model": "fake-nemotron"}}


class FakeOpenAI:
    """Answers chat.completions.with_raw_response.create by prompt: tool loop, cite, revise, judge."""

    def __init__(self, cite, revise=None, judge=None, fail=()):
        self.cite, self.revise, self.judge, self.fail = cite, revise, judge, set(fail)
        self.calls = []
        self.chat = type("C", (), {})()
        self.chat.completions = type("Cc", (), {})()
        self.chat.completions.with_raw_response = type("R", (), {"create": self._create})()

    def _create(self, **kw):
        system = kw["messages"][0]["content"]
        kind = ("tools" if kw.get("tools") else "cite" if system == AG.CITE_PROMPT else
                "revise" if system == AG.REVISE_PROMPT else "judge" if system == AG.JUDGE_PROMPT else "?")
        self.calls.append(kind)
        if kind in self.fail:
            raise openai.APITimeoutError(request=httpx.Request("POST", "http://fake/v1/chat/completions"))
        if kind == "tools":
            body = (completion(tool_calls=[("current_owner", {"system": "billing-api"})])
                    if kw["messages"][-1]["role"] == "user" else completion("Luca Ostrander owns billing-api."))
        else:
            user = json.loads(kw["messages"][1]["content"])
            body = completion(json.dumps({"cite": self.cite, "revise": self.revise, "judge": self.judge}[kind](user)))
        parsed = ChatCompletion.model_validate(body)
        return type("Raw", (), {"headers": httpx.Headers({"x-meterless-receipt": "hdr-9", "authorization": "x"}),
                                "parse": lambda self: parsed})()


def cite_stale(user):
    assert {r["id"] for r in user["records"]} >= {"ADR-0009", "msg-0148"}
    return {"answer": "Luca Ostrander owns billing-api since 2026-09-22.",
            "citations": [{"id": "ADR-0009", "quote": ADR_LINE}, {"id": "msg-0148", "quote": STALE},
                          {"id": "TCK-9999", "quote": "not a record it was given"}]}


def judge_by_text(user):
    text = user["record"]["current_text"]
    return {"verdict": "contradicts" if "on hold" in text else "supports", "note": f"now says: {text[:40]}"}


def revise_drop(user):
    notes = {n["id"]: n for n in user["verifier_notes"]}
    assert notes["msg-0148"]["accepted"] is False and notes["msg-0148"]["current_text"] == EDITED
    return {"answer": "Luca Ostrander owns billing-api since 2026-09-22; msg-0148 now says the transfer is on hold.",
            "citations": [{"id": "ADR-0009", "quote": ADR_LINE}]}


def model_cfg():
    return LLMConfig(base_url="http://fake/v1", model="fake-nemotron", api_key="sk-test-SECRET-123")


def test_model_loop_rejection_feedback_revision(graph, trace):
    src = LocalSample()
    original = R.tamper(src, "msg-0148", EDITED, trace)
    fake_i, fake_v = FakeOpenAI(cite_stale, revise_drop), FakeOpenAI(None, judge=judge_by_text)
    try:
        out = R.investigate(QUESTION, graph, src, trace, model_cfg(), sample=False,
                            investigator_client=fake_i, verifier_client=fake_v)
    finally:
        R.restore(src, "msg-0148", original, trace)
    assert out["status"] == "verified" and out["rounds"] == 2 and out["investigator_path"] == "llm"
    first = {c["id"]: c for c in out["rounds_detail"][0]["checks"]}
    assert first["msg-0148"] == {"id": "msg-0148", "accepted": False, "reason": "quote_not_in_record",
                                 "verdict": "contradicts"}
    assert out["answer"].endswith("msg-0148 now says the transfer is on hold.") and out["answer_author"] == "model"
    assert fake_i.calls == ["tools", "tools", "cite", "revise"] and fake_v.calls == ["judge"] * 3
    assert out["write_gate"]["source_ids"] == ["ADR-0009"]
    llm = [e for e in trace.events if e["step"] == "llm_call"]
    assert len(llm) == 7 and all(e["provider_receipt"] == {"id": "rcpt-123", "routed_model": "fake-nemotron"}
                                 for e in llm)
    assert all("authorization" not in e["response_headers"] for e in llm)
    warn = [e for e in trace.events if e["status"] == "warning"]
    assert warn and warn[0]["dropped"] == ["TCK-9999"]


def test_model_revision_is_capped(graph, trace):
    src = LocalSample()
    original = R.tamper(src, "msg-0148", EDITED, trace)
    stubborn = FakeOpenAI(cite_stale, revise=lambda u: cite_stale(u))
    try:
        out = R.investigate(QUESTION, graph, src, trace, model_cfg(), sample=False, max_revisions=2,
                            investigator_client=stubborn, verifier_client=FakeOpenAI(None, judge=judge_by_text))
    finally:
        R.restore(src, "msg-0148", original, trace)
    assert out["rounds"] == 3 and out["revisions"] == 2 and stubborn.calls.count("revise") == 2
    assert out["status"] == "partially_verified"
    assert out["write_gate"]["source_ids"] == ["ADR-0009"]      # only what was verified reaches the gate


def test_model_down_falls_back_everywhere(graph, trace):
    out = R.investigate(QUESTION, graph, LocalSample(), trace, model_cfg(), sample=False,
                        investigator_client=FakeOpenAI(None, fail={"tools", "cite"}),
                        verifier_client=FakeOpenAI(None, fail={"judge"}))
    assert out["status"] == "verified" and out["investigator_path"] == "router"   # the model never answered
    assert all(c["verdict"] == "skipped: model unavailable (APITimeoutError)" and c["quote_found"]
               for c in out["citations"])
    assert out["answer_author"] == "template"
    assert any(e["step"] == "llm_call" and e["status"] == "error" for e in trace.events)


def test_trace_lines_are_complete_and_hold_no_key(graph, tmp_path):
    t = Trace("keys", log_dir=tmp_path, echo=False)
    R.investigate(QUESTION, graph, LocalSample(), t, model_cfg(), sample=False,
                  investigator_client=FakeOpenAI(cite_stale, revise_drop),
                  verifier_client=FakeOpenAI(None, judge=judge_by_text))
    text = t.path.read_text()
    assert "sk-test-SECRET-123" not in text
    lines = [json.loads(line) for line in text.splitlines()]
    assert [e["seq"] for e in lines] == list(range(1, len(lines) + 1))
    assert all({"timestamp", "run_id", "agent", "step", "status", "mode"} <= set(e) for e in lines)


def test_cli_sample_end_to_end(graph, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OA_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("FALKORDB_HOST", os.environ.get("HM_TEST_HOST", "localhost"))
    monkeypatch.setenv("FALKORDB_PORT", str(graph.port))
    monkeypatch.setenv("OA_LLM_MODEL", "must-not-be-called")    # SAMPLE_MODE ignores any model
    spec = tmp_path / "in.json"
    spec.write_text(json.dumps({"question": "Who currently owns billing-api?", "tamper": "msg-0148",
                                "tamper_text": EDITED}))
    code = R.main(["--sample", "--input", str(spec), "--tenant", graph.tenant, "--out", str(tmp_path / "o.json")])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out == json.loads((tmp_path / "o.json").read_text())
    assert out["mode"] == "sample" and out["endpoint"] == "local-sample" and out["llm"] is None
    assert out["rounds"] == 2 and out["tamper"]["restored"] is True and "SAMPLE_MODE" in out["sample_mode_note"]
    trace_lines = [json.loads(x) for x in open(tmp_path / f"trace-{out['run_id']}.jsonl")]
    assert {e["mode"] for e in trace_lines} == {"sample"}
    assert not any(e["step"] == "llm_call" for e in trace_lines)


def test_json_reply_parsing_and_receipt_lookup():
    assert parse_json_object('<think>{"no": 1}</think> ok ```json\n{"verdict": "supports"}\n```') == {"verdict": "supports"}
    assert parse_json_object("no json here") is None
    assert provider_receipt({"x-meterless-receipt": "h"}, {}) == "h"
    assert provider_receipt({"content-type": "json"}, {"usage": 1}) is None


def test_api_key_from_file(tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_text("abc\n")
    monkeypatch.delenv("OA_LLM_API_KEY", raising=False)
    monkeypatch.setenv("OA_LLM_API_KEY_FILE", str(key))
    monkeypatch.setenv("OA_LLM_MODEL", "m")
    assert LLMConfig.from_env().api_key == "abc"
    assert isinstance(TracedClient(LLMConfig.from_env(), Trace("k", log_dir=tmp_path, echo=False), "X").chat, object)
