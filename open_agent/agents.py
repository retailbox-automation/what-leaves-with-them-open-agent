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

import re
from dataclasses import asdict, dataclass, field

from honest_memory import answer as A
from honest_memory.agent import Agent, AgentConfig, AgentRun, combined, model_view
from honest_memory.answer import _ABSENCE, _mentions, check_sentence

from .llm import LLMConfig, TracedClient
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
Reply with JSON only: {"answer": "...", "citations": [{"id": "...", "quote": "..."}]}"""

REVISE_PROMPT = """You are agent:investigator. agent:verifier re-read every record you cited in its source system \
and rejected some of your citations. Revise your answer from its notes.
- For each record the notes give current_lines: the record as its source system holds it now, line by line. A \
citation is a record id and ONE of its current_lines, copied exactly. Never join lines or quote a whole record.
- Drop a citation whose current lines do not speak to the answer.
- must_address lists the records the Verifier found contradicting your answer. Each must not just disappear: \
change the answer to what the records say now, or name that record id in the answer and say what it says now.
- Keep citations the Verifier accepted. Cite only records you were given.
Reply with JSON only: {"answer": "...", "citations": [{"id": "...", "quote": "..."}]}"""

JUDGE_PROMPT = """You are agent:verifier. You check one citation in another agent's answer against the record as \
its source system holds it right now. You did not write the answer and you do not trust it.
Decide whether the record, as it reads now, supports the part of the answer the citation backs:
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
                                        "what it says now, or change the answer"}


@dataclass
class Citation:
    id: str
    channel: str
    quote: str


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

    def to_dict(self) -> dict:
        return {"round": self.round, "answer": self.answer, "author": self.author,
                "citations": [asdict(c) for c in self.citations], "absence_terms": self.absence_terms}


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


def _client(cfg: LLMConfig | None, trace, agent: str, client) -> TracedClient | None:
    return TracedClient(cfg, trace, agent, client) if cfg and cfg.model else None


