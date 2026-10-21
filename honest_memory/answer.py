"""The object every tool returns, the sentence check and the fallback sentence.

An Answer is assembled from graph rows. A model may phrase one sentence from it, but
check_sentence rejects any sentence that names something the rows do not contain, and
template_sentence builds a flatter sentence from the rows alone (hackathon guide, problem 5).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

ANSWERED_WITH_PATH = "answered_with_path"
ANSWERED_FROM_MEMORY = "answered_from_memory"
NOT_IN_GRAPH = "not_in_graph"
UNKNOWN_ENTITY = "unknown_entity"
ACCEPTED = "accepted"
CONFIRMED = "confirmed"
SUPERSEDED = "superseded"
REFUSED = "refused"
CLOSED = "closed"

READ_OUTCOMES = (ANSWERED_WITH_PATH, ANSWERED_FROM_MEMORY, NOT_IN_GRAPH, UNKNOWN_ENTITY)
WRITE_OUTCOMES = (ACCEPTED, CONFIRMED, SUPERSEDED, REFUSED, CLOSED)
OUTCOMES = READ_OUTCOMES + WRITE_OUTCOMES

# What the write gate calls its decisions, mapped to the outcome an agent sees.
GATE_OUTCOMES = {"ACCEPT": ACCEPTED, "CONFIRM": CONFIRMED, "SUPERSEDE": SUPERSEDED, "REJECT": REFUSED,
                 "CLOSE": CLOSED}


@dataclass
class Source:
    id: str
    title: str = ""
    ts: str = ""
    channel: str = ""
    author: str = ""


@dataclass
class Receipt:
    id: str
    outcome: str = ""
    reasons: list[str] = field(default_factory=list)
    ts: str = ""
    predicate: str = ""
    subject: str = ""
    object: str = ""
    cites: list[str] = field(default_factory=list)
    claim: str = ""     # the episode a refused claim was read in, when it is not one of its sources


@dataclass
class ToolTrace:
    tool: str
    args: dict
    ms: float
    rows: int
    queries: list[str] = field(default_factory=list)


@dataclass
class Answer:
    outcome: str
    tool: str
    args: dict
    rows: list[dict] = field(default_factory=list)
    # Each path alternates node names and edge types; an edge type with a leading "<-" was
    # walked against its direction.
    paths: list[list[str]] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    receipts: list[Receipt] = field(default_factory=list)
    trace: list[ToolTrace] = field(default_factory=list)
    sentence: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def iso_ts(value) -> str:
    """Epoch seconds or an ISO string -> 'YYYY-MM-DDTHH:MM:SSZ' ('' when missing)."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def day(value) -> str:
    return iso_ts(value)[:10]


def render_path(path: list[str]) -> str:
    """['a', 'OWNS', 'b', '<-DEPENDS_ON', 'c'] -> 'a -OWNS-> b <-DEPENDS_ON- c'."""
    out = [path[0]] if path else []
    for i in range(1, len(path), 2):
        edge = path[i]
        out.append(f"<-{edge[2:]}-" if edge.startswith("<-") else f"-{edge}->")
        out.append(path[i + 1])
    return " ".join(out)


# --- sentence check ------------------------------------------------------------------------

_KEBAB = re.compile(r"(?<![\w-])[a-z][a-z0-9]*(?:-[a-z0-9]+)+(?![\w-])")
_RECORD_ID = re.compile(r"(?<![\w-])(?:ADR|TCK|RB|PM|DOC)-\d{2,4}(?![\w-])|(?<![\w-])msg-\d{4}(?![\w-])", re.I)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_BIGRAM = re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b")
_ABSENCE = re.compile(r"\b(no|not|nothing|none|never|unknown|cannot|can't|isn't|aren't|doesn't|don't|didn't|"
                      r"without|neither|nor|zero|unable)\b|n't\b", re.I)
# Ordinary hyphenated words are not system names.
_PLAIN_HYPHENATED = {"on-call", "follow-up", "up-to-date", "read-only", "long-term", "short-term", "real-time",
                     "end-to-end", "built-in", "in-memory", "multi-hop", "well-known", "high-level", "one-time",
                     "so-called", "non-functional", "third-party", "day-to-day", "first-hand", "set-up",
                     "hand-off", "check-in", "sign-off", "write-gate", "run-book"}


def _mentions(text: str, name: str) -> bool:
    """Whole-name match. Multi-word, hyphenated and numbered names ignore case; a plain single
    word like Support or auth must match exactly, so ordinary prose does not trip it."""
    loose = len(name.split()) > 1 or re.search(r"[-\d]", name) is not None
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text, re.I if loose else 0) is not None


