"""The write gate: plain code that decides what becomes a fact. No model is involved, ever.

A candidate fact passes the checks of docs/ARCHITECTURE.md in order and the first failing check decides:

    1 predicate in the schema, not forbidden, well-formed time,
      names up to 200 characters                                 -> bad_predicate / pii / bad_time / bad_name
    2 subject or object looks like personal data; attrs limited
      to role, title and note as short strings                   -> pii / bad_attrs
    3 at least one source id                                     -> no_source (+ conflicts_current)
    4 every source id is an existing Episode, none newer than
      the episode the claim was read in                          -> source_not_found / source_after_claim
    5 one line of a cited episode names both ends, with the
      relation's word (own, team, lead, depend, ...)             -> ungrounded
    6 both ends are known entities of the right label            -> unknown_entity
    7 the same fact is already current                           -> CONFIRM (source added; an exact repeat only
                                                                    bumps `repeats` on the first receipt)
    8 functional predicate with another current value            -> SUPERSEDE if newer, else older_than_current;
      a claim newer than its evidence, a closed fact without new
      evidence, a superseded decision superseding                -> valid_after_source / closed_fact /
                                                                    superseded_decision
    9 the same fact was ended after the time it claims           -> already_ended (replayed old evidence)
   10 otherwise                                                  -> ACCEPT

submit_end ends a current fact without a replacement (a person's last day). It runs checks 1-6 unchanged, except
that the line naming both ends must also say "no longer" (or "steps down"; the document that asserts the fact is
not its end) and a title cannot speak for the person; then: the fact must be current (else not_current, or
already_ended when history holds it), the end must come after the fact began (else older_than_current), and it must
be dated at the record that states it, a day earlier at most (valid_after_source / end_before_source). Outcome
CLOSE: the typed edge moves to WAS with invalid_at = the end and closed_by = the receipt.

Findings and handoffs: the agent id must look like agent:name, personal data is looked for in text, topic, note,
agent, session and party names, a cited receipt must not be a refusal (source_rejected), and nothing may be dated
before the sources it cites (source_after_claim).

Every call, refusals included, writes a GateDecision receipt with CITES edges to the episodes it was given
and ABOUT edges to the entities it resolved. A receipt never repeats personal data: a refused phone number is
stored as "[redacted]". Findings and handoffs written by agents go through the same door.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from .names import episode_states
from .schema import ENTITY_KEYS, FORBIDDEN_PREDICATES, PREDICATES, Predicate
from .store import Store, epoch

ACCEPT, CONFIRM, SUPERSEDE, REJECT, CLOSE = "ACCEPT", "CONFIRM", "SUPERSEDE", "REJECT", "CLOSE"

# Edge properties owned by the gate: a candidate's attrs may not overwrite them.
RESERVED = {"valid_at", "invalid_at", "source_ids", "gate_id", "closed_by", "predicate"}
# The word a source must use, in the same line as both names, to be said to state the relation. No cue: the names alone.
CUES = {"OWNS": r"\bown", "MEMBER_OF": r"\bteam\b|\bjoin|\bmember", "LEADS": r"\blead", "DECIDED": r"\bauthor\b|\bdecid",
        "SUPERSEDES": r"\bsupersede|\breplace", "DEPENDS_ON": r"\bdepend"}
# What an end must say, on the line that names both ends: "X no longer owns Y". The document that says X owns Y is
# the fact, not its end.
END_CUE = r"\bno longer\b|\bstep(?:s|ped)? down\b"
END_SLACK = 86400  # an end may be dated up to a day before the record that states it (a record filed late), never after
# Predicates whose edge carries `count`, the number of distinct sources (work items).
COUNTED = {"WORKED_ON"}
REDACTED = "[redacted]"
REDACTED_PII = "[redacted:pii]"
SNIPPET = 200
MAX_NAME = 200  # longest subject or object the gate will look at; receipts keep at most SNIPPET characters of a name
ATTR_KEYS = frozenset({"role", "title", "note"})  # the only extra properties a fact edge may carry, all short strings
MAX_ATTR = 300
AGENT_ID = re.compile(r"agent:[a-z0-9][a-z0-9_-]{0,63}")  # the shape the API and the tools accept


@dataclass
class Candidate:
    subject: str
    predicate: str
    object: str
    valid_at: str  # ISO-8601 with Z
    source_ids: list[str]
    proposer: str = "rules"
    claim_episode: str | None = None  # where the claim was read, when that is not (only) its source
    attrs: dict = field(default_factory=dict)


@dataclass
class GateResult:
    outcome: str  # ACCEPT | CONFIRM | SUPERSEDE | REJECT | CLOSE
    reasons: list[str]
    receipt_id: str
    written: bool
    closed: list[dict] = field(default_factory=list)
    record_id: str | None = None  # id of the Finding or Handoff that was written


# ---- personal data -------------------------------------------------------------------------------------

_EMAIL = re.compile(  # name@host.tld, or the same spelled "name [at] host [dot] tld"; starts at the head of a word run so it stays linear
    r"(?<![\w.+-])(?:[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
    r"|[\w.+-]+\s*[\[({]\s*at\s*[\])}]\s*[\w-]+(?:\s*[\[({]?\s*(?:dot|\.)\s*[\])}]?\s*[\w-]+)+)", re.IGNORECASE)
_DATETIME = re.compile(r"\d{4}-\d\d-\d\d(?:[T ]\d\d:\d\d(?::\d\d)?Z?)?|\d\d:\d\d(?::\d\d)?")
_NUMBER_RUN = re.compile(r"(?<![\w-])\+?\d[\d\s().-]{5,}\d(?![\w-])")
_CARD_RUN = re.compile(r"(?<![\w-])\d(?:[ -]?\d){12,18}(?![\w-])|(?<![\w-])\d{4}(?:\.\d{4}){3}(?![\w-])")
_NANP = re.compile(r"1?[2-9]\d{2}[2-9]\d{6}")
_STREET = re.compile(
    r"(?<![\w-])\d{1,5}\s+(?:[A-Z][A-Za-z'’-]*\s+){1,3}"
    r"(?i:street|st|avenue|ave|road|rd|lane|ln|court|ct|drive|dr|boulevard|blvd|way|place|pl|terrace|ter|circle|cir)\b")
_POBOX = re.compile(r"\bP\.?\s?O\.?\s?Box\s+\d+", re.IGNORECASE)


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _phone_like(run: str) -> bool:
    digits = re.sub(r"\D", "", run)
    if not 7 <= len(digits) <= 15:
        return False
    separators = set(re.sub(r"[\d+]", "", run))
    if not separators and not run.startswith("+"):
        return bool(_NANP.fullmatch(digits))  # a bare digit string is a phone only if it is shaped like one
    if separators == {"."}:
        return bool(re.fullmatch(r"\d{3}\.\d{3}\.\d{4}", run))  # not a version string or an address
    return True


def detect_pii(text: str) -> list[str]:
    """Kinds of personal data that look present in `text`: email, phone, address, card. Heuristics, on purpose
    conservative about dates, ids and version strings."""
    kinds = []
    if _EMAIL.search(text):
        kinds.append("email")
    scrubbed = _DATETIME.sub(" ", text)
    if any(_phone_like(m.group()) for m in _NUMBER_RUN.finditer(scrubbed)):
        kinds.append("phone")
    if _STREET.search(text) or _POBOX.search(text):
        kinds.append("address")
    if any(13 <= len(d) <= 19 and _luhn(d) for d in (re.sub(r"\D", "", m.group()) for m in _CARD_RUN.finditer(scrubbed))):
        kinds.append("card")
    return kinds


def _redact(value: str) -> str:
    kinds = detect_pii(value)
    return f"[redacted:{'+'.join(kinds)}]" if kinds else value


def _bad_attrs(attrs: dict) -> bool:
    """Gate-owned keys are ignored later; any other key must be in ATTR_KEYS with a short string value."""
    return any(k not in RESERVED and (k not in ATTR_KEYS or not isinstance(v, str) or len(v) > MAX_ATTR)
               for k, v in attrs.items())


def _pii_in(cand: Candidate) -> bool:
    values = [cand.subject, cand.object, *(v for v in cand.attrs.values() if isinstance(v, str))]
    return any(detect_pii(v) for v in values)


# ---- helpers ---------------------------------------------------------------------------------------------

def _dedupe(ids) -> list[str]:
    return list(dict.fromkeys(ids))


def _stamp(value: str | None) -> int | None:
    try:
        return epoch(value)
    except (ValueError, TypeError, AttributeError):
        return None


def _repeat_key(cand: Candidate, sources: list[str]) -> str:
    fields = [cand.subject, cand.predicate, cand.object, sorted(sources), cand.proposer]
    return hashlib.sha1(json.dumps(fields, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]


def _receipt_id(seq: int, *fields) -> str:
    digest = hashlib.sha1(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return f"GD-{seq:05d}-{digest[:10]}"


class _Names:
    """Entity lookups for one gate call, each (label, name) asked of the graph once."""

    def __init__(self, store: Store):
        self.store, self.cache = store, {}

    def label(self, name: str, labels) -> str | None:
        for label in labels:
            if (label, name) not in self.cache:
                self.cache[(label, name)] = self.store.find_entity(label, name)
            if self.cache[(label, name)]:
                return label
        return None


def _about(names: _Names, name: str, preferred) -> tuple[str, str] | None:
    """(label, name) of the entity a receipt is about, trying the labels the predicate expects first."""
    if detect_pii(name):
        return None
    label = names.label(name, [*preferred, *(l for l in ENTITY_KEYS if l not in preferred)])
    return (label, name) if label else None


def _current(store: Store, p: Predicate, subject: str | None, obj: str | None) -> list[tuple[dict, str]]:
    """Current edges of a predicate with the label of their object, over every label the object may carry."""
    return [(f, label) for label in p.object_labels for f in store.current_facts(p.name, subject, obj, object_label=label)]


def _conflicts(store: Store, p: Predicate, subject: str, obj: str) -> list[tuple[dict, str]]:
    if p.functional == "object":
        return [(f, l) for f, l in _current(store, p, None, obj) if f["subject"] != subject]
    if p.functional == "subject":
        return [(f, l) for f, l in _current(store, p, subject, None) if f["object"] != obj]
    return []


def _closed_without_news(store: Store, p: Predicate, cand: Candidate, label: str, stating: dict) -> bool:
    """True when this very fact was closed before and none of the sources that state it again is both newer than the
    closing and independent of the evidence that held it (a message that merely points at the old document is not)."""
    sk, ok = ENTITY_KEYS[p.subject], ENTITY_KEYS[label]
    closed = store.ro(f"MATCH (s:{p.subject} {{{sk}: $s}})-[h:WAS]->(o:{label} {{{ok}: $o}}) WHERE h.predicate = $p "
                      "RETURN h.invalid_at, h.source_ids", {"s": cand.subject, "o": cand.object, "p": p.name})
    if not closed:
        return False
    closed_at, held_by = max(r[0] for r in closed), [i for r in closed for i in r[1]]
    fresh = [s for s, ep in stating.items() if ep.get("ts", 0) > closed_at]
    relays = {r[0] for r in store.ro("MATCH (e:Episode)-[:REFERS_TO]->(r:Episode) WHERE e.id IN $ids AND r.id IN $old "
                                     "RETURN DISTINCT e.id", {"ids": fresh, "old": held_by})} if fresh else set()
    return not [s for s in fresh if s not in relays]


def _ended(store: Store, p: Predicate, subject: str, obj: str, label: str) -> list[int]:
    """invalid_at of every closed edge (WAS) of this exact fact."""
    sk, ok = ENTITY_KEYS[p.subject], ENTITY_KEYS[label]
    rows = store.ro(f"MATCH (s:{p.subject} {{{sk}: $s}})-[h:WAS]->(o:{label} {{{ok}: $o}}) "
                    "WHERE h.predicate = $p RETURN h.invalid_at", {"s": subject, "o": obj, "p": p.name})
    return [r[0] for r in rows if r[0] is not None]


@dataclass
class _Verdict:
    outcome: str
    reasons: list[str]
    object_label: str | None = None
    current: list[tuple[dict, str]] = field(default_factory=list)  # CONFIRM: the edge; SUPERSEDE: edges to close
    sources: list[str] = field(default_factory=list)  # the cited episodes that state the fact; the edge keeps only these


def _common_checks(store: Store, cand: Candidate, episodes: dict, names: _Names,
                   conflicts_note: bool, ending: bool = False) -> _Verdict:
    """Checks 1-6, shared by assertions and ends: a refusal, or a verdict-in-waiting carrying the object label."""
    refuse = lambda *reasons: _Verdict(REJECT, list(reasons))
    # 1
    if cand.predicate in FORBIDDEN_PREDICATES:
        return refuse("pii")
    p = PREDICATES.get(cand.predicate)
    if p is None:
        return refuse("bad_predicate")
    if _stamp(cand.valid_at) is None:
        return refuse("bad_time")
    if len(cand.subject) > MAX_NAME or len(cand.object) > MAX_NAME:
        return refuse("bad_name")
    # 2
    if _pii_in(cand):
        return refuse("pii")
    if _bad_attrs(cand.attrs):
        return refuse("bad_attrs")
    # 3
    if not cand.source_ids:
        conflict = conflicts_note and _conflicts(store, p, cand.subject, cand.object)
        return refuse("no_source", *(["conflicts_current"] if conflict else []))
    # 4
    if any(s not in episodes for s in cand.source_ids):
        return refuse("source_not_found")
    claim_ts = (episodes.get(cand.claim_episode) or {}).get("ts")
    if claim_ts is not None and any(episodes[s].get("ts", 0) > claim_ts for s in cand.source_ids):
        return refuse("source_after_claim")  # a claim cannot rest on a document that did not exist when it was read
    # 5
    cue = CUES.get(p.name, "")
    stating = [s for s in cand.source_ids if episode_states(episodes[s], cue, cand.subject, cand.object,
                                                            **({"also": END_CUE, "use_title": False} if ending else {}))]
    if not stating:
        return refuse("ungrounded")
    # 6
    object_label = names.label(cand.object, p.object_labels)
    if names.label(cand.subject, [p.subject]) is None or object_label is None:
        return refuse("unknown_entity")
    return _Verdict("", [], object_label, sources=stating)


def _judge(store: Store, cand: Candidate, episodes: dict, names: _Names) -> _Verdict:
    first = _common_checks(store, cand, episodes, names, conflicts_note=True)
    if first.outcome == REJECT:
        return first
    refuse = lambda *reasons: _Verdict(REJECT, list(reasons))
    p, valid, object_label, stating = PREDICATES[cand.predicate], _stamp(cand.valid_at), first.object_label, first.sources
    # 7
    same = [(f, object_label) for f in store.current_facts(p.name, cand.subject, cand.object, object_label=object_label)]
    if same:
        return _Verdict(CONFIRM, ["already_current"], object_label, same, stating)
    # 8
    conflicts = _conflicts(store, p, cand.subject, cand.object)
    if any(valid <= f["valid_at"] for f, _ in conflicts):
        return _Verdict(REJECT, ["older_than_current"], object_label)
    # 9: an end recorded after the time this claim speaks of means the claim is old evidence, not news
    if any(inv > valid for inv in _ended(store, p, cand.subject, cand.object, object_label)):
        return _Verdict(REJECT, ["already_ended"], object_label)
    if valid > max((episodes[s].get("ts", 0) for s in stating), default=0):
        return refuse("valid_after_source")  # a claim cannot be newer than the evidence that states it
    if _closed_without_news(store, p, cand, object_label, {s: episodes[s] for s in stating}):
        return refuse("closed_fact")
    if p.name == "SUPERSEDES" and store.ro("MATCH (d:Decision {id: $k}) WHERE d.status = 'superseded' RETURN 1", {"k": cand.subject}):
        return refuse("superseded_decision")
    if conflicts:
        return _Verdict(SUPERSEDE, ["newer_than_current"], object_label, conflicts, stating)
    # 10
    return _Verdict(ACCEPT, ["new_fact"], object_label, sources=stating)


def _judge_end(store: Store, cand: Candidate, episodes: dict, names: _Names) -> _Verdict:
    first = _common_checks(store, cand, episodes, names, conflicts_note=False, ending=True)
    if first.outcome == REJECT:
        return first
    p, at, label = PREDICATES[cand.predicate], _stamp(cand.valid_at), first.object_label
    current = [(f, label) for f in store.current_facts(p.name, cand.subject, cand.object, object_label=label)]
    if not current:
        ended = _ended(store, p, cand.subject, cand.object, label)
        return _Verdict(REJECT, ["already_ended" if ended else "not_current"], label)
    if any(at <= f["valid_at"] for f, _ in current):
        return _Verdict(REJECT, ["older_than_current"], label)
    recorded = max(episodes[s].get("ts", 0) for s in first.sources)
    if at > recorded:
        return _Verdict(REJECT, ["valid_after_source"], label)  # nobody has left yet, as far as the record knows
    if at < recorded - END_SLACK:
        return _Verdict(REJECT, ["end_before_source"], label)   # backdating an end rewrites who owned what in between
    return _Verdict(CLOSE, ["ended"], label, current)


# ---- writing receipts and records ---------------------------------------------------------------------------

def _write_receipt(store: Store, receipt_id: str, props: dict, cites: dict[str, list[str]],
                   about: list[tuple[str, str]], claimed_in: str | None = None) -> None:
    store.merge_entity("GateDecision", receipt_id, {k: v for k, v in props.items() if v is not None})
    for label, ids in cites.items():
        store.link("GateDecision", receipt_id, "CITES", label, ids)
    if claimed_in:
        store.link("GateDecision", receipt_id, "CLAIMED_IN", "Episode", [claimed_in])
    for label in dict.fromkeys(l for l, _ in about):
        store.link("GateDecision", receipt_id, "ABOUT", label, [n for l, n in about if l == label])


def submit_fact(store: Store, cand: Candidate, at: str | None = None) -> GateResult:
    """Judge one candidate fact, write the receipt, and (unless refused) the fact. `at` is the time the memory
    learned it; by default the latest episode involved, so replays are deterministic."""
    return _submit(store, cand, at, _judge, _apply)


def submit_end(store: Store, subject: str, predicate: str, object: str, sources: list[str], at: str,
               proposer: str = "rules", claim_episode: str | None = None) -> GateResult:
    """End a current fact without a replacement: a person's last day ends what they own, lead and belong to.
    Same provenance checks as any other write (checks 1-6); the fact must be current and must have begun before
    `at`. Outcome CLOSE: the typed edge moves to WAS with invalid_at = at and closed_by = the receipt id."""
    cand = Candidate(subject, predicate, object, at, list(sources), proposer, claim_episode)
    return _submit(store, cand, None, _judge_end, _apply_end)


def _apply_end(store: Store, p: Predicate, cand: Candidate, verdict: _Verdict, label: str, at: int,
               receipt_id: str) -> tuple[bool, list[dict]]:
    closed = []
    for old, old_label in verdict.current:
        store.close_fact(p.name, old["subject"], old["object"], at, receipt_id, object_label=old_label)
        closed.append({"predicate": p.name, "subject": old["subject"], "object": old["object"],
                       "valid_at": old["valid_at"], "invalid_at": at, "source_ids": old["source_ids"]})
    return True, closed


def _submit(store: Store, cand: Candidate, at: str | None, judge, apply) -> GateResult:
    ids = _dedupe(cand.source_ids)
    cand = Candidate(cand.subject, cand.predicate, cand.object, cand.valid_at, ids, cand.proposer,
                     cand.claim_episode, dict(cand.attrs))
    episodes = store.records("Episode", _dedupe([*ids, *([cand.claim_episode] if cand.claim_episode else [])]))
    names = _Names(store)
    verdict = judge(store, cand, episodes, names)

    valid = _stamp(cand.valid_at)
    repeat = None
    if verdict.outcome == CONFIRM and all(s in verdict.current[0][0]["source_ids"] for s in verdict.sources):
        # Nothing new to learn: the same claim, from the same proposer, on sources the edge already has. Count it on
        # the receipt of the first such repeat instead of writing one more receipt per call.
        repeat = _repeat_key(cand, verdict.sources)
        rows = store.rw("MATCH (g:GateDecision {repeat_key: $k}) SET g.repeats = g.repeats + 1 RETURN g.id", {"k": repeat})
        if rows:
            return GateResult(CONFIRM, verdict.reasons, rows[0][0], False)
    seq = store.next_sequence("gate")
    forbidden = cand.predicate in FORBIDDEN_PREDICATES
    subject, obj = _redact(cand.subject)[:SNIPPET], REDACTED if forbidden else _redact(cand.object)[:SNIPPET]
    fields = (subject, cand.predicate, obj, cand.valid_at, ids, cand.proposer, cand.claim_episode)
    receipt_id = _receipt_id(seq, *fields, *(["end"] if judge is _judge_end else []))

    written, closed = False, []
    if verdict.outcome != REJECT:
        p, label = PREDICATES[cand.predicate], verdict.object_label
        written, closed = apply(store, p, cand, verdict, label, valid, receipt_id)

    p = PREDICATES.get(cand.predicate)
    about = [a for a in (_about(names, cand.subject, (p.subject,) if p else ()),
                         None if forbidden else _about(names, cand.object, p.object_labels if p else ())) if a]
    stamps = [episodes[i]["ts"] for i in [cand.claim_episode, *ids] if i in episodes and "ts" in episodes[i]]
    given = _stamp(at) if at else None
    ts = given if given is not None else (max(stamps) if stamps else valid)
    claimed = cand.claim_episode if cand.claim_episode in episodes and cand.claim_episode not in ids else None
    _write_receipt(
        store, receipt_id,
        {"id": receipt_id, "ts": ts, "outcome": verdict.outcome, "reasons": verdict.reasons,
         "predicate": cand.predicate, "subject": subject, "object": obj, "proposer": cand.proposer,
         "valid_at": valid, "source_ids": ids, "claim_episode": cand.claim_episode,
         "repeat_key": repeat, "repeats": 1 if repeat else None},
        {"Episode": [i for i in ids if i in episodes]}, about, claimed)
    return GateResult(verdict.outcome, verdict.reasons, receipt_id, written, closed)


def _apply(store: Store, p: Predicate, cand: Candidate, verdict: _Verdict, label: str, valid: int,
           receipt_id: str) -> tuple[bool, list[dict]]:
    closed: list[dict] = []
    if verdict.outcome == CONFIRM:
        edge = verdict.current[0][0]
        new = [s for s in verdict.sources if s not in edge["source_ids"]]
        if new:
            store.add_fact_source(p.name, cand.subject, cand.object, new, object_label=label)
            if p.name in COUNTED:
                store.set_fact_props(p.name, cand.subject, cand.object,
                                     {"count": len(edge["source_ids"]) + len(new)}, object_label=label)
        _after_write(store, p, cand)
        return bool(new), closed
    for old, old_label in verdict.current:
        store.close_fact(p.name, old["subject"], old["object"], valid, receipt_id, object_label=old_label)
        closed.append({"predicate": p.name, "subject": old["subject"], "object": old["object"],
                       "valid_at": old["valid_at"], "invalid_at": valid, "source_ids": old["source_ids"]})
    props = {k: v for k, v in cand.attrs.items() if k not in RESERVED}
    props.update(valid_at=valid, source_ids=verdict.sources, gate_id=receipt_id)
    if p.name in COUNTED:
        props["count"] = len(verdict.sources)
    store.create_fact(p.name, cand.subject, cand.object, props, object_label=label)
    _after_write(store, p, cand)
    return True, closed


def _after_write(store: Store, p: Predicate, cand: Candidate) -> None:
    if p.name == "SUPERSEDES":  # the replaced decision is no longer in force
        store.merge_entity("Decision", cand.object, {"status": "superseded"})


# ---- findings and handoffs --------------------------------------------------------------------------------------

def _sources(store: Store, ids: list[str]) -> dict[str, list[str]]:
    """The given ids that exist, by label: Episodes and GateDecisions."""
    out = {}
    for label in ("Episode", "GateDecision"):
        got = store.records(label, ids)
        out[label] = [i for i in ids if i in got]
    return out


def _found_ids(found: dict[str, list[str]]) -> list[str]:
    return [i for ids in found.values() for i in ids]


def _refuse_record(store: Store, *, reasons: list[str], agent: str, predicate: str, obj: str,
                   at: int | None, source_ids: list[str], found: dict[str, list[str]],
                   about: list[tuple[str, str]]) -> GateResult:
    seq = store.next_sequence("gate")
    receipt_id = _receipt_id(seq, agent, predicate, obj, at, source_ids)
    _write_receipt(store, receipt_id,
                   {"id": receipt_id, "ts": at, "outcome": REJECT, "reasons": reasons, "predicate": predicate,
                    "subject": _redact(agent)[:SNIPPET], "object": obj, "proposer": _redact(agent)[:SNIPPET], "valid_at": at,
                    "source_ids": source_ids},
                   found, about)
    return GateResult(REJECT, reasons, receipt_id, False)


def _accept_record(store: Store, *, label: str, counter: str, prefix: str, props: dict, predicate: str, subject: str,
                   obj: str, at: int, source_ids: list[str], found: dict[str, list[str]],
                   about: list[tuple[str, str]], extra=None) -> GateResult:
    seq = store.next_sequence("gate")
    receipt_id = _receipt_id(seq, subject, predicate, obj, at, source_ids)
    record_id = f"{prefix}-{store.next_sequence(counter):05d}"
    store.merge_entity(label, record_id, {**props, "id": record_id, "ts": at, "gate_id": receipt_id,
                                         "source_ids": _found_ids(found)})
    if extra:
        extra(record_id)
    _write_receipt(store, receipt_id,
                   {"id": receipt_id, "ts": at, "outcome": ACCEPT, "reasons": [f"new_{label.lower()}"],
                    "predicate": predicate, "subject": subject, "object": obj, "proposer": subject, "valid_at": at,
                    "source_ids": source_ids}, found, about)
    return GateResult(ACCEPT, [f"new_{label.lower()}"], receipt_id, True, record_id=record_id)


def _record_checks(store: Store, text: str, source_ids: list[str], at: str) -> tuple[list[str], int | None, dict]:
    """The checks findings and handoffs share: time, personal data, sources. Returns (reasons, at, sources found)."""
    when = _stamp(at)
    found = _sources(store, source_ids)
    if when is None:
        return ["bad_time"], when, found
    if detect_pii(text):
        return ["pii"], when, found
    if not source_ids:
        return ["no_source"], when, found
    if not _found_ids(found):
        return ["source_not_found"], when, found
    cited = {label: store.records(label, sids) for label, sids in found.items() if sids}
    if any(r.get("outcome") == REJECT for r in cited.get("GateDecision", {}).values()):
        return ["source_rejected"], when, found  # a refusal is a record of what was not believed, not evidence
    if any(r.get("ts", 0) > when for records in cited.values() for r in records.values()):
        return ["source_after_claim"], when, found
    return [], when, found


def submit_finding(store: Store, agent: str, session: str, text: str, about: list[str], source_ids: list[str],
                   at: str) -> GateResult:
    """Keep what an agent concluded, if it is about known entities, rests on a known source and holds no
    personal data. Writes (Agent)-[:WROTE]->(Finding), (Finding)-[:ABOUT]->(entity), (Finding)-[:DERIVED_FROM]->(source)."""
    ids = _dedupe(source_ids)
    reasons, when, found = _record_checks(store, f"{text}\n{agent}\n{session}", ids, at)
    names = _Names(store)
    resolved = [(names.label(n, ENTITY_KEYS), n) for n in about]
    known = [(l, n) for l, n in resolved if l]
    if not reasons and not AGENT_ID.fullmatch(agent):
        reasons = ["bad_agent"]
    if not reasons:
        reasons = ["no_about"] if not about else ["unknown_entity"] if len(known) < len(about) else []
    snippet = (REDACTED_PII if "pii" in reasons else text)[:SNIPPET]
    if reasons:
        return _refuse_record(store, reasons=reasons, agent=agent, predicate="FINDING", obj=snippet, at=when,
                              source_ids=ids, found=found, about=known)

    def link(finding_id: str) -> None:
        store.merge_entity("Agent", agent)
        store.link("Agent", agent, "WROTE", "Finding", [finding_id])
        for label in dict.fromkeys(l for l, _ in known):
            store.link("Finding", finding_id, "ABOUT", label, [n for l, n in known if l == label])
        for label, sids in found.items():
            store.link("Finding", finding_id, "DERIVED_FROM", label, sids)

    return _accept_record(store, label="Finding", counter="finding", prefix="FND",
                          props={"text": text, "session": session, "agent": agent}, predicate="FINDING",
                          subject=agent, obj=snippet, at=when, source_ids=ids, found=found, about=known, extra=link)


def submit_handoff(store: Store, from_: str, to: str, topic: str, note: str, source_ids: list[str],
                   at: str, status: str = "open") -> GateResult:
    """Pass work on. Parties are Agents (names starting with "agent:" are created on demand), Teams or Persons.
    Writes a Handoff node with FROM / TO / CITES edges. `status` is "open", or "proposed" for a line of a handoff
    plan that a person still has to approve (nothing is sent anywhere either way)."""
    ids = _dedupe(source_ids)
    reasons, when, found = _record_checks(store, f"{topic}\n{note}\n{from_}\n{to}", ids, at)
    names = _Names(store)

    def party(name: str, order) -> tuple[str, str] | None:
        label = "Agent" if AGENT_ID.fullmatch(name) else names.label(name, order)
        return (label, name) if label else None

    src, dst = party(from_, ("Agent", "Team", "Person")), party(to, ("Agent", "Person", "Team"))
    known = [x for x in (src, dst) if x and (x[0] != "Agent" or names.label(x[1], ["Agent"]))]
    if not reasons and not (src and dst):
        reasons = ["unknown_entity"]
    obj = (REDACTED_PII if "pii" in reasons else to)[:SNIPPET]
    if reasons:
        return _refuse_record(store, reasons=reasons, agent=from_, predicate="HANDOFF", obj=obj, at=when,
                              source_ids=ids, found=found, about=known)

    def link(handoff_id: str) -> None:
        for label, name in (src, dst):
            if label == "Agent":
                store.merge_entity("Agent", name)
        store.link("Handoff", handoff_id, "FROM", src[0], [src[1]])
        store.link("Handoff", handoff_id, "TO", dst[0], [dst[1]])
        for label, sids in found.items():
            store.link("Handoff", handoff_id, "CITES", label, sids)

    return _accept_record(store, label="Handoff", counter="handoff", prefix="HND",
                          props={"topic": topic, "note": note, "status": status}, predicate="HANDOFF",
                          subject=from_, obj=to, at=when, source_ids=ids, found=found, about=[src, dst], extra=link)
