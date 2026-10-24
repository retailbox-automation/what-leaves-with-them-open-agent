"""The two agents of the loop.

Investigator (agent:investigator): the honest_memory agent. A model picks tools over the memory graph (or, with
no model, the deterministic router does), then it backs the answer with citations: a record id and a sentence
quoted from that record as the memory holds it. When the Verifier rejects a citation it revises the answer from
the Verifier's notes, which carry each record's text as its source system holds it now.

Verifier (agent:verifier): re-reads every cited record by id through the source endpoint (Zetaris, or the local
sample engine), checks deterministically that the quoted sentence is in the record (the hard gate), and asks its
own model whether the record as it reads now supports the answer. A "no" goes back to the Investigator as a note.
An answer that cites nothing because a name is not in the memory is checked against the three systems directly.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field

from honest_memory import answer as A
from honest_memory import tools as T
from honest_memory.agent import MEMORY_PREFIX, Agent, AgentConfig, AgentRun, combined, model_view
from honest_memory.answer import _ABSENCE, _mentions, check_sentence

from .llm import LLMConfig, TracedClient, make_client
from .sources import SYSTEM_OF, SourceUnavailable, by_id, mentions

MAX_CITATIONS = 4
MIN_QUOTE_WORDS = 4
MAX_QUOTE_CHARS = 300
VERDICTS = ("supports", "contradicts", "unrelated")

CITE_PROMPT = """You are agent:investigator. You answered a question from a company memory graph; now back the \
answer with citations that another agent will check against the source systems.
- Use only the records given. Each record comes as a list of lines. A citation is a record id and ONE of \
its lines, copied exactly. Never join lines, never quote a whole record, never paraphrase or shorten a line.
- Write the answer as one or two plain sentences that use only names, dates and ids found in the tool rows or the \
records. Keep what the draft answer says unless a record contradicts it.
- Cite at most 4 records, the ones that support the answer.
- For each citation give claim: the part of your answer that this line backs, in a few words.
Reply with JSON only: {"answer": "...", "citations": [{"id": "...", "quote": "...", "claim": "..."}]}"""

REVISE_PROMPT = """You are agent:investigator. agent:verifier re-read every record you cited in its source system \
and rejected some of your citations. Revise your answer from its notes.
- For each record the notes give current_lines: the record as its source system holds it now, line by line. A \
citation is a record id and ONE of its current_lines, copied exactly. Never join lines or quote a whole record.
- Drop a citation whose current lines do not speak to the answer.
- must_address lists records that contradict your answer or changed at their source after the memory read them. \
Answer only with what the records you cite support now. Do not restate, blend in or describe a must_address record: \
a separate conflict line, written for you from the Verifier's notes, reports each one and flags it for a human. \
If the records you cite no longer support the answer, change the answer.
- Keep citations the Verifier accepted. Cite only records you were given.
- For each citation give claim: the part of your answer that this line backs, in a few words.
Reply with JSON only: {"answer": "...", "citations": [{"id": "...", "quote": "...", "claim": "..."}]}"""

JUDGE_PROMPT = """You are agent:verifier. You check one citation in another agent's answer against the record as \
its source system holds it right now. You did not write the answer and you do not trust it.
Decide whether the record, as it reads now, supports the claim: the part of the answer this citation is meant to \
back (the whole answer when no claim is given). Judge that claim only; other parts of the answer are checked \
against other records.
- "supports": the record states it.
- "contradicts": the record states something that conflicts with it.
- "unrelated": the record does not speak to it.
Judge only from current_text. Reply with JSON only: {"verdict": "supports|contradicts|unrelated", "note": "one \
sentence for the investigator: what the record says now"}"""

REASON_TEXT = {"quote_not_in_record": "the quoted sentence is not in the record as its source system holds it now",
               "quote_too_short": f"the quote is shorter than {MIN_QUOTE_WORDS} words, too short to check",
               "quote_not_a_sentence": f"the quote must be one sentence or one line (at most {MAX_QUOTE_CHARS} "
                                       "characters), not a whole record",
               "record_missing": "the source system has no record with this id",
               "unreadable": "the source system could not be read",
               "model_contradicts": "the record as it reads now contradicts the answer",
               "model_unrelated": "the record as it reads now does not speak to the answer",
               "contradiction_dropped": "this record contradicted the answer and was dropped without a word; say "
                                        "what it says now, or change the answer",
               "evidence_changed": "the memory's answer rests on this record, and it changed at its source since the "
                                   "memory read it"}
MAX_EVIDENCE_REREADS = 12


@dataclass
class Citation:
    id: str
    channel: str
    quote: str
    claim: str = ""     # the part of the answer this quote backs (from the model; empty means the whole answer)


@dataclass
class Draft:
    question: str
    answer: str
    citations: list[Citation]
    run: AgentRun
    records: dict[str, dict]                    # cited-able records, as the memory holds them
    absence_terms: list[str] = field(default_factory=list)
    author: str = "template"                    # "model" or "template": who wrote answer and citations
    round: int = 1
    main: str = ""                              # the answer without its conflict lines ("" = the whole answer)
    conflicts: list[dict] = field(default_factory=list)   # records flagged for a human, one line each in answer

    @property
    def claim_text(self) -> str:
        """What the answer itself claims: the answer without the conflict lines the Investigator appended."""
        return self.main or self.answer

    def to_dict(self) -> dict:
        return {"round": self.round, "answer": self.answer, "author": self.author,
                "citations": [asdict(c) for c in self.citations], "absence_terms": self.absence_terms,
                "conflicts": [c["id"] for c in self.conflicts]}


@dataclass
class Check:
    id: str
    channel: str
    quote: str
    record_found: bool = False
    quote_found: bool = False
    verdict: str = ""
    note: str = ""
    accepted: bool = False
    reason: str = ""
    current_text: str | None = None
    claim: str = ""


@dataclass
class Review:
    round: int
    checks: list[Check] = field(default_factory=list)
    absence: list[dict] = field(default_factory=list)
    accepted: bool = False
    needs_revision: bool = False
    notes: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)   # records that contradicted the answer, then vanished from it
    ignored_checks: list[Check] = field(default_factory=list)
    contradicted: list[str] = field(default_factory=list)   # every record that contradicted the answer so far


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().strip("\"'“”").strip().rstrip(".").strip()


def quote_in_record(quote: str, text: str) -> bool:
    return bool(_norm(quote)) and _norm(quote) in re.sub(r"\s+", " ", text or "")


def sentences(text: str) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        out += [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line) if s.strip()]
    return out


def quotable(text: str) -> list[str]:
    """The lines of a record a citation may quote, as the agents see them: one sentence or line each."""
    return [s for s in sentences(text) if len(s.split()) >= MIN_QUOTE_WORDS and len(s) <= MAX_QUOTE_CHARS]


KIND_OF = {"chat": "chat message", "doc": "document", "ticket": "ticket"}
_CLAUSE = re.compile(r",?\s+(?:but|while|whereas|although|though|yet|however)\s+|;\s+|\s+[\u2014\u2013]\s+")


def _flat(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def conflict_line(c: "Check", kind: str) -> dict:
    """One plain sentence per flagged record, built from the Verifier's check (no model writes it)."""
    what = KIND_OF.get(c.channel, "record")
    if kind == "changed_at_source":
        now = c.current_text or ""
        line = (f'Conflict: {c.id} ({what}) was edited after the memory read it and now says "{_flat(now, 240)}"; '
                f'the earlier quote "{_flat(c.quote, 160)}" is no longer in the record. Flagged for a human.')
    else:
        now = c.quote if c.quote_found else (c.current_text or "")
        line = (f'Conflict: {c.id} ({what}) says "{_flat(now, 240)}", which contradicts this answer. '
                "Flagged for a human.")
    return {"id": c.id, "source": SYSTEM_OF.get(c.channel, c.channel), "kind": kind, "now_says": _flat(now, 600),
            "earlier_quote": c.quote, "verifier_note": c.note, "flagged_for": "human", "line": line}


