"""Summary JSON and the markdown results table."""

from __future__ import annotations

import json
from pathlib import Path

from .score import summarize

LABELS = {"plain-rag": "plain RAG", "honest-router": "honest-memory, router (no model)",
          "honest-agent": "honest-memory, LLM agent"}


def _cells(b: dict) -> str:
    return (f"{b['n']} | {b['correct']:.1f} | {b['stale']:.1f} | {b['unsupported']:.1f} | {b['clean']:.1f} | "
            f"{b['abstained']:.1f} | {b['median_ms']:,}")


def _overall(b: dict) -> str:
    return f"{_cells(b)} | {b['correct_literal']:.1f} | {b['correct_sentence_only']:.1f} | {b['leaked']}"


HEAD = "| {} | n | correct % | stale % | unsupported % | clean % | no answer % | median ms |\n|---|---|---|---|---|---|---|---|"


def build_summary(meta: dict, results: dict[str, list[dict]]) -> dict:
    return {"meta": meta, "systems": {name: summarize(rows) for name, rows in results.items() if rows}}


def _excerpt(text: str, n: int = 170) -> str:
    text = " ".join(str(text).split()).replace("|", "\\|")
    return text if len(text) <= n else text[:n - 1] + "…"


def _notes(results: dict[str, list[dict]]) -> list[str]:
    out = []
    plain = [r for r in results.get("plain-rag", []) if "retrieval" in r]
    if plain:
        n = len(plain)
        out.append(f"- Plain RAG retrieval: a gold source record was among the top-k for {sum(r['retrieval']['any'] for r in plain)} "
                   f"of {n} questions that have sources, and all of them for {sum(r['retrieval']['all'] for r in plain)}.")
    agent = results.get("honest-agent", [])
    if agent:
        model = sum(r.get("sentence_source") == "model" for r in agent)
        no_tool = sum("model called no tool" in r.get("rule", "") for r in agent)
        err = sum(bool(r.get("errors")) for r in agent)
        out.append(f"- Agent: {model} of {len(agent)} answers are the model's own sentence (it passed the check against the "
                   f"rows); the rest are the template sentence built from the rows. {no_tool} questions fell back to the router "
                   f"because the model called no tool; {err} runs recorded an error.")
    return out


def render(summary: dict, results: dict[str, list[dict]]) -> str:
    m = summary["meta"]
    chat, embed = m["models"]["chat"], m["models"]["embedding"]
    lines = [
        "# Benchmark results", "",
        f"Plain retrieval against honest-memory on the synthetic Quillfield Billing corpus: {m['questions']} questions, "
        f"seed {m['seed']}, corpus generator {m['generator_version']} (files sha256 `{m['corpus_sha256'][:12]}`).", "",
        f"- Run: {m['started']} to {m['finished']} UTC ({m['wall_seconds'] / 60:.1f} min), repo commit `{m['commit']}`.",
        f"- Chat model, for plain RAG and for the agent: `{chat['name']}` ({chat.get('parameters', '?')}, "
        f"{chat.get('quantization', '?')}, digest `{chat.get('digest', '?')}`), temperature 0, reasoning_effort `{m['reasoning']}`, via Ollama {m['ollama_version']}.",
        f"- Embeddings for plain RAG: `{embed['name']}` (digest `{embed.get('digest', '?')}`), top-k {m['k']}, units: {'whole episodes' if m.get('chunk', 'episode') == 'episode' else 'single lines'}.",
        f"- FalkorDB {m['falkordb_version']} on port {m['falkordb_port']}; ingest: {m['ingest']['episodes']} episodes, "
        f"{m['ingest']['gate_calls']} gate decisions in {m['ingest']['seconds']} s "
        f"({', '.join(f'{k} {v}' for k, v in m['ingest']['outcomes'].items())}).", "",
        "How each column is decided is in `bench/score.py` (names and dates, no model as judge) and `bench/README.md`. "
        "**clean** = correct and not stale, unsupported or leaking personal data. **no answer** = the answer says the "
        "records or memory do not hold it (a miss, not a wrong answer).", "",
        "## All questions", "", HEAD.format("system") .replace("median ms |", "median ms | literal-rule correct % | sentence-only correct % | personal data leaks |")
        + "---|---|---|",
    ]
    for name in results:
        if name in summary["systems"]:
            lines.append(f"| {LABELS.get(name, name)} | {_overall(summary['systems'][name]['overall'])} |")
    lines += [""] + _notes(results)
    types = sorted({t for s in summary["systems"].values() for t in s["by_type"]})
    lines += ["", "## By question type", ""]
    for name in results:
        if name not in summary["systems"]:
            continue
        lines += [f"### {LABELS.get(name, name)}", "", HEAD.format("type")]
        for t in types:
            if t in summary["systems"][name]["by_type"]:
                lines.append(f"| {t} | {_cells(summary['systems'][name]['by_type'][t])} |")
        lines.append("")
    lines += ["## Questions each system got wrong", "",
              "Every row below is `correct = false`; the full verdicts (matched names, stale and unsupported values, the "
              "retrieved records or tool trace) are in `bench/results/<system>-seed%d.jsonl`." % m["seed"], ""]
    for name, rows in results.items():
        wrong = [r for r in rows if not r["verdict"]["correct"]]
        lines += [f"### {LABELS.get(name, name)}: {len(wrong)} of {len(rows)}", ""]
        if wrong:
            lines += ["| id | type | question | gold | answer | why |", "|---|---|---|---|---|---|"]
            for r in wrong:
                v = r["verdict"]
                why = []
                if v["missing"]:
                    why.append("missing " + ", ".join(v["missing"]))
                if v["expects_refusal"] and not v["refused"]:
                    why.append("no refusal")
                if v["stale"]:
                    why.append("stale " + ", ".join(s["value"] for s in v["stale_values"]))
                if v["leaked"]:
                    why.append("leaked personal data")
                lines.append(f"| {r['id']} | {r['type']} | {_excerpt(r['question'], 70)} | {_excerpt(r['gold'], 90)} | "
                             f"{_excerpt(r['answer'])} | {'; '.join(why) or 'see jsonl'} |")
            lines.append("")
    lines += ["## Stale and unsupported answers", ""]
    for name, rows in results.items():
        flagged = [r for r in rows if r["verdict"]["stale"] or r["verdict"]["unsupported"]]
        lines += [f"### {LABELS.get(name, name)}: {len(flagged)} flagged", ""]
        if flagged:
            lines += ["| id | flag | value | evidence |", "|---|---|---|---|"]
            for r in flagged:
                for s in r["verdict"]["stale_values"]:
                    lines.append(f"| {r['id']} | stale ({s['kind']}) | {s['value']} | {_excerpt(s['sentence'])} |")
                for u in r["verdict"]["unsupported_values"]:
                    lines.append(f"| {r['id']} | unsupported | {_excerpt(u['value'], 40)} | {u['why']} |")
            lines.append("")
    return "\n".join(lines) + "\n"


