"""Run the input examples live, N times each, one after another, through the integrated loop.

    python scripts/live_series.py --tenant openagent_c --runs 5 [--ingest] [--examples 02,01,03,04]

Round 1 of each example writes output_examples/<name>.live.json and its trace and receipts into logs/; rounds 2..N
go to --out-dir (outputs, traces, receipts). Each run is a new process (`python -m open_agent.run`), niced, with
the models through Meterless Nightshift (one Nemotron lane loaded at a time: NIGHTSHIFT_SWAP=1). Before each run
the 1-minute load average is checked; above --max-load the series waits. One line per run in <out-dir>/series.log.
Needs Zetaris, the chat Postgres, the docs API, FalkorDB and Nightshift up (docs/OPEN-AGENT-NEW-WORK.md).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = {"01": "01_current_owner", "02": "02_tampered_source", "03": "03_not_in_these_systems",
            "04": "04_decision_superseded"}


def utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def wait_for_load(max_load: float, log) -> None:
    waited = 0
    while os.getloadavg()[0] > max_load and waited < 600:
        log(f"{utc()} load {os.getloadavg()[0]:.1f} > {max_load}: waiting 30 s")
        time.sleep(30)
        waited += 30


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--start-round", type=int, default=1)
    ap.add_argument("--examples", default="02,01,03,04")
    ap.add_argument("--ingest", action="store_true", help="rebuild the graph from Zetaris on the first run")
    ap.add_argument("--out-dir", default="docs/open-agent-runs-2026-10-24-nightshift")
    ap.add_argument("--max-load", type=float, default=14.0)
    args = ap.parse_args(argv)
    out_dir = REPO / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    series = open(out_dir / "series.log", "a", encoding="utf-8")

    def log(line: str) -> None:
        print(line, flush=True)
        series.write(line + "\n")
        series.flush()

    env = {k: v for k, v in os.environ.items() if k not in ("OA_LLM_MODEL", "OA_VERIFIER_MODEL", "OA_LLM_PROVIDER",
                                                             "SAMPLE_MODE")}
    env.setdefault("NIGHTSHIFT_SWAP", "1")
    env.setdefault("ZETARIS_BUNDLE_ENV", str(Path("~/zetaris-openagent/zetaris-platform/.env").expanduser()))
    env.setdefault("FALKORDB_PORT", "6395")
    first = True
    for rnd in range(args.start_round, args.runs + 1):
        for key in args.examples.split(","):
            name = EXAMPLES[key]
            wait_for_load(args.max_load, log)
            if rnd == 1:
                env_run = {**env, "OA_LOG_DIR": str(REPO / "logs")}
                out = REPO / "output_examples" / f"{name}.live.json"
            else:
                env_run = {**env, "OA_LOG_DIR": str(out_dir)}
                out = out_dir / f"{name}.run{rnd}.json"
            cmd = ["nice", "-n", "15", sys.executable, "-m", "open_agent.run", "--tenant", args.tenant,
                   "--input", f"input_examples/{name}.json", "--out", str(out)]
            if first and args.ingest:
                cmd.insert(cmd.index("--tenant"), "--ingest")
            first = False
            t0 = time.perf_counter()
            err = out.with_suffix(".stderr.log") if rnd > 1 else out_dir / f"{name}.run1.stderr.log"
            with open(err, "w", encoding="utf-8") as fh:
                code = subprocess.call(cmd, cwd=REPO, env=env_run, stdout=subprocess.DEVNULL, stderr=fh)
            try:
                res = json.loads(out.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                res = {}
            log(json.dumps({"at": utc(), "round": rnd, "example": name, "exit": code, "run_id": res.get("run_id"),
                            "status": res.get("status"), "rounds": res.get("rounds"),
                            "seconds": res.get("execution_time_seconds"), "wall_s": round(time.perf_counter() - t0, 1),
                            "receipts": (res.get("receipts") or {}).get("file"),
                            "signed": (res.get("receipts") or {}).get("signed_receipts"),
                            "conflicts": [c["id"] for c in res.get("conflicts") or []],
                            "memory_recalled": ((res.get("memory") or {}).get("recall") or {}).get("ids"),
                            "error": (res.get("error") or {}).get("type")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
