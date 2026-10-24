"""The integrated loop: Investigator <-> Verifier over the sources, Meterless Nightshift as the provider, H-MEM as the
memory. A fake Nightshift on a loopback port answers like the real one (OpenAI body + `meterless` receipt block); the
local sample engine stands in for Zetaris. Graph tests run on every FalkorDB in HM_TEST_PORTS."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from honest_memory.agent import MEMORY_PREFIX
from honest_memory.extract import RuleExtractor
from honest_memory.ingest import ingest_episodes
from open_agent import agents as AG
from open_agent import llm as L
from open_agent import nightshift as N
from open_agent import run as R
from open_agent.sources import LocalSample, read_all
from open_agent.trace import Trace
from tests.conftest import PORTS, make_store

STALE = "Per ADR-0009, I am now the owner of billing-api."
EDITED = "Per ADR-0009, the billing-api transfer is on hold. Sana Petrov remains the owner of billing-api until further notice."
ADR_LINE = ("Decision: Ownership of billing-api transfers from Sana Petrov (Payments) to Luca Ostrander (Platform) "
            "effective 2026-09-22.")
QUESTION = "Who owns billing-api now?"
MEMORY_COPY = (STALE + " Sana Petrov stays on as backup for 30 days. Send billing-api escalations to me.")  # msg-0148
ROUTE = {"wlwt-investigator": (N.LIGHTNING, "nemotron-lightning"), "wlwt-verifier": (N.NANO, "nemotron-nano")}


def receipt_block(app: str, rid: str, model: str | None = None, lanes=None, policy="local-only") -> dict:
    want, lane = ROUTE[app]
    receipt = {"app": app, "receipt_id": rid, "app_policy": {"lanes": lanes if lanes is not None else [lane]},
               "phases": [{"source_policy": policy}], "tool_degraded": None, "prompt_preview": "q"}
    return {"receipt_id": rid, "decision_id": rid[5:], "requested_model": want, "routed_model": model or want,
            "provider_model": "meterless/local", "mode": "local", "from_cache": False, "cache": {"status": "miss"},
            "cost": {"actual_usd": 0, "naive_usd": 0.001}, "privacy": {"path": "local-only"},
            "usage": {"input_tokens": 100, "output_tokens": 7, "total_tokens": 107, "latency_ms": 3},
            "trace": {"phases": [{"source_policy": policy}]}, "receipt": receipt,
            "receipt_envelope": {"body": {"receipt": receipt}, "sig_b64": "c2ln", "pubkey_b64": "cGs="}}


def judge_by_text(user):
    text = user["record"]["current_text"]
    return {"verdict": "contradicts" if "on hold" in text else "supports", "note": "now says: " + text[:40]}


def cite_stale(user):
    return {"answer": "Luca Ostrander owns billing-api since 2026-09-22.",
            "citations": [{"id": "ADR-0009", "quote": ADR_LINE, "claim": "Luca Ostrander owns billing-api"},
                          {"id": "msg-0148", "quote": STALE, "claim": "Luca Ostrander owns billing-api"}]}


def revise_blended(user):
    """What Nemotron did in lane A's live tamper runs: kept the ADR answer and blended the edited chat into it."""
    return {"answer": "Luca Ostrander owns billing-api since 2026-09-22, but Sana Petrov remains the owner of "
                      "billing-api until further notice.",
            "citations": [{"id": "ADR-0009", "quote": ADR_LINE, "claim": "Luca Ostrander owns billing-api"}]}


