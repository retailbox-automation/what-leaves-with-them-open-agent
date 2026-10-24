"""Live proof of both Meterless requirements on this Mac, written to logs/meterless-live-proof-<ts>.json:

1. session A (its own process): an agent's finding goes through the write gate and is kept by H-MEM (long_term);
2. session B (a new process): the agent is asked a paraphrase through Nightshift (Nemotron 3.5 Lightning, native tool
   calls); H-MEM's [MEMORY CONTEXT] is in front of the model before its first call;
3. the Verifier (Nemotron 3 Nano through Nightshift) judges B's answer against the cited records; its verdict becomes
   H-MEM feedback on the memories B was shown;
4. the run's signed receipts are exported to logs/receipts-<scope>.jsonl after the secrets/PII scan.

Needs Nightshift up (scripts/nightshift_headless.sh start) and FalkorDB with an ingested tenant. With --swap the
script loads one Nemotron lane at a time (Lightning for B, then Nano for the Verifier), for a Mac short on memory.

    FALKORDB_PORT=6395 python scripts/meterless_live_proof.py --tenant laneb_quillfield --swap
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
sys.path.insert(0, str(REPO))

from open_agent import nightshift as N  # noqa: E402

HEADLESS = REPO / "scripts" / "nightshift_headless.sh"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def session(code: str, env: dict) -> dict:
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env={**os.environ, **env}, capture_output=True,
                         text=True, timeout=900)
    if out.returncode != 0:
        raise SystemExit(f"session failed:\n{out.stderr[-3000:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def lane(lane_id: str, action: str) -> None:
    """Start or stop one lane through the headless script; a start returns only once the lane is ready."""
    out = subprocess.run([str(HEADLESS), "lane", lane_id, action], capture_output=True, text=True, timeout=400)
    if out.returncode != 0:
        raise SystemExit(f"lane {lane_id} {action} failed:\n{out.stdout[-1500:]}{out.stderr[-1500:]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="laneb_quillfield")
    ap.add_argument("--question", default="Which person is in charge of the billing-api system at the moment?")
    ap.add_argument("--swap", action="store_true", help="one Nemotron lane loaded at a time")
    ap.add_argument("--resume", default=None, help="a saved report: redo only the steps it lacks")
    args = ap.parse_args()
    prev = json.loads(Path(args.resume).read_text()) if args.resume else None
    scope = prev["scope"] if prev else N.new_scope("wlwt-proof")
    env = {"HM_MEMORY_BACKEND": "hmem", "HM_LLM_PROVIDER": "nightshift", "HM_NIGHTSHIFT_AGENT": "investigator",
           "PYTHONPATH": str(REPO)}
    report: dict = prev or {"scope": scope, "tenant": args.tenant, "started": now(), "steps": []}
    done = {st["step"]: st for st in report["steps"]}
    out = REPO / "logs" / f"meterless-live-proof-{scope}.json"

    def save() -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    # 1. session A: remember through the gate -> H-MEM keeps it
    a = done.get("A remember") or session(f"""
import json, os
from honest_memory.store import Store, connect
from honest_memory import tools as T
from open_agent import hmem_bridge as B
s = Store(connect(), {args.tenant!r})
ans = T.call(s, "remember", {{"text": "billing-api is owned by Luca Ostrander (Platform) since 2026-09-22, per ADR-0009.",
                             "about": ["billing-api", "Luca Ostrander"], "source_ids": ["ADR-0009", "msg-0148"]}},
             T.Context(agent="agent:support", session={scope + '-A'!r}))
print(json.dumps({{"outcome": ans.outcome, "record_id": ans.rows[0].get("record_id"), "memory": ans.rows[0].get("memory"),
                  "receipt": ans.receipts[0].id if ans.receipts else None, "pid": os.getpid(),
                  "hmem_dir": str(B.state_root() / {args.tenant!r})}}))
""", env)
    if "A remember" not in done:
        report["steps"].append({"step": "A remember", "at": now(), **a})
        save()

    # 2. session B: a new process asks a paraphrase through Nightshift (Lightning, tools)
    if args.swap and "B ask through Nightshift" not in done:
        lane("nemotron-nano", "stop")
        lane("nemotron-lightning", "start")
    t0 = time.perf_counter()
    b = done.get("B ask through Nightshift") or session(f"""
