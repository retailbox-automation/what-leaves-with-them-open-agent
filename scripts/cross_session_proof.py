"""Cross-session memory, end to end: two processes on a fresh tenant, the second recalls what the first verified.

    python scripts/cross_session_proof.py            # live: Zetaris + Nightshift (NIGHTSHIFT_SWAP=1) + H-MEM
    python scripts/cross_session_proof.py --sample   # SAMPLE_MODE: no Zetaris, no model; H-MEM still runs

Process A: `python -m open_agent.run --ingest` with input_examples/01_current_owner.json. Process B (a new process):
input_examples/05_cross_session_recall.json, a paraphrase. Writes
  output_examples/05_cross_session_recall.<live|sample>.json   B's result plus the proof block
  logs/hmem-ledger-<tenant>.jsonl                              the tenant's H-MEM ledger (create / read / feedback)
and prints the proof block: A's stored memory id, B's recalled ids, B's feedback, both pids and run ids.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def run(args: list[str], env: dict, out: Path) -> tuple[dict, int]:
    proc = subprocess.Popen(["nice", "-n", "15", sys.executable, "-m", "open_agent.run", *args, "--out", str(out)],
                            cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    code = proc.wait()
    return json.loads(out.read_text(encoding="utf-8")), proc.pid if code == 0 else -proc.pid


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--tenant", default=None)
    args = ap.parse_args(argv)
    mode = "sample" if args.sample else "live"
    tenant = args.tenant or f"oa_xs_{mode}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    env = {k: v for k, v in os.environ.items() if k not in ("OA_LLM_MODEL", "OA_VERIFIER_MODEL", "OA_LLM_PROVIDER")}
    env.setdefault("NIGHTSHIFT_SWAP", "1")
    env.setdefault("ZETARIS_BUNDLE_ENV", str(Path("~/zetaris-openagent/zetaris-platform/.env").expanduser()))
    env.setdefault("FALKORDB_PORT", "6395")
    env["OA_LOG_DIR"] = str(REPO / "logs")
    flag = ["--sample"] if args.sample else []
    out_a = REPO / "output_examples" / f"05_cross_session_recall.A.{mode}.json"
    out_b = REPO / "output_examples" / f"05_cross_session_recall.{mode}.json"
    a, pid_a = run([*flag, "--ingest", "--tenant", tenant, "--input", "input_examples/01_current_owner.json"], env, out_a)
    b, pid_b = run([*flag, "--tenant", tenant, "--input", "input_examples/05_cross_session_recall.json"], env, out_b)
    hmem_dir = Path(os.environ.get("HM_HMEM_DIR") or REPO / ".state" / "hmem") / tenant
    ledger_out = REPO / "logs" / f"hmem-ledger-{tenant}.jsonl"
    shutil.copyfile(hmem_dir / "ledger.jsonl", ledger_out)
    stored = (a.get("memory") or {}).get("stored") or {}
    recalled = ((b.get("memory") or {}).get("recall") or {}).get("ids") or []
    ledger = [json.loads(x) for x in ledger_out.read_text().splitlines()]
    mid = stored.get("memory_id")
    proof = {"tenant": tenant, "mode": mode,
             "A": {"pid": pid_a, "run_id": a.get("run_id"), "question": a.get("question"), "status": a.get("status"),
                   "stored_memory": stored, "finding": (a.get("write_gate") or {}).get("finding"),
                   "trace_file": a.get("trace_file"), "receipts": (a.get("receipts") or {}).get("file")},
             "B": {"pid": pid_b, "run_id": b.get("run_id"), "question": b.get("question"), "status": b.get("status"),
                   "recalled_memory_ids": recalled,
                   "recall_status": ((b.get("memory") or {}).get("recall") or {}).get("status"),
                   "feedback": (b.get("memory") or {}).get("feedback"), "trace_file": b.get("trace_file"),
                   "receipts": (b.get("receipts") or {}).get("file")},
             "a_memory_recalled_by_b": bool(mid and mid in recalled),
             "ledger_file": str(ledger_out.relative_to(REPO)),
             "ledger_for_that_memory": [{"action": e["action"], "actor": e.get("actor"), "timestamp": e["timestamp"]}
                                        for e in ledger if e["memoryId"] == mid]}
    b["cross_session_proof"] = proof
    out_b.write_text(json.dumps(b, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2))
    return 0 if proof["a_memory_recalled_by_b"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