def _grams(text: str, n: int = 5) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9][a-z0-9-]*", (text or "").lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def strip_restatements(answer: str, flagged: list[dict], support: str) -> str:
    """The answer without the clauses that name a flagged record or repeat its wording (a run of 5 words found in
    the flagged record and in none of the cited ones). Sentences with nothing to take out are kept verbatim."""
    ids = [cf["id"] for cf in flagged]
    bad = set().union(*(_grams(cf["now_says"]) for cf in flagged)) - _grams(support)
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", (answer or "").strip()):
        parts = [p.strip() for p in _CLAUSE.split(sentence) if p and p.strip()]
        keep = [p for p in parts if not (any(_mentions(p, i) for i in ids) or _grams(p) & bad)]
        if len(keep) == len(parts):
            kept.append(sentence)
        elif keep:
            kept.append("; ".join(p.rstrip(" .") for p in keep) + ".")
    return " ".join(kept)


def _client(cfg: LLMConfig | None, trace, agent: str, client) -> TracedClient | None:
    return make_client(cfg, trace, agent, client)


class Investigator:
    NAME = "agent:investigator"

    def __init__(self, store, trace, llm: LLMConfig | None = None, client=None):
        self.store, self.trace = store, trace
        self.client = _client(llm, trace, "Investigator", client)
        cfg = (AgentConfig(base_url=llm.base_url, model=llm.model, api_key=llm.api_key, timeout=llm.timeout)
               if self.client else AgentConfig(model=None))
        self.agent = Agent(store, name=self.NAME, role="investigator", config=cfg, client=self.client)
        self.agent.tool = self._tool        # every graph tool call lands in the trace as it happens
        self.memory: dict = {"status": "not_run", "ids": [], "context": "", "memories": []}
        self.conflicts: dict[str, dict] = {}   # record id -> its conflict line, over all rounds

    def _tool(self, name: str, args: dict | None = None):
        try:
            ans = Agent.tool(self.agent, name, args)
        except Exception as exc:
            self.trace.event("Investigator", "tool_call", status="error", tool=name, args=args,
                             error=f"{type(exc).__name__}: {str(exc)[:200]}")
            raise
        t = ans.trace[0] if ans.trace else None
        self.trace.event("Investigator", "tool_call", tool=name, args=t.args if t else args, outcome=ans.outcome,
                         rows=len(ans.rows), ms=t.ms if t else None, graph_queries=len(t.queries) if t else 0,
                         sources=[s.id for s in ans.sources], receipts=[r.id for r in ans.receipts])
        return ans

    # -- first answer -------------------------------------------------------------------------

    def recall_memory(self, question: str) -> dict:
        """H-MEM before the Investigator answers: findings the write gate accepted earlier (any process, any run)
        that match the question. The model gets them as a system message before its first call; the router reads
        none, but the recall is recorded either way, so the Verifier's verdict can be fed back to those memories."""
        if os.environ.get("HM_MEMORY_CONTEXT", "on").strip().lower() == "off":
            why = "HM_MEMORY_CONTEXT=off"
        else:
            backend, why = T.memory_backend()
            why = "" if backend == "hmem" else why
        if why:
            self.trace.event("Investigator", "memory_recall", status="skipped", reason=why, memory_ids=[])
            return {"status": "skipped", "reason": why, "ids": [], "context": "", "memories": []}
        try:
            from .hmem_bridge import memory_context

            context, hits = memory_context(self.store, question)
        except Exception as exc:  # no memory is not a reason to refuse the question; the trace says it failed
            err = f"{type(exc).__name__}: {str(exc)[:200]}"
            self.trace.event("Investigator", "memory_recall", status="error", error=err, memory_ids=[])
            return {"status": "error", "reason": err, "ids": [], "context": "", "memories": []}
        mems = [{"memory_id": h["memory"]["id"], "relevance": round(h["relevance"], 4),
                 "confidence": round(h["memory"].get("confidence", 0), 4), "layer": h["memory"].get("layer"),
                 "finding": next((r for r in (h["memory"].get("provenance") or {}).get("refs", [])[1:2]), None),
                 "session": h["memory"].get("chatId"), "content": (h["memory"].get("content") or "")[:300]}
                for h in hits]
        used_by = ("model: a system message before its first call" if self.client and context else
                   "nobody: the deterministic router reads no memory (recorded for the Verifier's feedback)"
                   if context else "nothing matched")
        self.trace.event("Investigator", "memory_recall", backend="hmem", memory_ids=[m["memory_id"] for m in mems],
                         memories=mems, used_by=used_by)
        return {"status": "ok", "ids": [m["memory_id"] for m in mems], "context": context, "memories": mems}

    def _memory_messages(self, run: AgentRun, memory: dict) -> list[dict]:
        if not memory["context"]:
            return []
        run.memory_ids = list(memory["ids"])
        return [{"role": "system", "content": MEMORY_PREFIX + memory["context"]}]

    def investigate(self, question: str) -> Draft:
        self.trace.event("Investigator", "question", question=question,
                         model=self.client.cfg.model if self.client else None,
                         path="model picks tools" if self.client else "deterministic router (no model)")
        self.memory = self.recall_memory(question)
        # the agent's own lookup is replaced by the one just traced (one recall per question, one ledger read)
        self.agent._memory_messages = lambda run, q: self._memory_messages(run, self.memory)
        run = self.agent.ask(question)
        if not run.memory_ids:
            run.memory_ids = list(self.memory["ids"])
        self.trace.event("Investigator", "graph_answer", outcome=run.outcome, path=run.mode, sentence=run.sentence,
                         sentence_source=run.sentence_source, sentence_check=run.check, rejected=run.rejected,
                         errors=run.errors, ms=run.ms)
        records = self._records(run)
        draft = Draft(question, run.sentence, [], run, records, self._absence_terms(run))
        if records:
            made = self._from_model("cite", CITE_PROMPT, self._context(draft), draft) if self.client else None
            draft = made or self._cite_from_rows(draft)
        self.trace.event("Investigator", "draft", target_agent="Verifier", **draft.to_dict())
        return draft

    def _records(self, run: AgentRun) -> dict[str, dict]:
        ids = list(dict.fromkeys(s.id for a in run.answers for s in a.sources))
        props = self.store.records("Episode", ids)
        return {i: {"id": i, "channel": props[i].get("channel", ""), "title": props[i].get("title", ""),
                    "text": props[i].get("text", "")} for i in ids if i in props}

    @staticmethod
    def _absence_terms(run: AgentRun) -> list[str]:
        terms = [m[1] for a in run.answers if a.outcome == A.UNKNOWN_ENTITY
                 for m in [re.search(r"named '([^']+)'", a.note or "")] if m]
        return list(dict.fromkeys(terms))

    def _context(self, draft: Draft, review: Review | None = None) -> dict:
        useful = [a for a in draft.run.answers if a.outcome not in (A.UNKNOWN_ENTITY,)] or draft.run.answers
        # A record the Verifier re-read is shown as its source holds it now; the memory's older copy is not offered.
        now = {c.id: c.current_text for c in (review.checks + review.ignored_checks if review else [])
               if c.current_text is not None}
        ctx = {"question": draft.question, "draft_answer": draft.claim_text, "tool_rows": model_view(combined(useful)),
               "records": [{"id": r["id"], "source": SYSTEM_OF.get(r["channel"], r["channel"]), "title": r["title"],
                            "lines": quotable(now.get(r["id"], r["text"])),
                            "as_of": "source now" if r["id"] in now else "memory"} for r in draft.records.values()]}
        if review:
            ctx["previous_citations"] = [asdict(c) for c in draft.citations]
            ctx["verifier_notes"] = [{"id": c.id, "quote": c.quote, "accepted": c.accepted,
                                      "reason": REASON_TEXT.get(c.reason, c.reason), "verdict": c.verdict,
                                      "note": c.note, "current_lines": quotable(c.current_text or "")}
                                     for c in review.checks]
            ctx["verifier_notes"] += [{"id": c.id, "quote": c.quote, "accepted": False,
                                       "verdict": c.verdict or "changed at its source",
                                       "reason": REASON_TEXT["evidence_changed" if c.reason == "evidence_changed"
                                                             else "contradiction_dropped"], "note": c.note,
                                       "current_lines": quotable(c.current_text or "")} for c in review.ignored_checks]
            ctx["verifier_summary"] = review.notes
            ctx["must_address"] = list(dict.fromkeys(list(self.conflicts) + review.contradicted))
        return ctx

    def _cite_from_rows(self, draft: Draft) -> Draft:
        """No model: quote, from each source record, the sentence that names the most things the answer names."""
        names = sorted(self.agent.names(), key=len, reverse=True)
        key = [n for n in names if _mentions(draft.answer, n)]
        cites = []
        for rec in draft.records.values():
            scored = [(sum(_mentions(s, n) for n in key), -i, s) for i, s in enumerate(quotable(rec["text"]))]
            best = max(scored, default=None)
            if best and best[0] > 0:
                cites.append(Citation(rec["id"], rec["channel"], best[2]))
            if len(cites) == MAX_CITATIONS:
                break
        draft.citations, draft.author = cites, "template"
        return draft

    def _from_model(self, purpose: str, prompt: str, context: dict, draft: Draft, extra_text: str = "") -> Draft | None:
        obj, why = self.client.ask_json(purpose, prompt, context)
        if obj is None:
            self.trace.event("Investigator", purpose, status="fallback", reason=why)
            return None
        answer = str(obj.get("answer") or "").strip()[:1000]
        cites, dropped = [], []
        for c in obj.get("citations") or []:
            cid = str((c or {}).get("id") or "").strip() if isinstance(c, dict) else ""
            quote = str((c or {}).get("quote") or "").strip() if isinstance(c, dict) else ""
            claim = str((c or {}).get("claim") or "").strip()[:300] if isinstance(c, dict) else ""
            if cid in draft.records and quote and not any(x.id == cid and x.quote == quote for x in cites):
                cites.append(Citation(cid, draft.records[cid]["channel"], quote, claim))
            else:
                dropped.append(cid or repr(c)[:60])
        if dropped:
            self.trace.event("Investigator", purpose, status="warning", reason="citations of records it was not given "
                             "or without a quote were dropped", dropped=dropped)
        texts = " ".join(r["text"] for r in draft.records.values()) + " " + extra_text
        ok, why = (check_sentence(answer, combined(draft.run.answers), self.agent.names(), asked=draft.question + " " + texts)
                   if answer and draft.run.answers else (False, "empty answer"))
        if not ok:
            self.trace.event("Investigator", purpose, status="warning", reason=f"the model's answer failed the "
                             f"sentence check ({why}); keeping the previous answer", rejected_answer=answer)
            answer = draft.claim_text
        if not cites:
            self.trace.event("Investigator", purpose, status="fallback", reason="the model gave no usable citation")
            return None
        return Draft(draft.question, answer, cites[:MAX_CITATIONS], draft.run, draft.records, draft.absence_terms,
                     "model", draft.round)

    # -- revision -----------------------------------------------------------------------------

    def revise(self, draft: Draft, review: Review) -> Draft:
        self.trace.event("Investigator", "revise", round=review.round + 1, notes=review.notes)
        self._note_conflicts(review)
        new = None
        if self.client and draft.citations:
            current = " ".join(c.current_text or "" for c in review.checks)
            new = self._from_model("revise", REVISE_PROMPT, self._context(draft, review), draft, extra_text=current)
        new = new or self._revise_from_rows(draft, review)
        self._address_contradictions(new, review)
        new.round = review.round + 1
        self.trace.event("Investigator", "draft", target_agent="Verifier", **new.to_dict())
        return new

    def _note_conflicts(self, review: Review) -> None:
        """Records to flag for a human, from the Verifier's notes: changed at the source after the memory read them
        (the quoted line is gone from a record that is still there), or judged as contradicting the answer."""
        for c in review.ignored_checks + review.checks:
            if c.record_found and not c.quote_found:
                self.conflicts[c.id] = conflict_line(c, "changed_at_source")
            elif c.verdict == "contradicts" and c.record_found and c.id not in self.conflicts:
                self.conflicts[c.id] = conflict_line(c, "contradicts")

    def _address_contradictions(self, new: Draft, review: Review) -> None:
        """A record that changed at its source or contradicts the answer is never dropped without a word, and never
        blended into the answer. The answer keeps what the cited records support (clauses that restate a flagged
        record are taken out); each flagged record gets one deterministic conflict line, flagged for a human."""
        cited = {c.id for c in new.citations}
        flagged = [cf for i, cf in self.conflicts.items() if i not in cited]
        if not flagged:
            return
        support = " ".join(new.records[c.id]["text"] for c in new.citations if c.id in new.records)
        support += " " + " ".join(c.current_text or "" for c in review.checks if c.id in cited)
        before = new.claim_text
        main = strip_restatements(before, flagged, support) or before
        if cited and not any(_mentions(main, i) for i in cited):
            main = main.rstrip().rstrip(".") + f" [{', '.join(c.id for c in new.citations)}]."
        new.main, new.conflicts = main, flagged
        new.answer = " ".join([main] + [cf["line"] for cf in flagged])
        self.trace.event("Investigator", "conflict_flagged", status="warning", round=review.round + 1,
                         records=[cf["id"] for cf in flagged], kinds=[cf["kind"] for cf in flagged],
                         removed_from_answer=before if main != before else None, flagged_for="human",
                         reason="records that changed at their source or contradict the answer are reported on their "
                                "own line, not blended into it")

    def _revise_from_rows(self, draft: Draft, review: Review) -> Draft:
        """No model: keep the accepted citations, drop the rejected ones and say why in the answer."""
        kept = [Citation(c.id, c.channel, c.quote, c.claim) for c in review.checks if c.accepted]
        gone = [c for c in review.checks if not c.accepted]
        answer = draft.claim_text
        if gone and not kept:
            answer = (f"The memory's answer was: {draft.answer} The records behind it changed at their source since "
                      f"the memory read them ({', '.join(c.id for c in gone)}), so it is not confirmed now.")
        elif gone:
            answer = re.sub(r" Sources: [^.]*\.", f" Sources: {', '.join(c.id for c in kept)}.", answer)
            answer += "".join(f" {c.id} is no longer cited: {REASON_TEXT.get(c.reason, c.reason)}." for c in gone
                              if c.id not in self.conflicts)      # a flagged record gets its conflict line instead
        for a in review.absence:
            if not a["accepted"] and a["ids"]:
                answer += (f" The source systems do mention {a['term']} ({', '.join(a['ids'])}), but the memory "
                           "holds no entity of that name.")
        return Draft(draft.question, answer, kept, draft.run, draft.records, draft.absence_terms, "template", draft.round)


