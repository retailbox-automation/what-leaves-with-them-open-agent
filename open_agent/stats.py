"""Numbers from trace files: what the docs quote, recomputed from the traces themselves.

    python -m open_agent.stats logs/trace-*.jsonl docs/open-agent-runs-2026-10-24/trace-*.jsonl
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict


def _span(values: list[float]) -> str:
    if not values:
        return "-"
    return f"n={len(values)} p50={statistics.median(values):.3f} max={max(values):.3f}"


def summarize(paths: list[str]) -> dict:
    runs, lat, llm, tokens = {}, defaultdict(list), defaultdict(list), defaultdict(list)
    verdicts, reasons, rounds = Counter(), Counter(), defaultdict(int)
    for path in paths:
        for line in open(path, encoding="utf-8"):
            e = json.loads(line)
            key = (e["mode"], e["run_id"])
            if e["step"] == "start":
                runs[key] = {"question": e.get("question"), "tamper": False}
            elif e["step"] == "tamper_source":
                runs[key]["tamper"] = True
            elif e["step"] == "end":
                runs[key].update(status=e.get("status_of_run"), seconds=e.get("seconds"))
            elif e["step"] in ("select", "reread", "absence_check") and e["status"] == "ok":
                lat[f"{e['mode']} {e['endpoint']} {e['step']} {e.get('source', '')}".strip()].append(e["seconds"])
            elif e["step"] == "llm_call" and e["status"] == "ok":
                llm[f"{e['agent']} {e['purpose']}"].append(e["seconds"])
                tokens[f"{e['agent']} {e['purpose']}"].append((e.get("usage") or {}).get("prompt_tokens") or 0)
            elif e["step"] == "check":
                verdicts[(e.get("verdict") or "").split(":")[0]] += 1
                reasons[e.get("reason")] += 1
            elif e["step"] == "verdict":
                rounds[key] = max(rounds[key], e["round"])
    by_question = defaultdict(list)
    for key, r in runs.items():
        by_question[(key[0], r["question"], r["tamper"])].append((r.get("status"), rounds.get(key, 0), r.get("seconds")))
    return {"runs": len(runs),
            "per_question": {f"{m} | {q} | tamper={t}": {
                "runs": len(v), "status": dict(Counter(s for s, _, _ in v)), "rounds": dict(Counter(n for _, n, _ in v)),
                "seconds": _span([s for _, _, s in v if s is not None])} for (m, q, t), v in sorted(by_question.items())},
            "source_reads_s": {k: _span(v) for k, v in sorted(lat.items())},
            "llm_calls_s": {k: _span(v) for k, v in sorted(llm.items())},
            "llm_prompt_tokens_p50": {k: statistics.median(v) for k, v in sorted(tokens.items())},
            "check_verdicts": dict(verdicts), "check_reasons": dict(reasons)}


if __name__ == "__main__":
    print(json.dumps(summarize(sys.argv[1:]), indent=2, ensure_ascii=False))
