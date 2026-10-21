"""queries/*.cypher: written by scripts/export_queries.py from the tools' own traces. Two checks on every engine:
the committed files are what the tools run today (on 6.x, the engine that writes them), and every statement in every
file runs as written, with FalkorDB's CYPHER parameter header, and returns the row count recorded next to it."""

import re
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import export_queries as X  # noqa: E402

from honest_memory.api import falkordb_version  # noqa: E402
from honest_memory.schema import graph_name  # noqa: E402
from tests.conftest import PORTS, RUN  # noqa: E402

FILES = sorted((ROOT / "queries").glob("*.cypher"))
MARK = re.compile(r"^// \[(\d+)\] \[(tenant|scratch)\] rows: (\d+)$")


def statements(path: Path) -> list[tuple[str, int, str]]:
    lines, out = path.read_text().splitlines(), []
    for i, line in enumerate(lines):
        m = MARK.match(line)
        if m:
            text = lines[i + 1]
            assert text.endswith(";"), (path.name, i + 2)
            out.append((m.group(2), int(m.group(3)), text[:-1]))
    return out


@pytest.fixture(scope="module", params=PORTS, ids=lambda p: f"falkordb:{p}")
def qstore(request):
    s = X.build_store(request.param, f"test_lane_{RUN}_q{request.param}")
    yield s
    s.drop()


def test_files_exist_and_cover_the_departure_tools():
    assert [f.stem for f in FILES] == [c[0] for c in X.CALLS]
    assert all(statements(f) for f in FILES)
    assert any("algo.WCC" in s for _, _, s in statements(ROOT / "queries/04-without.cypher"))
    assert any("algo.betweenness" in s for _, _, s in statements(ROOT / "queries/05-bridges.cypher"))


def test_committed_files_are_what_the_tools_run(qstore):
    if not falkordb_version(qstore.db).startswith("6."):
        pytest.skip("the files are written on FalkorDB 6.x; 4.22 samples betweenness more times")
    fresh = X.export(qstore)
    for f in FILES:
        assert fresh[f.name] == f.read_text(), f"{f.name} drifted: run scripts/export_queries.py"


def test_every_statement_runs_as_written(qstore):
    v4 = falkordb_version(qstore.db).startswith("4.")
    tenant = qstore.db.select_graph(graph_name(qstore.tenant))
    for f in FILES:
        scratch = qstore.db.select_graph(f"{graph_name(qstore.tenant)}__file_{uuid.uuid4().hex[:8]}")
        try:
            for where, rows, text in statements(f):
                g = scratch if where == "scratch" else tenant
                got = len(g.query(text).result_set)
                if v4 and "algo.betweenness" in text:
                    continue  # sampled on 4.22: the number of runs differs, each still answers
                assert got == rows, (f.name, text[:120], got, rows)
        finally:
            try:
                scratch.delete()
            except Exception:
                pass
