"""One HTTP server for judges: the Investigator <-> Verifier loop behind POST /run, plus the existing API and panel.

    python serve.py                     (what the Docker image runs; PORT, default 8000)
    curl -X POST localhost:8000/run -H 'content-type: application/json' -d @input_examples/01_current_owner.json

POST /run takes either the input_examples shape ({"question": ..., "tamper"?: ..., "tamper_text"?: ...}) or the
organizer's shape ({"run_id"?: ..., "input": {"query" | "question": ..., ...}, "options": {"sample_mode": bool}}).
It runs `python -m open_agent.run --input <file> --out <file>` in a child process, with a timeout, and returns
that command's JSON result under "output", with the run id, the trace file and the wall time. Nothing here
answers a question by itself: the loop, the SQL, the Verifier and the write gate are open_agent's code.

GET /run/{run_id}/trace returns the JSONL trace of a run made by this server (logs/trace-<run_id>.jsonl).
Everything else (GET /health, /v1/..., the panel at /) is honest_memory.api, unchanged.

Env: SAMPLE_MODE (default true here: no Zetaris, no model, no credentials), OA_RUN_TIMEOUT (seconds, default 240),
HOST (default 127.0.0.1: loopback only; docker-compose.yml sets 0.0.0.0 inside the container, published only on
127.0.0.1 of the host), PORT (default 8000), FALKORDB_HOST/FALKORDB_PORT, HM_DEMO_TENANT (the panel's tenant, default quillfield),
and everything open_agent.run reads (OA_LLM_*, ZETARIS_*, NIGHTSHIFT_*) for a live run.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get("OA_LOG_DIR") or ROOT / "logs")
DEFAULT_HOST = "127.0.0.1"
RUN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
TRACE_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{4}$")
AGENTS = [
    {"name": "Investigator", "id": "agent:investigator",
     "role": "answers from the memory graph with cited records (record id, quoted line, claim); revises on the "
             "Verifier's notes"},
    {"name": "Verifier", "id": "agent:verifier",
     "role": "re-reads each cited record by id at its source system; the quote check cannot be overruled; then a "
             "model judgement; rejects with notes, at most 2 revisions"},
]

_one_run = threading.Semaphore(1)  # runs share one graph tenant and a tamper restores at the end: one at a time


def bind_host() -> str:
    """Loopback unless HOST says otherwise (the container sets it; its port is published on 127.0.0.1 only)."""
    return os.environ.get("HOST") or DEFAULT_HOST


def env_sample() -> bool:
    return os.environ.get("SAMPLE_MODE", "true").strip().lower() in ("1", "true", "yes")


class RunBody(BaseModel):
    run_id: str | None = Field(default=None, max_length=64)
    question: str | None = Field(default=None, max_length=500)
    query: str | None = Field(default=None, max_length=500)
    tamper: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    tamper_text: str | None = Field(default=None, max_length=2000)
    input: dict[str, Any] | None = None
    options: dict[str, Any] | None = None
    what_it_shows: str | None = None  # carried by the input_examples files; ignored


def spec_of(body: RunBody) -> dict:
    """The --input file open_agent.run reads: question, and optionally tamper and tamper_text."""
    inner = body.input or {}
    question = inner.get("query") or inner.get("question") or body.query or body.question
    if not isinstance(question, str) or not question.strip():
        raise HTTPException(422, "give a question: {\"question\": ...} or {\"input\": {\"query\": ...}}")
    spec = {"question": question.strip()[:500]}
    tamper = inner.get("tamper") or body.tamper
    if tamper:
        if not isinstance(tamper, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", tamper):
            raise HTTPException(422, "tamper must be a record id such as msg-0148")
        spec["tamper"] = tamper
        text = inner.get("tamper_text") or body.tamper_text
        if text:
            spec["tamper_text"] = str(text)[:2000]
    return spec


def run_loop(spec: dict, sample: bool, timeout: float) -> tuple[dict | None, str, int]:
    """Run open_agent.run in a child process. Returns (result or None, stderr tail, exit code)."""
    with tempfile.TemporaryDirectory(prefix="oa-run-") as tmp:
        inp, out = Path(tmp) / "input.json", Path(tmp) / "output.json"
        inp.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        cmd = [sys.executable, "-m", "open_agent.run", "--input", str(inp), "--out", str(out)]
        if sample:
            cmd.append("--sample")
        env = dict(os.environ, SAMPLE_MODE="true" if sample else "false", OA_LOG_DIR=str(LOG_DIR))
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
        sys.stderr.write(proc.stderr)  # the readable trace lines, into the container log
        sys.stderr.flush()
        result = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else None
        return result, proc.stderr[-2000:], proc.returncode


def add_routes(app) -> None:
    @app.post("/run")
    def run(body: RunBody):
        spec = spec_of(body)
        opt = (body.options or {}).get("sample_mode")
        sample = env_sample() if opt is None else bool(opt)
        client_id = body.run_id if body.run_id and RUN_ID.match(body.run_id) else None
        timeout = float(os.environ.get("OA_RUN_TIMEOUT", "240"))
        if not _one_run.acquire(timeout=timeout):
            return JSONResponse({"status": "error", "error": {"type": "Busy", "recoverable": True,
                                 "message": "another run is in progress; try again"}}, 429)
        t0 = time.perf_counter()
        try:
            result, tail, code = run_loop(spec, sample, timeout)
        except subprocess.TimeoutExpired:
            return JSONResponse({"run_id": client_id, "status": "error", "mode": "sample" if sample else "live",
                                 "error": {"type": "RunTimeout", "recoverable": True,
                                           "message": f"the run did not finish in {timeout:.0f} s"},
                                 "execution_time_seconds": round(time.perf_counter() - t0, 2)}, 504)
        finally:
            _one_run.release()
        elapsed = round(time.perf_counter() - t0, 2)
        if result is None:
            return JSONResponse({"run_id": client_id, "status": "error", "mode": "sample" if sample else "live",
                                 "error": {"type": "RunFailed", "recoverable": False, "exit_code": code,
                                           "message": tail.strip().splitlines()[-1][:300] if tail.strip() else
                                           "open_agent.run wrote no result"},
                                 "execution_time_seconds": elapsed}, 500)
        trace_id = result.get("run_id")
        failed = code != 0 or result.get("status") == "error"
        payload = {
            "run_id": client_id or trace_id,
            "status": "error" if failed else "success",
            "mode": result.get("mode"),
            "output": result,
            "agents": AGENTS,
            "trace_id": trace_id,
            "log_file": f"logs/trace-{trace_id}.jsonl" if trace_id else None,
            "trace_url": f"/run/{trace_id}/trace" if trace_id else None,
            "execution_time_seconds": elapsed,
        }
        if failed:
            err = result.get("error") or {}
            payload["error"] = {"type": err.get("type", "RunFailed"), "message": err.get("message", ""),
                                "hint": err.get("hint"), "recoverable": True}
        return JSONResponse(payload, 200 if not failed else 502)

    @app.get("/run/{trace_id}/trace")
    def trace(trace_id: str):
        if not TRACE_ID.match(trace_id):
            raise HTTPException(422, "a trace id looks like 20261024T123154Z-720b")
        path = LOG_DIR / f"trace-{trace_id}.jsonl"
        if not path.is_file():
            raise HTTPException(404, f"no trace {trace_id}")
        return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="application/x-ndjson")


def create_app():
    from honest_memory.api import create_app as api_app

    app = api_app()
    add_routes(app)
    # the panel's static mount ("/") was added last by honest_memory.api; keep it last so /run wins
    app.router.routes.sort(key=lambda r: getattr(r, "name", "") == "web")
    return app


def main() -> int:
    import secrets

    import uvicorn

    tenant = os.environ.get("HM_DEMO_TENANT", "quillfield")
    corpus = ROOT / "data" / "corpus" / "42"
    if corpus.is_dir() and os.environ.get("OA_SKIP_BOOTSTRAP") != "1":
        # the panel's tenant (the synthetic company through the write gate); the /run tenant is built on first run
        subprocess.run([sys.executable, str(ROOT / "deploy" / "bootstrap.py"), "--corpus", str(corpus),
                        "--tenant", tenant], cwd=ROOT, check=True)
    if not os.environ.get("HM_WRITE_TOKEN") and os.environ.get("HM_PUBLIC_WRITES", "0") != "1":
        os.environ["HM_WRITE_TOKEN"] = secrets.token_urlsafe(32)  # the API's write routes stay locked
        print("serve: API writes are locked (HM_PUBLIC_WRITES is not 1); POST /run is not affected", flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("PORT", "8000"))
    host = bind_host()
    print(f"serve: SAMPLE_MODE={'true' if env_sample() else 'false'}; POST /run and the panel on {host}:{port}",
          flush=True)
    uvicorn.run(create_app(), host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
