"""Python side of the Meterless H-MEM engine: a Node sidecar (open_agent/hmem_sidecar.mjs) over a pipe.

The engine is the H-MEM reference implementation from github.com/Meterless/Meterless (engines/hmem/reference),
vendored and pinned under vendor/hmem (see vendor/hmem/README-PIN.md) and bundled to plain ESM so it runs on any
Node >= 20 with no npm install. Nothing listens on a port: one child process per state directory, JSON lines on
stdin/stdout, state in <dir>/state.json and the audit trail in <dir>/ledger.jsonl.

What the product uses it for (docs/METERLESS.md):
- an answer that passed the checks (the write gate, the Verifier) is stored with layer long_term;
- before answering, the question is looked up and the [MEMORY CONTEXT] block is put in front of the model;
- the Verifier's verdict on an answer that drew on a memory becomes H-MEM feedback (helpful / not_helpful / wrong).

Env: HM_HMEM_DIR (state root, default <repo>/.state/hmem), HM_NODE (node binary), HMEM_ENGINE (engine bundle).
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SIDECAR = REPO / "open_agent" / "hmem_sidecar.mjs"
ENGINE = REPO / "vendor" / "hmem" / "dist" / "hmem.mjs"
BUNDLED_NODE = "/Applications/Nightshift.app/Contents/MacOS/node"
DEFAULT_ROOT = REPO / ".state" / "hmem"
TIMEOUT_S = 15.0

# Verifier verdicts -> H-MEM feedback kinds (deltas +0.05 / -0.03 / -0.20 confidence, AGENTS.md section 9.2).
VERDICT_FEEDBACK = {
    "verified": "helpful", "supported": "helpful", "approved": "helpful", "pass": "helpful", "passed": "helpful",
    "unsupported": "not_helpful", "insufficient": "not_helpful", "revise": "not_helpful", "unclear": "not_helpful",
    "partial": "not_helpful",
    "refuted": "wrong", "contradicted": "wrong", "wrong": "wrong", "fail": "wrong", "failed": "wrong",
    "rejected": "wrong",
}


class HMemError(RuntimeError):
    """The sidecar could not start, did not answer in time, or refused a request."""


def node_binary() -> str | None:
    for cand in (os.environ.get("HM_NODE"), shutil.which("node"), BUNDLED_NODE):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def available() -> bool:
    """True when a Node binary, the sidecar and the engine bundle are all present."""
    engine = Path(os.environ.get("HMEM_ENGINE") or ENGINE)
    return bool(node_binary()) and SIDECAR.is_file() and engine.is_file()


def state_root() -> Path:
    return Path(os.environ.get("HM_HMEM_DIR") or DEFAULT_ROOT)


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:80] or "default"


def verdict_to_feedback(verdict: str) -> str | None:
    return VERDICT_FEEDBACK.get((verdict or "").strip().lower())


class HMem:
    """One sidecar process bound to one state directory. Use as a context manager or call close()."""

    def __init__(self, persist_dir: str | os.PathLike, actor: str = "wlwt", timeout: float = TIMEOUT_S):
        node = node_binary()
        if not node:
            raise HMemError("no node binary found (set HM_NODE)")
        if not available():
            raise HMemError(f"H-MEM engine missing: {os.environ.get('HMEM_ENGINE') or ENGINE}")
        self.persist_dir = Path(persist_dir)
        self.timeout = timeout
        self._seq = 0
        self._lock = threading.Lock()
        self._proc = subprocess.Popen([node, str(SIDECAR), "--persist-dir", str(self.persist_dir), "--actor", actor],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, bufsize=1)
        self._lines: "queue.Queue[str | None]" = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.info = self._read(0)

    # -- transport ----------------------------------------------------------------------------

    def _pump(self) -> None:
        for line in self._proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _read(self, want_id: int) -> dict:
        deadline = time.monotonic() + self.timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise HMemError(f"sidecar did not answer within {self.timeout:.0f} s")
            try:
                line = self._lines.get(timeout=left)
            except queue.Empty:
                continue
            if line is None:
                self._proc.wait(timeout=5)
                err = self._proc.stderr.read()
                raise HMemError(f"sidecar exited (code {self._proc.returncode}): {err.strip()[:300]}")
            msg = json.loads(line)
            if msg.get("id") != want_id:
                continue
            if not msg.get("ok"):
                raise HMemError(msg.get("error") or "sidecar refused the request")
            return msg.get("result")

    def call(self, op: str, **args):
        with self._lock:
            if self._proc.poll() is not None:
                raise HMemError(f"sidecar is not running (exit code {self._proc.returncode})")
            self._seq += 1
            self._proc.stdin.write(json.dumps({"id": self._seq, "op": op, "args": args}) + "\n")
            self._proc.stdin.flush()
            return self._read(self._seq)

    def close(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
                proc.wait(timeout=5)
        for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
            try:
                stream and stream.close()
            except Exception:
                pass

    def __enter__(self) -> "HMem":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    # -- the engine's surface -----------------------------------------------------------------

    def add(self, content: str, source: str, *, layer: str = "long_term", type: str = "factual",
            tags: list[str] | None = None, confidence: float = 0.7, provenance: dict | None = None,
            chat_id: str | None = None, derived_from: list[str] | None = None) -> dict:
        args = {"content": content, "source": source, "layer": layer, "type": type, "tags": tags or [],
                "confidence": confidence}
        if provenance:
            args["provenance"] = provenance
        if chat_id:
            args["chatId"] = chat_id
        if derived_from:
            args["derivedFrom"] = derived_from
        return self.call("add", **args)

    def query(self, text: str, top_n: int = 5, threshold: float = 0.1) -> dict:
        return self.call("query", text=text, topN=top_n, threshold=threshold)

    def feedback(self, memory_id: str, kind: str) -> dict:
        if kind not in ("helpful", "not_helpful", "wrong"):
            raise HMemError(f"feedback kind must be helpful, not_helpful or wrong, not {kind!r}")
        return self.call("feedback", id=memory_id, kind=kind)

    def get(self, memory_id: str | None = None, ref: str | None = None) -> dict | None:
        return self.call("get", id=memory_id, ref=ref)

    def list(self, tag: str | None = None) -> list[dict]:
        return self.call("list", tag=tag)

    def import_records(self, records: list[dict]) -> dict:
        return self.call("import", records=records)

    def stats(self) -> dict:
        return self.call("stats")

    # -- the product's use of it --------------------------------------------------------------

    def store_answer(self, question: str, answer: str, *, agent: str, citations: list[str] | None = None,
                     run_id: str | None = None, verdict: str | None = None, tags: list[str] | None = None,
                     at_ms: int | None = None) -> dict:
        """Keep an answer that passed the checks. Long-term, with the question and the cited record ids."""
        refs = [f"answer:{run_id}" if run_id else f"answer:{int(time.time() * 1000)}"] + list(citations or [])
        prov = {"origin": "model_response", "learnedAt": at_ms or int(time.time() * 1000),
                "label": f"approved answer ({verdict or 'passed the checks'}) by {agent}", "refs": refs}
        content = f"Q: {' '.join(question.split())} | A: {' '.join(answer.split())}"
        return self.add(content, source=agent, layer="long_term", type="factual",
                        tags=["answer"] + [f"cites:{c}" for c in citations or []] + list(tags or []),
                        confidence=0.7, provenance=prov)

    def recall(self, question: str, top_n: int = 3, threshold: float = 0.2,
               tag: str | None = "answer") -> tuple[str, list[dict]]:
        """Memories to put in front of the model before it answers: ([MEMORY CONTEXT] block, hits)."""
        res = self.call("recall", text=question, topN=top_n, threshold=threshold, tag=tag)
        return res["context"], res["memories"]

    def verdict(self, memory_ids: list[str], verdict: str) -> list[dict]:
        """Map the Verifier's verdict on an answer that used these memories to H-MEM feedback."""
        kind = verdict_to_feedback(verdict)
        if kind is None:
            raise HMemError(f"unknown verdict {verdict!r}; known: {', '.join(sorted(VERDICT_FEEDBACK))}")
        return [self.feedback(mid, kind) for mid in memory_ids]


