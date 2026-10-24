"""The Nightshift client against a fake Nightshift on a loopback port: token per request, headers, the routing
check that fails loudly, retry, the honest_memory agent through it, and the receipts export scan. No model runs here;
tests/test_nightshift_live.py is the live check."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from open_agent import nightshift as N

LIGHT, NANO = N.LIGHTNING, N.NANO


def meterless(model=LIGHT, policy="local-only", lanes=("nemotron-lightning",), cost=0, rid="rcpt_req_t1",
              app="wlwt-investigator", scope="s", preview="Who owns billing-api?"):
    receipt = {"app": app, "scope": scope, "app_policy": {"lanes": list(lanes) if lanes is not None else None},
               "phases": [{"phase": "final_synthesis", "source_policy": policy}], "tool_degraded": None,
               "prompt_preview": preview}
    return {"receipt_id": rid, "decision_id": rid.replace("rcpt_", ""), "requested_model": model,
            "routed_model": model, "provider_model": "meterless/local", "mode": "local", "from_cache": False,
            "cache": {"status": "miss"}, "cost": {"actual_usd": cost, "naive_usd": 0.001},
            "privacy": {"path": "local-only"}, "usage": {"input_tokens": 10, "output_tokens": 2, "latency_ms": 5},
            "trace": {"phases": [{"source_policy": policy}]}, "receipt": receipt,
            "receipt_envelope": {"body": {"receipt": receipt}, "sig_b64": "c2ln", "pubkey_b64": "cGs="}}


def completion(content="OK", tool_calls=None, finish="stop", **mkw):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": "x", "object": "chat.completion", "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "meterless": meterless(**mkw)}


class FakeNightshift:
    """Answers POST /v1/chat/completions with queued (status, body) pairs; records every request."""

    def __init__(self):
        self.queue, self.requests, self.receipts = [], [], []
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

            def do_POST(self):
                n = int(self.headers.get("content-length", 0))
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                       "body": json.loads(self.rfile.read(n) or b"{}")})
                status, body = outer.queue.pop(0)
                self._send(status, body)

            def do_GET(self):
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
                self._send(200, {"object": "list", "data": outer.receipts})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ns(tmp_path, monkeypatch):
    fake = FakeNightshift()
    tok = tmp_path / "proxy-token"
    tok.write_text("tok-one-0123456789abcdef\n")
    monkeypatch.setenv("NIGHTSHIFT_BASE_URL", fake.url)
    monkeypatch.setenv("NIGHTSHIFT_TOKEN_FILE", str(tok))
    monkeypatch.delenv("NIGHTSHIFT_VERIFIER_LANE", raising=False)
    monkeypatch.delenv("NIGHTSHIFT_INVESTIGATOR_LANE", raising=False)
    monkeypatch.delenv("NIGHTSHIFT_TRACE", raising=False)
    monkeypatch.setattr(N, "RETRY_DELAY_S", 0.0)
    fake.token = tok
    yield fake
    fake.close()


def test_routes_name_one_nemotron_model_per_agent(monkeypatch):
    assert N.route_for("investigator") == N.Route("investigator", "wlwt-investigator", "nemotron-lightning", LIGHT)
    assert N.route_for("verifier") == N.Route("verifier", "wlwt-verifier", "nemotron-nano", NANO)
    monkeypatch.setenv("NIGHTSHIFT_VERIFIER_LANE", "nemotron-lightning")
    assert N.route_for("verifier").model == LIGHT
    monkeypatch.setenv("NIGHTSHIFT_VERIFIER_LANE", "ollama")
    with pytest.raises(ValueError):
        N.route_for("verifier")


def test_the_token_is_read_on_every_request_and_the_headers_are_nightshifts(ns):
    ns.queue += [(200, completion()), (200, completion())]
    N.chat([{"role": "user", "content": "hi"}], agent="investigator", scope="run-1")
    ns.token.write_text("tok-two-0123456789abcdef")             # Nightshift rotated the key between the calls
    N.chat([{"role": "user", "content": "hi"}], agent="investigator", scope="run-1")
    h1, h2 = ns.requests[0]["headers"], ns.requests[1]["headers"]
    assert h1["authorization"] == "Bearer tok-one-0123456789abcdef" and h2["authorization"] == "Bearer tok-two-0123456789abcdef"
    assert (h1["meterless-app"], h1["meterless-scope"], h1["meterless-trace"]) == ("wlwt-investigator", "run-1", "on")
    assert ns.requests[0]["body"]["model"] == LIGHT and ns.requests[0]["path"] == "/v1/chat/completions"


def test_the_receipt_comes_back_with_the_call(ns):
    ns.queue.append((200, completion(tool_calls=[{"id": "c1", "type": "function", "function": {
        "name": "current_owner", "arguments": '{"system": "billing-api"}'}}], finish="tool_calls")))
    r = N.chat([{"role": "user", "content": "who"}], agent="investigator", scope="run-2", tools=[N._TOOL])
    assert r.finish_reason == "tool_calls" and r.message["tool_calls"][0]["function"]["name"] == "current_owner"
    rc = r.receipt
    assert (rc.receipt_id, rc.routed_model, rc.source_policy, rc.lanes, rc.cost_usd) == (
        "rcpt_req_t1", LIGHT, "local-only", ["nemotron-lightning"], 0)
    assert rc.signed and rc.latency_ms > 0 and rc.scope == "run-2"
    assert ns.requests[0]["body"]["tools"][0]["function"]["name"] == "current_owner"
    assert ns.requests[0]["body"]["tool_choice"] == "auto"


def test_an_explicit_pin_is_accepted_too(ns):
    ns.queue.append((200, completion(policy=f"pinned-model:{LIGHT}", lanes=None)))
    assert N.chat([{"role": "user", "content": "x"}], agent="investigator", scope="s").receipt.lanes is None


@pytest.mark.parametrize("mkw, why", [
    ({"model": NANO}, "routed_model"),                                    # another model answered
    ({"policy": "seated-fleet", "lanes": None}, "source_policy"),          # the 1.2.1 unpinned path
    ({"lanes": ("nemotron-lightning", "nemotron-nano")}, "source_policy"),  # allowlist wider than one lane
    ({"cost": 0.002}, "cost"),                                             # a paid route
])
def test_anything_but_the_agents_model_under_its_pin_fails_loudly(ns, mkw, why):
    ns.queue.append((200, completion(**mkw)))
    with pytest.raises(N.RoutingMismatch, match=why):
        N.chat([{"role": "user", "content": "x"}], agent="investigator", scope="s")


def test_one_retry_on_a_server_error_none_on_a_refusal(ns):
    ns.queue += [(503, {"error": {"message": "engine_saturated"}}), (200, completion())]
    assert N.chat([{"role": "user", "content": "x"}], agent="investigator", scope="s").message["content"] == "OK"
    ns.queue += [(400, {"error": {"code": "pin_refused"}}), (200, completion())]
    with pytest.raises(N.NightshiftUnavailable, match="pin_refused"):
        N.chat([{"role": "user", "content": "x"}], agent="investigator", scope="s")
    assert len(ns.requests) == 3 and len(ns.queue) == 1


def test_a_lane_that_is_still_coming_up_is_waited_for_without_spending_the_retry(ns, monkeypatch):
    ns.queue += [(400, {"error": {"code": "no_provider_configured"}}),
                 (503, {"error": {"meterless_detail": "local_runtime_not_ready: still listing its models"}}),
                 (503, {"error": {"message": "engine_saturated"}}), (200, completion())]
    assert N.chat([{"role": "user", "content": "x"}], agent="investigator", scope="s").message["content"] == "OK"
    assert len(ns.requests) == 4                            # two warming waits, then the one retry
    monkeypatch.setattr(N, "WARMING_WAIT_S", 0.0)          # a lane that never comes up: the retry, then the error
    ns.queue += [(400, {"error": {"code": "no_provider_configured"}})] * 2
    with pytest.raises(N.NightshiftUnavailable, match="no_provider_configured"):
        N.chat([{"role": "user", "content": "x"}], agent="investigator", scope="s")
    assert len(ns.requests) == 6


def test_a_missing_token_file_is_unavailable_not_a_crash(ns, tmp_path, monkeypatch):
    monkeypatch.setenv("NIGHTSHIFT_TOKEN_FILE", str(tmp_path / "nope"))
    with pytest.raises(N.NightshiftUnavailable, match="token file"):
        N.chat([{"role": "user", "content": "x"}], agent="investigator", scope="s")
    assert ns.requests == []


def test_receipts_go_to_the_trace_file(ns, tmp_path, monkeypatch):
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv("NIGHTSHIFT_TRACE", str(trace))
    ns.queue.append((200, completion()))
    N.chat([{"role": "user", "content": "x"}], agent="investigator", scope="s")
    line = json.loads(trace.read_text())
    assert line["receipt_id"] == "rcpt_req_t1" and line["agent"] == "investigator"
    assert "tok-one" not in trace.read_text()


# --- the honest_memory agent through Nightshift ------------------------------------------------

def _nightshift_agent(store):
    from honest_memory.agent import Agent, AgentConfig

    cfg = AgentConfig(model=LIGHT, provider="nightshift", nightshift_agent="investigator")
    return Agent(store, name="agent:support", role="support", session="s1", config=cfg)


def test_the_agent_calls_tools_through_nightshift_and_keeps_the_receipts(fx_store, ns, monkeypatch):
    monkeypatch.setenv("HM_MEMORY_CONTEXT", "off")
    ns.queue += [
        (200, completion(content=None, finish="tool_calls", tool_calls=[{"id": "c1", "type": "function", "function": {
            "name": "current_owner", "arguments": json.dumps({"system": "billing-api"})}}])),
        (200, completion(content="Luca Ostrander owns billing-api.", rid="rcpt_req_t2")),
    ]
    ag = _nightshift_agent(fx_store)
    run = ag.ask("Who owns billing-api?")
    assert run.mode == "llm" and run.answers[0].tool == "current_owner" and run.errors == []
    assert [r.receipt_id for r in ag.client.receipts] == ["rcpt_req_t1", "rcpt_req_t2"]
    sent = ns.requests[1]["body"]["messages"]
    assert sent[-1]["role"] == "tool" and sent[-2]["tool_calls"][0]["id"] == "c1"
    assert ns.requests[0]["headers"]["meterless-app"] == "wlwt-investigator"


def test_a_routing_mismatch_is_not_hidden_behind_the_router(fx_store, ns, monkeypatch):
    monkeypatch.setenv("HM_MEMORY_CONTEXT", "off")
    ns.queue.append((200, completion(model=NANO, policy="seated-fleet", lanes=None)))
    with pytest.raises(N.RoutingMismatch):
        _nightshift_agent(fx_store).ask("Who owns billing-api?")


def test_nightshift_down_falls_back_to_the_router_and_says_so(fx_store, monkeypatch, tmp_path):
    monkeypatch.setenv("HM_MEMORY_CONTEXT", "off")
    monkeypatch.setenv("NIGHTSHIFT_BASE_URL", "http://127.0.0.1:9/v1")      # nothing listens on the discard port
    monkeypatch.setattr(N, "RETRY_DELAY_S", 0.0)
    tok = tmp_path / "t"
    tok.write_text("x" * 20)
    monkeypatch.setenv("NIGHTSHIFT_TOKEN_FILE", str(tok))
    run = _nightshift_agent(fx_store).ask("Who owns billing-api?")
    assert run.mode == "router" and run.errors and "Nightshift unreachable" in run.errors[0]


def test_from_env_picks_the_agents_model(monkeypatch):
    from honest_memory.agent import AgentConfig

    monkeypatch.setenv("HM_LLM_PROVIDER", "nightshift")
    monkeypatch.delenv("HM_LLM_MODEL", raising=False)
    monkeypatch.setenv("HM_NIGHTSHIFT_AGENT", "verifier")
    cfg = AgentConfig.from_env()
    assert (cfg.provider, cfg.model, cfg.nightshift_agent) == ("nightshift", NANO, "verifier")


# --- the receipts export -----------------------------------------------------------------------

def _envelope(app, scope, preview, rid):
    return {"body": {"receipt": {"app": app, "scope": scope, "prompt_preview": preview, "receipt_id": rid},
                     "signer": "meterlessd", "ts_ms": 1}, "sig_b64": "c2ln", "pubkey_b64": "cGs="}


def test_export_keeps_one_runs_receipts_and_scans_them(ns, tmp_path):
    ns.receipts = [  # the route lists newest first
        _envelope("wlwt-verifier", "run-7", "Reach the customer at +1-555-0159 please", "r4"),
        _envelope("wlwt-investigator", "run-7", "the key is tok-one-0123456789abcdef", "r3"),
        _envelope("other-app", "run-7", "x", "r2"),
        _envelope("wlwt-investigator", "run-7", "Who owns billing-api since 2026-09-10 (ADR-0009)?", "r1"),
        _envelope("wlwt-investigator", "run-6", "older run", "r0"),
    ]
    out = tmp_path / "receipts-run-7.jsonl"
    res = N.export_receipts(["r1", "r2", "r3", "r4", "r9"], out)      # r2 is another app's, r9 is not listed
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert res == {"path": str(out), "receipts": 3, "redacted": 2, "listed": 5, "missing": ["r2", "r9"]}
    assert [e["body"]["receipt"]["receipt_id"] for e in lines] == ["r1", "r3", "r4"]   # oldest first, ours only
    assert lines[0] == ns.receipts[3] and "redacted" not in lines[0]                    # clean: byte-for-byte
    assert lines[1]["redacted"] == ["token-literal"] and "tok-one" not in out.read_text()
    assert lines[2]["redacted"] == ["phone"] and "555" not in out.read_text()
    assert ns.requests[-1]["path"] == "/v1/meterless/receipts?limit=200"


def test_a_runs_receipt_ids_come_from_the_trace_file(tmp_path):
    trace = tmp_path / "t.jsonl"
    trace.write_text("\n".join(json.dumps({"scope": s, "receipt_id": i}) for s, i in
                                [("run-7-a", "r1"), ("run-6", "r0"), ("run-7-b", "r2")]) + "\n")
    assert N.receipt_ids_from_trace(trace, "run-7") == ["r1", "r2"]


def test_hashes_signatures_and_timestamps_are_not_secrets_but_text_is_scanned(ns):
    block = meterless(preview="Who owns billing-api?")
    block["receipt_envelope"]["body"].update(manifest_hash="ab" * 32, llama_cpp_ref="08659901c43b51de735740f1cf61bb82fbe0c4e4",
                                             ts_ms=1792841883733)
    assert N.clean_block(block) is block                                    # clean: returned untouched
    dirty = meterless(preview="mail me at someone@example.com")
    out = N.clean_block(dirty)
    assert out["redacted"] == ["email"] and "example.com" not in json.dumps(out)
