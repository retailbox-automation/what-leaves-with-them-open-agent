"""The HTTP API through FastAPI's TestClient, on every FalkorDB in HM_TEST_PORTS."""

import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from honest_memory.api import create_app  # noqa: E402
from honest_memory.corpus.generator import build, write_corpus
from honest_memory.ingest import ingest_corpus
from tests.conftest import PORTS, make_store


def client_for(store, **kw) -> TestClient:
    return TestClient(create_app(store.db, web_dir="/nonexistent", **kw))


@pytest.fixture
def api(fx_store, monkeypatch):
    for name in ("HM_LLM_MODEL", "HM_WRITE_TOKEN", "HM_TRUST_PROXY"):
        monkeypatch.delenv(name, raising=False)
    return client_for(fx_store), fx_store.tenant


def test_health_pings_falkordb_and_reports_its_version(api):
    client, _ = api
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["falkordb"]["ping"] is True
    assert re.fullmatch(r"\d+\.\d+\.\d+", body["falkordb"]["version"])
    assert body["tools"] == 21 and body["write_gate"] is True  # 14 + the 7 departure tools (N5) and body["llm"]["configured"] is False


def test_tool_list_carries_json_schemas(api):
    client, tenant = api
    body = client.get(f"/v1/{tenant}/tools").json()
    names = [t["name"] for t in body["tools"]]
    assert names[:3] == ["current_owner", "owner_at", "history"] and "remember" in names
    impact = next(t for t in body["tools"] if t["name"] == "impact")
    assert impact["parameters"]["properties"]["depth"]["maximum"] == 4


def test_run_a_tool_and_get_the_answer_with_its_path(api):
    client, tenant = api
    r = client.post(f"/v1/{tenant}/tools/current_owner", json={"system": "billing-api"})
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "answered_with_path" and body["rows"][0]["owner"] == "Luca Ostrander"
    assert body["paths"][0][:3] == ["Luca Ostrander", "OWNS", "billing-api"]
    assert body["sources"][0]["id"] == "ADR-0009" and body["trace"][0]["tool"] == "current_owner"
    assert body["sentence"].startswith("Luca Ostrander (Platform) owns billing-api")


def test_unknown_entity_and_not_in_graph_are_answers_not_errors(api):
    client, tenant = api
    unknown = client.post(f"/v1/{tenant}/tools/current_owner", json={"system": "payment-service"})
    empty = client.post(f"/v1/{tenant}/tools/impact", json={"system": "webhooks"})
    assert (unknown.status_code, unknown.json()["outcome"]) == (200, "unknown_entity")
    assert (empty.status_code, empty.json()["outcome"]) == (200, "not_in_graph")


def test_bad_requests_get_clear_errors(api):
    client, tenant = api
    assert client.post(f"/v1/{tenant}/tools/nope", json={}).status_code == 404
    r = client.post(f"/v1/{tenant}/tools/impact", json={"system": "auth", "depth": 9})
    assert r.status_code == 422 and "depth must be" in r.json()["error"]
    assert client.post(f"/v1/{tenant}/tools/current_owner", json={}).status_code == 422
    assert client.post(f"/v1/{tenant}/tools/current_owner", json={"system": "auth", "agent": "x"}).status_code == 422
    assert client.get("/v1/no-such-tenant/tools").status_code == 404
    bad = client.get("/v1/a b/tools")
    assert bad.status_code in (404, 422)
    inject = client.get("/v1/x}) DETACH DELETE n //".replace(" ", "%20") + "/tools")
    assert inject.status_code in (404, 422)


def test_ask_uses_the_router_when_no_model_is_set(api):
    client, tenant = api
    r = client.post(f"/v1/{tenant}/ask", json={"question": "Who owns billing-api?"})
    body = r.json()
    assert r.status_code == 200 and body["mode"] == "router" and body["rule"] == "current_owner"
    assert body["outcome"] == "answered_with_path" and body["sentence"].startswith("Luca Ostrander")
    none = client.post(f"/v1/{tenant}/ask", json={"question": "What is the weather?"}).json()
    assert none["outcome"] == "no_tool" and none["answers"] == []
    assert client.post(f"/v1/{tenant}/ask", json={"question": ""}).status_code == 422
    assert client.post(f"/v1/{tenant}/ask", json={"question": "x" * 501}).status_code == 422
    assert client.post(f"/v1/{tenant}/ask", json={"question": "Who owns auth?", "agent": "ops"}).status_code == 422