class Investigator:
    NAME = "agent:investigator"

    def __init__(self, store, trace, llm: LLMConfig | None = None, client=None):
        self.store, self.trace = store, trace
        self.client = _client(llm, trace, "Investigator", client)
        cfg = (AgentConfig(base_url=llm.base_url, model=llm.model, api_key=llm.api_key, timeout=llm.timeout)
               if self.client else AgentConfig(model=None))
        self.agent = Agent(store, name=self.NAME, role="investigator", config=cfg, client=self.client)
        self.agent.tool = self._tool        # every graph tool call lands in the trace as it happens

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

    def investigate(self, question: str) -> Draft:
        self.trace.event("Investigator", "question", question=question,
                         model=self.client.cfg.model if self.client else None,
                         path="model picks tools" if self.client else "deterministic router (no model)")
        run = self.agent.ask(question)
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
        ctx = {"question": draft.question, "draft_answer": draft.answer, "tool_rows": model_view(combined(useful)),
               "records": [{"id": r["id"], "source": SYSTEM_OF.get(r["channel"], r["channel"]), "title": r["title"],
                            "lines": quotable(now.get(r["id"], r["text"])),
                            "as_of": "source now" if r["id"] in now else "memory"} for r in draft.records.values()]}
        if review:
            ctx["previous_citations"] = [asdict(c) for c in draft.citations]
            ctx["verifier_notes"] = [{"id": c.id, "quote": c.quote, "accepted": c.accepted,
                                      "reason": REASON_TEXT.get(c.reason, c.reason), "verdict": c.verdict,
                                      "note": c.note, "current_lines": quotable(c.current_text or "")}
                                     for c in review.checks]
            ctx["verifier_notes"] += [{"id": c.id, "quote": c.quote, "accepted": False, "verdict": "contradicts",
                                       "reason": REASON_TEXT["contradiction_dropped"], "note": c.note,
                                       "current_lines": quotable(c.current_text or "")} for c in review.ignored_checks]
            ctx["verifier_summary"] = review.notes
            ctx["must_address"] = review.contradicted
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
            if cid in draft.records and quote and not any(x.id == cid and x.quote == quote for x in cites):
                cites.append(Citation(cid, draft.records[cid]["channel"], quote))
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
            answer = draft.answer
        if not cites:
            self.trace.event("Investigator", purpose, status="fallback", reason="the model gave no usable citation")
            return None
        return Draft(draft.question, answer, cites[:MAX_CITATIONS], draft.run, draft.records, draft.absence_terms,
                     "model", draft.round)

    # -- revision -----------------------------------------------------------------------------

    def revise(self, draft: Draft, review: Review) -> Draft:
        self.trace.event("Investigator", "revise", round=review.round + 1, notes=review.notes)
        new = None
        if self.client and draft.citations:
            current = " ".join(c.current_text or "" for c in review.checks)
            new = self._from_model("revise", REVISE_PROMPT, self._context(draft, review), draft, extra_text=current)
        new = new or self._revise_from_rows(draft, review)
        self._address_contradictions(new, review)
        new.round = review.round + 1
        self.trace.event("Investigator", "draft", target_agent="Verifier", **new.to_dict())
        return new

    def _address_contradictions(self, new: Draft, review: Review) -> None:
        """Like the sentence check on the model's sentence: a record that contradicted the answer is never dropped
        without a word. If the revision neither cites nor names it, say what it reads now at its source."""
        current = {c.id: c.current_text for c in review.checks + review.ignored_checks if c.current_text}
        cited = {c.id for c in new.citations}
        silent = [i for i in review.contradicted if i not in cited and not _mentions(new.answer, i)]
        for i in silent:
            text = re.sub(r"\s+", " ", current.get(i, "")).strip()
            new.answer += (f' {i} says otherwise now: "{text[:240]}"' if text else f" {i} contradicts this answer.")
        if silent:
            self.trace.event("Investigator", "revise", status="warning", reason="the revision dropped records that "
                             "contradict the answer without a word; added what they say now", records=silent)

    def _revise_from_rows(self, draft: Draft, review: Review) -> Draft:
        """No model: keep the accepted citations, drop the rejected ones and say why in the answer."""
        kept = [Citation(c.id, c.channel, c.quote) for c in review.checks if c.accepted]
        gone = [c for c in review.checks if not c.accepted]
        answer = draft.answer
        if gone and not kept:
            answer = (f"The memory's answer was: {draft.answer} The records behind it changed at their source since "
                      f"the memory read them ({', '.join(c.id for c in gone)}), so it is not confirmed now.")
        elif gone:
            answer = re.sub(r" Sources: [^.]*\.", f" Sources: {', '.join(c.id for c in kept)}.", answer)
            answer += "".join(f" {c.id} is no longer cited: {REASON_TEXT.get(c.reason, c.reason)}." for c in gone)
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
        self._recheck_dropped(draft, cited)
        self.supported.update({c.id: c for c in rv.checks if c.accepted})
        rv.contradicted = list(self.contradicted)
        rv.ignored = [i for i in self.contradicted if i not in cited and not _mentions(draft.answer, i)]
        if rv.ignored:
            rv.accepted, rv.needs_revision = False, True
            rv.notes += [f"{i}: {REASON_TEXT['contradiction_dropped']}; it says: {self.contradicted[i].note}"
                         for i in rv.ignored]
            rv.ignored_checks = [self.contradicted[i] for i in rv.ignored]
        self.trace.event("Verifier", "verdict", round=draft.round, accepted=rv.accepted, target_agent=(
            "Investigator" if rv.needs_revision else "write-gate" if rv.accepted else "final"),
                         checks=[{k: v for k, v in asdict(c).items() if k != "current_text"} for c in rv.checks],
                         absence=rv.absence, ignored=rv.ignored, notes=rv.notes)
        return rv

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
        chk = Check(c.id, c.channel, c.quote)
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
        self.trace.event("Verifier", "check", round=draft.round, record_id=c.id, quote=c.quote,
                         found=chk.quote_found, verdict=chk.verdict, note=chk.note, accepted=chk.accepted,
                         reason=chk.reason)
        return chk

    def _judge(self, draft: Draft, c: Citation, text: str) -> tuple[str, str]:
        if self.client is None:
            return ("skipped: no model in SAMPLE_MODE" if self.sample else "skipped: no model configured"), ""
        obj, why = self.client.ask_json("judge", JUDGE_PROMPT, {
            "answer": draft.answer, "question": draft.question,
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
