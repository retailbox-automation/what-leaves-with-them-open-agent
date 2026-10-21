"""Track 02 demo: two agents share one memory across sessions, and the memory stays honest when facts change.

Session 1, agent:support: answers a customer question about billing-api, writes what it concluded as a
Finding (through the write gate) and hands the case to agent:ops, citing the ticket.
Session 2, agent:ops (a new session id): first recalls what was left for it and gets answered_from_memory with
the date, then answers the question with the path.
Then an ownership-change episode arrives through the extractor and the write gate. Both agents ask again and
their answers change; the old finding is flagged as older than the change.

Runs with the deterministic router, so it needs nothing but FalkorDB. With a local model (HM_LLM_MODEL set, or
--llm for the default one) the model picks the tools instead. Every step prints the trace: tool, arguments,
milliseconds, rows.

    FALKORDB_PORT=6379 python examples/two_agents.py --tenant two-agents --ingest [--llm] [--drop]

--ingest replays the seed-42 corpus into the tenant first (dropping that tenant's graph); without it the
tenant must already hold an ingested corpus.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from honest_memory import answer as A  # noqa: E402
from honest_memory import tools as T  # noqa: E402
from honest_memory.agent import DEFAULT_MODEL, Agent, AgentConfig, AgentRun  # noqa: E402
from honest_memory.corpus.generator import build, write_corpus  # noqa: E402
from honest_memory.ingest import ingest_corpus, ingest_episode  # noqa: E402
from honest_memory.schema import graph_name  # noqa: E402
from honest_memory.store import Store, connect  # noqa: E402

SYSTEM = "billing-api"
QUESTION = f"Who owns {SYSTEM}?"


def show_trace(answers: list[A.Answer], out) -> None:
    for ans in answers:
        for t in ans.trace:
            out(f"    trace  {t.tool} {t.args}  {t.ms} ms  {t.rows} rows  ({len(t.queries)} queries)")


def show_answer(label: str, run: AgentRun, out) -> None:
    out(f"  {label}: {run.question}")
    out(f"    mode={run.mode}  outcome={run.outcome}  sentence from the {run.sentence_source}")
    if run.rejected:
        out(f"    the model's sentence was rejected ({run.check}): {run.rejected}")
    for p in (run.primary.paths[:3] if run.primary else []):
        out(f"    path   {A.render_path(p)}")
    out(f"    says   {run.sentence}")
    show_trace(run.answers, out)


def show_tool(label: str, ans: A.Answer, out) -> None:
    out(f"  {label}: {ans.tool}({', '.join(f'{k}={v!r}' for k, v in ans.args.items())})")
    out(f"    outcome={ans.outcome}  says {ans.sentence}")
    for r in ans.receipts[:2]:
        out(f"    receipt {r.id}  {r.outcome}  {', '.join(r.reasons)}  cites {', '.join(r.cites) or '-'}")
    show_trace([ans], out)


def latest_ticket(store: Store, system: str) -> tuple[str, str]:
    rows = store.ro("MATCH (e:Episode {channel: 'ticket'})-[:MENTIONS]->(:System {name: $s}) "
                    "RETURN e.id, e.title ORDER BY e.ts DESC, e.id LIMIT 1", {"s": system})
    if not rows:
        raise SystemExit(f"no ticket mentions {system}; ingest the corpus first (--ingest)")
    return rows[0][0], rows[0][1]


def ownership_change(store: Store, agent: Agent, system: str, ts: str, out) -> dict:
    """An ADR dated `ts` that moves `system` to another member of the owner's team, phrased as the corpus ADRs are."""
    owner = agent.tool("current_owner", {"system": system}).rows[0]
    team_rows = agent.tool("facts_about", {"entity": owner["team"], "predicate": "MEMBER_OF"}).rows
    newcomer = next(r["other"] for r in team_rows if r["other"] != owner["owner"])
    lead = agent.tool("facts_about", {"entity": owner["team"], "predicate": "LEADS"}).rows[0]["other"]
    n = max(int(i[0].split("-")[1]) for i in store.ro("MATCH (d:Decision) RETURN d.id")) + 1
    day = ts[:10]
    adr = f"ADR-{n:04d}"
    title = f"{adr}: Transfer {system} ownership from {owner['owner']} to {newcomer}"
    text = (f"{title}\nAuthor: {lead}\nDraft opened: {day}. Status: accepted on {day}.\n"
            f"Context: {owner['owner']} is moving to other work.\n"
            f"Decision: Ownership of {system} transfers from {owner['owner']} ({owner['team']}) to {newcomer} "
            f"({owner['team']}) effective {day}. {owner['owner']} stays available as backup for 30 days.\n"
            f"Consequences: Escalations for {system} go to {newcomer}. The service catalog entry changes accordingly.")
    episode = {"id": adr, "ts": ts, "channel": "doc", "author": lead, "title": title, "text": text, "refs": []}
    out(f"  episode {adr} ({ts}) arrives: {title}")
    results = ingest_episode(store, episode, T.get_extractor())
    for r in results:
        out(f"    gate  {r.outcome:<9} {', '.join(r.reasons):<22} receipt {r.receipt_id}"
            + (f"  closed {[(c['subject'], c['predicate'], c['object']) for c in r.closed]}" if r.closed else ""))
    return {"adr": adr, "previous": owner["owner"], "new": newcomer, "results": [r.outcome for r in results]}


