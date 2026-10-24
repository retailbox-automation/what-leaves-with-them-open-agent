"""Ask one question; the Investigator answers with cited records, the Verifier re-reads each one at its source.

    python -m open_agent.run --question "Who owns billing-api now?"
    python -m open_agent.run --input input_examples/02_tampered_source.json
    python -m open_agent.run --sample --question "..."        (or SAMPLE_MODE=true)

Live: the sources are read through the local Zetaris (ZETARIS_BUNDLE_ENV or ZETARIS_USERNAME/ZETARIS_PASSWORD),
the models through Meterless Nightshift (the Investigator on Nemotron 3.5 Lightning, the Verifier on Nemotron 3 Nano;
OA_LLM_PROVIDER in open_agent/llm.py), the graph is FalkorDB (FALKORDB_HOST/FALKORDB_PORT), the agents' memory is
the Meterless H-MEM engine (open_agent/hmem_bridge.py). Nightshift down: the run stops with exit code 4 and says
how to start it; no other provider is tried. The run's signed receipts go to logs/receipts-<run_id>.jsonl.
SAMPLE_MODE: the same SQL runs on sample_data/sources/ in an in-process SQLite and no model is used; the
agent logic, the Verifier's re-reads, the write gate and H-MEM are the same code. Both need FalkorDB.

The graph is built from the three sources on first use (or with --ingest). --tamper <chat id> changes that
chat record at its source after the graph was built, so the memory holds a stale copy; it is restored at the end.
Prints the result as JSON; the trace is logs/trace-<run_id>.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

from redis.exceptions import RedisError

from honest_memory import tools as T
from honest_memory.answer import _mentions
from honest_memory.extract import RuleExtractor
from honest_memory.ingest import ingest_episodes
from honest_memory.store import Store, connect

from .agents import REASON_TEXT, Investigator, Verifier
from .llm import LLMConfig, ProviderConfigError, ProviderDown, for_agent, preflight, resolve
from .nightshift import RoutingMismatch
from .sources import SYSTEM_OF, LocalSample, SourceUnavailable, ZetarisSource, read_all
from .trace import Trace, new_run_id

MAX_REVISIONS = 2
DEFAULT_TAMPER_TEXT = "[edited] This message was changed after it was first posted; the earlier text is withdrawn."
SAMPLE_NOTE = ("SAMPLE_MODE: the three sources are read from sample_data/sources/ by an in-process SQLite "
               "(endpoint local-sample) with the same SQL text, not through Zetaris; no model is called: the "
               "Investigator uses the deterministic router and the Verifier's model judgement is skipped. The agent "
               "loop, the quote re-reads, the write gate and the H-MEM memory (recall, store, feedback; no model needed) are "
               "the real code on this cached data.")


def sample_mode(flag: bool) -> bool:
    return flag or os.environ.get("SAMPLE_MODE", "").strip().lower() in ("1", "true", "yes")


def episode_count(store: Store) -> int:
    try:
        return store.ro("MATCH (e:Episode) RETURN count(e)")[0][0]
    except Exception as exc:  # a graph that was never written does not exist yet
        if "empty key" in str(exc).lower() or "invalid graph" in str(exc).lower():
            return 0
        raise


def ensure_graph(store: Store, source, trace: Trace, force: bool = False) -> dict | None:
    """Build the memory graph from the three sources (one SELECT each), unless it already holds them."""
    n = episode_count(store)
    if n and not force:
        trace.event("Ingest", "graph_ready", tenant=store.tenant, episodes=n,
                    note="graph already built from the sources; --ingest rebuilds it")
        return None
    episodes = read_all(source, trace)
    summary = ingest_episodes(store, episodes, reset=True, extractor=RuleExtractor())
    trace.event("Ingest", "graph_built", endpoint=source.endpoint, tenant=store.tenant,
                episodes=summary["episodes"], by_source=dict(Counter(e["channel"] for e in episodes)),
                gate_calls=summary["gate_calls"], outcomes=summary["outcomes"], seconds=summary["seconds"])
    return summary


def tamper(source, record_id: str, text: str, trace: Trace) -> str:
    original = source.chat_text(record_id)
    if original is None:
        raise SourceUnavailable(f"--tamper changes a chat record (Postgres chat); there is no chat record {record_id}")
    statement = source.set_chat_text(record_id, text)
    trace.event("Operator", "tamper_source", record_id=record_id, source=SYSTEM_OF["chat"], statement=statement,
                before=original, after=text, note="an edit at the source after the memory read the record")
    return original


def restore(source, record_id: str, original: str, trace: Trace) -> bool:
    statement = source.set_chat_text(record_id, original)
    ok = source.chat_text(record_id) == original
    trace.event("Operator", "restore_source", status="ok" if ok else "error", record_id=record_id,
                statement=statement, restored=ok)
    return ok


def write_finding(inv: Investigator, draft, review, trace: Trace) -> dict:
    """The verified answer goes through honest_memory's write gate as a Finding citing only verified records."""
    ids = [c.id for c in review.checks if c.accepted]
    if review.ignored:
        reason = f"records contradict the answer and it does not say so ({', '.join(review.ignored)}); nothing written"
        trace.event("Investigator", "write_gate", status="skipped", reason=reason)
        return {"written": False, "reason": reason}
    if ids and not review.accepted:  # the answer text may still carry the claims the rejected citations backed
        reason = "the Verifier did not accept every citation of this answer; nothing written"
        trace.event("Investigator", "write_gate", status="skipped", reason=reason)
        return {"written": False, "reason": reason}
    if not ids:
        reason = "the write gate keeps a finding only with a verified source; nothing written"
        trace.event("Investigator", "write_gate", status="skipped", reason=reason)
        return {"written": False, "reason": reason}
    text = draft.claim_text      # the answer's own claims; conflict lines stay in the result, not in the memory
    about = [n for n in sorted(inv.agent.names(), key=len, reverse=True) if _mentions(text, n)][:5]
    if not about:
        reason = "the answer names nothing the memory knows; nothing written"
        trace.event("Investigator", "write_gate", status="skipped", reason=reason)
        return {"written": False, "reason": reason}
    try:
        ans = inv.agent.tool("remember", {"text": text[:1000], "about": about, "source_ids": ids})
    except (T.ArgError, T.GateUnavailable) as exc:
        trace.event("Investigator", "write_gate", status="error", error=str(exc))
        return {"written": False, "reason": str(exc)}
    r = ans.receipts[0] if ans.receipts else None
    row = ans.rows[0] if ans.rows else {}
    out = {"written": ans.outcome == "accepted", "outcome": ans.outcome, "receipt": r.id if r else None,
           "reasons": r.reasons if r else [], "finding": row.get("record_id"), "source_ids": ids, "about": about,
           "memory": row.get("memory"),     # H-MEM: {memory_id, backend} for a kept finding (layer long_term)
           "conflicts_not_written": [c["id"] for c in draft.conflicts]}
    trace.event("Investigator", "write_gate", **out)
    return out


