"""Run the benchmark: plain RAG, honest-memory's router and honest-memory's LLM agent over every question.

    python -m bench.run                       # all three systems on FalkorDB at localhost:6393, seed 42
    python -m bench.run --port 6390 --systems honest-router,honest-agent --out bench/results/falkordb-6.0.2
    python -m bench.run --rescore             # score the saved answers again (after a scoring change), no models

The corpus is built fresh from the repo's generator, so whatever questions and gold facts it holds are what runs.
Results: <out>/<system>-seed<N>.jsonl (one JSON per question), <out>/summary.json, and the markdown table.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

from . import report
from .common import (BASE_URL, BENCH, EMBED_MODEL, RESULTS, Corpus, Ollama, git_head, load_corpus, now_utc,
                     ollama_models)
from .plain_rag import K, PlainRag, render_record
from .score import Truth, score

SYSTEMS = ("plain-rag", "honest-router", "honest-agent")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def evidence_of(row: dict, by_id: dict[str, dict]) -> str:
    """The text a system answered from: stored by the honest paths, rebuilt from the retrieved ids for plain RAG."""
    if "evidence" in row:
        return row["evidence"]
    return "\n\n".join(render_record(by_id[r["id"]]) for r in row.get("retrieved", []) if r["id"] in by_id)


def scored_text(row: dict) -> str:
    """What the user reads. For honest-memory that is the sentence plus the source ids the tools returned with it (an
    answer carries its sources); for plain retrieval it is the model's text, with whatever ids the model chose to cite."""
    cited = row.get("cited")
    return row["answer"] + (" Sources: " + ", ".join(cited) + "." if cited else "")


def finish(q: dict, system: str, out: dict, truth: Truth, by_id: dict) -> dict:
    row = {"id": q["id"], "type": q["type"], "scenario": q.get("scenario"), "system": system, "question": q["question"],
           "as_of": q.get("as_of"), "gold": q["answer"], **out}
    evidence = evidence_of(row, by_id)
    row["verdict"] = score(q, scored_text(row), evidence, truth)
    row["verdict"]["correct_sentence_only"] = score(q, row["answer"], evidence, truth)["correct"]
    if "retrieved" in row and q.get("source_ids"):
        got, gold = {r["id"] for r in row["retrieved"]}, set(q["source_ids"])
        row["gold_sources"] = sorted(gold)
        row["retrieval"] = {"any": bool(got & gold), "all": gold <= got, "found": len(got & gold), "of": len(gold)}
    return row


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def run_system(name: str, ask, questions: list[dict], path: Path, truth: Truth, by_id: dict, resume: bool) -> list[dict]:
    done = {r["id"]: r for r in read_rows(path)} if resume else {}
    rows = []
    with path.open("w", encoding="utf-8") as fh:
        for q in questions:
            if q["id"] in done:
                row = done[q["id"]]
            else:
                try:
                    out = ask(q["question"])
                except Exception as exc:  # a failed question is a result, not a crash: it counts as no answer
                    out = {"answer": "", "errors": [f"{type(exc).__name__}: {str(exc)[:300]}"], "ms": 0.0}
                row = finish(q, name, out, truth, by_id)
            rows.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            v = row["verdict"]
            log(f"[{name}] {q['id']} {q['type']:<18} {'ok ' if v['correct'] else 'BAD'}"
                f"{' stale' if v['stale'] else ''}{' unsupported' if v['unsupported'] else ''} {row['ms'] / 1000:.1f}s")
    return rows


def preflight(chat_model: str, embed_model: str, host: str, port: int) -> None:
    root = BASE_URL.rsplit("/v1", 1)[0]
    try:
        with urllib.request.urlopen(f"{root}/api/tags", timeout=10) as r:
            have = {m["name"] for m in json.load(r)["models"]}
    except OSError as exc:
        raise SystemExit(f"Ollama is not reachable at {root}: {exc}")
    for model in (chat_model, embed_model):
        if model not in have and f"{model}:latest" not in have:
            raise SystemExit(f"Ollama has no model {model!r}; it has {sorted(have)}")
    from falkordb import FalkorDB

    try:
        FalkorDB(host=host, port=port, socket_timeout=10, socket_connect_timeout=5).connection.ping()
    except Exception as exc:
        raise SystemExit(f"FalkorDB is not reachable at {host}:{port}: {exc}")


def ollama_version() -> str:
    try:
        with urllib.request.urlopen(BASE_URL.rsplit("/v1", 1)[0] + "/api/version", timeout=10) as r:
            return json.load(r)["version"]
    except (OSError, ValueError, KeyError):
        return "unknown"


