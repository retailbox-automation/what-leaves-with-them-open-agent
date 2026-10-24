"""The Meterless H-MEM engine as the product's memory: across processes, behind remember/recall, before answering,
and the Verifier's verdict as feedback. Needs Node (any >= 20) and the pinned engine in vendor/hmem."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from open_agent import hmem_bridge as B

pytestmark = pytest.mark.skipif(not B.available(), reason="node or vendor/hmem/dist/hmem.mjs is missing")

REPO = Path(__file__).resolve().parent.parent
CTX_AT = "2026-10-15T12:00:00Z"


def ledger(d) -> list[dict]:
    return [json.loads(x) for x in (Path(d) / "ledger.jsonl").read_text().splitlines()]


def run_py(code: str, env: dict) -> dict:
    """A fresh Python process (a new session): prints one JSON line."""
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env={**os.environ, **env}, capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


# --- the engine across sessions -----------------------------------------------------------------

def test_an_approved_answer_survives_the_process_and_is_recalled_from_a_paraphrase(tmp_path):
    d = str(tmp_path / "hmem")
    a = run_py(f"""
import json
from open_agent.hmem_bridge import HMem
with HMem({d!r}) as h:
    m = h.store_answer("Who owns billing-api now?", "Luca Ostrander owns billing-api since 2026-09-22 (ADR-0009).",
                       agent="agent:verifier", citations=["ADR-0009"], run_id="run-a", verdict="verified")
print(json.dumps({{"id": m["id"], "pid": h.info["pid"]}}))
""", {})
    b = run_py(f"""
import json
from open_agent.hmem_bridge import HMem
with HMem({d!r}) as h:
    ctx, hits = h.recall("which person is the current owner of the billing-api service?")
print(json.dumps({{"ctx": ctx, "hits": [x["memory"]["id"] for x in hits], "pid": h.info["pid"],
                  "layer": hits[0]["memory"]["layer"] if hits else None}}))