def status_of(draft, reviews) -> str:
    last = reviews[-1]
    if last.ignored:
        return "contradicted"
    if last.accepted:
        return "verified" if last.checks else "absence_confirmed" if last.absence else "no_claim"
    return "partially_verified" if any(c.accepted for c in last.checks) else "unverified"


def dropped(reviews) -> list[dict]:
    """Citations the Verifier rejected in some round that the final answer no longer carries, with why."""
    final = {c.id for c in reviews[-1].checks if c.accepted}
    out = {}
    for r in reviews:
        for c in r.checks:
            if not c.accepted and c.id not in final:
                out[c.id] = {"id": c.id, "round": r.round, "reason": c.reason, "verdict": c.verdict, "note": c.note,
                             "quote": c.quote, "text_at_source_now": c.current_text}
    return list(out.values())


def verifier_llm(llm: LLMConfig | None) -> LLMConfig | None:
    """The Verifier's settings: on Nightshift its own Nemotron model (lane nemotron-nano); on ollama the same model
    unless OA_VERIFIER_MODEL names another one at the same endpoint."""
    return for_agent(llm, "verifier")


# The Verifier's verdict on the whole answer, as H-MEM feedback on the memories put in front of the Investigator.
FEEDBACK_VERDICT = {"verified": "verified", "partially_verified": "unsupported", "unverified": "unsupported",
                    "contradicted": "contradicted"}


