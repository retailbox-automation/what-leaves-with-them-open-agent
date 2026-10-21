"""Shared fixtures: one throwaway tenant per test on every FalkorDB in HM_TEST_PORTS.

Locally: HM_TEST_PORTS=6390,6393 runs both engines (6.0.2 Rust and 4.22 C, the Cloud free version).
Each run uses a unique tenant prefix and drops what it created.
"""

import os
import uuid

import pytest

from honest_memory.store import Store

PORTS = [int(p) for p in os.environ.get("HM_TEST_PORTS", "6379").split(",")]
RUN = uuid.uuid4().hex[:8]


def make_store(port: int, name: str) -> Store:
    from falkordb import FalkorDB

    db = FalkorDB(host=os.environ.get("HM_TEST_HOST", "localhost"), port=port, socket_timeout=15)
    s = Store(db, f"test_lane_{RUN}_{name}")
    s.drop()
    return s


@pytest.fixture(params=PORTS, ids=lambda p: f"falkordb:{p}")
def port(request):
    return request.param


@pytest.fixture
def empty_store(port, request):
    s = make_store(port, request.node.name.split("[")[0][:24] + str(port))
    s.ensure_schema()
    yield s
    s.drop()


@pytest.fixture
def fx_store(port, request):
    """The small fixture graph from tests/fixtures.py."""
    from tests.fixtures import build_fixture

    s = make_store(port, "fx" + str(port))
    gate = build_fixture(s)
    s.gate_ids = gate
    s.trace.clear()
    yield s
    s.drop()