def allowed_text(answer: Answer) -> str:
    """Everything a sentence may mention: rows, paths, sources, receipts and the call arguments."""
    blob = {"rows": answer.rows, "paths": answer.paths, "args": answer.args, "note": answer.note,
            "sources": [asdict(s) for s in answer.sources], "receipts": [asdict(r) for r in answer.receipts]}
    return json.dumps(blob, ensure_ascii=False, default=str)


def check_sentence(sentence: str, answer: Answer, known_names, *, asked: str = "") -> tuple[bool, str]:
    """Accept a sentence only if everything it names is in the answer (or in the question, `asked`).

    Rejects: a graph entity that is absent from the answer; a system-like name, record id or
    date that the answer does not contain (invented); a person name that mixes known first and
    last names into one that does not exist; any claim of impact when nothing was found.
    """
    text = sentence.strip()
    if not text:
        return False, "empty sentence"
    allowed = allowed_text(answer) + " " + asked
    names = sorted(set(known_names), key=len, reverse=True)
    absent = [n for n in names if _mentions(text, n) and not _mentions(allowed, n)]
    if absent:
        return False, f"names not in the answer: {absent[:5]}"
    if invented := {t for t in _RECORD_ID.findall(text) if not _mentions(allowed, t)}:
        return False, f"record ids not in the answer: {sorted(invented)}"
    scrubbed = _RECORD_ID.sub(" ", text)
    known_lower = {n.lower() for n in names}
    invented = {t for t in _KEBAB.findall(scrubbed)
                if t.lower() not in known_lower and t.lower() not in _PLAIN_HYPHENATED and t.lower() not in allowed.lower()}
    if invented:
        return False, f"names not in the graph: {sorted(invented)}"
    if invented := {d for d in _ISO_DATE.findall(text) if d not in allowed}:
        return False, f"dates not in the answer: {sorted(invented)}"
    firsts = {n.split()[0] for n in names if " " in n}
    lasts = {n.split()[-1] for n in names if " " in n}
    for pair in _BIGRAM.findall(text):
        first, last = pair.split()
        if (first in firsts or last in lasts) and not any(pair in n for n in names):
            return False, f"person name not in the graph: {pair!r}"
    if (not answer.rows or answer.outcome in (NOT_IN_GRAPH, UNKNOWN_ENTITY)) and not _ABSENCE.search(text):
        return False, "claimed a result when the answer holds none"
    return True, "ok"


# --- fallback sentence ---------------------------------------------------------------------

def _ids(ids) -> str:
    return ", ".join(ids) if ids else "no source recorded"


def _who(name, team) -> str:
    return f"{name} ({team})" if team else str(name)


def _current_owner(a: Answer) -> str:
    r = a.rows[0]
    s = f"{_who(r['owner'], r.get('team'))} owns {r['system']}, since {day(r['since'])}"
    if r.get("previous_owner"):
        s += f"; before that {_who(r['previous_owner'], r.get('previous_team'))} until {day(r['previous_until'])}"
    return f"{s}. Sources: {_ids(r['source_ids'])}."


def _owner_at(a: Answer) -> str:
    when = a.args.get("date", "")
    parts = []
    for r in a.rows:
        end = f" until {day(r['to'])}" if r.get("to") else ""
        parts.append(f"{r['owner']} (from {day(r['from'])}{end}; sources: {_ids(r['source_ids'])})")
    return f"On {when} {a.rows[0]['system']} was owned by {', then by '.join(parts)}."


def _history(a: Answer) -> str:
    parts = [f"{r['owner']} from {day(r['from'])} to {day(r['to']) if r.get('to') else 'now'} "
             f"({_ids(r['source_ids'])})" for r in a.rows]
    return f"Ownership of {a.rows[0]['system']}: " + "; ".join(parts) + "."


def _escalation(a: Answer) -> str:
    r = a.rows[0]
    s = f"For {r['system']}, call {_who(r['owner'], r.get('team'))}"
    if r.get("team") and r.get("lead"):
        return f"{s}; the {r['team']} lead is {r['lead']}."
    return f"{s}; no team lead is recorded." if r.get("team") else f"{s}; no team is recorded for them."


def _impact(a: Answer) -> str:
    parts = []
    for r in a.rows:
        owner = f", owner {r['owner']}" if r.get("owner") else ", no owner recorded"
        parts.append(f"{r['system']} ({r['hops']} hop{'s' if r['hops'] != 1 else ''}{owner})")
    n, depth = len(a.rows), a.args.get("depth", 4)
    when = f"As of {a.args['as_of']}, if" if a.args.get("as_of") else "If"
    return (f"{when} {a.args['system']} goes down, {n} system{'s' if n != 1 else ''} depend on it within {depth} "
            f"hop{'s' if depth != 1 else ''}: " + "; ".join(parts) + ".")