# --- one sidecar per state directory per process --------------------------------------------

_OPEN: "OrderedDict[str, HMem]" = OrderedDict()
_OPEN_MAX = 4
_OPEN_LOCK = threading.Lock()


def for_tenant(tenant: str, actor: str = "wlwt") -> HMem:
    """The process-wide sidecar for one tenant's state directory (opened on first use)."""
    path = str(state_root() / _safe(tenant))
    with _OPEN_LOCK:
        h = _OPEN.get(path)
        if h is not None and h.alive:
            _OPEN.move_to_end(path)
            return h
        h = HMem(path, actor=actor)
        _OPEN[path] = h
        while len(_OPEN) > _OPEN_MAX:
            _, old = _OPEN.popitem(last=False)
            old.close()
        return h


def memory_context(store, question: str, top_n: int = 3, threshold: float = 0.2) -> tuple[str, list[dict]]:
    """Before answering: findings the write gate accepted (and the graph still holds) that match the question.

    Returns the engine's [MEMORY CONTEXT] block and the hits ({memory, relevance}); empty when nothing matches."""
    from honest_memory.tools import _finding_ref

    live = [_finding_ref(fid, text) for fid, text in store.ro("MATCH (f:Finding) RETURN f.id, f.text")]
    if not live:
        return "", []
    res = for_tenant(store.tenant).call("recall", text=question, topN=top_n, threshold=threshold, tag="finding",
                                        allowRefs=live)
    return res["context"], res["memories"]


def verdict_feedback(store, memory_ids: list[str], verdict: str) -> list[dict]:
    """After the Verifier: its verdict on an answer that used these memories, as H-MEM feedback."""
    return for_tenant(store.tenant).verdict(memory_ids, verdict) if memory_ids else []


def close_all() -> None:
    with _OPEN_LOCK:
        while _OPEN:
            _, h = _OPEN.popitem()
            h.close()


atexit.register(close_all)