class Verifier:
    NAME = "agent:verifier"

    def __init__(self, source, trace, llm: LLMConfig | None = None, client=None, sample: bool = False):
        self.source, self.trace, self.sample = source, trace, sample
        self.client = None if sample else _client(llm, trace, "Verifier", client)
        self.contradicted: dict[str, Check] = {}   # record id -> its last "contradicts" check, over all rounds
        self.supported: dict[str, Check] = {}      # records accepted in an earlier round
        self.changed: dict[str, Check] = {}        # uncited records the answer rests on that changed at their source

    def review(self, draft: Draft) -> Review:
        rv = Review(draft.round)
        self.trace.event("Verifier", "received", round=draft.round, citations=len(draft.citations),
                         endpoint=self.source.endpoint)
        for c in draft.citations:
            rv.checks.append(self._check(draft, c))
        if not draft.citations and draft.absence_terms:
            rv.absence = [self._absence(draft, t) for t in draft.absence_terms]
        if rv.checks:
            rv.accepted = all(c.accepted for c in rv.checks)
            rv.needs_revision = any(not c.accepted and c.reason != "unreadable" for c in rv.checks)
        elif rv.absence:
            rv.accepted = all(a["accepted"] for a in rv.absence)
            rv.needs_revision = any(not a["accepted"] and a["ids"] for a in rv.absence)
        else:  # nothing cited: acceptable only when the answer claims nothing (no tool, or an absence)
            rv.accepted = not draft.run.answers or draft.run.outcome in (A.NOT_IN_GRAPH, A.UNKNOWN_ENTITY)
            if not rv.accepted:
                rv.notes.append("the answer states a result but cites no record")
        rv.notes += [f"{c.id}: {REASON_TEXT.get(c.reason, c.reason)}" + (f"; {c.note}" if c.note else "")
                     for c in rv.checks if not c.accepted]
        rv.notes += [f"{a['term']}: mentioned in the source systems ({', '.join(a['ids'])})" for a in rv.absence
                     if not a["accepted"]]
        # A record that contradicted the answer may be answered or cited again, but not dropped without a word.
        self.contradicted.update({c.id: c for c in rv.checks if c.verdict == "contradicts" and c.record_found})
        cited = {c.id for c in draft.citations}
        if draft.round == 1:
            self._reread_evidence(draft, cited)
        self._recheck_dropped(draft, cited)
        self.supported.update({c.id: c for c in rv.checks if c.accepted})
        rv.contradicted = list(self.contradicted)
        flagged = {**self.changed, **self.contradicted}
        rv.ignored = [i for i in flagged if i not in cited and not _mentions(draft.answer, i)]
        if rv.ignored:
            rv.accepted, rv.needs_revision = False, True
            rv.notes += [f"{i}: {REASON_TEXT['contradiction_dropped']}; it says: {flagged[i].note}"
                         if i in self.contradicted else f"{i}: {REASON_TEXT['evidence_changed']}; it now reads: "
                         f"{_flat(flagged[i].current_text or '', 240)}" for i in rv.ignored]
            rv.ignored_checks = [flagged[i] for i in rv.ignored]
        self.trace.event("Verifier", "verdict", round=draft.round, accepted=rv.accepted, target_agent=(
            "Investigator" if rv.needs_revision else "write-gate" if rv.accepted else "final"),
                         checks=[{k: v for k, v in asdict(c).items() if k != "current_text"} for c in rv.checks],
                         absence=rv.absence, ignored=rv.ignored, notes=rv.notes)
        return rv

    def _reread_evidence(self, draft: Draft, cited: set[str]) -> None:
        """The records the memory's answer rests on but the draft does not cite are re-read by id too. One that
        changed at its source since the memory read it may not pass without a word: the answer has to name it
        (the Investigator adds a conflict line). Deterministic, so it does not depend on what the model cited."""
        for rid, rec in list(draft.records.items())[:MAX_EVIDENCE_REREADS]:
            if rid in cited or rid in self.changed or rec["channel"] not in SYSTEM_OF:
                continue
            sql = by_id(rec["channel"], rid)
            try:
                rows, secs = self.source.sql(sql)
            except SourceUnavailable as exc:
                self.trace.event("Verifier", "evidence_reread", status="error", endpoint=self.source.endpoint,
                                 sql=sql, record_id=rid, error=str(exc))
                continue
            now = str(rows[0].get("text") or "") if rows else None
            changed = now is not None and _flat(now, 10**6) != _flat(rec["text"], 10**6)
            self.trace.event("Verifier", "evidence_reread", endpoint=self.source.endpoint,
                             source=SYSTEM_OF[rec["channel"]], sql=sql, record_id=rid, rows=len(rows),
                             seconds=round(secs, 3), changed=changed, cited=False)
            if changed:
                self.changed[rid] = Check(rid, rec["channel"], _flat(rec["text"], 300), True, False, "",
                                          "changed at its source since the memory read it", False,
                                          "evidence_changed", now)

    def _recheck_dropped(self, draft: Draft, cited: set[str]) -> None:
        """A record accepted in an earlier round that the new draft neither cites nor names is judged again
        against the new answer (from the text already re-read): evidence for the old answer may contradict the new."""
        for rid, old in list(self.supported.items()):
            if rid in cited or _mentions(draft.answer, rid) or rid in self.contradicted:
                continue
            del self.supported[rid]
            verdict, note = self._judge(draft, Citation(old.id, old.channel, old.quote), old.current_text or "")
            self.trace.event("Verifier", "recheck_dropped", round=draft.round, record_id=rid, verdict=verdict, note=note)
            if verdict == "contradicts":
                self.contradicted[rid] = Check(old.id, old.channel, old.quote, True, old.quote_found, verdict, note,
                                               False, "contradiction_dropped", old.current_text)

    def _check(self, draft: Draft, c: Citation) -> Check:
        chk = Check(c.id, c.channel, c.quote, claim=c.claim)
        sql = by_id(c.channel, c.id) if c.channel in SYSTEM_OF else None
        if sql is None:
            chk.reason = "record_missing"
            return chk
        try:
            rows, secs = self.source.sql(sql)
        except SourceUnavailable as exc:
            self.trace.event("Verifier", "reread", status="error", endpoint=self.source.endpoint, sql=sql,
                             record_id=c.id, error=str(exc))
            chk.reason, chk.note = "unreadable", str(exc)[:200]
            return chk
        self.trace.event("Verifier", "reread", endpoint=self.source.endpoint, source=SYSTEM_OF[c.channel], sql=sql,
                         record_id=c.id, rows=len(rows), seconds=round(secs, 3))
        if not rows:
            chk.reason = "record_missing"
            self.trace.event("Verifier", "check", round=draft.round, record_id=c.id, quote=c.quote, found=False,
                             accepted=False, reason=chk.reason)
            return chk
        chk.record_found, chk.current_text = True, str(rows[0].get("text") or "")
        chk.quote_found = quote_in_record(c.quote, chk.current_text)
        shape = ("quote_too_short" if len(c.quote.split()) < MIN_QUOTE_WORDS else "quote_not_a_sentence"
                 if "\n" in c.quote.strip() or len(c.quote) > MAX_QUOTE_CHARS else "")
        chk.verdict, chk.note = self._judge(draft, c, chk.current_text)
        model_ok = chk.verdict == "supports" or chk.verdict.startswith("skipped")
        chk.accepted = chk.quote_found and not shape and model_ok
        chk.reason = ("ok" if chk.accepted else "quote_not_in_record" if not chk.quote_found
                      else shape or f"model_{chk.verdict}")
        self.trace.event("Verifier", "check", round=draft.round, record_id=c.id, quote=c.quote, claim=c.claim,
                         found=chk.quote_found, verdict=chk.verdict, note=chk.note, accepted=chk.accepted,
                         reason=chk.reason)
        return chk

    def _judge(self, draft: Draft, c: Citation, text: str) -> tuple[str, str]:
        if self.client is None:
            return ("skipped: no model in SAMPLE_MODE" if self.sample else "skipped: no model configured"), ""
        obj, why = self.client.ask_json("judge", JUDGE_PROMPT, {
            "answer": draft.claim_text, "claim": c.claim or draft.claim_text, "question": draft.question,
            "record": {"id": c.id, "source": SYSTEM_OF[c.channel], "current_text": text}, "quote": c.quote})
        if obj is None:
            return f"skipped: {why}", ""
        verdict = str(obj.get("verdict") or "").strip().lower()
        note = str(obj.get("note") or "").strip()[:300]
        return (verdict, note) if verdict in VERDICTS else (f"skipped: model gave no verdict ({verdict[:30]!r})", note)

    def _absence(self, draft: Draft, term: str) -> dict:
        sql = mentions(term)
        try:
            rows, secs = self.source.sql(sql)
        except SourceUnavailable as exc:
            self.trace.event("Verifier", "absence_check", status="error", endpoint=self.source.endpoint, sql=sql,
                             term=term, error=str(exc))
            return {"term": term, "ids": [], "accepted": False, "reason": "unreadable"}
        ids = sorted({r["id"] for r in rows})
        states_absence = bool(_ABSENCE.search(draft.answer))
        accepted = states_absence and all(i in draft.answer for i in ids)
        out = {"term": term, "ids": ids, "accepted": accepted, "states_absence": states_absence,
               "reason": "absent from all three systems" if not ids else "mentioned in the source systems"}
        self.trace.event("Verifier", "absence_check", endpoint=self.source.endpoint, sql=sql, term=term,
                         rows=len(rows), seconds=round(secs, 3), **{k: v for k, v in out.items() if k != "term"})
        return out