def memory_feedback(store: Store, memory: dict, status: str, trace: Trace) -> dict:
    """verified -> helpful, partially_verified / unverified -> not_helpful, contradicted -> wrong (tagged review).
    absence_confirmed and no_claim give no verdict on an answer, so no feedback."""
    from .hmem_bridge import verdict_feedback, verdict_to_feedback

    ids = memory.get("ids") or []
    if not ids:
        return {"given": False, "reason": "no memory was recalled for this question"}
    verdict = FEEDBACK_VERDICT.get(status)
    if verdict is None:
        out = {"given": False, "reason": f"status {status} is not a verdict on an answer", "memory_ids": ids}
        trace.event("Verifier", "memory_feedback", status="skipped", **out)
        return out
    before = {m["memory_id"]: m.get("confidence") for m in memory.get("memories") or []}
    try:
        after = verdict_feedback(store, ids, verdict)
    except Exception as exc:  # the answer stands; the trace says the feedback did not land
        out = {"given": False, "reason": f"H-MEM feedback failed: {type(exc).__name__}: {str(exc)[:200]}",
               "memory_ids": ids}
        trace.event("Verifier", "memory_feedback", status="error", **out)
        return out
    out = {"given": True, "verdict": verdict, "kind": verdict_to_feedback(verdict), "memory_ids": ids,
           "memories": [{"memory_id": m["id"], "confidence_before": before.get(m["id"]),
                         "confidence_after": round(m.get("confidence", 0), 4),
                         "needs_review": "review" in (m.get("tags") or [])} for m in after]}
    trace.event("Verifier", "memory_feedback", target_agent="H-MEM", **out)
    return out


def investigate(question: str, store: Store, source, trace: Trace, llm: LLMConfig | None, sample: bool,
                max_revisions: int = MAX_REVISIONS, investigator_client=None, verifier_client=None) -> dict:
    """The loop: Investigator drafts, Verifier checks; a rejection goes back with notes, at most max_revisions
    times; then the write gate. Returns the result without timing or run metadata."""
    inv = Investigator(store, trace, for_agent(llm, "investigator"), client=investigator_client)
    ver = Verifier(source, trace, verifier_llm(llm), client=verifier_client, sample=sample)
    draft = inv.investigate(question)
    reviews = []
    while True:
        review = ver.review(draft)
        reviews.append(review)
        if review.accepted or not review.needs_revision or len(reviews) > max_revisions:
            break
        trace.event("Verifier", "feedback", target_agent="Investigator", round=review.round, notes=review.notes)
        draft = inv.revise(draft, review)
    last = reviews[-1]
    gate = write_finding(inv, draft, last, trace)
    status = status_of(draft, reviews)
    feedback = memory_feedback(store, inv.memory, status, trace)
    checks = [c for r in reviews for c in r.checks]
    gone = dropped(reviews)
    return {
        "status": status, "answer": draft.answer, "answer_author": draft.author,
        "citations": [{"id": c.id, "source": SYSTEM_OF.get(c.channel, c.channel), "quote": c.quote,
                       "quote_found": c.quote_found, "verdict": c.verdict, "note": c.note, "accepted": c.accepted,
                       "reason": c.reason} for c in last.checks],
        "dropped_citations": gone,
        "conflicts": [{k: v for k, v in c.items() if k != "line"} | {"line": c["line"]} for c in draft.conflicts],
        "notices": [f"{d['id']} is not cited: {REASON_TEXT.get(d['reason'], d['reason'])}."
                    + (f" At its source it now reads: \"{d['text_at_source_now'][:240]}\"" if d["text_at_source_now"]
                       and d["reason"] == "quote_not_in_record" else "") for d in gone],
        "absence_checks": last.absence,
        "contradictions_unaddressed": last.ignored,
        "rounds": len(reviews), "revisions": len(reviews) - 1,
        "rounds_detail": [{"round": r.round, "accepted": r.accepted, "notes": r.notes, "ignored": r.ignored,
                           "checks": [{"id": c.id, "accepted": c.accepted, "reason": c.reason, "verdict": c.verdict}
                                      for c in r.checks]} for r in reviews],
        "verdict_counts": dict(Counter(c.verdict.split(":")[0] for c in checks)),
        "quote_checks": {"total": len(checks), "found": sum(c.quote_found for c in checks)},
        "write_gate": gate,
        "memory": {"engine": "Meterless H-MEM (vendor/hmem)" if inv.memory["status"] != "skipped" else None,
                   "recall": {k: inv.memory.get(k) for k in ("status", "reason", "ids", "memories") if k in inv.memory},
                   "stored": gate.get("memory"), "feedback": feedback},
        "investigator_path": draft.run.mode,     # "llm" (the model picked the tools) or "router" (no model / fell back)
        "agents": [{"name": "Investigator", "id": Investigator.NAME,
                    "role": "answers from the memory graph with cited records; revises on the Verifier's notes"},
                   {"name": "Verifier", "id": Verifier.NAME,
                    "role": "re-reads each cited record by id at its source; quote check, then a model judgement"}],
    }


