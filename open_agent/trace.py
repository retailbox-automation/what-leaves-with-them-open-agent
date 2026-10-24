"""One JSONL trace per run (logs/trace-<run_id>.jsonl), one event per step, echoed to stdout as a readable line."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + format(time.time_ns() % 0xFFFF, "04x")


class Trace:
    def __init__(self, run_id: str, log_dir: Path | str | None = None, echo: bool = True, mode: str = "live"):
        self.run_id, self.mode, self.echo = run_id, mode, echo
        self.path = Path(log_dir or os.environ.get("OA_LOG_DIR") or LOG_DIR) / f"trace-{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[dict] = []

    def event(self, agent: str, step: str, status: str = "ok", **fields) -> dict:
        if clash := {"timestamp", "run_id", "seq", "mode", "agent", "step"} & set(fields):
            raise ValueError(f"trace field names reserved: {sorted(clash)}")
        rec = {"timestamp": utc_now(), "run_id": self.run_id, "seq": len(self.events) + 1, "mode": self.mode,
               "agent": agent, "step": step, "status": status, **fields}
        self.events.append(rec)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        if self.echo:
            print(f"[{'INFO' if status == 'ok' else status.upper()}] run_id={self.run_id} {agent} {step}"
                  f"{' - ' + _summary(fields) if fields else ''}", file=sys.stderr)
        return rec


def _summary(fields: dict) -> str:
    keys = ("question", "endpoint", "citations", "source", "rows", "seconds", "tool", "outcome", "model", "provider_receipt", "round",
            "record_id", "found", "verdict", "accepted", "status_of_run", "reason", "error", "note")
    parts = [f"{k}={str(fields[k])[:120]}" for k in keys if k in fields and fields[k] not in (None, "", [])]
    return " ".join(parts) or ", ".join(fields)[:120]