def test_ask_with_a_model_that_is_down_falls_back_to_the_router(api, monkeypatch):
    client, tenant = api
    monkeypatch.setenv("HM_LLM_MODEL", "some-model")
    monkeypatch.setenv("HM_LLM_BASE_URL", "http://127.0.0.1:9/v1")  # nothing listens on the discard port
    body = client.post(f"/v1/{tenant}/ask", json={"question": "Who owns billing-api?", "agent": "agent:ops"}).json()
    assert body["mode"] == "router" and body["agent"] == "agent:ops" and body["outcome"] == "answered_with_path"
    assert "model unavailable" in body["errors"][0]


def test_receipts_newest_first_with_a_filter_and_no_personal_data(api):
    client, tenant = api
    allr = client.get(f"/v1/{tenant}/receipts?limit=500")
    assert allr.status_code == 422
    refused = client.get(f"/v1/{tenant}/receipts?outcome=refused").json()
    ids = [r["id"] for r in refused["receipts"]]
    assert ids == ["gd-0101", "gd-0100", "gd-0102"]  # newest first: 2026-09-30, 09-29, 09-03
    assert refused["outcome"] == "REJECT" and refused["receipts"][1]["reasons"] == ["no_source", "conflicts_current"]
    pii = refused["receipts"][2]
    assert pii["reasons"] == ["pii"] and pii["subject"] == "[personal data]" and "555" not in str(refused)
    two = client.get(f"/v1/{tenant}/receipts?limit=2").json()["receipts"]
    assert len(two) == 2
    assert client.get(f"/v1/{tenant}/receipts?outcome=supersede").json()["receipts"][0]["id"] == "gd-0900"
    assert client.get(f"/v1/{tenant}/receipts?outcome=bogus").status_code == 422


def test_rate_limit_is_per_client_and_leaves_health_alone(fx_store, monkeypatch):
    monkeypatch.delenv("HM_TRUST_PROXY", raising=False)
    client = client_for(fx_store, rate_limit=3)
    url = f"/v1/{fx_store.tenant}/tools"
    assert [client.get(url).status_code for _ in range(3)] == [200, 200, 200]
    blocked = client.get(url)
    assert blocked.status_code == 429 and blocked.headers["retry-after"] and "rate limit" in blocked.json()["error"]
    assert client.get("/health").status_code == 200  # monitoring is never limited


def test_rate_limit_reads_the_forwarded_address_only_when_told_to(fx_store, monkeypatch):
    monkeypatch.setenv("HM_TRUST_PROXY", "1")
    client = client_for(fx_store, rate_limit=2)
    url = f"/v1/{fx_store.tenant}/tools"
    a, b = {"x-forwarded-for": "203.0.113.1"}, {"x-forwarded-for": "203.0.113.2"}
    assert [client.get(url, headers=a).status_code for _ in range(3)] == [200, 200, 429]
    assert client.get(url, headers=b).status_code == 200  # a different visitor behind the same proxy


