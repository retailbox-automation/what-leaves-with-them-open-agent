"""Deterministic question -> (tool, args) router: regex and keyword rules, no model, no database.

Used when no LLM is configured, so the live demo answers with nothing but FalkorDB behind it.
Rules run in order and the first match wins. Names are captured as written; the tool resolves them
against the graph, so a misspelt or invented name comes back as unknown_entity, not as a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

DATE = r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?"
NAME = r"[\w][\w.-]*"                       # a system, ADR or ticket id
RECORD = r"(?:ADR|TCK|RB|PM|DOC)-\d{2,4}|msg-\d{4}"
ADR = r"ADR-\d{2,4}"
OWNS = r"(?:is the (?:current )?owner of|owns|owned|is responsible for|runs|maintains)"


@dataclass(frozen=True)
class Route:
    tool: str
    args: dict
    rule: str


def _clean(text: str) -> str:
    """Drop a trailing parenthetical, quotes and sentence punctuation from a captured name."""
    text = re.sub(r"\s*\(.*?\)", "", text)
    return re.sub(r"\s+", " ", text).strip(" ?!.,;:\"'`")


def _strip_article(text: str) -> str:
    return re.sub(r"^(?:the|a|an)\s+", "", _clean(text), flags=re.I)


def _depth(q: str) -> dict:
    m = re.search(r"\b(\d)\s*[- ]?hops?\b|\bdepth\s*(?:of\s*)?(\d)\b", q, re.I)
    if m:
        return {"depth": max(1, min(4, int(m.group(1) or m.group(2))))}
    return {"depth": 1} if re.search(r"\b(directly|direct|immediate(?:ly)?)\b", q, re.I) else {}


def _as_of(q: str) -> dict:
    m = re.search(rf"\bas of (?P<date>{DATE})", q, re.I)
    return {"as_of": m["date"]} if m else {}


PII_PREDICATES = (
    (r"phone|telephone|mobile number|cell number", "HAS_PHONE"),
    (r"e-?mail", "HAS_EMAIL"),
    (r"(?:mailing |street |home |postal )?address", "HAS_ADDRESS"),
    (r"card number|credit card|debit card", "HAS_CARD"),
    (r"birth ?date|date of birth|birthday", "HAS_BIRTHDATE"),
)


def _pii(kind: str, subject: str) -> tuple[str, dict]:
    predicate = next(p for pat, p in PII_PREDICATES if re.fullmatch(pat, kind, re.I))
    return "why", {"subject": subject, "predicate": predicate}


# A full name as written, "Oskar Alvarez": capitalised words, matched case-sensitively inside the case-blind rules.
PERSON = r"(?P<p>(?-i:[A-Z][\w'.-]*(?: [A-Z][\w'.-]*){1,3}))"


def _leaves(m: re.Match, default_depth: int | None = None) -> tuple[str, dict]:
    depth = _depth(m.string).get("depth") or default_depth
    return "what_leaves", {"person": _clean(m["p"]), **({"depth": depth} if depth else {}), **_as_of(m.string)}


def _promise(m: re.Match) -> tuple[str, dict]:
    to = re.match(r"\s*(?P<to>[A-Z][\w&'-]*(?: [A-Z][\w&'-]*)*)", m["rest"] or "")
    since = re.search(rf"\b(?:since|after) (?P<d>{DATE})", m.string)
    return "promises", {"person": _clean(m["p"]), **({"to": to["to"]} if to else {}), **({"since": since["d"]} if since else {})}


def _stale(m: re.Match) -> tuple[str, dict]:
    """A runbook or catalog entry versus the present: the history shows both."""
    system = m["sys"] if m["sys"] and m["sys"].lower() not in ("the", "this", "that", "a", "an") else None
    if not system and (found := re.search(rf"\b(?:owns|owner of|owned) ({NAME})", m.string, re.I)):
        system = found.group(1)
    return "history", {"system": _clean(system or "")}


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern
    build: Callable[[re.Match], tuple[str, dict]]


def _rule(name: str, pattern: str, build) -> Rule:
    return Rule(name, re.compile(pattern, re.I), build)


RULES: tuple[Rule, ...] = (
    # personal data: ask why nothing is held (the gate refused to store it)
    _rule("personal_data", rf"\b(?P<kind>{'|'.join(p for p, _ in PII_PREDICATES)})\b.*?\b(?:of|for)\b.*?(?P<rec>{RECORD})",
          lambda m: _pii(m["kind"], m["rec"])),
    _rule("personal_data_person", rf"\b(?:what(?:'s| is)|give me|tell me) (?:the |their |his |her )?"
          rf"(?P<kind>{'|'.join(p for p, _ in PII_PREDICATES)}) (?:of|for) (?P<who>.+?)\??$",
          lambda m: _pii(m["kind"], _strip_article(re.sub(r"^customer (?:in|on|from)\s+", "", m["who"], flags=re.I)))),
    # N5: a departure. Names are full person names; the tools resolve them.
    _rule("promises", rf"\b(?:did|has|have|does) {PERSON} (?:ever )?(?:promise[ds]?|commit(?:ted)?(?: to)?|agree(?:d)? to|"
          rf"guarantee[ds]?)\b(?P<rest>.*)$|\bwhat did {PERSON.replace('?P<p>', '?P<p2>')} promise\b(?P<rest2>.*)$",
          lambda m: _promise(m) if m["p"] else _promise(re.match(r"(?P<p>.+?)\|(?P<rest>.*)", f"{m['p2']}|{m['rest2']}"))),
    _rule("handoff_plan", rf"\b(?:make |build |draft |write )?(?:a |the )?handoff plan (?:for|of) (?P<p>.+?)"
          rf"(?: (?:before|by|until) (?P<date>{DATE}))?[.?!]*$|\bplan (?:the )?handoff (?:for|of) (?P<p2>.+?)[.?!]*$",
          lambda m: ("handoff_plan", {"person": _clean(m["p"] or m["p2"]), **({"last_day": m["date"]} if m["date"] else {})})),
    _rule("successors", r"\bwho (?:could|can|should|might|would) (?:take over|step in) (?:from|for) (?P<p>.+?)\??$|"
          r"\bsuccessors? (?:to|for|of) (?P<p2>.+?)\??$|\bwho could replace (?P<p3>.+?)\??$",
          lambda m: ("successors", {"person": _clean(m["p"] or m["p2"] or m["p3"])})),
    _rule("single_source", rf"\b(?:rests?|depends?|stands?) only on (?:the )?(?:records?|sources?|documents?) (?:written )?"
          rf"by {PERSON}|\bwhat does only {PERSON.replace('?P<p>', '?P<p2>')} know\b|"
          rf"\bsingle[- ]source\b.*?\b(?:for|of|by|on) {PERSON.replace('?P<p>', '?P<p3>')}",
          lambda m: ("single_source", {"person": _clean(m["p"] or m["p2"] or m["p3"]), **_as_of(m.string)})),
    _rule("without", rf"\b(?:nobody|no one|nothing) (?:be |would be |is )?connected to without (?P<p>.+?)\??$|"
          rf"\bwithout {PERSON.replace('?P<p>', '?P<p2>')},? (?:what|who)\b|"
          rf"\bif {PERSON.replace('?P<p>', '?P<p3>')} (?:leaves|left|were gone|was gone|is gone|quits|goes)\b.*"
          rf"\b(?:islands?|cut off|disconnected|unconnected|orphaned)\b",
          lambda m: ("without", {"person": _clean(m["p"] or m["p2"] or m["p3"])})),
    _rule("what_leaves", rf"\bwhat (?:leaves|goes|walks out|would leave|would we lose|do we lose|does the company lose) "
          rf"(?:with|if) {PERSON}",
          _leaves),
    _rule("what_leaves_held", rf"\bwhat does {PERSON} own,? lead,? (?:or|and) belong to\b", lambda m: _leaves(m, 1)),
    _rule("what_leaves_decisions", rf"\bwhich decisions (?:of|by|made by) {PERSON} (?:govern|constrain|affect)\b",
          lambda m: _leaves(m, 2)),
    _rule("bridges", r"\b(?:strongest|biggest|top|main|best)? ?bridges? between teams\b|\bwho (?:is|are) the "
          r"(?:only )?(?:person|people|ones?) link(?:ing|s) (?P<a>\w+) and (?P<b>\w+)|\bwho links (?:the )?\w+ and \w+",
          lambda m: ("bridges", {**({"top": int(t[1])} if (t := re.search(r"\btop (\d+)", m.string)) else {})})),
    # is the claim true / why does the memory think so
    _rule("claim_check", rf"^(?:is|are|does|did) (?P<s>.+?) (?:the )?(?:current )?(?:owner of|owns?) (?P<o>{NAME})\??$",
          lambda m: ("why", {"subject": _strip_article(m["s"]), "predicate": "OWNS", "object": _clean(m["o"])})),
    _rule("why_belief", rf"\bwhy (?:do we|does the memory|does it|do you) (?:believe|think|know|say)\b (?:that )?(?P<s>.+?) "
          rf"(?:is the owner of|owns) (?P<o>{NAME})\??$",
          lambda m: ("why", {"subject": _clean(m["s"]), "predicate": "OWNS", "object": _clean(m["o"])})),
    # stale document check: what a runbook/catalog says versus what is current
    _rule("stale_doc", rf"\b(?:according to|per|the)\b.*?(?:(?P<sys>{NAME}) )?(?:runbook|catalogue|catalog|doc|document|wiki)\b.*"
          rf"\b(?:who owns|owner|owned)\b.*\b(?:current|up to date|stale|still|outdated|valid)\b",
          _stale),
    # time
    _rule("owner_at", rf"\b(?:who|which (?:person|engineer))\b.*?\b(?:owned|was the owner of|had|owns)\b (?P<sys>{NAME}) "
          rf"(?:on|at|as of|in|during|by|around) (?P<date>{DATE})",
          lambda m: ("owner_at", {"system": _clean(m["sys"]), "date": m["date"]})),
    _rule("owner_at_2", rf"\bowner of (?P<sys>{NAME}) (?:on|at|as of|in|during|by) (?P<date>{DATE})",
          lambda m: ("owner_at", {"system": _clean(m["sys"]), "date": m["date"]})),
    _rule("adrs_since", rf"\b(?:which|what|list)\b.*\b(?:adrs?|decisions?)\b.*\b(?:since|after)\b (?P<date>{DATE})",
          lambda m: ("what_changed", {"since": m["date"], "predicate": "DECIDED"})),
    _rule("changed_since", rf"\b(?:what|anything|which)\b.*\b(?:changed?|new|learned|closed|moved)\b.*\b(?:since|after)\b (?P<date>{DATE})",
          lambda m: ("what_changed", {"since": m["date"], **({"predicate": "OWNS"} if re.search(r"owner", m.string, re.I) else {})})),
    _rule("changes_since", rf"\bchanges?\b.*\b(?:since|after)\b (?P<date>{DATE})",
          lambda m: ("what_changed", {"since": m["date"], **({"predicate": "OWNS"} if re.search(r"owner", m.string, re.I) else {})})),
    # decisions
    _rule("who_decided", rf"\bwho (?:decided|approved|accepted|authored|wrote|made)\b.*?(?P<d>{ADR})",
          lambda m: ("facts_about", {"entity": m["d"], "predicate": "DECIDED"})),
    _rule("decision_trail", rf"\b(?:which decision|what|which adr)\b.*\b(?:replaced|superseded|replaces|supersedes|succeeded)\b.*?(?P<d>{ADR})|"
          rf"\b(?:supersession|history|trail|evolution|lineage)\b.*?(?P<d2>{ADR})|\bhow did (?P<d3>{ADR})\b|"
          rf"\b(?P<d4>{ADR})\b.*\b(?:replaced|superseded|evolve|evolved)\b",
          lambda m: ("decision_trail", {"decision": m["d"] or m["d2"] or m["d3"] or m["d4"]})),
    # provenance
    _rule("provenance_team", r"\bwhere\b.*\brecorded\b.*\bwhich team (?P<p>.+?) (?:is|was) (?:in|on|a member)\b",
          lambda m: ("facts_about", {"entity": _clean(m["p"]), "predicate": "MEMBER_OF"})),
    _rule("provenance_before", rf"\bwhere\b.*\b(?:was|is)\b (?P<sys>{NAME}) ownership recorded\b.*\b(?:before|previously|earlier|originally|prior)\b",
          lambda m: ("history", {"system": _clean(m["sys"])})),
    _rule("provenance_past_owner", rf"\bwhere\b.*\brecorded\b.*\bthat (?P<s>.+?) (?:owned|used to own|was the owner of) (?P<o>{NAME})",
          lambda m: ("history", {"system": _clean(m["o"])})),
    _rule("provenance_owner", rf"\bwhere\b.*\brecorded\b.*\bthat (?P<s>.+?) {OWNS} (?P<o>{NAME})",
          lambda m: ("current_owner", {"system": _clean(m["o"])})),
    _rule("provenance_ownership", rf"\bwhere\b.*\b(?P<sys>{NAME}) ownership recorded\b",
          lambda m: ("current_owner", {"system": _clean(m["sys"])})),
    _rule("independent_sources", rf"\bhow many\b.*\bsources?\b.*\b(?P<s>.+?) {OWNS} (?P<o>{NAME})",
          lambda m: ("current_owner", {"system": _clean(m["o"])})),
    # people and teams
    _rule("team_of", r"\b(?:which|what) team (?:is|was|does) (?P<p>.+?) (?:in|on|part of|a member of|belong to|work (?:in|on)|work for)\b",
          lambda m: ("facts_about", {"entity": _clean(m["p"]), "predicate": "MEMBER_OF"})),
    _rule("team_members", r"\bwho (?:is|are) (?:in|on|part of) (?:the )?(?P<t>.+?)(?: team)?\??$",
          lambda m: ("facts_about", {"entity": _strip_article(m["t"]), "predicate": "MEMBER_OF"})),
    _rule("leads", r"\bwho leads (?:the )?(?:project |team |group )?(?P<x>.+?)\??$",
          lambda m: ("facts_about", {"entity": _strip_article(m["x"]), "predicate": "LEADS"})),
    # who to call, what breaks, who knows
    _rule("escalation", rf"\bwho (?:should i|do i|to|can i|must i)\b.*\b(?:call|page|contact|escalate|wake|ping)\b.*?\b(?:about|for|on|to)\b (?P<sys>{NAME})|"
          rf"\bescalation (?:path|contact)\b.*?\b(?:for|of|on)\b (?P<sys2>{NAME})|\bwhom to call\b.*?\b(?:about|for)\b (?P<sys3>{NAME})",
          lambda m: ("escalation_path", {"system": _clean(m["sys"] or m["sys2"] or m["sys3"])})),
    _rule("impact", rf"\bwhat (?:breaks|fails|is affected|gets affected|would break|will break|stops)\b.*\bif (?P<sys>{NAME})\b|"
          rf"\bwhat (?:depends on|relies on|sits (?:upstream|downstream) of|uses) (?P<sys2>{NAME})|"
          rf"\b(?:impact|blast radius|dependents?|downstream) (?:of|for|on) (?P<sys3>{NAME})|"
          rf"\bwhat (?:is|are) (?:the )?(?:systems? )?(?:affected|impacted) (?:by|if) (?P<sys4>{NAME})",
          lambda m: ("impact", {"system": _clean(m["sys"] or m["sys2"] or m["sys3"] or m["sys4"]), **_depth(m.string), **_as_of(m.string)})),
    _rule("who_knows", rf"\bwho (?:knows|has worked on|worked on|is the expert (?:on|in)|are the experts (?:on|in))\b(?: the most)?(?: about| on| in| with)? (?P<sys>{NAME})|"
          rf"\brank the people who worked on (?P<sys2>{NAME})|\bwho should i ask about (?P<sys3>{NAME})",
          lambda m: ("who_knows", {"system": _clean(m["sys"] or m["sys2"] or m["sys3"])})),
    # connections
    _rule("path_between", r"\bhow (?:is|are) (?P<a>.+?) (?:connected|related|linked) (?:to|with) (?P<b>.+?)\??$|"
          r"\b(?:path|connection|route|link)s? (?:between|from) (?P<a2>.+?) (?:and|to) (?P<b2>.+?)\??$|"
          r"\bhow are (?P<a3>.+?) and (?P<b3>.+?) (?:connected|related|linked)\??$",
          lambda m: ("path_between", {"a": _strip_article(m["a"] or m["a2"] or m["a3"]), "b": _strip_article(m["b"] or m["b2"] or m["b3"])})),
    # memory of agents
    _rule("handoff_received", r"\bwho (?:received|got|took|took over|is handling|was handed|picked up)\b.*\b(?:from|by) (?P<t>[\w -]+?)\??$",
          lambda m: ("recall", {"entity": _strip_article(m["t"])})),
    _rule("recall", r"\b(?:what|anything)\b.*\b(?:did|have|has|do) (?:we|you|agents?|anyone|support|ops|they)\b.*\b(?:conclude|learn|learned|note|noted|remember|record|recorded|find|found)\b.*?\b(?:about|on|regarding) (?P<e>.+?)\??$|"
          r"\brecall (?P<e2>.+?)\??$|\b(?:earlier|previous|prior|past) (?:findings?|notes?|handoffs?|sessions?)\b.*?\b(?:about|on|for) (?P<e3>.+?)\??$|"
          r"\bwhat do we remember about (?P<e4>.+?)\??$|\bhandoffs? (?:about|for|from|to) (?P<e5>.+?)\??$",
          lambda m: ("recall", {"entity": _strip_article(m["e"] or m["e2"] or m["e3"] or m["e4"] or m["e5"])})),
    # current owner
    _rule("current_owner", rf"\bwho (?:currently |now |today )?(?:owns|is the owner of|is responsible for|runs|maintains|has)\b (?P<sys>{NAME})|"
          rf"\b(?:current |present )?owner of (?P<sys2>{NAME})|\bwho(?:'s| is) (?:the )?owner (?:of )?(?P<sys3>{NAME})|"
          rf"\bwho owns (?P<sys4>{NAME})",
          lambda m: ("current_owner", {"system": _clean(m["sys"] or m["sys2"] or m["sys3"] or m["sys4"])})),
    # everything about one thing
    _rule("facts_about", r"\b(?:what do we know about|tell me about|facts about|describe|what is known about) (?P<e>.+?)\??$",
          lambda m: ("facts_about", {"entity": _strip_article(m["e"])})),
)


def route(question: str) -> Route | None:
    """The first rule that matches the question, or None when no tool fits."""
    q = " ".join(question.split())
    for rule in RULES:
        m = rule.pattern.search(q)
        if not m:
            continue
        built = rule.build(m)
        if built is None:
            continue
        tool, args = built
        if all(v for v in args.values()):
            return Route(tool, args, rule.name)
    return None