class FakeNightshift:
    """Loopback fake: /healthz, POST /v1/chat/completions answered by app and prompt, GET /v1/meterless/receipts."""

    def __init__(self, cite=cite_stale, revise=revise_blended, judge=judge_by_text):
        self.cite, self.revise, self.judge = cite, revise, judge
        self.requests, self.envelopes = [], []
        self.override = None          # callable(app, body) -> (status, body) | None, to inject failures
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/healthz":
                    return self._send(200, {"ok": True})
                outer.requests.append({"path": self.path})
                self._send(200, {"object": "list", "data": list(reversed(outer.envelopes))})

            def do_POST(self):
                n = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                app = self.headers.get("meterless-app")
                outer.requests.append({"path": self.path, "app": app, "body": body,
                                       "auth": self.headers.get("authorization")})
                if outer.override and (got := outer.override(app, body)):
                    return self._send(*got)
                self._send(200, outer.answer(app, body))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, app: str, body: dict) -> dict:
        msgs = body["messages"]
        system = msgs[0]["content"]
        msg = {"role": "assistant", "content": None}
        if body.get("tools"):
            if msgs[-1]["role"] == "user":
                msg["tool_calls"] = [{"id": "c1", "type": "function", "function": {
                    "name": "current_owner", "arguments": json.dumps({"system": "billing-api"})}}]
            else:
                msg["content"] = "Luca Ostrander owns billing-api."
        else:
            prompt, data = system.split("\n\nINPUT (JSON):\n", 1)      # the data rides in the system message
            assert msgs[-1] == {"role": "user", "content": L.NightshiftClient.ASK}
            fn = {AG.CITE_PROMPT: self.cite, AG.REVISE_PROMPT: self.revise, AG.JUDGE_PROMPT: self.judge}[prompt]
            msg["content"] = json.dumps(fn(json.loads(data)))
        rid = f"rcpt_req_{len(self.envelopes):04d}"
        block = receipt_block(app, rid)
        self.envelopes.append(block["receipt_envelope"])
        return {"id": rid, "object": "chat.completion", "choices": [{"index": 0, "message": msg,
                                                                     "finish_reason": "stop"}], "meterless": block}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ns(tmp_path, monkeypatch):
    fake = FakeNightshift()
    tok = tmp_path / "proxy-token"
    tok.write_text("tok-lane-c-0123456789abcdef\n")
    monkeypatch.setenv("NIGHTSHIFT_BASE_URL", fake.url)
    monkeypatch.setenv("NIGHTSHIFT_TOKEN_FILE", str(tok))
    for k in ("NIGHTSHIFT_VERIFIER_LANE", "NIGHTSHIFT_INVESTIGATOR_LANE", "NIGHTSHIFT_TRACE", "OA_LLM_PROVIDER",
              "OA_LLM_MODEL", "OA_VERIFIER_MODEL", "OA_LLM_BASE_URL", "SAMPLE_MODE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(N, "RETRY_DELAY_S", 0.0)
    monkeypatch.setattr(N, "WARMING_WAIT_S", 0.0)
    yield fake
    fake.close()


@pytest.fixture(params=PORTS, ids=lambda p: f"falkordb:{p}")
def graph(request, tmp_path):
    import re
    store = make_store(request.param, "lc" + re.sub(r"[^A-Za-z0-9]", "", request.node.name.split("[")[0])[-20:]
                       + str(request.param))
    ingest_episodes(store, read_all(LocalSample(), Trace("t", log_dir=tmp_path, echo=False)), reset=True,
                    extractor=RuleExtractor())
    store.port = request.param
    yield store
    store.drop()


@pytest.fixture
def trace(tmp_path):
    return Trace("lanec", log_dir=tmp_path, echo=False)


def nightshift_cfg(run_id="lanec"):
    return L.resolve(run_id)


# --- provider wiring -------------------------------------------------------------------------

def test_default_provider_is_nightshift_and_each_agent_has_its_own_nemotron(ns):
    cfg = nightshift_cfg()
    assert (cfg.provider, cfg.base_url, cfg.scope) == ("nightshift", ns.url, "wlwt-lanec")
    assert L.for_agent(cfg, "Investigator").model == N.LIGHTNING and L.for_agent(cfg, "Verifier").model == N.NANO
    assert L.preflight(cfg)["routes"]["verifier"] == {"app": "wlwt-verifier", "lane": "nemotron-nano", "model": N.NANO}


def test_provider_settings_that_contradict_are_refused(monkeypatch, ns):
    monkeypatch.setenv("OA_LLM_MODEL", "nemotron-3-nano:30b-a3b-q4_K_M")       # a leftover Ollama setting
    with pytest.raises(L.ProviderConfigError, match="OA_LLM_PROVIDER=ollama"):
        L.resolve("x")
    monkeypatch.setenv("OA_LLM_PROVIDER", "ollama")
    assert L.resolve("x").provider == "ollama"                                 # only when asked for
    monkeypatch.delenv("OA_LLM_MODEL")
    with pytest.raises(L.ProviderConfigError, match="needs OA_LLM_MODEL"):
        L.resolve("x")
    monkeypatch.setenv("OA_LLM_PROVIDER", "none")
    assert L.resolve("x") is None
    monkeypatch.setenv("OA_LLM_PROVIDER", "openrouter")
    with pytest.raises(L.ProviderConfigError):
        L.resolve("x")


def test_every_model_call_carries_its_receipt_in_the_trace(graph, ns, trace):
    out = R.investigate(QUESTION, graph, LocalSample(), trace, nightshift_cfg(), sample=False)
    assert out["status"] == "verified" and out["investigator_path"] == "llm"
    calls = [e for e in trace.events if e["step"] == "llm_call"]
    assert [(e["agent"], e["purpose"]) for e in calls] == [("Investigator", "tool_loop"), ("Investigator", "tool_loop"),
                                                           ("Investigator", "cite"), ("Verifier", "judge"),
                                                           ("Verifier", "judge")]
    for e in calls:
        app = "wlwt-investigator" if e["agent"] == "Investigator" else "wlwt-verifier"
        model, lane = ROUTE[app]
        assert e["provider"] == "nightshift" and e["app"] == app and e["lane"] == lane and e["lanes"] == [lane]
        assert e["requested_model"] == e["routed_model"] == e["model"] == model
        assert e["receipt_id"].startswith("rcpt_req_") and e["cost_usd"] == 0 and e["from_cache"] is False
        assert e["latency_ms"] > 0 and e["usage"]["prompt_tokens"] == 100 and e["signed"] is True
        assert e["source_policy"] == "local-only"
    assert all(r["app"] in ROUTE for r in ns.requests if "app" in r)
    assert all(r["body"]["max_tokens"] == L.MAX_TOKENS() for r in ns.requests if "body" in r)
    assert "tok-lane-c" not in trace.path.read_text()


def test_the_run_exports_its_signed_receipts_and_points_to_them(graph, ns, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OA_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("FALKORDB_HOST", os.environ.get("HM_TEST_HOST", "localhost"))
    monkeypatch.setenv("FALKORDB_PORT", str(graph.port))
    monkeypatch.setattr(R, "ZetarisSource", lambda timeout: LocalSample())   # live code path, local tables
    code = R.main(["--question", QUESTION, "--tenant", graph.tenant])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["status"] == "verified" and out["mode"] == "live"
    assert out["llm"]["provider"] == "nightshift" and out["llm"]["verifier"]["model"] == N.NANO
    rec = out["receipts"]
    assert rec["calls"] == rec["signed_receipts"] == 5 and rec["missing"] == [] and rec["redacted"] == 0
    lines = [json.loads(x) for x in open(rec["file"])]
    ids = [e["receipt_id"] for e in map(json.loads, open(tmp_path / f"trace-{out['run_id']}.jsonl"))
           if e["step"] == "llm_call"]
    assert [e["body"]["receipt"]["receipt_id"] for e in lines] == ids
    assert rec["file"].endswith(f"receipts-{out['run_id']}.jsonl")


def test_another_model_answering_stops_the_run(graph, ns, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OA_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("FALKORDB_PORT", str(graph.port))
    monkeypatch.setattr(R, "ZetarisSource", lambda timeout: LocalSample())

    def wrong_model(app, body):
        if app == "wlwt-verifier":     # the Verifier's call comes back from Lightning, not its Nano lane
            blk = receipt_block(app, "rcpt_req_bad", model=N.LIGHTNING)
            return 200, {"id": "x", "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": '{"verdict": "supports"}'}}], "meterless": blk}
        return None

    ns.override = wrong_model
    code = R.main(["--question", QUESTION, "--tenant", graph.tenant])
    out = json.loads(capsys.readouterr().out)
    assert code == 4 and out["status"] == "error" and out["error"]["type"] == "RoutingMismatch"
    assert "rcpt_req_bad" in out["error"]["message"] and "citations" not in out
    assert sum(1 for r in ns.requests if r.get("app") == "wlwt-verifier") == 1      # no retry, no other model


