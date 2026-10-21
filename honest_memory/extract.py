"""Rule extractor: episodes in, candidate facts out. A proposal is not a decision; the gate decides.

It matches the phrasings that corpus/templates.py and the corpus generator write, and nothing else, so an
episode in any other words yields nothing. It proposes eagerly (a phone number in a ticket becomes a
HAS_PHONE candidate) because refusing is the gate's job.

How sources are chosen, because the gate acts on them:
  * a statement that stands on its own cites its own episode, plus the episodes it refers to that name
    both ends of the fact; its valid_at is the time of the earliest such episode, else its own;
  * a statement that only repeats what a document says ("It lists X as the owner of Y") cites the document
    alone, with the statement kept as claim_episode;
  * hearsay ("Heads up: ...", "I heard that ...") and a repeat without a document cite nothing:
    source_ids=[] and claim_episode set;
  * a "last day" record proposes ends, not facts: each line "X no longer owns Y" becomes a candidate with
    attrs {"record": "End"}, which ingest sends to the gate's CLOSE path (submit_end), dated at the record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .corpus import templates as T
from .gate import Candidate
from .names import NameIndex, episode_mentions, is_lookalike
from .store import epoch

SYSTEM = r"[a-z][a-z0-9-]*"

CATALOG_LINE = re.compile(rf"^- (?P<s>{SYSTEM}): (?P<purpose>.+?)\. Owner: (?P<p>.+?) \((?P<t>[A-Za-z]+)\)\.$", re.M)
ROSTER_LINE = re.compile(r"^- (?P<p>[^:]+): (?P<role>.+?), (?P<t>[A-Za-z]+) team\.$", re.M)
CHARTER_LEAD = re.compile(r"^Lead: (?P<p>.+?) \((?P<t>[A-Za-z]+)\)\.$", re.M)
CHARTER_GOAL = re.compile(r"^Goal: (?P<goal>.+?)\.$", re.M)
OWNER_LINE = re.compile(r"^Owner: (?P<p>.+?) \((?P<t>[A-Za-z]+)\)\.$", re.M)
ADR_AUTHOR = re.compile(r"^Author: (?P<p>.+)$", re.M)
ADR_COAUTHOR = re.compile(r"^Co-author: (?P<p>.+)$", re.M)
ADR_CONSTRAINS = re.compile(r"^Constrains: (?P<systems>.+)\.$", re.M)
TITLE_PURPOSE = re.compile(r"^[^:\n]+: [^ \n]+ \((?P<purpose>.+?)\)\.$", re.M)
KIND_LINE = re.compile(r"^Kind: (?P<kind>.+?)\.$", re.M)
LAST_DAY_LINE = re.compile(r"^Last day: (?P<day>\d{4}-\d{2}-\d{2})\.$", re.M)
END_MEMBER = re.compile(r"^- (?P<p>.+?) is no longer a member of the (?P<t>[A-Za-z]+) team\.$", re.M)
END_OWNS = re.compile(rf"^- (?P<p>.+?) no longer owns (?P<s>{SYSTEM})\.$", re.M)
END_LEADS = re.compile(r"^- (?P<p>.+?) no longer leads (?P<x>.+?)\.$", re.M)
HANDOFF_CONSTRAINS = re.compile(rf"^- (?P<d>ADR-\d{{4}}) constrains (?P<s>{SYSTEM}): ", re.M)
HANDOFF_DEPENDS = re.compile(rf"^- (?P<a>{SYSTEM}) depends on (?P<b>{SYSTEM}): ", re.M)
ADR_SUPERSEDES = re.compile(r"^Supersedes: (?P<old>ADR-\d{4})\.$", re.M)
ADR_TRANSFER = re.compile(rf"Ownership of (?P<s>{SYSTEM}) transfers from (?P<a>.+?) \((?P<ta>[A-Za-z]+)\) "
                          r"to (?P<b>.+?) \((?P<tb>[A-Za-z]+)\) effective")
PM_DECISION = re.compile(r"^Decision: (?P<new>ADR-\d{4}) supersedes (?P<old>ADR-\d{4})\.", re.M)
DEPENDS = re.compile(rf"(?<![\w-])(?P<a>{SYSTEM}) depends on (?P<b>{SYSTEM})(?![\w-])")

TICKET_WORK = re.compile(rf"\bArea: (?P<c>.+?) in (?P<s>{SYSTEM})\. I traced it to ")
FIRST_PERSON_OWNER = re.compile(rf"^Per (?P<adr>ADR-\d{{4}}), I am now the owner of (?P<s>{SYSTEM})\.")
HEARSAY_OWNED = re.compile(rf"^Heads up: (?P<s>{SYSTEM}) is owned by (?P<p>[A-Za-z][\w ]*?) now\b")
HEARSAY_HEARD = re.compile(rf"^(?:I heard|Rumou?r has it|Word is)(?: that)? (?P<p>.+?) (?:now )?owns (?P<s>{SYSTEM})\b")
RELAYED_OWNER = re.compile(rf"\bIt lists (?P<p>.+?) as the owner of (?P<s>{SYSTEM})\b")
ASSIGNED_OWNER = re.compile(rf"Assigning it to (?P<p>.+?), who owns (?P<s>{SYSTEM})\.")
HANDING_OVER = re.compile(r"^Handing this incident to (?P<p>.+?), (?P<t>[A-Za-z]+) on-call\.")
HANDOFF_TITLE = re.compile(r"^Handoff: (?P<topic>.+?) \(")
CUSTOMER_CONTACT = re.compile(r"callback number, (?P<phone>.+?), and a mailing address: (?P<addr>.+?)\.?$")


def _from_template(template: str) -> re.Pattern:
    """A chat template such as "Finished the {c} cleanup in {s}, ..." as a regex with the system in group s."""
    rx = re.escape(template)
    for name, sub in (("c", r".+?"), ("s", rf"(?P<s>{SYSTEM})"), ("cause", r".+?")):
        rx = rx.replace(re.escape("{" + name + "}"), sub)
    return re.compile(rx + "$")


@dataclass(frozen=True)
class EntityDecl:
    """An entity a document brings into existence: a node to merge before facts can name it."""
    label: str
    key: str
    props: dict = field(default_factory=dict)


class RuleExtractor:
    """Stateful over one replay: it remembers the episodes it has seen (to read what a reference says), the
    entity names documents declared and who belongs to which team."""

    def __init__(self) -> None:
        self.known = NameIndex()
        self.team_of: dict[str, str] = {}
        self.hydrated = False  # set by ingest once `known` has been filled from a graph that already holds entities
        self._seen: dict[str, dict] = {}
        self._work_chat = [_from_template(t) for t in T.CHAT_WORK]

    # ---- entities (documents only) ----------------------------------------------------------------------------
    def entities(self, ep: dict) -> list[EntityDecl]:
        first: dict[tuple[str, str], EntityDecl] = {}
        for d in self._declare(ep) if ep["channel"] == "doc" else ():
            first.setdefault((d.label, d.key), d)
        decls = []
        for d in first.values():
            # A name that only looks like a known one is not a new entity: it would be a second "Sana Petrov".
            # It is still proposed in facts; the gate refuses those as unknown_entity.
            if not is_lookalike(d.key, self.known.names(d.label)):
                self.known.add(d.label, d.key)
                decls.append(d)
        return decls

    def _declare(self, ep: dict):
        kind, text = _kind(ep), ep["text"]
        if kind == "catalog":
            for m in CATALOG_LINE.finditer(text):
                yield EntityDecl("System", m["s"], _props(purpose=m["purpose"]))
                yield EntityDecl("Person", m["p"])
                yield EntityDecl("Team", m["t"])
        elif kind == "roster":
            for m in ROSTER_LINE.finditer(text):
                self.team_of[m["p"]] = m["t"]
                yield EntityDecl("Person", m["p"])
                yield EntityDecl("Team", m["t"])
        elif kind == "charter" and (lead := CHARTER_LEAD.search(text)):
            goal = CHARTER_GOAL.search(text)
            yield EntityDecl("Project", ep["title"].split(": ", 1)[1], _props(goal=goal and goal["goal"]))
            yield EntityDecl("Person", lead["p"])
            yield EntityDecl("Team", lead["t"])
        elif kind == "adr":
            yield EntityDecl("Decision", ep["id"], {"title": _adr_title(ep), "status": "accepted"})
            for m in (ADR_AUTHOR.search(text), ADR_COAUTHOR.search(text)):
                if m:
                    yield EntityDecl("Person", m["p"])
        elif kind in ("integration", "runbook") and (kind_line := KIND_LINE.search(text)):
            # an integration or a runbook that says what it is becomes a System with a `kind`
            purpose = TITLE_PURPOSE.search(text)
            yield EntityDecl("System", ep["title"].split(": ", 1)[1],
                             _props(kind=_system_kind(kind_line["kind"]), purpose=purpose and purpose["purpose"]))
            if owner := OWNER_LINE.search(text):
                yield EntityDecl("Person", owner["p"])
                yield EntityDecl("Team", owner["t"])
        elif kind == "resignation" and (last := LAST_DAY_LINE.search(text)):
            # notice is a property of the person, read by the departure tools; ends come with the last-day record
            yield EntityDecl("Person", ep["title"].split(": ", 1)[1],
                             {"notice_at": epoch(ep["ts"]), "last_day": last["day"], "notice_source": ep["id"]})

    # ---- candidates ----------------------------------------------------------------------------------------------
    def extract(self, ep: dict) -> list[Candidate]:
        self.entities(ep)
        cands = self._from_document(ep) if ep["channel"] == "doc" else self._from_statement(ep)
        self._seen[ep["id"]] = ep
        return cands

    def _cand(self, ep, subject, predicate, obj, sources, valid_at=None, attrs=None) -> Candidate:
        return Candidate(subject, predicate, obj, valid_at or ep["ts"], list(sources), "rules", ep["id"], attrs or {})

    def _supporting(self, ep: dict, *names: str) -> list[str]:
        """The episodes `ep` refers to that name every one of `names`."""
        return [r for r in ep["refs"] if r in self._seen and episode_mentions(self._seen[r], *names)]

    def _standing(self, ep, subject, predicate, obj, attrs=None) -> Candidate:
        """A statement that stands on its own: cites itself and the references that back it."""
        support = self._supporting(ep, subject, obj)
        valid_at = min(self._seen[r]["ts"] for r in support) if support else ep["ts"]
        return self._cand(ep, subject, predicate, obj, [ep["id"], *support], valid_at, attrs)

    def _relayed(self, ep, subject, predicate, obj) -> Candidate:
        """A statement that repeats a document: cites the document alone, or nothing when it names none."""
        support = self._supporting(ep, subject, obj)
        valid_at = min(self._seen[r]["ts"] for r in support) if support else ep["ts"]
        return self._cand(ep, subject, predicate, obj, support, valid_at)

    def _hearsay(self, ep, subject, predicate, obj) -> Candidate:
        return self._cand(ep, subject, predicate, obj, [])

    def _from_document(self, ep: dict) -> list[Candidate]:
        kind, text = _kind(ep), ep["text"]
        out: list[Candidate] = []
        if kind == "catalog":
            out += [self._standing(ep, m["p"], "OWNS", m["s"]) for m in CATALOG_LINE.finditer(text)]
        elif kind == "roster":
            for m in ROSTER_LINE.finditer(text):
                out.append(self._standing(ep, m["p"], "MEMBER_OF", m["t"], {"role": m["role"]}))
                if m["role"] == f"{m['t']} Lead":
                    out.append(self._standing(ep, m["p"], "LEADS", m["t"], {"role": m["role"]}))
        elif kind == "charter" and (lead := CHARTER_LEAD.search(text)):
            out.append(self._standing(ep, lead["p"], "LEADS", ep["title"].split(": ", 1)[1]))
        elif kind in ("runbook", "integration") and (owner := OWNER_LINE.search(text)):
            out.append(self._standing(ep, owner["p"], "OWNS", ep["title"].split(": ", 1)[1]))
        elif kind == "adr":
            for m in (ADR_AUTHOR.search(text), ADR_COAUTHOR.search(text)):
                if m:
                    out.append(self._standing(ep, m["p"], "DECIDED", ep["id"], {"title": _adr_title(ep)}))
            if m := ADR_CONSTRAINS.search(text):
                systems = self.known.names("System")
                out += [self._standing(ep, ep["id"], "CONSTRAINS", x.strip()) for x in m["systems"].split(",")
                        if x.strip() in systems]
            if old := ADR_SUPERSEDES.search(text):
                out.append(self._standing(ep, ep["id"], "SUPERSEDES", old["old"]))
            if m := ADR_TRANSFER.search(text):
                out.append(self._standing(ep, m["b"], "OWNS", m["s"]))
        elif kind == "postmortem" and (m := PM_DECISION.search(text)):
            out.append(self._standing(ep, m["new"], "SUPERSEDES", m["old"]))
        elif kind == "handoff":
            # handoff notes and their review restate facts the graph holds; the gate CONFIRMs them with a new source
            out += [self._standing(ep, m["d"], "CONSTRAINS", m["s"]) for m in HANDOFF_CONSTRAINS.finditer(text)]
            out += [self._standing(ep, m["a"], "DEPENDS_ON", m["b"]) for m in HANDOFF_DEPENDS.finditer(text)]
        elif kind == "last_day":
            ends = ([(m["p"], "MEMBER_OF", m["t"]) for m in END_MEMBER.finditer(text)]
                    + [(m["p"], "OWNS", m["s"]) for m in END_OWNS.finditer(text)]
                    + [(m["p"], "LEADS", m["x"]) for m in END_LEADS.finditer(text)])
            out += [self._cand(ep, p, pred, o, [ep["id"]], attrs={"record": "End"}) for p, pred, o in ends]
        if kind in ("overview", "adr", "integration"):
            systems = self.known.names("System")
            out += [self._standing(ep, m["a"], "DEPENDS_ON", m["b"]) for m in DEPENDS.finditer(text)
                    if m["a"] in systems and m["b"] in systems]
        return out

    def _from_statement(self, ep: dict) -> list[Candidate]:
        text, who = ep["text"], ep["author"]
        out: list[Candidate] = []
        work = [m for rx in self._work_chat if (m := rx.match(text))]
        if ep["channel"] == "ticket" and (m := TICKET_WORK.search(text)):
            work.append(m)
        out += [self._cand(ep, who, "WORKED_ON", m["s"], [ep["id"]]) for m in work]
        if m := FIRST_PERSON_OWNER.match(text):
            out.append(self._standing(ep, who, "OWNS", m["s"]))
        if m := ASSIGNED_OWNER.search(text):
            out.append(self._standing(ep, m["p"], "OWNS", m["s"]))
        if m := RELAYED_OWNER.search(text):
            out.append(self._relayed(ep, m["p"], "OWNS", m["s"]))
        if m := HEARSAY_OWNED.match(text) or HEARSAY_HEARD.match(text):
            out.append(self._hearsay(ep, m["p"], "OWNS", m["s"]))
        if m := CUSTOMER_CONTACT.search(text):
            who_is = f"Customer in {ep['id']}"
            out += [self._cand(ep, who_is, "HAS_PHONE", m["phone"], [ep["id"]]),
                    self._cand(ep, who_is, "HAS_ADDRESS", m["addr"], [ep["id"]])]
        if m := HANDING_OVER.match(text):
            out.append(self._handoff(ep, m))
        return out

    def _handoff(self, ep: dict, m: re.Match) -> Candidate:
        title = HANDOFF_TITLE.match(ep["title"])
        attrs = {"record": "Handoff", "ticket": ep["id"], "topic": title["topic"] if title else ep["title"],
                 "to_team": m["t"]}
        if ep["author"] in self.team_of:
            attrs["from_team"] = self.team_of[ep["author"]]
        return self._cand(ep, ep["author"], "HANDOFF_TO", m["p"], [ep["id"], *ep["refs"]], attrs=attrs)


def _props(**props) -> dict:
    return {k: v for k, v in props.items() if v is not None}


def _adr_title(ep: dict) -> str:
    return ep["title"].split(": ", 1)[1]


def _system_kind(text: str) -> str:
    """'customer integration' -> 'integration', 'on-call runbook' -> 'runbook', else 'service'."""
    text = text.lower()
    return "integration" if "integration" in text else "runbook" if "runbook" in text else "service"


def _kind(ep: dict) -> str | None:
    title = ep["title"]
    if title == "Service catalog":
        return "catalog"
    if title == "Team roster":
        return "roster"
    if title == "Architecture overview":
        return "overview"
    if title.startswith("Project charter: "):
        return "charter"
    if title.startswith("Runbook: "):
        return "runbook"
    if title.startswith("Postmortem: "):
        return "postmortem"
    if title.startswith("Integration: "):
        return "integration"
    if title.startswith("Resignation: "):
        return "resignation"
    if title.startswith("Last day: "):
        return "last_day"
    if title.startswith(("Handoff notes: ", "Handoff review: ")):
        return "handoff"
    if re.match(r"ADR-\d{4}: ", title):
        return "adr"
    return None