import json, os
from honest_memory.store import Store, connect
from honest_memory.agent import Agent, AgentConfig
s = Store(connect(), {args.tenant!r})
ag = Agent(s, name="agent:support", session={scope + '-B'!r}, config=AgentConfig.from_env())
ag.client.scope = {scope!r}
run = ag.ask({args.question!r})
d = run.to_dict()
print(json.dumps({{"pid": os.getpid(), "mode": run.mode, "memory_ids": run.memory_ids, "sentence": run.sentence,
                  "sentence_source": run.sentence_source, "check": run.check, "errors": run.errors, "ms": run.ms,
                  "tools": [a["tool"] for a in d["answers"]],
                  "sources": sorted({{x["id"] for a in d["answers"] for x in a.get("sources", [])}}),
                  "receipts": [r.to_dict() for r in ag.client.receipts]}}))
""", env)
    if "B ask through Nightshift" not in done:
        b["wall_s"] = round(time.perf_counter() - t0, 1)
        report["steps"].append({"step": "B ask through Nightshift", "at": now(), **b})
        save()

    # 3. the Verifier on Nano judges B's sentence against the cited records; verdict -> H-MEM feedback
    if args.swap and "Verifier on Nano" not in done:
        lane("nemotron-lightning", "stop")
        lane("nemotron-nano", "start")
    v = done.get("Verifier on Nano") or session(f"""
import json, re
from honest_memory.store import Store, connect
from open_agent import nightshift as N
from open_agent.hmem_bridge import verdict_feedback
s = Store(connect(), {args.tenant!r})
ids = {b["sources"]!r}
rows = s.ro("MATCH (e:Episode) WHERE e.id IN $ids RETURN e.id, e.text", {{"ids": ids}})
records = "\\n".join(f"[{{i}}] {{(t or '')[:600]}}" for i, t in rows)
reply = N.chat([{{"role": "system", "content": 'You check one answer against the records. Reply with JSON only: '
                 '{{"verdict": "verified" | "unsupported" | "refuted", "why": "<one sentence>"}}.'}},
               {{"role": "user", "content": "Question: " + {args.question!r} + "\\nAnswer: " + {b["sentence"]!r} +
                 "\\nRecords:\\n" + records}}], agent="verifier", scope={scope!r}, max_tokens=600)
text = reply.message.get("content") or ""
m = re.search(r"\\{{.*\\}}", text, re.S)
verdict = (json.loads(m.group()) if m else {{}}).get("verdict", "unsupported")
fb = verdict_feedback(s, {b["memory_ids"]!r}, verdict)
print(json.dumps({{"verdict": verdict, "reply": text[:400], "records": [i for i, _ in rows],
                  "feedback": [{{"id": x["id"], "confidence": x["confidence"], "tags": x["tags"]}} for x in fb],
                  "receipt": reply.receipt.to_dict()}}))
""", env)
    if "Verifier on Nano" not in done:
        report["steps"].append({"step": "Verifier on Nano", "at": now(), **v})
        save()

    # 4. the ledger and the signed receipts
    hdir = Path(a["hmem_dir"])
    led = [json.loads(x) for x in (hdir / "ledger.jsonl").read_text().splitlines()]
    mem_id = (a.get("memory") or {}).get("memory_id")
    report["ledger_for_memory"] = [{k: e.get(k) for k in ("action", "actor", "timestamp")} | {"kind": (e.get("details") or {}).get("kind")}
                                   for e in led if e["memoryId"] == mem_id]
    ids = [r["receipt_id"] for r in b.get("receipts", [])] + [v["receipt"]["receipt_id"]]
    report["receipts_export"] = N.export_receipts(ids, REPO / "logs" / f"receipts-{scope}.jsonl")
    report["finished"] = now()
    save()
    print(out)
    print(json.dumps({"memory_ids_seen_by_B": b["memory_ids"], "verdict": v["verdict"],
                      "receipts": report["receipts_export"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
