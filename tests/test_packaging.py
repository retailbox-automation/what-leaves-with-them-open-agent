"""Packaging checks for judges: metadata.json, .env.example, README links, the default bind host, and the
SAMPLE_MODE command line end to end (needs FalkorDB: HM_TEST_PORTS=<port>, else that test is skipped)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_metadata_has_the_judged_keys():
    meta = json.loads((ROOT / "metadata.json").read_text(encoding="utf-8"))
    for key in ("name", "summary", "track", "agents", "sponsor_technology", "data_sources", "synthetic_data",
                "entry_points", "sample_mode", "pre_existing", "license"):
        assert key in meta, key
    assert meta["track"] == "The Agent That Can Explain Why"
    assert {a["name"] for a in meta["agents"]} == {"Investigator", "Verifier"}
    for a in meta["agents"]:
        assert a["role"] and a["model_live"] and a["model_sample_mode"]
        assert (ROOT / a["code"]).is_file()
    for sponsor in ("zetaris", "meterless_nightshift", "meterless_engines", "nvidia", "cursor"):
        assert meta["sponsor_technology"].get(sponsor), sponsor
    assert {"cli", "api", "docker"} <= set(meta["entry_points"])
    assert meta["synthetic_data"] is True


def test_env_example_holds_placeholders_only():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    secretish = re.compile(r"(PASSWORD|API_KEY|TOKEN|SECRET)$", re.I)
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if "=" not in line or " " in line.split("=", 1)[0]:
            continue
        name, value = line.split("=", 1)
        if secretish.search(name):
            assert value.startswith("your-"), f"{name} must be a placeholder"
    assert not re.search(r"sk-[A-Za-z0-9]{16,}|nvapi-|-----BEGIN|ghp_[A-Za-z0-9]{20}", text)
    assert re.search(r"^SAMPLE_MODE=true$", text, re.M)
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignored and "!.env.example" in ignored


def test_readme_links_resolve():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = []
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        path = target.split("#", 1)[0]
        if not path or path.startswith(("http://", "https://", "mailto:")):
            continue
        if not (ROOT / path).exists():
            missing.append(target)
    assert not missing, missing


def test_readme_first_screen_names_track_sponsors_and_run():
    head = "\n".join((ROOT / "README.md").read_text(encoding="utf-8").splitlines()[:6])
    assert "Track:** 02" in head and "The Agent That Can Explain Why" in head
    for word in ("Zetaris", "Meterless", "Nightshift", "NVIDIA", "docker compose up", "POST", "/run"):
        assert word in head, word
    assert "Developed in Cursor" not in (ROOT / "README.md").read_text(encoding="utf-8")


def test_default_bind_host_is_loopback(monkeypatch):
    monkeypatch.delenv("HOST", raising=False)
    sys.path.insert(0, str(ROOT))
    import serve

    assert serve.DEFAULT_HOST == "127.0.0.1"
    assert serve.bind_host() == "127.0.0.1"
    src = (ROOT / "serve.py").read_text(encoding="utf-8")
    assert 'host="0.0.0.0"' not in src and "host='0.0.0.0'" not in src
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    published = re.findall(r'^\s*-\s*"([^"]+)"\s*$', compose, re.M)
    assert published and all(p.startswith("127.0.0.1:") for p in published), published


@pytest.mark.skipif(not os.environ.get("HM_TEST_PORTS"), reason="needs FalkorDB: set HM_TEST_PORTS=<port>")
def test_sample_mode_cli_runs_offline(tmp_path):
    port = os.environ["HM_TEST_PORTS"].split(",")[0]
    env = {"PATH": os.environ.get("PATH", ""), "SAMPLE_MODE": "true", "FALKORDB_PORT": port,
           "OA_TENANT": "pkg_test_sample", "OA_LOG_DIR": str(tmp_path / "logs"),
           "HM_HMEM_DIR": str(tmp_path / "hmem"), "HOME": str(tmp_path)}
    out = tmp_path / "out.json"
    proc = subprocess.run([sys.executable, "-m", "open_agent.run", "--sample", "--ingest", "--input",
                           "input_examples/02_tampered_source.json", "--out", str(out)],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-800:]
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["mode"] == "sample" and result["endpoint"] == "local-sample"
    assert result["status"] == "verified" and result["rounds"] == 2
    assert any(d["id"] == "msg-0148" and d["reason"] == "quote_not_in_record" for d in result["dropped_citations"])
    assert result["tamper"]["restored"] is True
    trace = tmp_path / "logs" / f"trace-{result['run_id']}.jsonl"
    assert trace.is_file() and sum(1 for _ in trace.open()) > 5