def main(argv=None) -> int:
    from honest_memory.agent import DEFAULT_MODEL

    ap = argparse.ArgumentParser(prog="python -m bench.run", description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--corpus", help="a corpus directory (episodes.jsonl, gold_facts.jsonl, questions.jsonl); default: build it")
    ap.add_argument("--systems", default=",".join(SYSTEMS), help="comma-separated, from: " + ", ".join(SYSTEMS))
    ap.add_argument("--host", default="localhost", help="FalkorDB host")
    ap.add_argument("--port", type=int, default=6393, help="FalkorDB port (6393: 4.22.0, 6390: 6.0.2 on this machine)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="chat model for plain RAG and for the agent")
    ap.add_argument("--embed-model", default=EMBED_MODEL)
    ap.add_argument("--reasoning", default="none", help="reasoning_effort for every model call: none (thinking off), "
                    "low, medium, high, or default (leave the model's own setting)")
    ap.add_argument("--k", type=int, default=K, help="episodes retrieved per question for plain RAG")
    ap.add_argument("--chunk", default="episode", choices=("episode", "lines"),
                    help="plain RAG units: whole episodes (default) or one per line of an episode")
    ap.add_argument("--limit", type=int, help="only the first N questions (smoke test)")
    ap.add_argument("--only", help="only these question ids, comma-separated (smoke test)")
    ap.add_argument("--out", default=str(RESULTS), help="results directory")
    ap.add_argument("--report", help="markdown table path (default: bench/RESULTS.md for bench/results, else <out>/RESULTS.md)")
    ap.add_argument("--resume", action="store_true", help="keep answers already saved in --out and only run the rest")
    ap.add_argument("--rescore", action="store_true", help="score the saved answers again; run no model")
    ap.add_argument("--keep-tenant", action="store_true", help="do not drop the tenant graph at the end")
    args = ap.parse_args(argv)

    systems = [s for s in args.systems.split(",") if s]
    unknown = [s for s in systems if s not in SYSTEMS]
    if unknown:
        raise SystemExit(f"unknown system(s) {unknown}; choose from {list(SYSTEMS)}")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = Path(args.report) if args.report else (BENCH / "RESULTS.md" if out_dir == RESULTS else out_dir / "RESULTS.md")

    corpus: Corpus = load_corpus(args.seed, args.corpus)
    questions = corpus.questions[:args.limit] if args.limit else corpus.questions
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    truth = Truth(corpus.episodes, corpus.gold, corpus.now)
    by_id = {e["id"]: e for e in corpus.episodes}
    log(f"corpus {corpus.generator_version} seed {corpus.seed}: {len(corpus.episodes)} episodes, {len(questions)} questions")
    paths = {s: out_dir / f"{s}-seed{args.seed}.jsonl" for s in systems}

    if args.rescore:
        results = {}
        for s in systems:
            old = read_rows(paths[s])
            if not old:
                continue
            qs = {q["id"]: q for q in corpus.questions}
            results[s] = [finish(qs[r["id"]], s, {k: v for k, v in r.items() if k not in
                                                  ("id", "type", "scenario", "system", "question", "as_of", "gold", "verdict")},
                                 truth, by_id) for r in old if r["id"] in qs]
            paths[s].write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results[s]), encoding="utf-8")
        meta_path = out_dir / "summary.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))["meta"] if meta_path.exists() else None
        if meta is None:
            raise SystemExit(f"nothing to rescore: {meta_path} is missing")
        report.write(out_dir, report_path, meta, results)
        log(f"rescored {sum(len(r) for r in results.values())} answers; wrote {report_path}")
        return 0

    preflight(args.model, args.embed_model, args.host, args.port)
    started, t0 = now_utc(), time.perf_counter()
    ollama = Ollama()
    log("warming up the models")
    reasoning = None if args.reasoning == "default" else args.reasoning
    ollama.chat(args.model, [{"role": "user", "content": "Reply with the word ok."}], reasoning)
    ollama.embed(args.embed_model, ["warm up"])

    results: dict[str, list[dict]] = {}
    ingest: dict = {}
    falkordb = "not used"
    if "plain-rag" in systems:
        rag = PlainRag(corpus, ollama, args.model, args.embed_model, args.k, reasoning, args.chunk)
        results["plain-rag"] = run_system("plain-rag", rag.ask, questions, paths["plain-rag"], truth, by_id, args.resume)
    if any(s.startswith("honest") for s in systems):
        from .honest import Honest

        with Honest(corpus, args.host, args.port, args.model, keep=args.keep_tenant, reasoning=reasoning) as honest:
            ingest, falkordb = honest.ingest_summary, honest.version
            log(f"tenant {honest.tenant} on FalkorDB {falkordb}: {ingest['gate_calls']} gate decisions in {ingest['seconds']} s")
            if "honest-router" in systems:
                results["honest-router"] = run_system("honest-router", honest.ask_router, questions, paths["honest-router"],
                                                      truth, by_id, args.resume)
            if "honest-agent" in systems:
                results["honest-agent"] = run_system("honest-agent", honest.ask_agent, questions, paths["honest-agent"],
                                                     truth, by_id, args.resume)

    models = ollama_models([args.model, args.embed_model])
    meta = {"started": started, "finished": now_utc(), "wall_seconds": round(time.perf_counter() - t0, 1),
            "seed": corpus.seed, "generator_version": corpus.generator_version, "corpus_sha256": corpus.sha256,
            "questions": len(questions), "k": args.k, "chunk": args.chunk, "reasoning": args.reasoning, "commit": git_head(), "ollama_version": ollama_version(),
            "models": {"chat": {"name": args.model, **models[args.model]},
                       "embedding": {"name": args.embed_model, **models[args.embed_model]}},
            "falkordb_version": falkordb, "falkordb_port": args.port, "systems": systems,
            "ingest": ingest or {"episodes": len(corpus.episodes), "gate_calls": 0, "seconds": 0, "outcomes": {}}}
    summary = report.write(out_dir, report_path, meta, results)
    for name, s in summary["systems"].items():
        o = s["overall"]
        log(f"{name:<14} correct {o['correct']:>5.1f}%  stale {o['stale']:>5.1f}%  unsupported {o['unsupported']:>5.1f}%  "
            f"clean {o['clean']:>5.1f}%  median {o['median_ms']} ms")
    log(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