def test_nightshift_down_fails_loudly_and_tries_no_other_provider(graph, tmp_path, monkeypatch, capsys):
    tok = tmp_path / "t"
    tok.write_text("x" * 24)
    monkeypatch.setenv("NIGHTSHIFT_TOKEN_FILE", str(tok))
    monkeypatch.setenv("NIGHTSHIFT_BASE_URL", "http://127.0.0.1:9/v1")       # nothing listens on the discard port
    monkeypatch.setenv("OA_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("FALKORDB_PORT", str(graph.port))
    monkeypatch.delenv("OA_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OA_LLM_MODEL", raising=False)
    monkeypatch.setattr(R, "ZetarisSource", lambda timeout: LocalSample())
    monkeypatch.setattr("openai.OpenAI", lambda *a, **k: pytest.fail("no other provider may be called"))
    code = R.main(["--question", QUESTION, "--tenant", graph.tenant])
    out = json.loads(capsys.readouterr().out)
    assert code == 4 and out["error"]["type"] == "ProviderDown" and "nightshift_headless.sh start" in out["error"]["message"]
    assert "receipts" not in out or out["receipts"]["calls"] == 0


def test_a_lane_that_goes_down_mid_run_stops_the_run_and_the_tamper_is_restored(graph, ns, tmp_path, monkeypatch,
                                                                                 capsys):
    monkeypatch.setenv("OA_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("FALKORDB_PORT", str(graph.port))
    src = LocalSample()
    original = src.chat_text("msg-0148")
    monkeypatch.setattr(R, "ZetarisSource", lambda timeout: src)
    ns.override = lambda app, body: ((400, {"error": {"code": "no_provider_configured"}})
                                     if app == "wlwt-verifier" else None)
    code = R.main(["--question", QUESTION, "--tenant", graph.tenant, "--tamper", "msg-0148", "--tamper-text", EDITED])
    out = json.loads(capsys.readouterr().out)
    assert code == 4 and out["error"]["type"] == "ProviderDown" and "lane nemotron-nano" in out["error"]["message"]
    assert out["tamper"]["restored"] is True and src.chat_text("msg-0148") == original
    trace = [json.loads(x) for x in open(tmp_path / f"trace-{out['run_id']}.jsonl")]
    assert not any(e["step"] == "verdict" for e in trace)          # the Verifier never "skipped" its judgement
    assert out["receipts"]["signed_receipts"] == 3                 # the Investigator's calls before the failure


def test_an_empty_reply_is_visible_and_not_a_down_provider(graph, ns, trace):
    def empty_cite(app, body):
        if app == "wlwt-investigator" and not body.get("tools"):
            return 502, {"error": {"code": "provider_error", "message": "local-runtime/x_empty_response"}}
        return None

    ns.override = empty_cite
    out = R.investigate(QUESTION, graph, LocalSample(), trace, nightshift_cfg(), sample=False)
    assert out["status"] == "verified" and out["answer_author"] == "template"
    warn = [e for e in trace.events if e["step"] == "llm_call" and e["status"] == "warning"]
    assert warn and "empty reply" in warn[0]["error"]
    cites = [r for r in ns.requests if r.get("app") == "wlwt-investigator" and not r["body"].get("tools")]
    assert len(cites) == 1                       # an empty reply is not asked again
    assert any(e["step"] == "cite" and e["status"] == "fallback" for e in trace.events)


# --- the conflict line (a source changed after the memory read it) ----------------------------

def test_a_changed_source_is_reported_on_its_own_line_not_blended_into_the_answer(graph, ns, trace):
    src = LocalSample()
    original = R.tamper(src, "msg-0148", EDITED, trace)
    try:
        out = R.investigate(QUESTION, graph, src, trace, nightshift_cfg(), sample=False)
    finally:
        R.restore(src, "msg-0148", original, trace)
    assert out["status"] == "verified" and out["rounds"] == 2
    assert out["answer"] == (
        "Luca Ostrander owns billing-api since 2026-09-22 [ADR-0009]. Conflict: msg-0148 (chat message) was edited "
        f'after the memory read it and now says "{EDITED}"; the earlier quote "{STALE}" is no longer in the record. '
        "Flagged for a human.")
    assert "but Sana Petrov remains" not in out["answer"]
    [c] = out["conflicts"]
    assert (c["id"], c["kind"], c["flagged_for"], c["now_says"]) == ("msg-0148", "changed_at_source", "human", EDITED)
    flag = [e for e in trace.events if e["step"] == "conflict_flagged"]
    assert flag and flag[0]["removed_from_answer"].endswith("but Sana Petrov remains the owner of billing-api until "
                                                            "further notice.")
    # the Verifier judged the ADR claim without the conflict line, and the memory keeps the claim only
    judged = [json.loads(r["body"]["messages"][0]["content"].split("INPUT (JSON):\n", 1)[1]) for r in ns.requests
              if r.get("app") == "wlwt-verifier"][-1]
    assert "Conflict:" not in judged["answer"]
    assert out["write_gate"]["written"] and out["write_gate"]["conflicts_not_written"] == ["msg-0148"]
    text = graph.ro("MATCH (f:Finding {id: $id}) RETURN f.text", {"id": out["write_gate"]["finding"]})[0][0]
    assert text == "Luca Ostrander owns billing-api since 2026-09-22 [ADR-0009]."


def test_restatement_is_found_by_wording_not_by_shared_names():
    flagged = [{"id": "msg-0148", "now_says": EDITED}]
    support = ADR_LINE
    keep = "Luca Ostrander (Platform) is the owner of billing-api since 2026-09-22, taking over from Sana Petrov."
    assert AG.strip_restatements(keep, flagged, support) == keep
    mixed = keep + " However msg-0148 now says Sana Petrov remains the owner."
    assert AG.strip_restatements(mixed, flagged, support) == keep
    assert AG.strip_restatements("A is true; Sana Petrov remains the owner of billing-api until further notice.",
                                 flagged, support) == "A is true."


# --- H-MEM in the loop ------------------------------------------------------------------------

def test_memory_is_recalled_stored_and_fed_back_across_runs_without_a_model(graph, tmp_path, monkeypatch):
    monkeypatch.setenv("HM_HMEM_DIR", str(tmp_path / "hmem"))
    t1 = Trace("m1", log_dir=tmp_path, echo=False)
    first = R.investigate(QUESTION, graph, LocalSample(), t1, None, sample=True)
    assert first["memory"]["recall"]["ids"] == [] and first["memory"]["feedback"]["given"] is False
    stored = first["memory"]["stored"]
    assert first["write_gate"]["written"] and stored["backend"] == "hmem" and stored["memory_id"]
    t2 = Trace("m2", log_dir=tmp_path, echo=False)
    second = R.investigate("Who is the current owner of billing-api?", graph, LocalSample(), t2, None, sample=True)
    recall = [e for e in t2.events if e["step"] == "memory_recall"][0]
    assert stored["memory_id"] in recall["memory_ids"] and recall["used_by"].startswith("nobody: the deterministic")
    fb = second["memory"]["feedback"]
    assert fb["given"] and fb["kind"] == "helpful"
    m = {x["memory_id"]: x for x in fb["memories"]}[stored["memory_id"]]
    assert m["confidence_after"] > m["confidence_before"]
    ledger = [json.loads(x) for x in open(tmp_path / "hmem" / graph.tenant / "ledger.jsonl")]
    ops = [e["action"] for e in ledger if e["memoryId"] == stored["memory_id"]]
    assert ops[0] == "create" and "read" in ops and "feedback" in ops


def test_the_model_gets_the_memory_before_its_first_call(graph, ns, tmp_path, monkeypatch):
    monkeypatch.setenv("HM_HMEM_DIR", str(tmp_path / "hmem"))
    R.investigate(QUESTION, graph, LocalSample(), Trace("a", log_dir=tmp_path, echo=False), nightshift_cfg(),
                  sample=False)
    ns.requests.clear()
    t = Trace("b", log_dir=tmp_path, echo=False)
    out = R.investigate("Which person currently owns billing-api?", graph, LocalSample(), t, nightshift_cfg(),
                        sample=False)
    first = next(r for r in ns.requests if r.get("app") == "wlwt-investigator")
    memo = [m for m in first["body"]["messages"] if m["role"] == "system" and m["content"].startswith(MEMORY_PREFIX)]
    assert memo and out["memory"]["recall"]["ids"] and out["memory"]["feedback"]["kind"] == "helpful"
    assert [e for e in t.events if e["step"] == "memory_recall"][0]["used_by"].startswith("model")


def test_no_feedback_for_an_answer_that_states_no_result(graph, tmp_path, monkeypatch):
    monkeypatch.setenv("HM_HMEM_DIR", str(tmp_path / "hmem"))
    t = Trace("abs", log_dir=tmp_path, echo=False)
    fb = R.memory_feedback(graph, {"ids": ["mem-1"], "memories": []}, "absence_confirmed", t)
    assert fb["given"] is False and "not a verdict" in fb["reason"]
    assert R.memory_feedback(graph, {"ids": []}, "verified", t)["given"] is False


def test_a_changed_record_the_model_did_not_cite_is_still_found_and_flagged(graph, ns, trace):
    """Nemotron Lightning often cites only ADR-0009; the memory's answer also rests on msg-0148, which was edited."""
    ns.cite = lambda u: {"answer": "Luca Ostrander owns billing-api since 2026-09-22.",
                         "citations": [{"id": "ADR-0009", "quote": ADR_LINE, "claim": "Luca Ostrander owns billing-api"}]}
    ns.revise = lambda u: {"answer": "Luca Ostrander owns billing-api since 2026-09-22.",
                           "citations": [{"id": "ADR-0009", "quote": ADR_LINE}]}
    src = LocalSample()
    original = R.tamper(src, "msg-0148", EDITED, trace)
    try:
        out = R.investigate(QUESTION, graph, src, trace, nightshift_cfg(), sample=False)
    finally:
        R.restore(src, "msg-0148", original, trace)
    first = out["rounds_detail"][0]
    assert first["accepted"] is False and first["ignored"] == ["msg-0148"]          # rejected: changed, unmentioned
    assert [c["id"] for c in first["checks"]] == ["ADR-0009"] and first["checks"][0]["accepted"]
    reread = [e for e in trace.events if e["step"] == "evidence_reread"]
    assert [(e["record_id"], e["changed"]) for e in reread] == [("msg-0148", True)]
    assert out["status"] == "verified" and out["rounds"] == 2
    assert out["answer"] == ("Luca Ostrander owns billing-api since 2026-09-22 [ADR-0009]. Conflict: msg-0148 (chat "
                             f'message) was edited after the memory read it and now says "{EDITED}"; the earlier quote '
                             f'"{MEMORY_COPY}" is no longer in the record. Flagged for a human.')
    revise = [r for r in ns.requests if r.get("body") and AG.REVISE_PROMPT in r["body"]["messages"][0]["content"]]
    shown = json.loads(revise[0]["body"]["messages"][0]["content"].split("INPUT (JSON):\n", 1)[1])
    assert shown["must_address"] == ["msg-0148"]


def test_an_unchanged_uncited_record_is_reread_and_not_flagged(graph, ns, trace):
    ns.cite = lambda u: {"answer": "Luca Ostrander owns billing-api since 2026-09-22.",
                         "citations": [{"id": "ADR-0009", "quote": ADR_LINE}]}
    out = R.investigate(QUESTION, graph, LocalSample(), trace, nightshift_cfg(), sample=False)
    assert out["status"] == "verified" and out["rounds"] == 1 and out["conflicts"] == []
    assert [(e["record_id"], e["changed"]) for e in trace.events if e["step"] == "evidence_reread"] == [("msg-0148", False)]


def test_adding_the_citation_marker_is_not_reported_as_a_removal(graph, ns, trace):
    ns.revise = lambda u: {"answer": "Luca Ostrander owns billing-api since 2026-09-22.",
                           "citations": [{"id": "ADR-0009", "quote": ADR_LINE}]}
    src = LocalSample()
    original = R.tamper(src, "msg-0148", EDITED, trace)
    try:
        R.investigate(QUESTION, graph, src, trace, nightshift_cfg(), sample=False)
    finally:
        R.restore(src, "msg-0148", original, trace)
    [flag] = [e for e in trace.events if e["step"] == "conflict_flagged"]
    assert flag["removed_from_answer"] is None
    assert flag["answer_before"] == "Luca Ostrander owns billing-api since 2026-09-22."
