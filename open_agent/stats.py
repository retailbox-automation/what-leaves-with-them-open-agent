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
    providers, routed, receipts, swaps, memory = Counter(), Counter(), Counter(), [], Counter()
    cost, naive, cache = 0.0, 0.0, Counter()
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
                model = e.get("routed_model") or e.get("response_model") or e.get("model")
                key = f"{e['agent']} {e['purpose']} [{e.get('provider') or 'openai-compatible'} {model}]"
                # Nightshift: latency_ms is the answered request; seconds also counts waiting for a lane just started
                llm[key].append(e["latency_ms"] / 1000 if e.get("latency_ms") else e["seconds"])
                tokens[key].append((e.get("usage") or {}).get("prompt_tokens") or 0)
                providers[e.get("provider") or "openai-compatible"] += 1
                routed[f"{e['agent']} -> {model}"] += 1
                if e.get("receipt_id"):
                    receipts["calls with a receipt id"] += 1
                    receipts["signed"] += bool(e.get("signed"))
                    cost += e.get("cost_usd") or 0
                    naive += e.get("naive_usd") or 0
                    cache["from_cache" if e.get("from_cache") else "not from cache"] += 1
            elif e["step"] == "llm_call":
                providers[f"{e.get('provider') or 'openai-compatible'} {e['status']}"] += 1
            elif e["step"] == "lane_swap":
                swaps.append(e["seconds"])
            elif e["step"] == "memory_recall":
                memory["recalls with a memory" if e.get("memory_ids") else f"recalls: {e.get('used_by') or e['status']}"] += 1
            elif e["step"] == "memory_feedback" and e["status"] == "ok":
                memory[f"feedback {e.get('kind')}"] += 1
            elif e["step"] == "write_gate" and (e.get("memory") or {}).get("memory_id"):
                memory["stored in H-MEM (long_term)"] += 1
            elif e["step"] == "receipts_export":
                receipts[f"exported files ({e['status']})"] += 1
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
            "check_verdicts": dict(verdicts), "check_reasons": dict(reasons),
            "providers": dict(providers), "routed": dict(routed), "receipts": dict(receipts),
            "cost_usd": round(cost, 6), "naive_usd_reference": round(naive, 6), "cache": dict(cache),
            "lane_swaps_s": _span(swaps), "memory": dict(memory)}


if __name__ == "__main__":
    print(json.dumps(summarize(sys.argv[1:]), indent=2, ensure_ascii=False))