""", {})
    assert a["pid"] != b["pid"]                                 # two sidecars, two Python processes
    assert b["hits"] == [a["id"]] and b["layer"] == "long_term"
    assert "[MEMORY CONTEXT]" in b["ctx"] and "Luca Ostrander" in b["ctx"] and f"<{a['id']}>" in b["ctx"]
    acts = [(e["memoryId"], e["action"]) for e in ledger(d)]
    assert acts == [(a["id"], "create"), (a["id"], "read")]
    assert not (Path(d) / "sidecar.lock").exists()              # released on exit


def test_one_sidecar_per_directory(tmp_path):
    with B.HMem(tmp_path) as h:
        with pytest.raises(B.HMemError, match="held by pid"):
            B.HMem(tmp_path)
        assert h.call("ping")["records"] == 0


def test_verdicts_become_feedback(tmp_path):
    with B.HMem(tmp_path) as h:
        m = h.store_answer("Q?", "A.", agent="agent:verifier", run_id="r")
        assert h.verdict([m["id"]], "verified")[0]["confidence"] == pytest.approx(0.75)
        assert h.verdict([m["id"]], "Unsupported")[0]["confidence"] == pytest.approx(0.72)
        wrong = h.verdict([m["id"]], "refuted")[0]
        assert wrong["confidence"] == pytest.approx(0.52) and "review" in wrong["tags"]
        with pytest.raises(B.HMemError, match="unknown verdict"):
            h.verdict([m["id"]], "maybe")
    kinds = [e["details"]["kind"] for e in ledger(tmp_path) if e["action"] == "feedback"]
    assert kinds == ["helpful", "not_helpful", "wrong"]


def test_a_record_without_a_source_is_refused_by_the_engine(tmp_path):
    with B.HMem(tmp_path) as h, pytest.raises(B.HMemError, match="no source"):
        h.add("orphan", source="")


# --- the product: remember -> H-MEM, recall from H-MEM, context before answering -------------------

@pytest.fixture
def hmem_env(tmp_path, monkeypatch):
    d = tmp_path / "hmem-root"
    monkeypatch.setenv("HM_HMEM_DIR", str(d))
    monkeypatch.setenv("HM_MEMORY_BACKEND", "hmem")
    B.close_all()
    yield d
    B.close_all()


def test_remember_keeps_the_gates_finding_in_hmem_and_recall_serves_it(fx_store, hmem_env):
    from honest_memory import answer as A
    from honest_memory import tools as T

    ctx = T.Context(agent="agent:support", session="s1", at=CTX_AT)
    ans = T.call(fx_store, "remember", {"text": "Luca Ostrander owns billing-api per ADR-0009.",
                                        "about": ["billing-api"], "source_ids": ["ADR-0009"]}, ctx)
    assert ans.outcome == A.ACCEPTED and ans.rows[0]["memory"]["backend"] == "hmem"
    mem_id = ans.rows[0]["memory"]["memory_id"]
    rec = T.call(fx_store, "recall", {"entity": "billing-api", "limit": 20}, ctx)
    mine = next(r for r in rec.rows if r["kind"] == "finding" and r["id"] == ans.rows[0]["record_id"])
    assert (mine["memory"], mine["memory_id"], mine["source_ids"], mine["at"]) == ("hmem", mem_id, ["ADR-0009"], CTX_AT)
    assert all(r["memory"] == "hmem" for r in rec.rows if r["kind"] == "finding")
    tenant_dir = hmem_env / fx_store.tenant
    state = json.loads((tenant_dir / "state.json").read_text())
    kept = next(r for r in state["records"] if r["id"] == mem_id)
    assert kept["layer"] == "long_term" and "about:billing-api" in kept["tags"] and kept["source"] == "agent:support"
    assert {"create", "read"} <= {e["action"] for e in ledger(tenant_dir)}


def test_graph_backend_is_the_old_path(fx_store, hmem_env, monkeypatch):
    from honest_memory import tools as T

    monkeypatch.setenv("HM_MEMORY_BACKEND", "graph")
    rec = T.call(fx_store, "recall", {"entity": "billing-api", "limit": 20}, T.Context())
    assert rec.rows and all(r.get("memory") == "graph" and "memory_id" not in r for r in rec.rows if r["kind"] == "finding")
    assert not (hmem_env / fx_store.tenant).exists()


def test_a_broken_engine_falls_back_to_the_graph_and_says_so(fx_store, hmem_env, monkeypatch, tmp_path):
    from honest_memory import tools as T

    bad = tmp_path / "broken.mjs"
    bad.write_text("throw new Error('engine broken on purpose');\n")
    monkeypatch.setenv("HMEM_ENGINE", str(bad))
    rec = T.call(fx_store, "recall", {"entity": "billing-api", "limit": 20}, T.Context())
    assert rec.rows and "H-MEM unavailable" in rec.note
    assert all(r["memory"] == "graph" for r in rec.rows if r["kind"] == "finding")


def test_a_rebuilt_graph_does_not_resurrect_old_memories(port, hmem_env):
    from honest_memory import tools as T
    from tests.conftest import make_store
    from tests.fixtures import build_fixture

    store = make_store(port, "hmemrebuild")
    try:
        build_fixture(store)
        ctx = T.Context(agent="agent:support", session="s1", at=CTX_AT)
        T.call(store, "remember", {"text": "A finding that only the first graph had.", "about": ["billing-api"],
                                   "source_ids": ["ADR-0009"]}, ctx)
        store.drop()
        build_fixture(store)                       # same tenant, fresh graph: finding ids start again
        rec = T.call(store, "recall", {"entity": "billing-api", "limit": 20}, ctx)
        assert not any("only the first graph" in str(r.get("text")) for r in rec.rows)
        held = json.loads((hmem_env / store.tenant / "state.json").read_text())["records"]
        assert any("only the first graph" in r["content"] for r in held)   # kept in H-MEM, not served
    finally:
        store.drop()


def test_a_new_session_gets_the_memory_before_it_answers(fx_store, port, hmem_env):
    """Session A remembers through the gate and exits; session B's model sees it before its first call."""
    env = {"HM_HMEM_DIR": str(hmem_env), "HM_MEMORY_BACKEND": "hmem",
           "HM_TEST_HOST": os.environ.get("HM_TEST_HOST", "localhost")}
    a = run_py(f"""
import json
from falkordb import FalkorDB
from honest_memory.store import Store
from honest_memory import tools as T
s = Store(FalkorDB(host={env['HM_TEST_HOST']!r}, port={port}), {fx_store.tenant!r})
ans = T.call(s, "remember", {{"text": "Luca Ostrander owns billing-api per ADR-0009.", "about": ["billing-api"],
                              "source_ids": ["ADR-0009"]}}, T.Context(agent="agent:support", session="sA", at={CTX_AT!r}))
print(json.dumps(ans.rows[0]["memory"]))
""", env)
    b = run_py(f"""
import json
from types import SimpleNamespace
from falkordb import FalkorDB
from honest_memory.store import Store
from honest_memory.agent import Agent, AgentConfig
from open_agent.hmem_bridge import verdict_feedback
seen = []
def create(**kw):
    seen.append([dict(m) for m in kw["messages"]])
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[SimpleNamespace(
        id="c1", function=SimpleNamespace(name="current_owner", arguments=json.dumps({{"system": "billing-api"}})))])
        if len(seen) == 1 else SimpleNamespace(content="Luca Ostrander owns billing-api.", tool_calls=None))])
client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
s = Store(FalkorDB(host={env['HM_TEST_HOST']!r}, port={port}), {fx_store.tenant!r})
run = Agent(s, session="sB", client=client, config=AgentConfig(model="fake")).ask("Who is the current owner of billing-api?")
fb = verdict_feedback(s, run.memory_ids, "refuted")
print(json.dumps({{"memory_ids": run.memory_ids, "first_call": seen[0], "mode": run.mode,
                  "after": [(m["id"], round(m["confidence"], 2), "review" in m["tags"]) for m in fb]}}))
""", env)
    assert b["mode"] == "llm" and b["memory_ids"] == [a["memory_id"]]
    memo = [m for m in b["first_call"] if m["role"] == "system" and "[MEMORY CONTEXT]" in m["content"]]
    assert memo and "Luca Ostrander owns billing-api per ADR-0009." in memo[0]["content"]
    assert b["after"] == [[a["memory_id"], 0.5, True]]          # the Verifier said refuted: -0.20 and flagged
    acts = [e["action"] for e in ledger(hmem_env / fx_store.tenant) if e["memoryId"] == a["memory_id"]]
    assert acts[0] == "create" and "read" in acts and acts[-1] == "feedback"
    from honest_memory import tools as T

    rec = T.call(fx_store, "recall", {"entity": "billing-api", "limit": 20}, T.Context())
    flagged = next(r for r in rec.rows if r.get("memory_id") == a["memory_id"])
    assert flagged["needs_review"] is True and flagged["confidence"] == pytest.approx(0.5)