def write(out_dir: Path, report: Path, meta: dict, results: dict[str, list[dict]]) -> dict:
    summary = build_summary(meta, results)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report.write_text(render(summary, results), encoding="utf-8")
    return summary


# --- sensitivity table: every result set under bench/results, one row per system ----------------------------------

VARIANTS = (("", "plain-rag", "plain RAG, whole episodes, k=8 (the baseline in RESULTS.md)"),
            ("plain-rag-k16", "plain-rag", "plain RAG, whole episodes, k=16"),
            ("plain-rag-lines-k8", "plain-rag", "plain RAG, one line per unit, k=8"),
            ("plain-rag-lines-k16", "plain-rag", "plain RAG, one line per unit, k=16"),
            ("", "honest-router", "honest-memory router, FalkorDB 4.22"),
            ("falkordb-6.0.2", "honest-router", "honest-memory router, FalkorDB 6.0.2"),
            ("", "honest-agent", "honest-memory LLM agent, thinking off, FalkorDB 4.22 (the agent in RESULTS.md)"),
            ("falkordb-6.0.2", "honest-agent", "honest-memory LLM agent, thinking off, FalkorDB 6.0.2"),
            ("agent-thinking-default", "honest-agent", "honest-memory LLM agent, the model's own thinking, FalkorDB 4.22"))


def sensitivity(results_dir: Path) -> str:
    first = next((json.loads((results_dir / f / "summary.json").read_text(encoding="utf-8"))["meta"]
                  for f, _, _ in VARIANTS if (results_dir / f / "summary.json").exists()), {})
    lines = ["# Sensitivity runs", "",
             f"The same {first.get('questions', '')} questions (corpus generator {first.get('generator_version', '')}) with the baseline made stronger, the other FalkorDB engine and the model's own thinking "
             "left on. Each row is a full run; the files are under `bench/results/<variant>/`. The rows labelled "
             "\"the baseline in RESULTS.md\" and \"the agent in RESULTS.md\" are the ones that file reports.", "",
             "| run | correct % | stale % | unsupported % | no answer % | clean % | median ms | commit |",
             "|---|---|---|---|---|---|---|---|"]
    for folder, system, label in VARIANTS:
        path = results_dir / folder / "summary.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if system not in data["systems"]:
            continue
        b = data["systems"][system]["overall"]
        lines.append(f"| {label} | {b['correct']:.1f} | {b['stale']:.1f} | {b['unsupported']:.1f} | {b['abstained']:.1f} | "
                     f"{b['clean']:.1f} | {b['median_ms']:,} | `{data['meta']['commit'].split()[0]}` |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    """python -m bench.report: write bench/SENSITIVITY.md from the result sets that exist."""
    from .common import BENCH, RESULTS

    (BENCH / "SENSITIVITY.md").write_text(sensitivity(RESULTS), encoding="utf-8")
    print(f"wrote {BENCH / 'SENSITIVITY.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