def _who_knows(a: Answer) -> str:
    parts = [f"{r['person']} ({r['count']})" for r in a.rows[:6]]
    return f"Work items on {a.args['system']}, most active first: " + ", ".join(parts) + "."


def _decision_trail(a: Answer) -> str:
    parts = []
    for r in a.rows:
        who = f", decided by {r['decided_by']}" if r.get("decided_by") else ""
        parts.append(f"{r['decision']} ({r.get('status') or 'status unknown'}{who})")
    return "Decision trail, oldest first: " + " -> ".join(parts) + f". Sources: {_ids([s.id for s in a.sources])}."


def _claim(r: dict) -> str:
    verb = _VERBS.get(r["predicate"], (r["predicate"].lower(),))[0]
    return " ".join(x for x in (r.get("subject"), verb, r.get("object")) if x)


def _why(a: Answer) -> str:
    parts = []
    for r in a.rows:
        if r["kind"] == "receipt":
            verdict = GATE_OUTCOMES.get(r["outcome"], str(r["outcome"]).lower())
            reasons = f" ({', '.join(r['reasons'])})" if r["reasons"] else ""
            parts.append(f"receipt {r['receipt']} {verdict}{reasons}: {_claim(r)}")
        else:
            parts.append(f"the memory currently holds that {_claim(r)} since {day(r['since'])} "
                         f"({_ids(r['source_ids'])})")
    text = "; ".join(parts)
    return text[:1].upper() + text[1:] + "."


def _what_changed(a: Answer) -> str:
    verbs = {"learned": "learned", "closed": "closed", "refused": "refused"}
    parts = [f"{verbs[r['change']]} {r['subject']} {r['predicate']} {r['object']} ({day(r['at'])})" for r in a.rows[:8]]
    more = f" and {len(a.rows) - 8} more" if len(a.rows) > 8 else ""
    return f"Since {a.args['since']}: " + "; ".join(parts) + more + "."


def _path_between(a: Answer) -> str:
    r = a.rows[0]
    return f"{r['from']} and {r['to']} are {r['hops']} hop{'s' if r['hops'] != 1 else ''} apart: {render_path(a.paths[0])}."


def _recall(a: Answer) -> str:
    parts = []
    for r in a.rows[:3]:
        if r["kind"] == "finding":
            parts.append(f"{r['agent'] or 'an agent'} noted on {day(r['at'])}: \"{r['text']}\"")
        else:
            parts.append(f"{r['from']} handed over to {r['to']} on {day(r['at'])}: \"{r['topic']}\"")
    return "From memory: " + "; ".join(parts) + "."


_VERBS = {"OWNS": ("owns", "is owned by"), "MEMBER_OF": ("is a member of", "has member"),
          "LEADS": ("leads", "is led by"), "WORKED_ON": ("worked on", "was worked on by"),
          "DECIDED": ("decided", "was decided by"), "SUPERSEDES": ("supersedes", "is superseded by"),
          "DEPENDS_ON": ("depends on", "is a dependency of"),
          "CONSTRAINS": ("constrains", "is constrained by")}


def _facts_about(a: Answer) -> str:
    me = a.rows[0]["entity"]
    parts = []
    for r in a.rows[:8]:
        verb = _VERBS[r["predicate"]][0 if r["direction"] == "out" else 1]
        parts.append(f"{me} {verb} {r['other']} (since {day(r['since'])}; {_ids(r['source_ids'])})")
    return "; ".join(parts) + "."


def _write(a: Answer) -> str:
    rid = a.receipts[0].id if a.receipts else "none"
    what = "finding" if a.tool == "remember" else "handoff"
    if a.outcome == REFUSED:
        reasons = ", ".join(a.receipts[0].reasons) if a.receipts and a.receipts[0].reasons else "no reason given"
        return f"Refused: {reasons}. Nothing was stored except receipt {rid}."
    return f"The {what} was {a.outcome} (receipt {rid})."


_ROLE = {"OWNS": "owner", "LEADS": "lead", "MEMBER_OF": "member"}


def _held(r: dict) -> str:
    role = _ROLE[r["relation"]]
    return f"{r['item']} ({role}, ended {day(r['until'])})" if r["state"] == "ended" else f"{r['item']} ({role})"


def _what_leaves(a: Answer) -> str:
    s = a.rows[0]
    held = [r for r in a.rows if r.get("kind") == "held"]
    risk = [r for r in a.rows if r.get("kind") == "at_risk"]
    when = f" as of {s['as_of']}" if s.get("as_of") else ""
    text = f"At depth 1{when}, {s['person']}: " + (", ".join(_held(r) for r in held) or "nothing held")
    if s["depth"] > 1:
        parts = [f"{r['item']} ({r['hops']} hops via {r['decision'] or r['via']}"
                 f"{', only their records' if r.get('only_theirs') else ''}, owner "
                 f"{r['owner'] + (', ' + r['owner_team'] if r['owner_team'] else '') if r['owner'] else 'none'})"
                 for r in risk[:8]]
        more = f" and {len(risk) - 8} more" if len(risk) > 8 else ""
        text += (f". Within {s['depth']} hops, {len(risk)} more at risk: " + "; ".join(parts) + more if risk
                 else f". Within {s['depth']} hops, no decision of theirs governs a system someone else owns")
    return text + "."