def describe_llm(llm: LLMConfig | None) -> dict | None:
    """Which provider and which model each agent uses, for the result and the trace."""
    if llm is None:
        return None
    if llm.provider == "nightshift":
        from .nightshift import route_for

        return {"provider": "nightshift", "endpoint": llm.base_url, "scope": llm.scope,
                **{a: {"app": r.app, "lane": r.lane, "model": r.model}
                   for a, r in ((a, route_for(a)) for a in ("investigator", "verifier"))}}
    return {"provider": llm.provider, "endpoint": llm.base_url, "investigator_model": llm.model,
            "verifier_model": verifier_llm(llm).model, "note": "development override (OA_LLM_PROVIDER=ollama)"}


def _shown(path: Path) -> str:
    return str(path.relative_to(Path.cwd())) if path.is_relative_to(Path.cwd()) else str(path)


def export_run_receipts(trace: Trace) -> dict:
    """The run's signed Nightshift receipts (by the receipt ids in its llm_call events) to receipts-<run_id>.jsonl
    next to the trace, after the secrets and personal-data scan (open_agent.nightshift.export_receipts)."""
    from .nightshift import export_receipts

    ids = [e["receipt_id"] for e in trace.events if e["step"] == "llm_call" and e.get("receipt_id")]
    if not ids:
        return {"file": None, "calls": 0, "note": "no model call returned a receipt"}
    path = trace.path.parent / f"receipts-{trace.run_id}.jsonl"
    try:
        got = export_receipts(ids, path)
    except Exception as exc:  # the run is done; its trace still holds every receipt id, the result says why
        out = {"file": None, "calls": len(ids), "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        trace.event("Run", "receipts_export", status="error", **out)
        return out
    out = {"file": _shown(path), "calls": len(ids), "signed_receipts": got["receipts"], "redacted": got["redacted"],
           "missing": got["missing"], "scan": "secrets and personal data (open_agent.nightshift.scan_text)"}
    trace.event("Run", "receipts_export", status="ok" if not got["missing"] else "warning", **out)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m open_agent.run", description=__doc__.splitlines()[0])
    ap.add_argument("--question")
    ap.add_argument("--input", help="JSON file with question, and optionally tamper and tamper_text")
    ap.add_argument("--sample", action="store_true", help="SAMPLE_MODE: local copy of the sources, no model")
    ap.add_argument("--tamper", help="chat record id to change at its source before asking (restored afterwards)")
    ap.add_argument("--tamper-text", help="the new text of the tampered record")
    ap.add_argument("--ingest", action="store_true", help="rebuild the graph from the sources first")
    ap.add_argument("--tenant", help="graph tenant (default openagent, or openagent_sample in SAMPLE_MODE)")
    ap.add_argument("--max-revisions", type=int, default=MAX_REVISIONS)
    ap.add_argument("--out", help="also write the result JSON here")
    args = ap.parse_args(argv)
    spec = json.loads(Path(args.input).read_text(encoding="utf-8")) if args.input else {}
    question = args.question or spec.get("question")
    tamper_id = args.tamper or spec.get("tamper")
    tamper_text = args.tamper_text or spec.get("tamper_text") or DEFAULT_TAMPER_TEXT
    if not question:
        ap.error("--question or --input is required")
    if not 0 <= args.max_revisions <= 5:
        ap.error("--max-revisions must be 0 to 5")

    sample = sample_mode(args.sample)
    run_id = new_run_id()
    trace = Trace(run_id, mode="sample" if sample else "live")
    t0 = time.perf_counter()
    try:
        llm = None if sample else resolve(run_id)
    except ProviderConfigError as exc:
        trace.event("Run", "error", status="error", error=str(exc))
        print(json.dumps({"run_id": run_id, "status": "error", "error": {"type": "ProviderConfigError",
                                                                          "message": str(exc)}}, indent=2))
        return 4
    source = LocalSample() if sample else ZetarisSource(timeout=float(os.environ.get("OA_ZETARIS_TIMEOUT", "30")))
    tenant = args.tenant or os.environ.get("OA_TENANT") or ("openagent_sample" if sample else "openagent")
    llm_info = describe_llm(llm)
    trace.event("Run", "start", question=question, endpoint=source.endpoint, tenant=tenant, llm=llm_info,
                note=SAMPLE_NOTE if sample else ("OA_LLM_PROVIDER=none: no model, deterministic paths"
                                                 if not llm else f"live, models through {llm.provider}"))
    result = {"run_id": run_id, "mode": "sample" if sample else "live", "endpoint": source.endpoint,
              "question": question, "tenant": tenant, "llm": llm_info}
    if sample:
        result["sample_mode_note"] = SAMPLE_NOTE
    original = None
    try:
        if llm and llm.provider == "nightshift":
            trace.event("Run", "provider_ready", provider="nightshift", endpoint=llm.base_url, **preflight(llm))
        store = Store(connect(), tenant)
        ensure_graph(store, source, trace, force=args.ingest)
        if tamper_id:
            original = tamper(source, tamper_id, tamper_text, trace)
            result["tamper"] = {"record_id": tamper_id, "new_text": tamper_text, "original_text": original}
        result.update(investigate(question, store, source, trace, llm, sample, args.max_revisions))
        code = 0
    except ProviderDown as exc:
        trace.event("Run", "error", status="error", error=str(exc))
        result.update(status="error", error={"type": "ProviderDown", "message": str(exc)[:600],
                                             "hint": "scripts/nightshift_headless.sh start (or --sample)"})
        code = 4
    except RoutingMismatch as exc:
        trace.event("Run", "error", status="error", error=str(exc))
        result.update(status="error", error={"type": "RoutingMismatch", "message": str(exc)[:600],
                                             "hint": "Nightshift answered with another model or without the "
                                                     "agent's lane pin; check the app policy (nightshift_headless.sh)"})
        code = 4
    except SourceUnavailable as exc:
        trace.event("Run", "error", status="error", error=str(exc))
        result.update(status="error", error={"type": "SourceUnavailable", "message": str(exc),
                                             "hint": "run with --sample (SAMPLE_MODE=true) to use the local copy"})
        code = 2
    except (RedisError, OSError) as exc:  # FalkorDB not reachable or timed out
        trace.event("Run", "error", status="error", error=f"{type(exc).__name__}: {exc}")
        result.update(status="error", error={"type": "GraphUnavailable", "message": str(exc)[:300],
                                             "hint": "start FalkorDB and set FALKORDB_HOST/FALKORDB_PORT"})
        code = 2
    finally:
        if tamper_id and original is not None:
            try:
                result["tamper"]["restored"] = restore(source, tamper_id, original, trace)
            except SourceUnavailable as exc:
                trace.event("Operator", "restore_source", status="error", record_id=tamper_id, error=str(exc))
                result["tamper"]["restored"] = False
                code = 3
    result["execution_time_seconds"] = round(time.perf_counter() - t0, 2)
    result["trace_file"] = _shown(trace.path)
    if llm and llm.provider == "nightshift":
        result["receipts"] = export_run_receipts(trace)
    trace.event("Run", "end", status="ok" if code == 0 else "error", status_of_run=result.get("status"),
                seconds=result["execution_time_seconds"])
    text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