def timeline(store: Store):
    """A clock for the demo: it starts after the newest episode (or now, if later) so every write is newer
    than what is already stored, and the three acts are minutes apart."""
    last = store.ro("MATCH (e:Episode) RETURN max(e.ts)")[0][0]
    base = max(datetime.now(timezone.utc).replace(microsecond=0), datetime.fromtimestamp(last, timezone.utc) + timedelta(hours=1))
    return lambda minutes: (base + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def demo(store: Store, config: AgentConfig, out=print) -> dict:
    at = timeline(store)
    support = Agent(store, name="agent:support", role="support", session="s-support-1", config=config, ctx_at=at(0))
    ops = Agent(store, name="agent:ops", role="ops", session="s-ops-2", config=config, ctx_at=at(5))
    result: dict = {}

    out("== Session 1: agent:support (session s-support-1)")
    first = support.ask(QUESTION)
    show_answer("customer question", first, out)
    owner = first.primary.rows[0]["owner"]
    evidence = [s.id for s in first.primary.sources]
    ticket, ticket_title = latest_ticket(store, SYSTEM)
    finding = support.tool("remember", {
        "text": f"A customer asked who owns {SYSTEM}. The memory says {owner}, per {', '.join(evidence)}.",
        "about": [SYSTEM, owner], "source_ids": evidence})
    show_tool("support writes a finding", finding, out)
    handoff = support.tool("handoff", {
        "to": "agent:ops", "topic": f"{SYSTEM} escalation: {ticket_title}",
        "note": f"The customer wants to reach the owner of {SYSTEM}; see {ticket}.", "source_ids": [ticket]})
    show_tool(f"support hands off to agent:ops, citing {ticket}", handoff, out)
    result.update(owner_before=owner, finding=finding.outcome, handoff=handoff.outcome, ticket=ticket)

    out("\n== Session 2: agent:ops (new session s-ops-2)")
    recalled = ops.tool("recall", {"entity": SYSTEM, "limit": 5})
    show_tool("ops recalls before it does anything else", recalled, out)
    for row in recalled.rows:
        what = row["text"] if row["kind"] == "finding" else f"handoff from {row['from']} to {row['to']}: {row['topic']}"
        out(f"    memory {row['at']}  {row['kind']:<8} {what}")
    second = ops.ask(QUESTION)
    show_answer("ops answers with the path", second, out)
    result.update(recall_outcome=recalled.outcome, recall_dates=[r["at"] for r in recalled.rows],
                  owner_ops_before=second.primary.rows[0]["owner"])

    out("\n== An ownership change arrives")
    change = ownership_change(store, ops, SYSTEM, at(10), out)
    result.update(change=change)

    out("\n== Both agents ask again")
    after_support = support.ask(QUESTION)
    show_answer("agent:support", after_support, out)
    after_ops = ops.ask(QUESTION)
    show_answer("agent:ops", after_ops, out)
    stale = ops.tool("recall", {"entity": SYSTEM, "limit": 5})
    for row in stale.rows:
        if row["kind"] == "finding":
            note = f"written before {row['closed_since']} later change(s) to {SYSTEM}" if row["closed_since"] else "still current"
            out(f"    memory {row['at']}  finding {row['id']}: {note}")
    why = ops.tool("why", {"subject": change["new"], "predicate": "OWNS", "object": SYSTEM})
    show_tool("why does the memory now believe this", why, out)
    result.update(owner_support_after=after_support.primary.rows[0]["owner"], owner_ops_after=after_ops.primary.rows[0]["owner"],
                  previous_owner=after_ops.primary.rows[0]["previous_owner"],
                  stale_findings=[r["id"] for r in stale.rows if r["kind"] == "finding" and r["closed_since"]],
                  why_receipts=[r.outcome for r in why.receipts])
    out(f"\n  {QUESTION} before: {owner}. After the change: support says {result['owner_support_after']}, "
        f"ops says {result['owner_ops_after']} (previously {result['previous_owner']}).")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tenant", default=os.environ.get("HM_TENANT", "two-agents"))
    ap.add_argument("--ingest", action="store_true", help="replay the seed-42 corpus into the tenant first (resets it)")
    ap.add_argument("--llm", action="store_true", help=f"let a local model pick the tools (default {DEFAULT_MODEL})")
    ap.add_argument("--drop", action="store_true", help="delete the tenant graph when done")
    args = ap.parse_args(argv)

    db = connect()
    store = Store(db, args.tenant)
    if args.ingest:
        with tempfile.TemporaryDirectory() as tmp:
            summary = ingest_corpus(store, write_corpus(build(42), Path(tmp) / "corpus"), reset=True)
        print(f"ingested {summary['episodes']} episodes into tenant {args.tenant}: {summary['outcomes']}\n")
    elif graph_name(args.tenant) not in db.list_graphs():
        raise SystemExit(f"tenant {args.tenant!r} has no graph; run with --ingest")
    use_llm = args.llm or bool(os.environ.get("HM_LLM_MODEL"))
    config = AgentConfig.from_env(default_model=DEFAULT_MODEL if use_llm else None)
    if not use_llm:
        config.model = None
    try:
        demo(store, config)
    finally:
        if args.drop:
            store.drop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
