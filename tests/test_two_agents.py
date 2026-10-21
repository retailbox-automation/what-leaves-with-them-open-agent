"""examples/two_agents.py end to end on a freshly ingested corpus, with the router (no model)."""

import importlib.util
from pathlib import Path

import pytest

from honest_memory.agent import AgentConfig
from honest_memory.corpus.generator import build, write_corpus
from honest_memory.ingest import ingest_corpus
from honest_memory.schema import graph_name
from tests.conftest import PORTS, RUN, make_store

SCRIPT = Path(__file__).resolve().parent.parent / "examples" / "two_agents.py"


def load_example():
    spec = importlib.util.spec_from_file_location("two_agents_example", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(params=PORTS, ids=lambda p: f"falkordb:{p}")
def corpus_store(request, tmp_path):
    s = make_store(request.param, f"two{request.param}")
    ingest_corpus(s, write_corpus(build(42), tmp_path / "corpus"))
    yield s
    s.drop()


def test_the_demo_runs_and_both_agents_change_their_answer(corpus_store):
    lines: list[str] = []
    res = load_example().demo(corpus_store, AgentConfig(model=None), out=lines.append)
    assert res["owner_before"] == res["owner_ops_before"] == "Luca Ostrander"
    assert (res["finding"], res["handoff"]) == ("accepted", "accepted")
    assert res["recall_outcome"] == "answered_from_memory" and len(res["recall_dates"]) == 2
    assert all(d.startswith("20") for d in res["recall_dates"])
    change = res["change"]
    assert change["previous"] == "Luca Ostrander" and change["new"] != "Luca Ostrander"
    assert "SUPERSEDE" in change["results"]
    assert res["owner_support_after"] == res["owner_ops_after"] == change["new"]
    assert res["previous_owner"] == "Luca Ostrander" and len(res["stale_findings"]) == 1
    assert res["why_receipts"] == ["SUPERSEDE"]
    text = "\n".join(lines)
    assert "== Session 1" in text and "== Session 2" in text and "trace  current_owner" in text
    assert text.count("trace  ") >= 8  # every step prints tool, arguments, ms and rows


def test_the_command_line_ingests_runs_and_drops_its_tenant(port, monkeypatch, capsys):
    tenant = f"test_lane_{RUN}_cli{port}"
    monkeypatch.setenv("FALKORDB_PORT", str(port))
    monkeypatch.delenv("HM_LLM_MODEL", raising=False)
    assert load_example().main(["--tenant", tenant, "--ingest", "--drop"]) == 0
    out = capsys.readouterr().out
    assert "ingested 403 episodes" in out and "After the change" in out
    from falkordb import FalkorDB

    assert graph_name(tenant) not in FalkorDB(host="localhost", port=port).list_graphs()


def test_the_command_line_refuses_an_empty_tenant(port, monkeypatch):
    monkeypatch.setenv("FALKORDB_PORT", str(port))
    with pytest.raises(SystemExit):
        load_example().main(["--tenant", f"test_lane_{RUN}_empty{port}"])