def test_a_forged_forwarded_prefix_does_not_reset_the_limit(fx_store, monkeypatch):
    monkeypatch.setenv("HM_TRUST_PROXY", "1")
    client = client_for(fx_store, rate_limit=2)
    url = f"/v1/{fx_store.tenant}/tools"
    codes = [client.get(url, headers={"x-forwarded-for": f"198.51.100.{i}, 203.0.113.9"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]  # same real client (last hop), different forged first entries


def test_the_default_limit_is_generous_for_a_shared_nat(fx_store, monkeypatch):
    monkeypatch.delenv("HM_RATE_LIMIT_PER_MIN", raising=False)
    client = client_for(fx_store)
    assert all(client.get(f"/v1/{fx_store.tenant}/tools").status_code == 200 for _ in range(60))
    monkeypatch.setenv("HM_RATE_LIMIT_PER_MIN", "2")
    tight = client_for(fx_store)
    assert [tight.get(f"/v1/{fx_store.tenant}/tools").status_code for _ in range(3)] == [200, 200, 429]


def test_write_tools_over_http_stamp_the_callers_identity(api):
    client, tenant = api
    headers = {"x-hm-agent": "agent:support", "x-hm-session": "s9"}
    r = client.post(f"/v1/{tenant}/tools/remember", headers=headers, json={
        "text": "Customer asked who owns billing-api; per ADR-0009 it is Luca Ostrander.",
        "about": ["billing-api"], "source_ids": ["ADR-0009"]})
    assert r.status_code == 200 and r.json()["outcome"] == "accepted"
    mem = client.post(f"/v1/{tenant}/tools/recall", json={"entity": "billing-api", "limit": 20}).json()
    assert any(row["kind"] == "finding" and row["session"] == "s9" and row["agent"] == "agent:support" for row in mem["rows"])
    assert client.post(f"/v1/{tenant}/tools/remember", headers={"x-hm-agent": "bob"}, json={
        "text": "note about auth", "about": ["auth"], "source_ids": ["DOC-01"]}).status_code == 422


def test_writes_need_the_token_when_one_is_set(api, monkeypatch):
    client, tenant = api
    monkeypatch.setenv("HM_WRITE_TOKEN", "s3cret")
    body = {"text": "note about auth", "about": ["auth"], "source_ids": ["DOC-01"]}
    assert client.post(f"/v1/{tenant}/tools/remember", json=body).status_code == 401
    assert client.post(f"/v1/{tenant}/tools/remember", json=body, headers={"authorization": "Bearer nope"}).status_code == 401
    ok = client.post(f"/v1/{tenant}/tools/remember", json=body, headers={"authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert client.post(f"/v1/{tenant}/tools/current_owner", json={"system": "auth"}).status_code == 200  # reads stay open
    ep = {"id": "msg-9999", "ts": "2026-10-15T10:00:00Z", "channel": "chat", "author": "Luca Ostrander", "text": "hi"}
    assert client.post(f"/v1/{tenant}/episodes", json=ep).status_code == 401
    plan = f"/v1/{tenant}/tools/handoff_plan"  # a read, until propose=true makes it write proposed handoffs
    assert client.post(plan, json={"person": "Mateo Sandoval"}).status_code == 200
    assert client.post(plan, json={"person": "Mateo Sandoval", "propose": True}).status_code == 401
    assert client.post(plan, json={"person": "Mateo Sandoval", "propose": True},
                       headers={"authorization": "Bearer s3cret"}).json()["rows"][0]["proposed"] > 0


def test_static_panel_is_served_when_the_folder_exists(fx_store, tmp_path):
    (tmp_path / "index.html").write_text("<h1>panel</h1>")
    client = TestClient(create_app(fx_store.db, web_dir=tmp_path))
    assert client.get("/").text == "<h1>panel</h1>"
    assert client.get("/health").json()["status"] == "ok"  # the API routes still win
    assert client.get(f"/v1/{fx_store.tenant}/tools").status_code == 200
    assert TestClient(create_app(fx_store.db, web_dir=tmp_path / "missing")).get("/").status_code == 404


# --- the episode endpoint, on a tenant ingested through the real extractor and gate ----------

@pytest.fixture(params=PORTS, ids=lambda p: f"falkordb:{p}")
def ingested(request, tmp_path, monkeypatch):
    monkeypatch.delenv("HM_WRITE_TOKEN", raising=False)
    s = make_store(request.param, f"api{request.param}")
    ingest_corpus(s, write_corpus(build(42), tmp_path / "corpus"))
    yield s
    s.drop()


TRANSFER = """ADR-0014: Transfer billing-api ownership from Luca Ostrander to Pavel Hollis
Author: Selma Marsh
Draft opened: 2026-10-08. Status: accepted on 2026-10-14.
Context: Luca Ostrander is moving to the status-page rebuild.
Decision: Ownership of billing-api transfers from Luca Ostrander (Platform) to Pavel Hollis (Platform) effective 2026-10-14. Luca Ostrander stays available as backup for 30 days.
Consequences: Escalations for billing-api go to Pavel Hollis. The service catalog entry changes accordingly."""


def test_an_ownership_change_posted_as_an_episode_changes_the_next_answer(ingested):
    client = client_for(ingested)
    tenant = ingested.tenant
    before = client.post(f"/v1/{tenant}/tools/current_owner", json={"system": "billing-api"}).json()
    assert before["rows"][0]["owner"] == "Luca Ostrander"
    ep = {"id": "ADR-0014", "ts": "2026-10-14T15:00:00Z", "channel": "doc", "author": "Selma Marsh",
          "title": "ADR-0014: Transfer billing-api ownership from Luca Ostrander to Pavel Hollis", "text": TRANSFER}
    r = client.post(f"/v1/{tenant}/episodes", json=ep)
    assert r.status_code == 200, r.text
    outcomes = {x["outcome"] for x in r.json()["result"]}
    assert "SUPERSEDE" in outcomes
    after = client.post(f"/v1/{tenant}/tools/current_owner", json={"system": "billing-api"}).json()
    row = after["rows"][0]
    assert (row["owner"], row["previous_owner"], row["since"]) == ("Pavel Hollis", "Luca Ostrander", "2026-10-14T15:00:00Z")
    assert row["source_ids"] == ["ADR-0014"]
    hist = client.post(f"/v1/{tenant}/tools/history", json={"system": "billing-api"}).json()
    assert [x["owner"] for x in hist["rows"]] == ["Sana Petrov", "Luca Ostrander", "Pavel Hollis"]
    refused = client.get(f"/v1/{tenant}/receipts?outcome=supersede").json()["receipts"]
    assert len(refused) == 2 and refused[0]["cites"] == ["ADR-0014"]


def test_episode_endpoint_validates_the_record(ingested):
    client = client_for(ingested)
    url = f"/v1/{ingested.tenant}/episodes"
    ok = {"id": "msg-9001", "ts": "2026-10-15T10:00:00Z", "channel": "chat", "author": "Luca Ostrander", "text": "hello"}
    assert client.post(url, json={**ok, "channel": "email"}).status_code == 422
    assert client.post(url, json={**ok, "ts": "yesterday"}).status_code == 422
    assert client.post(url, json={**ok, "ts": "2030-01-01T00:00:00Z"}).status_code == 422  # would outrank every real update
    assert client.post(url, json={**ok, "ts": "2026-13-45T10:00:00Z"}).status_code == 422
    assert client.post(url, json={**ok, "id": "bad id"}).status_code == 422
    assert client.post(url, json={**ok, "text": ""}).status_code == 422
    assert client.post(url, json={**ok, "text": "x" * 20001}).status_code == 422
    assert client.post(url, json=ok).status_code == 200  # a chat line with nothing to extract is stored, no facts
    assert client.post("/v1/nobody/episodes", json=ok).status_code == 404


def test_ask_answers_corpus_questions_with_no_model(ingested):
    client = client_for(ingested)
    tenant = ingested.tenant
    for question, expect in (("Who currently owns billing-api?", "Luca Ostrander"),
                             ("What depends on status-page within 4 hops, and who must be told?", "No system depends on"),
                             ("Is Growth the current owner of billing-api?", "refused")):
        body = client.post(f"/v1/{tenant}/ask", json={"question": question}).json()
        assert expect in body["sentence"], body["sentence"]


def test_without_falkordb_nothing_is_answered(monkeypatch):
    """FalkorDB is the only source: with the database unreachable, health is down and every tool call is a 503 with
    no rows, so no answer can come from anywhere else (no cache, no model, no fallback)."""
    for name in ("HM_LLM_MODEL", "HM_WRITE_TOKEN", "HM_TRUST_PROXY", "FALKORDB_USERNAME", "FALKORDB_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FALKORDB_HOST", "127.0.0.1")
    monkeypatch.setenv("FALKORDB_PORT", "1")  # nothing listens on port 1; the app connects lazily, as in production
    monkeypatch.setenv("FALKORDB_SOCKET_TIMEOUT", "1")
    client = TestClient(create_app(None, web_dir="/nonexistent"), raise_server_exceptions=False)
    health = client.get("/health")
    assert health.status_code == 503 and health.json()["falkordb"]["ping"] is False
    for name, args in (("current_owner", {"system": "billing-api"}), ("what_leaves", {"person": "Oskar Alvarez", "depth": 4}),
                       ("without", {"person": "Oskar Alvarez"})):
        r = client.post(f"/v1/quillfield/tools/{name}", json=args)
        assert r.status_code == 503, (name, r.status_code, r.text)
        assert "rows" not in r.json() and "sentence" not in r.json()
    assert client.get("/v1/quillfield/tools").status_code == 503