def _single_source(a: Answer) -> str:
    s = a.rows[0]
    items = a.rows[1:]
    single = [r for r in items if r["single_source"]]
    shared = [r for r in items if not r["single_source"] and r["item_kind"] == "decision"]
    when = f" as of {s['as_of']}" if s.get("as_of") else ""
    text = (f"{len(single)} item{'s' if len(single) != 1 else ''} rest only on records written by {s['person']}{when}: "
            + "; ".join(f"{r['item']} ({', '.join(r['episodes'][:3])}{', ...' if len(r['episodes']) > 3 else ''})"
                        for r in single))
    if shared:
        text += ". Checked, with records by others: " + "; ".join(f"{r['item']} ({', '.join(r['authors'])})" for r in shared)
    return text + "."


def _without(a: Answer) -> str:
    s = a.rows[0]
    parts = [f"{' and '.join(i['items'])} (through {', '.join(sorted({c['relation'] for c in i['connected_through']}))})"
             for i in a.rows[1:]]
    return (f"Without {s['person']}, nobody in the graph is connected to: " + "; ".join(parts)
            + f". Connected components on a temporary copy, {s['wall_ms']} ms.")


def _bridges(a: Answer) -> str:
    top = [r for r in a.rows if r.get("kind") == "bridge"]
    only = [r for r in a.rows if r.get("kind") == "only_link"]
    text = "Top bridges: " + "; ".join(f"{r['person']} (score {r['score']}, links {', '.join(r['teams'])})" for r in top)
    if only:
        text += ". Linked by one person only: " + "; ".join(f"{r['teams'][0]} and {r['teams'][1]} by {r['person']}"
                                                            for r in only[:4])
    return text + ("." if a.rows[0].get("exact") else ". Scores are estimates on this engine.")


def _successors(a: Answer) -> str:
    parts = []
    for r in a.rows:
        if not r["candidates"]:
            parts.append(f"{r['item']}: {r['note']}")
            continue
        names = [f"{c['person']} ({c['on_item']} on it)" if c["on_item"] else
                 f"{c['person']} ({c['nearby']} on {', '.join(c['via'])})" for c in r["candidates"]]
        parts.append(f"{r['item']}: " + ", ".join(names))
    return "Candidates, not decisions. " + "; ".join(parts) + "."


def _handoff_plan(a: Answer) -> str:
    s = a.rows[0]
    lines = [f"{r['item']}: {r['action']}" for r in a.rows[1:] if r.get("kind") == "line"]
    asks = [f"({r['n']}) {r['question']}" for r in a.rows[1:] if r.get("kind") == "question"]
    when = f" before {s['last_day']}" if s.get("last_day") else ""
    done = f" {s['proposed']} written as proposed handoffs." if s.get("proposed") else ""
    text = f"{s['lines']} handoff line{'s' if s['lines'] != 1 else ''} for {s['person']}{when}. " + " ".join(lines) + done
    return text + (f" Questions to ask {s['person']}: " + " ".join(asks) if asks else "")


def _promises(a: Answer) -> str:
    found = [r for r in a.rows if r.get("kind") == "promise"]
    return (f"{len(found)} written promise{'s' if len(found) != 1 else ''}: "
            + "; ".join(f"{r['episode']} ({day(r['at'])}, {r['channel']}): \"{r['line']}\"" for r in found) + ".")


TEMPLATES = {"current_owner": _current_owner, "owner_at": _owner_at, "history": _history,
             "escalation_path": _escalation, "impact": _impact, "who_knows": _who_knows,
             "decision_trail": _decision_trail, "why": _why, "what_changed": _what_changed,
             "path_between": _path_between, "recall": _recall, "facts_about": _facts_about,
             "remember": _write, "handoff": _write,
             "what_leaves": _what_leaves, "single_source": _single_source, "without": _without, "bridges": _bridges,
             "successors": _successors, "handoff_plan": _handoff_plan, "promises": _promises}


def template_sentence(answer: Answer) -> str:
    """A sentence built from the rows alone; the fallback when the model's sentence fails the check."""
    if answer.outcome in (UNKNOWN_ENTITY, NOT_IN_GRAPH) or not answer.rows and answer.outcome not in WRITE_OUTCOMES:
        return answer.note or "The graph holds no rows for this question."
    return TEMPLATES[answer.tool](answer)
