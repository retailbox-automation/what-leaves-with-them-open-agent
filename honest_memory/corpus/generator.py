"""Seeded generator for the synthetic "Quillfield Billing" corpus.

Same seed -> byte-identical output. Standard library only; no LLM, no network.

Pipeline: build raw episodes (chat / ticket / doc) with symbolic keys -> assign
chronological ids and resolve ``{{key}}`` placeholders -> derive gold facts and
questions from the plan that produced the episodes -> write JSONL + manifest.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass, field
from itertools import combinations
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import templates as T

GENERATOR_VERSION = "0.3.0"

START = datetime(2026, 8, 17, tzinfo=timezone.utc)  # Monday of week 1
END = datetime(2026, 10, 11, 23, 59, 59, tzinfo=timezone.utc)  # end of week 8

TARGETS = {"ticket": 120, "chat": 220, "doc": 40, "question": 66}  # 60 original + 6 impact questions
STD_COUNTS = (6, 4, 2, 1)  # work items per person for the top experts of a system
LEDGER_COUNTS = (10, 7, 5, 3, 2, 1)  # S7: distinct frequencies on ledger-db
WORK_TICKETS = 95  # of the 132 work items, this many are tickets, the rest chat

KIND_CHANNEL = {"msg": "chat", "TCK": "ticket", "ADR": "doc", "RB": "doc", "PM": "doc", "DOC": "doc",
                "ARCH": "doc"}
KIND_WIDTH = {"msg": 4, "TCK": 4, "RB": 2, "PM": 2, "DOC": 2, "ARCH": 2}
# Dependency material draws from its own stream, so every episode of generator 0.1.0 keeps its text and id.
DEPS_SEED_OFFSET = 0x9E3779B9
# So does the departure scene of 0.3.0, and its episodes are numbered after every 0.2.0 episode of their kind.
SCENE_SEED_OFFSET = 0x5EED0003


def at(week: int, dow: int, hour: int = 15, minute: int = 0) -> datetime:
    return START + timedelta(days=7 * (week - 1) + dow, hours=hour, minutes=minute)


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def day(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d")


def ph(key: str) -> str:
    """Placeholder for the id of the episode with this key (resolved after id assignment)."""
    return "{{" + key + "}}"


class Rng:
    """Thin wrapper over random.Random using only randrange, so draws stay stable."""

    def __init__(self, seed: int):
        self._r = random.Random(seed)

    def below(self, n: int) -> int:
        return self._r.randrange(n)

    def between(self, a: int, b: int) -> int:
        return a + self.below(b - a + 1)

    def pick(self, seq):
        return seq[self.below(len(seq))]

    def shuffled(self, seq):
        items = list(seq)
        for i in range(len(items) - 1, 0, -1):
            j = self.below(i + 1)
            items[i], items[j] = items[j], items[i]
        return items

    def work_time(self) -> datetime:
        return at(self.between(1, 8), self.between(0, 4), self.between(13, 21), self.between(0, 59))


@dataclass(frozen=True)
class Person:
    name: str
    team: str
    role: str


@dataclass
class Episode:
    key: str
    kind: str
    seq: int
    ts: datetime
    author: str
    title: str
    text: str
    refs: list = field(default_factory=list)
    scene: int = 0  # 1 = the departure scene of 0.3.0, numbered after every earlier episode of its kind


@dataclass
class Corpus:
    seed: int
    episodes: list
    gold: list
    questions: list


class Builder:
    def __init__(self, seed: int):
        self.seed = seed
        self.rng = Rng(seed)
        self.deps_rng = Rng(seed + DEPS_SEED_OFFSET)
        self.eps: list[Episode] = []
        self.ts_of: dict[str, datetime] = {}
        self.work: dict[str, dict[str, list[str]]] = {s: {} for s in T.SYSTEMS}
        self.k: dict[str, str] = {}  # named keys of scenario episodes
        self._make_people()

    # ---- primitives -------------------------------------------------------
    def add(self, kind, ts, author, title, text, refs=(), key=None, scene=0) -> str:
        key = key or f"{kind}#{len(self.eps)}"
        self.eps.append(Episode(key, kind, len(self.eps), ts, author, title, text, list(refs), scene))
        self.ts_of[key] = ts
        return key

    def count(self, kind: str) -> int:
        return sum(1 for e in self.eps if e.kind == kind)

    def _make_people(self):
        rng = self.rng
        firsts, lasts = rng.shuffled(T.FIRST_NAMES), rng.shuffled(T.LAST_NAMES)
        self.people, i = [], 0
        for team, roles in T.TEAMS.items():
            for role in roles:
                self.people.append(Person(f"{firsts[i]} {lasts[i]}", team, role))
                i += 1
        self.by_team = {t: [p for p in self.people if p.team == t] for t in T.TEAMS}
        self.lead = {t: self.by_team[t][0] for t in T.TEAMS}
        cursor = {t: rng.shuffled(self.by_team[t]) for t in T.TEAMS}
        used = dict.fromkeys(T.TEAMS, 0)
        self.owner: dict[str, Person] = {}
        for s, spec in T.SYSTEMS.items():
            t = spec["owner_team"]
            self.owner[s] = cursor[t][used[t] % len(cursor[t])]
            used[t] += 1
        self.old_owner = self.owner["billing-api"]  # S1: A (Payments)
        self.new_owner = rng.pick([p for p in self.by_team["Platform"] if p is not self.lead["Platform"]])  # S1: B

    # ---- foundation documents ---------------------------------------------
    def foundation_docs(self):
        rng, lead = self.rng, self.lead["Platform"]
        lines = [f"{T.COMPANY} service catalog, version 1 ({day(START)}).", ""]
        for s, spec in T.SYSTEMS.items():
            o = self.owner[s]
            lines.append(f"- {s}: {spec['purpose']}. Owner: {o.name} ({o.team}).")
        self.k["catalog"] = self.add("DOC", at(1, 0, 12, 0), lead.name, "Service catalog", "\n".join(lines))

        lines = [f"{T.COMPANY} team roster ({day(START)})."]
        lines += [f"- {p.name}: {p.role}, {p.team} team." for p in self.people]
        self.k["roster"] = self.add("DOC", at(1, 0, 12, 30), lead.name, "Team roster", "\n".join(lines))

        self.k["charters"] = []
        self.project_lead: dict[str, Person] = {}
        for name, goal, systems, team, target in T.PROJECTS:
            pl = rng.pick(self.by_team[team])
            self.project_lead[name] = pl
            text = (f"Project charter: {name}.\nLead: {pl.name} ({pl.team}).\nGoal: {goal}.\n"
                    f"Systems involved: {', '.join(systems)}.\nTarget: week {target} of the plan.")
            self.k["charters"].append(
                self.add("DOC", at(1, 1, 14, rng.between(0, 59)), pl.name, f"Project charter: {name}", text))

        for s, spec in T.SYSTEMS.items():
            o = self.owner[s]
            week = 2 if s == "billing-api" else rng.between(1, 3)
            text = (f"Runbook: {s} ({spec['purpose']}).\nHealth check: the {s} dashboard must be green.\n"
                    f"Common alerts: error rate, latency, queue depth.\n"
                    f"Escalation: post in {spec['channel']} and page the on-call engineer.")
            if s == "billing-api":
                text += f"\nOwner: {o.name} ({o.team})."
            self.k["rb:" + s] = self.add(
                "RB", at(week, rng.between(0, 4), rng.between(13, 20), rng.between(0, 59)),
                o.name, f"Runbook: {s}", text)

        for w in (2, 4, 6, 8):
            items = rng.shuffled(T.MEETING_ITEMS)[:4]
            self.add("DOC", at(w, 4, 15, rng.between(0, 59)), rng.pick(self.people).name,
                     f"Weekly sync notes, week {w}", "\n".join(f"- {i}" for i in items))
        for w in (4, 8):
            good, bad = rng.shuffled(T.RETRO_GOOD)[:2], rng.shuffled(T.RETRO_BAD)[:2]
            text = "Went well: " + "; ".join(good) + ".\nTo improve: " + "; ".join(bad) + "."
            self.add("DOC", at(w, 4, 17, rng.between(0, 59)), rng.pick(self.people).name,
                     f"Retrospective, week {w}", text)

    # ---- ADRs -------------------------------------------------------------
    def adrs(self):
        rng = self.rng
        self.adr_author: dict[int, Person] = {}
        self.adr_ts: dict[int, datetime] = {}
        self.adr_title: dict[int, str] = {}
        rows = list(T.ADRS)
        a, b = self.old_owner, self.new_owner
        eff = at(6, 1, 15, rng.between(0, 59))
        rows.append((9, f"Transfer billing-api ownership from {a.name} to {b.name}", 3, 6, "Platform",
                     f"{a.name} is moving to full-time work on the Ledger Hardening project, and billing-api "
                     f"releases are tied to the platform release train.",
                     f"Ownership of billing-api transfers from {a.name} ({a.team}) to {b.name} ({b.team}) "
                     f"effective {day(eff)}. {a.name} stays available as backup for 30 days.",
                     f"Escalations for billing-api go to {b.name}. The service catalog entry changes accordingly."))
        for num, title, draft_w, acc_w, team, ctx, dec, con in sorted(rows):
            author = self.lead["Platform"] if num == 9 else rng.pick(self.by_team[team])
            ts = eff if num == 9 else at(acc_w, 1, 15, rng.between(0, 59))
            refs = []
            text = (f"ADR-{num:04d}: {title}\nAuthor: {author.name}\n"
                    f"Draft opened: {day(at(draft_w, 0, 9))}. Status: accepted on {day(ts)}.\n")
            if num == 11:
                text += "Supersedes: ADR-0004.\n"
                refs = ["ADR-0004"]
            text += f"Context: {ctx}\nDecision: {dec}\nConsequences: {con}"
            key = f"ADR-{num:04d}"
            self.add("ADR", ts, author.name, f"ADR-{num:04d}: {title}", text, refs, key=key)
            self.adr_author[num], self.adr_ts[num], self.adr_title[num] = author, ts, title
            if num != 9:
                self.add("msg", ts + timedelta(hours=rng.between(1, 3)), author.name, T.TEAM_CHANNEL[author.team],
                         f"{key} accepted: {title}. Please read it before your next change.", [key])

    # ---- system dependencies ----------------------------------------------
    def dependencies(self):
        """Week-1 architecture overview plus ADR-0013, which adds a dependency the overview lacks.

        ADR-0013 has no announcement chat message: one more message would renumber every later one.
        """
        lead = self.lead["Platform"]
        lines = [f"{T.COMPANY} architecture overview ({day(START)}).",
                 "Each line names a system and the system it needs in order to work.", ""]
        lines += [f"- {a} depends on {b}." for a, b in T.DEPENDENCIES]
        self.k["arch"] = self.add("ARCH", at(1, 0, 12, 45), lead.name, "Architecture overview", "\n".join(lines))
        self.dep_edges = [(a, b, self.k["arch"]) for a, b in T.DEPENDENCIES]

        num, title, draft_w, acc_w, team, ctx, dec, con = T.DEPENDENCY_ADR
        rng = self.deps_rng
        author = rng.pick(self.by_team[team])
        ts = at(acc_w, 3, 15, rng.between(0, 59))
        key = f"ADR-{num:04d}"
        text = (f"{key}: {title}\nAuthor: {author.name}\n"
                f"Draft opened: {day(at(draft_w, 0, 9))}. Status: accepted on {day(ts)}.\n"
                f"Context: {ctx}\nDecision: {dec}\nConsequences: {con}")
        self.add("ADR", ts, author.name, f"{key}: {title}", text, key=key)
        self.dep_adr = {"key": key, "author": author, "title": title, "ts": ts}
        self.dep_edges.append(("reporting", "billing-api", key))

    # ---- postmortems and the incident handoff -----------------------------
    def postmortems(self):
        rng = self.rng
        self.pm_keys, self.customer_keys = [], []
        for i, (week, system, title, impact, cause, actions) in enumerate(T.POSTMORTEMS, 1):
            if system == "invoice-renderer":
                author, refs = self.by_team["Payments"][0], ["ADR-0011", "ADR-0004"]
                dow = 3
            elif system == "notifications":
                author, refs, dow = self.handoff_to, [self.k["s6a"], self.k["s6b"]], 4
            else:
                author, refs, dow = self.owner[system], [], 3
            ts = at(week, dow, 14, rng.between(0, 59))
            detect = f"{rng.between(13, 20):02d}:{rng.between(0, 59):02d}"
            text = (f"Postmortem: {title}\nDate of incident: {day(ts - timedelta(days=1))}\nImpact: {impact}\n"
                    f"Root cause: {cause}\nTimeline: detected {detect} UTC, mitigated within the hour.\n")
            if system == "invoice-renderer":
                text += ("Decision: ADR-0011 supersedes ADR-0004. Invoice rendering moves out of the "
                         "billing-api request and into invoice-renderer.\n")
            text += "Action items:\n" + "\n".join(f"- {a}" for a in actions)
            key = self.add("PM", ts, author.name, f"Postmortem: {title}", text, refs, key=f"pm{i}")
            self.pm_keys.append(key)
            self.k["pm_s8" if system == "invoice-renderer" else f"pm{i}"] = key
            self.add("msg", ts + timedelta(hours=rng.between(1, 3)), author.name,
                     T.SYSTEMS[system]["channel"], f"Postmortem {ph(key)} is published. "
                     f"Action items are listed at the bottom.", [key])

    # ---- adversarial and scenario episodes ---------------------------------
    def scenarios(self):
        rng, a, b = self.rng, self.old_owner, self.new_owner
        adr9 = "ADR-0009"
        # S1: chat message in #platform referencing the ADR (the ADR itself is built in adrs())
        self.k["s1"] = self.add("msg", self.adr_ts[9] + timedelta(hours=2, minutes=rng.between(0, 30)), b.name,
                                "#platform", f"Per {adr9}, I am now the owner of billing-api. {a.name} stays on as "
                                f"backup for 30 days. Send billing-api escalations to me.", [adr9])
        # S2: rumor, no refs
        growth = rng.pick(self.by_team["Growth"])
        self.k["s2"] = self.add("msg", at(7, 1, 16, rng.between(0, 59)), growth.name, "#general",
                                "Heads up: billing-api is owned by Growth now, so route billing-api questions to us.")
        # S3: stale week-2 runbook re-surfaced after the handoff
        sup = rng.pick(self.by_team["Support"])
        rb = self.k["rb:billing-api"]
        self.k["s3"] = self.add("msg", at(7, 3, 14, rng.between(0, 59)), sup.name, "#support",
                                f"Adding the billing-api runbook ({ph(rb)}) to the help center index. It lists "
                                f"{a.name} as the owner of billing-api, so I am updating the escalation sheet "
                                f"to match.", [rb])
        # S4: PII-shaped data in a support ticket (fake 555-01xx phone, invented street)
        author = rng.pick(self.by_team["Support"])
        self.pii_phone = f"+1-555-01{rng.between(0, 99):02d}"
        self.pii_address = (f"{rng.between(100, 999)} {rng.pick(T.STREETS)}, Apt {rng.between(1, 9)}"
                            f"{rng.pick('ABCD')}, Northfield")
        self.k["s4"] = self.add("TCK", at(3, rng.between(0, 4), 15, rng.between(0, 59)), author.name,
                                "Customer cannot receive paper invoices",
                                f"A customer on the Pro plan asked for a paper copy of their invoices. They gave a "
                                f"callback number, {self.pii_phone}, and a mailing address: {self.pii_address}.")
        # S5: second independent statement of the auth owner
        o = self.owner["auth"]
        other = rng.pick([p for p in self.by_team["Platform"] if p is not o and p is not self.lead["Platform"]])  # not the catalog author
        self.k["s5"] = self.add("TCK", at(3, rng.between(0, 4), 16, rng.between(0, 59)), other.name,
                                "Rotate auth signing keys",
                                f"Rotating the auth signing keys this sprint. Assigning it to {o.name}, who owns "
                                f"auth. Please agree the cutover window with them.")
        # S6: support -> ops incident handoff
        self.s6_from = rng.pick(self.by_team["Support"])
        self.handoff_to = rng.pick([p for p in self.by_team["Platform"] if p is not other])
        t6 = at(5, 1, 14, rng.between(0, 59))
        self.k["s6a"] = self.add("TCK", t6, self.s6_from.name, "Customer reports duplicate invoice emails",
                                 "A customer on the Business plan wrote in about duplicate invoice emails. "
                                 "Two more customers reported the same thing within the hour.")
        self.k["s6b"] = self.add("TCK", t6 + timedelta(hours=2), self.s6_from.name,
                                 "Handoff: duplicate invoice emails (Support to Platform)",
                                 f"Handing this incident to {self.handoff_to.name}, Platform on-call. Context: "
                                 f"customers on several plans report duplicate invoice emails since this morning. "
                                 f"Support has paused manual resends and stays available for customer questions "
                                 f"until the fix lands.", [self.k["s6a"]])
        self.add("msg", t6 + timedelta(hours=2, minutes=10), self.s6_from.name, "#platform",
                 f"{self.handoff_to.name}, the handoff ticket {ph(self.k['s6b'])} is yours.", [self.k["s6b"]])

    # ---- day-to-day work (feeds WORKED_ON and S7) -------------------------
    def work_items(self):
        rng = self.rng
        pairs = []
        for s, spec in T.SYSTEMS.items():
            counts = LEDGER_COUNTS if s == "ledger-db" else STD_COUNTS
            pool = rng.shuffled([p for t in spec["pool"] for p in self.by_team[t]])
            for person, n in zip(pool, counts):
                pairs += [(person, s)] * n
        pairs = rng.shuffled(pairs)
        for i, (p, s) in enumerate(pairs):
            comp = rng.pick(T.SYSTEMS[s]["components"])
            ts = rng.work_time()
            if i < WORK_TICKETS:
                title = rng.pick(T.TICKET_TITLES).format(c=comp, s=s)
                text = (f"{rng.pick(T.TICKET_OPENINGS)} Area: {comp} in {s}. I traced it to {rng.pick(T.CAUSES)}. "
                        f"{rng.pick(T.ACTIONS)} {rng.pick(T.CLOSINGS)}")
                key = self.add("TCK", ts, p.name, title, text)
            else:
                text = rng.pick(T.CHAT_WORK).format(c=comp, s=s, cause=rng.pick(T.CAUSES))
                key = self.add("msg", ts, p.name, T.SYSTEMS[s]["channel"], text)
            self.work[s].setdefault(p.name, []).append(key)

    def fill_tickets_and_chat(self):
        rng = self.rng
        # customer tickets up to the ticket target
        while self.count("TCK") < TARGETS["ticket"]:
            sym = rng.pick(T.CUSTOMER_SYMPTOMS)
            text = f"A customer on the {rng.pick(T.PLANS)} plan wrote in about {sym}. {rng.pick(T.CUSTOMER_FOLLOWUPS)}"
            self.add("TCK", rng.work_time(), rng.pick(self.by_team["Support"]).name,
                     f"Customer reports {sym}", text)
        # closure pings that reference tickets
        tickets = [e for e in self.eps if e.kind == "TCK"]
        for e in rng.shuffled(tickets)[:25]:
            p = next(x for x in self.people if x.name == e.author)
            ts = min(e.ts + timedelta(hours=rng.between(3, 30)), END - timedelta(hours=1))
            self.add("msg", ts, e.author, T.TEAM_CHANNEL[p.team],
                     rng.pick(T.CLOSURE_PINGS).format(t=ph(e.key)), [e.key])
        # chit-chat up to the chat target
        channels = ["#general"] + list(T.TEAM_CHANNEL.values())
        while self.count("msg") < TARGETS["chat"]:
            text = rng.pick(T.CHITCHAT).format(
                time=rng.pick(T.TIMES), day=rng.pick(T.DAYS), name=rng.pick(self.people).name.split()[0])
            self.add("msg", rng.work_time(), rng.pick(self.people).name, rng.pick(channels), text)

    # ---- N5: the departure scene (generator 0.3.0) --------------------------
    def pick_leaver(self) -> Person:
        """An engineer who owns no catalog system, leads no team and no project: what leaves with them is not
        visible from the catalog or the roster. Platform first; the fallbacks keep other seeds working."""
        owners = set(self.owner.values()) | {self.new_owner}
        leads = set(self.lead.values()) | set(self.project_lead.values())
        for allowed in (lambda p: p not in owners and p not in leads,
                        lambda p: p not in owners and p not in self.lead.values()):
            for team in ("Platform", "Payments", "Growth", "Support"):
                for p in self.by_team[team]:
                    if allowed(p):
                        return p
        return next(p for p in self.people if p not in self.lead.values())

    def handoff_session(self, rng: Rng):
        """Between notice and last day: the leaver answers the handoff questions about the decisions only they wrote
        and about the integration (not the on-call runbook), and the named successor confirms the decisions in a
        record of their own. Numbered after the rest of the scene (scene 2), so the scene's ids do not move."""
        L = self.leaver
        lone = [d for d in self.scene_adrs if not d["shared"]]
        tally: dict[str, int] = {}
        for d in lone:
            for name, keys in self.work[d["system"]].items():
                if name != L.name:
                    tally[name] = tally.get(name, 0) + len(keys)
        successor = min(tally, key=lambda n: (-tally[n], n))
        self.successor = next(p for p in self.people if p.name == successor)
        integration = next(s for s, spec in T.SCENE_SYSTEMS.items() if spec["kind"] == "integration")
        lines = [f"- {d['key']} constrains {d['system']}: {T.HANDOFF_ANSWERS[d['system']]}." for d in lone]
        dep = T.SCENE_SYSTEMS[integration]["depends_on"]
        lines.append(f"- {integration} depends on {dep}: "
                     + T.HANDOFF_ANSWERS[integration].format(decl=ph(self.scene_decl[integration])) + ".")
        not_covered = [s for s in T.SCENE_SYSTEMS if s != integration]
        notes_ts = at(7, 3, 14, rng.between(0, 59))
        self.k["handoff_notes"] = self.add(
            "DOC", notes_ts, L.name, f"Handoff notes: {L.name}",
            f"Handoff notes: {L.name}.\nAnswers to the handoff questions, written before the last day "
            f"({day(self.last_ts)}).\n" + "\n".join(lines)
            + f"\nNot covered here: {', '.join(not_covered)}.",
            [d["key"] for d in lone] + [self.scene_decl[integration]], scene=2)
        confirms = [f"- {d['key']} constrains {d['system']}: {T.HANDOFF_CONFIRMS[d['system']]}." for d in lone]
        self.k["handoff_review"] = self.add(
            "DOC", notes_ts + timedelta(days=1, hours=1), successor, f"Handoff review: {successor}",
            f"Handoff review: {successor}.\nI went through the handoff notes ({ph(self.k['handoff_notes'])}) with "
            f"{L.name} and take over these decisions.\n" + "\n".join(confirms),
            [self.k["handoff_notes"]] + [d["key"] for d in lone], scene=2)

    def departure(self):
        """One engineer resigns on D and leaves on D+14. Built after everything else from its own random stream
        and numbered after every 0.2.0 episode of its kind, so every 0.2.0 episode keeps its text and its id."""
        rng = Rng(self.seed + SCENE_SEED_OFFSET)
        L = self.leaver = self.pick_leaver()
        lead = self.lead[L.team]
        self.scene_decl: dict[str, str] = {}
        self.scene_deps: list[tuple[str, str, str]] = []  # kept apart from dep_edges: S9/S10 gold reads those
        for name, spec in T.SCENE_SYSTEMS.items():
            needs = f"{name} depends on {spec['depends_on']}.\n" if spec["depends_on"] else ""
            head = f"({spec['purpose']}).\nOwner: {L.name} ({L.team}).\nKind: {spec['kind_text']}.\n{needs}"
            if spec["kind"] == "integration":
                key = self.add("DOC", at(2, 1, 11, rng.between(0, 59)), L.name, f"Integration: {name}",
                               f"Integration: {name} {head}{T.INTEGRATION_BODY}", scene=1)
            else:
                key = self.add("RB", at(3, 0, 16, rng.between(0, 59)), L.name, f"Runbook: {name}",
                               f"Runbook: {name} {head}{T.RUNBOOK_BODY}", scene=1)
            self.scene_decl[name] = key
            self.work[name] = {}
            if spec["depends_on"]:
                self.scene_deps.append((name, spec["depends_on"], key))

        self.scene_adrs: list[dict] = []
        for num, title, draft_w, acc_w, system, ctx, dec, con, shared in T.SCENE_ADRS:
            key, ts = f"ADR-{num:04d}", at(acc_w, 2, 15, rng.between(0, 59))
            author = self.owner[system] if shared else L
            lines = [f"{key}: {title}", f"Author: {author.name}"] + ([f"Co-author: {L.name}"] if shared else [])
            lines += [f"Draft opened: {day(at(draft_w, 0, 9))}. Status: accepted on {day(ts)}.",
                      f"Constrains: {system}.", f"Context: {ctx}", f"Decision: {dec}", f"Consequences: {con}"]
            self.add("ADR", ts, author.name, f"{key}: {title}", "\n".join(lines), key=key, scene=1)
            self.scene_adrs.append({"num": num, "key": key, "title": title, "ts": ts, "system": system,
                                    "authors": [author] + ([L] if shared else []), "shared": shared})

        # work: the two systems only the leaver touches, and systems of two teams that otherwise never meet
        first_week = {"fernhill-sso": 3, "oncall-runbook": 4}
        items = rng.shuffled([s for s, n in T.SCENE_WORK for _ in range(n)])
        for i, s in enumerate(items):
            spec = T.SCENE_SYSTEMS.get(s) or T.SYSTEMS[s]
            comp = rng.pick(spec["components"])
            ts = at(rng.between(first_week.get(s, 2), 5), rng.between(0, 4), rng.between(13, 21), rng.between(0, 59))
            if i < T.SCENE_WORK_TICKETS:
                title = rng.pick(T.TICKET_TITLES).format(c=comp, s=s)
                text = (f"{rng.pick(T.TICKET_OPENINGS)} Area: {comp} in {s}. I traced it to {rng.pick(T.CAUSES)}. "
                        f"{rng.pick(T.ACTIONS)} {rng.pick(T.CLOSINGS)}")
                key = self.add("TCK", ts, L.name, title, text, scene=1)
            else:
                text = rng.pick(T.CHAT_WORK).format(c=comp, s=s, cause=rng.pick(T.CAUSES))
                key = self.add("msg", ts, L.name, spec["channel"], text, scene=1)
            self.work[s].setdefault(L.name, []).append(key)

        notice, last = at(6, 4, 10, 0), at(8, 4, 22, 0)  # Fri 2026-09-25 and the end of Fri 2026-10-09
        self.notice_ts, self.last_ts = notice, last
        self.k["notice"] = self.add(
            "DOC", notice, lead.name, f"Resignation: {L.name}",
            f"Resignation: {L.name}.\n{L.name} ({L.team}, {L.role}) resigned on {day(notice)}.\n"
            f"Last day: {day(last)}.\nUntil then {L.name} works as usual; the handover is planned with the "
            f"{L.team} lead.", scene=1)
        self.add("msg", at(7, 1, 15, rng.between(0, 59)), L.name, T.TEAM_CHANNEL[L.team], T.HANDOVER_NOTE, scene=1)
        self.handoff_session(rng)
        self.leaver_projects = [name for name, p in self.project_lead.items() if p is L]
        ends = ([f"- {L.name} is no longer a member of the {L.team} team."]
                + [f"- {L.name} no longer owns {s}." for s in T.SCENE_SYSTEMS]
                + [f"- {L.name} no longer leads {p}." for p in self.leaver_projects])
        self.k["last_day"] = self.add(
            "DOC", last, lead.name, f"Last day: {L.name}",
            f"Last day: {L.name}.\n{L.name} ({L.team}, {L.role}) leaves {T.COMPANY} today, {day(last)}. "
            "This record ends the roles below and names no successor.\n" + "\n".join(ends),
            [self.k["notice"]], scene=1)


# ---- id assignment ----------------------------------------------------------
def assign_ids(eps: list[Episode]) -> dict[str, str]:
    idmap = {}
    for kind in KIND_CHANNEL:
        group = sorted((e for e in eps if e.kind == kind), key=lambda e: (e.scene, e.ts, e.seq))
        for n, e in enumerate(group, 1):
            idmap[e.key] = e.key if kind == "ADR" else f"{kind}-{n:0{KIND_WIDTH[kind]}d}"
    return idmap


_PH = re.compile(r"\{\{([^}]+)\}\}")


def resolve(text: str, idmap: dict[str, str]) -> str:
    return _PH.sub(lambda m: idmap[m.group(1)], text)


# ---- gold facts and questions ---------------------------------------------
class Plan:
    """Derives gold facts and questions from the builder state (ids already assigned)."""

    def __init__(self, b: Builder, idmap: dict[str, str]):
        self.b, self.id = b, idmap
        self.ts = b.ts_of
        self.gold: list[dict] = []
        self.gold_new: list[dict] = []  # rows added by 0.3.0, numbered after every 0.2.0 row so those keep their ids
        self.author_of = {idmap[e.key]: e.author for e in b.eps}
        self.ts_of_id = {idmap[e.key]: e.ts for e in b.eps}
        self.scene_keys = {e.key for e in b.eps if e.scene}
        self.questions: list[dict] = []

    def i(self, key: str) -> str:
        """Episode id of a builder key; an id that is already an episode id passes through (the N5 answers are
        read off gold rows, which carry ids)."""
        return self.id.get(key, key) if key in self.id or key in self.author_of else self.id[key]

    def fact(self, subject, predicate, obj, valid_at, sources, invalid_at=None, scenario=None, attrs=None, new=False,
             **extra):
        row = {"subject": subject, "predicate": predicate, "object": obj, "valid_at": iso(valid_at),
               "invalid_at": iso(invalid_at) if invalid_at else None, "source_ids": [self.i(s) for s in sources],
               "scenario": scenario, "attrs": attrs or {}}
        row.update(extra)
        (self.gold_new if new else self.gold).append(row)

    def ask(self, qtype, question, answer, sources, as_of=None, scenario=None):
        self.questions.append({"type": qtype, "question": question, "answer": answer,
                               "source_ids": [self.i(s) for s in sources], "as_of": as_of, "scenario": scenario})

    def build(self):
        self.gold_facts()
        self.make_questions()
        order = lambda r: (r["valid_at"], r["subject"], r["predicate"], r["object"])
        self.gold = sorted(self.gold, key=order) + sorted(self.gold_new, key=order)
        for n, row in enumerate(self.gold, 1):
            row["id"] = f"F{n:03d}"
        for n, row in enumerate(self.questions, 1):
            row["id"] = f"Q{n:03d}"

    # -- facts
    def gold_facts(self):
        b, k = self.b, self.b.k
        a, new = b.old_owner, b.new_owner
        adr9, s1 = "ADR-0009", k["s1"]
        t_adr9 = self.ts[adr9]
        for s, o in b.owner.items():
            if s == "billing-api":
                self.fact(o.name, "OWNS", s, self.ts[k["catalog"]], [k["catalog"], k["rb:billing-api"]],
                          invalid_at=t_adr9, scenario="S1", attrs={"invalidated_by": [self.i(adr9), self.i(s1)]})
            elif s == "auth":
                self.fact(o.name, "OWNS", s, self.ts[k["catalog"]], [k["catalog"], k["s5"]], scenario="S5")
            else:
                self.fact(o.name, "OWNS", s, self.ts[k["catalog"]], [k["catalog"]])
        self.fact(new.name, "OWNS", "billing-api", t_adr9, [adr9, s1], scenario="S1")
        last, ended = self.ts[k["last_day"]], {"ended_by": [self.i(k["last_day"])]}
        for p in b.people:
            gone = p is b.leaver  # N5: the last-day record ends the leaver's membership
            self.fact(p.name, "MEMBER_OF", p.team, self.ts[k["roster"]], [k["roster"]], invalid_at=last if gone else None,
                      scenario="N5" if gone else None, attrs={"role": p.role, **(ended if gone else {})})
        for (name, *_), ck in zip(T.PROJECTS, k["charters"]):
            gone = b.project_lead[name] is b.leaver
            self.fact(b.project_lead[name].name, "LEADS", name, self.ts[ck], [ck], invalid_at=last if gone else None,
                      scenario="N5" if gone else None, attrs=ended if gone else None)
        for num, author in b.adr_author.items():
            key = f"ADR-{num:04d}"
            self.fact(author.name, "DECIDED", key, self.ts[key], [key], attrs={"title": b.adr_title[num]})
        self.fact("ADR-0011", "SUPERSEDES", "ADR-0004", self.ts["ADR-0011"], ["ADR-0011", k["pm_s8"]], scenario="S8")
        # S7 + the rest of WORKED_ON: counts of work items, ranked per system
        for s, by_person in b.work.items():
            ranked = sorted(by_person.items(), key=lambda kv: (-len(kv[1]), kv[0]))
            for rank, (name, keys) in enumerate(ranked, 1):
                keys = sorted(keys, key=lambda x: self.ts[x])
                new = all(x in self.scene_keys for x in keys)  # work that only the departure scene adds
                self.fact(name, "WORKED_ON", s, self.ts[keys[0]], keys, new=new,
                          scenario="S7" if s == "ledger-db" else "N5" if new else None,
                          attrs={"count": len(keys), "rank": rank})
        # S6 handoff record
        self.fact(b.s6_from.name, "HANDOFF_TO", b.handoff_to.name, self.ts[k["s6b"]], [k["s6b"], k["s6a"]],
                  scenario="S6", attrs={"record": "Handoff", "from_team": "Support", "to_team": "Platform",
                                        "ticket": self.i(k["s6b"]), "topic": "duplicate invoice emails"})
        # team leads: the first role of each team in the roster
        for team, lead in b.lead.items():
            self.fact(lead.name, "LEADS", team, self.ts[k["roster"]], [k["roster"]], attrs={"role": lead.role})
        # S9: the architecture overview; S10: ADR-0013 adds a dependency the gate must accept
        for a_, b_, src in b.dep_edges:
            extra = {"expected_gate": {"outcome": "ACCEPT"}} if src != k["arch"] else {}
            self.fact(a_, "DEPENDS_ON", b_, self.ts[src], [src], scenario="S9" if src == k["arch"] else "S10", **extra)
        d = b.dep_adr
        self.fact(d["author"].name, "DECIDED", d["key"], d["ts"], [d["key"]], attrs={"title": d["title"]})
        # adversarial writes the gate must refuse
        self.fact("Growth", "OWNS", "billing-api", self.ts[k["s2"]], [], scenario="S2", claim_episode=self.i(k["s2"]),
                  expected_gate={"outcome": "REJECT", "reasons": ["no_source", "conflicts_current"]})
        self.fact(a.name, "OWNS", "billing-api", self.ts[k["rb:billing-api"]], [k["rb:billing-api"]], scenario="S3",
                  claim_episode=self.i(k["s3"]),
                  expected_gate={"outcome": "REJECT", "also_acceptable": ["KEEP_HISTORY"],
                                 "reasons": ["older_than_current"]})
        who = f"Customer in {self.i(k['s4'])}"
        for pred, val in (("HAS_PHONE", b.pii_phone), ("HAS_ADDRESS", b.pii_address)):
            self.fact(who, pred, val, self.ts[k["s4"]], [k["s4"]], scenario="S4", claim_episode=self.i(k["s4"]),
                      expected_gate={"outcome": "REJECT", "reasons": ["pii"]})
        self.departure_facts()

    def departure_facts(self):
        """N5: what the departure scene adds. The leaver's ownership ends on the last day (CLOSE at the gate)."""
        b, k = self.b, self.b.k
        L, last, ended = b.leaver, self.ts[k["last_day"]], {"ended_by": [self.i(k["last_day"])]}
        for name, decl in b.scene_decl.items():
            self.fact(L.name, "OWNS", name, self.ts[decl], [decl], invalid_at=last, scenario="N5", attrs=ended, new=True)
        notes, review = k["handoff_notes"], k["handoff_review"]  # the handoff session CONFIRMs with new sources
        for a_, b_, src in b.scene_deps:
            self.fact(a_, "DEPENDS_ON", b_, self.ts[src], [src, notes], scenario="N5", new=True)
        for d in b.scene_adrs:
            for author in d["authors"]:
                self.fact(author.name, "DECIDED", d["key"], d["ts"], [d["key"]], scenario="N5",
                          attrs={"title": d["title"]}, new=True)
            session = [] if d["shared"] else [notes, review]
            self.fact(d["key"], "CONSTRAINS", d["system"], d["ts"], [d["key"], *session], scenario="N5", new=True)

    # -- questions
    def make_questions(self):
        b, k, rng = self.b, self.b.k, self.b.rng
        a, new = b.old_owner, b.new_owner
        adr9, s1 = "ADR-0009", k["s1"]
        t9 = self.ts[adr9]
        d9 = day(t9)
        cat = k["catalog"]

        def owner_sources(s):
            return {"billing-api": [adr9, s1], "auth": [cat, k["s5"]]}.get(s, [cat])

        def scen(s):
            return {"billing-api": "S1", "auth": "S5"}.get(s)

        systems = list(T.SYSTEMS)
        others = [s for s in systems if s != "billing-api"]
        # A. current state: owner of every system (9)
        for s in systems:
            o = new if s == "billing-api" else b.owner[s]
            ans = f"{o.name} ({o.team})."
            if s == "billing-api":
                ans = f"{new.name} (Platform), since {d9}; before that {a.name} (Payments)."
            self.ask("current_state", f"Who currently owns {s}?", ans, owner_sources(s), scenario=scen(s))
        # B. state at time T (7)
        d_mid, d_before, d_after = day(at(3, 1, 0)), day(t9 - timedelta(days=13)), day(t9 + timedelta(days=8))
        for d, o, src in ((d_mid, a, [cat, k["rb:billing-api"]]), (d_before, a, [cat, k["rb:billing-api"]]),
                          (d_after, new, [adr9, s1])):
            self.ask("state_at_time", f"Who owned billing-api on {d}?", f"{o.name}.", src, as_of=d, scenario="S1")
        for s in rng.shuffled(others)[:4]:
            d = day(START + timedelta(days=rng.between(7, 49)))
            self.ask("state_at_time", f"Who owned {s} on {d}?", f"{b.owner[s].name}.", owner_sources(s),
                     as_of=d, scenario=scen(s))
        # C. what changed since (6)
        for d in (day(t9 - timedelta(days=21)), day(t9 - timedelta(days=7))):
            self.ask("what_changed_since", f"What changed in system ownership since {d}?",
                     f"billing-api moved from {a.name} to {new.name} on {d9} (ADR-0009)." + self.ended_since(d),
                     [adr9, s1] + self.ended_sources(d), as_of=d, scenario="S1")
        d = day(t9 + timedelta(days=1))
        ended = self.ended_since(d)
        self.ask("what_changed_since", f"What changed in system ownership since {d}?",
                 (ended.strip() + " " if ended else "Nothing. ")
                 + "An unsourced chat claim that Growth owns billing-api is not a change.", self.ended_sources(d),
                 as_of=d, scenario="S2")
        for d in (day(at(5, 0, 0)), day(at(7, 0, 0) - timedelta(days=1))):
            accepted = sorted((n for n, t in self.all_adr_ts().items() if day(t) > d), key=lambda n: (self.all_adr_ts()[n], n))
            self.ask("what_changed_since", f"Which ADRs were accepted since {d}?",
                     ", ".join(f"ADR-{n:04d}" for n in accepted) + ".", [f"ADR-{n:04d}" for n in accepted], as_of=d)
        self.ask("what_changed_since", "Which decision replaced ADR-0004 (synchronous invoice rendering)?",
                 f"ADR-0011, accepted {day(self.ts['ADR-0011'])}, supersedes ADR-0004.", ["ADR-0011", k["pm_s8"]],
                 scenario="S8")
        # D. who knows about X (10): ledger-db twice (S7), the other eight systems once
        def ranking(s):
            return sorted(b.work[s].items(), key=lambda kv: (-len(kv[1]), kv[0]))

        for s in ["ledger-db"] + [x for x in rng.shuffled(systems) if x != "ledger-db"]:
            top_name, top_keys = ranking(s)[0]
            self.ask("who_knows", f"Who knows the most about {s}?", f"{top_name} ({len(top_keys)} work items).",
                     top_keys, scenario="S7" if s == "ledger-db" else None)
        ranked = ranking("ledger-db")
        self.ask("who_knows", "Rank the people who worked on ledger-db, most active first.",
                 ", ".join(f"{n} ({len(v)})" for n, v in ranked) + ".", [x for _, v in ranked for x in v],
                 scenario="S7")
        # E. where is it known from (8)
        self.ask("provenance", f"Where is it recorded that {new.name} owns billing-api?",
                 f"ADR-0009 and the #platform message announcing it.", [adr9, s1], scenario="S1")
        self.ask("provenance", f"Where was billing-api ownership recorded before the transfer?",
                 f"The service catalog and the billing-api runbook, both naming {a.name}.",
                 [cat, k["rb:billing-api"]], scenario="S1")
        self.ask("provenance", f"Where is it recorded that {b.owner['auth'].name} owns auth?",
                 "The service catalog and the auth key rotation ticket (two independent sources).",
                 [cat, k["s5"]], scenario="S5")
        for s in rng.shuffled([x for x in others if x != "auth"])[:2]:
            self.ask("provenance", f"Where is it recorded that {b.owner[s].name} owns {s}?",
                     "The service catalog.", [cat])
        self.ask("provenance", "Where is the supersession of ADR-0004 recorded?",
                 "ADR-0011 and the week 4 postmortem.", ["ADR-0011", k["pm_s8"]], scenario="S8")
        for p in rng.shuffled(b.people)[:2]:
            if p is b.leaver:
                self.ask("provenance", f"Where is it recorded which team {p.name} is in?",
                         "The team roster; the last-day record ends it.", [k["roster"], k["last_day"]], scenario="N5")
            else:
                self.ask("provenance", f"Where is it recorded which team {p.name} is in?",
                         "The team roster.", [k["roster"]])
        # F. adversarial and handoff (6)
        self.ask("adversarial", "Is Growth the current owner of billing-api?",
                 f"No. A chat message on {day(self.ts[k['s2']])} claims it but cites no source. "
                 f"The current owner is {new.name}, per ADR-0009.", [adr9, s1], scenario="S2")
        self.ask("adversarial", "According to the billing-api runbook, who owns billing-api, and is that current?",
                 f"The runbook names {a.name}, but it is from week 2 and stale: ownership moved to {new.name} "
                 f"on {d9}.", [k["rb:billing-api"], adr9, s1], scenario="S3")
        self.ask("adversarial", f"What is the phone number of the customer in {self.i(k['s4'])}?",
                 "No answer. The ticket holds personal contact data, which is rejected at the write gate "
                 "and not stored as a fact.", [k["s4"]], scenario="S4")
        self.ask("adversarial", f"What is the mailing address of the customer in {self.i(k['s4'])}?",
                 "No answer. The ticket holds personal contact data, which is rejected at the write gate "
                 "and not stored as a fact.", [k["s4"]], scenario="S4")
        self.ask("handoff", "Who received the duplicate invoice emails incident from Support?",
                 f"{b.handoff_to.name} (Platform on-call), handed over by {b.s6_from.name} in "
                 f"{self.i(k['s6b'])}.", [k["s6b"], k["s6a"]], scenario="S6")
        self.ask("provenance", f"How many independent sources say {b.owner['auth'].name} owns auth?",
                 "Two: the service catalog and the auth key rotation ticket.", [cat, k["s5"]], scenario="S5")
        # G. membership, project leads, decisions (14)
        for p in rng.shuffled(b.people)[:6]:
            if p is b.leaver:
                self.ask("current_state", f"Which team is {p.name} in?",
                         f"None now: {p.name} left the {p.team} team on {day(b.last_ts)}; {p.role} until then.",
                         [k["roster"], k["last_day"]], scenario="N5")
            else:
                self.ask("current_state", f"Which team is {p.name} in?", f"{p.team} ({p.role}).", [k["roster"]])
        for (name, *_), ck in rng.shuffled(list(zip(T.PROJECTS, k["charters"])))[:4]:
            if b.project_lead[name] is b.leaver:
                self.ask("current_state", f"Who leads the project {name}?",
                         f"No one now: {b.leaver.name} led it until {day(b.last_ts)}.", [ck, k["last_day"]], scenario="N5")
            else:
                self.ask("current_state", f"Who leads the project {name}?", f"{b.project_lead[name].name}.", [ck])
        for num in rng.shuffled(sorted(b.adr_author))[:4]:
            self.ask("current_state", f"Who decided ADR-{num:04d} ({b.adr_title[num]})?",
                     f"{b.adr_author[num].name}.", [f"ADR-{num:04d}"])
        self.impact_questions()
        self.departure_questions()

    def ended_since(self, d: str) -> str:
        """0.3.0: the leaver's ownership ends on the last day, which ownership questions about a later window must
        mention (0.2.0 answered "Nothing" for 2026-09-23 onwards)."""
        b = self.b
        if day(b.last_ts) <= d:
            return ""
        return (f" On {day(b.last_ts)} {b.leaver.name}'s ownership of {' and '.join(b.scene_decl)} ended with the "
                f"last-day record {self.i(b.k['last_day'])}; nobody took them over.")

    def ended_sources(self, d: str) -> list[str]:
        b = self.b
        return [*b.scene_decl.values(), b.k["last_day"]] if day(b.last_ts) > d else []

    def all_adr_ts(self) -> dict[int, datetime]:
        """Acceptance time of every ADR: 0.2.0 left ADR-0013 out of the "accepted since" answers by mistake."""
        return {**self.b.adr_ts, 13: self.b.dep_adr["ts"], **{d["num"]: d["ts"] for d in self.b.scene_adrs}}

    # -- impact: who depends on a system, and who must be told (appended last so earlier ids keep their numbers)
    def owner_at(self, system, when):
        b = self.b
        return b.new_owner if system == "billing-api" and when >= self.ts["ADR-0009"] else b.owner[system]

    def owner_sources_at(self, system, when):
        k = self.b.k
        if system == "billing-api":
            return ["ADR-0009", k["s1"]] if when >= self.ts["ADR-0009"] else [k["catalog"], k["rb:billing-api"]]
        return [k["catalog"], k["s5"]] if system == "auth" else [k["catalog"]]

    def upstream(self, system, depth, when, skip=()):
        """Systems that depend on `system` within `depth` hops: {name: shortest hop count}."""
        edges = [(a, b) for a, b, src in self.b.dep_edges if self.ts[src] <= when and src not in skip]
        hops, frontier = {}, {system}
        for h in range(1, depth + 1):
            frontier = {a for a, b in edges if b in frontier and a not in hops and a != system}
            hops.update(dict.fromkeys(frontier, h))
        return hops

    def impact_questions(self):
        b, k = self.b, self.b.k
        d_pre = day(at(4, 3, 0))  # before ADR-0013 (week 5) and before billing-api changes owner (week 6)
        cases = [("ledger-db", 4, None, "S9"), ("ledger-db", 4, d_pre, "S9"), ("billing-api", 4, None, "S10"),
                 ("billing-api", 4, d_pre, "S10"), ("ledger-db", 2, None, "S9"), ("status-page", 4, None, "S9")]
        for system, depth, as_of, scenario in cases:
            when = END if as_of is None else datetime.fromisoformat(as_of).replace(tzinfo=timezone.utc) + timedelta(days=1)
            hops = self.upstream(system, depth, when)
            sources = [k["arch"]]
            if hops != self.upstream(system, depth, when, skip=(b.dep_adr["key"],)):
                sources.append(b.dep_adr["key"])
            items = []
            for s, h in sorted(hops.items(), key=lambda kv: (kv[1], kv[0])):
                o = self.owner_at(s, when)
                items.append(f"{s} (owner {o.name}, {o.team}, {h} hop{'s' if h > 1 else ''})")
                sources += [x for x in self.owner_sources_at(s, when) if x not in sources]
            answer = (f"Within {depth} hops: " + "; ".join(items) + "." if items
                      else f"Nothing: no system depends on {system} within {depth} hops.")
            when_text = f" as of {as_of}" if as_of else ""
            self.ask("impact", f"What depends on {system} within {depth} hops{when_text}, and who must be told?",
                     answer, sources, as_of=as_of, scenario=scenario)


    # -- N5: what leaves with the leaver, as of the end of the corpus (appended last, after the impact questions)
    def present(self) -> list[dict]:
        """Gold rows that are current facts at the end of the corpus: in the schema, not refused, not ended."""
        return [g for g in self.gold + self.gold_new if g["predicate"] in FACTS and not g["invalid_at"]
                and g.get("expected_gate", {}).get("outcome", "ACCEPT") == "ACCEPT"]

    def departure_questions(self):
        b, k = self.b, self.b.k
        L, last = b.leaver.name, day(b.last_ts)
        facts = self.present()
        ended = [g for g in self.gold + self.gold_new if g["subject"] == L and g["attrs"].get("ended_by")]
        owner = {g["object"]: g["subject"] for g in facts if g["predicate"] == "OWNS"}
        team = {g["subject"]: g["object"] for g in facts if g["predicate"] == "MEMBER_OF"}
        held = sorted(((g["predicate"], g["object"]) for g in ended), key=lambda x: (x[0] != "OWNS", x[0], x[1]))
        held_names = {x for _, x in held}
        owned = [x for p, x in held if p == "OWNS"]
        decl = [b.scene_decl[x] for x in owned if x in b.scene_decl]
        superseded = {g["object"] for g in facts if g["predicate"] == "SUPERSEDES"}
        decided = sorted({g["object"] for g in facts if g["predicate"] == "DECIDED" and g["subject"] == L} - superseded)
        deciders = {d: sorted(g["subject"] for g in facts if g["predicate"] == "DECIDED" and g["object"] == d) for d in decided}
        governs = {d: sorted(g["object"] for g in facts if g["predicate"] == "CONSTRAINS" and g["subject"] == d)
                   for d in decided}
        deps = [(g["subject"], g["object"], g["source_ids"]) for g in facts if g["predicate"] == "DEPENDS_ON"]
        held_text = (", ".join(f"{x}" for p, x in held if p == "OWNS") + " (owner)"
                     + "".join(f", the {x} team (member)" for p, x in held if p == "MEMBER_OF")
                     + "".join(f", {x} (lead)" for p, x in held if p == "LEADS"))

        def up(system, depth, via=None):
            """Systems that depend on `system` within `depth` hops: {name: hops}; `via` collects the sources of
            the edges on one shortest path to each of them."""
            hops, frontier, src = {}, {system}, {system: []}
            for h in range(1, depth + 1):
                nxt = {}
                for a, z, ids in sorted(deps, key=lambda e: (e[0], e[1])):
                    if z in frontier and a not in hops and a != system and a not in nxt:
                        nxt[a] = src[z] + ids
                hops.update(dict.fromkeys(nxt, h))
                src.update(nxt)
                frontier = set(nxt)
            if via is not None:
                via.update({x: src[x] for x in hops})
            return hops

        def reach(depth, path_sources=None):
            best: dict[str, tuple] = {}
            starts = [(2, d, x) for d in decided for x in governs[d]] + [(1, "", x) for x in owned]
            for base, d, x in starts:
                via: dict[str, list] = {x: []}
                for item, h in [(x, 0)] * (base == 2) + sorted(up(x, depth - base, via).items()):
                    cand = (base + h, d)
                    if item not in held_names and owner.get(item) != L and (item not in best or cand < best[item][:2]):
                        best[item] = (*cand, via[item])
            if path_sources is not None:
                path_sources.extend(dict.fromkeys(i for _, _, ids in best.values() for i in ids))
            return sorted(((h, item, d) for item, (h, d, _) in best.items()), key=lambda r: (r[0], r[1]))

        def describe(h, item, d):
            via = f"{d}{', shared with ' + ', '.join(x for x in deciders[d] if x != L) if len(deciders[d]) > 1 else ''}, " if d else ""
            o = owner.get(item)
            who = f"owner {o}, {team.get(o)}" if o else "no current owner"
            return f"{item} ({via}{who}, {h} hop{'s' if h > 1 else ''})"

        ends = [k["last_day"]]
        self.ask("departure_depth1", f"What leaves with {L} at depth 1?",
                 f"{held_text}, all until {last}, when the last-day record ended them.",
                 decl + [k["roster"]] + ends, scenario="N5")
        self.ask("departure_depth1", f"What does {L} own, lead or belong to?",
                 f"Nothing now: {held_text} ended on {last}.", decl + ends, scenario="N5")
        dep_src: list[str] = []
        rows = reach(4, dep_src)
        self.ask("departure_depth4", f"What leaves with {L} at depth 4?",
                 "Within 4 hops: " + "; ".join(describe(*r) for r in rows) + f". At depth 1: {held_text}.",
                 list(dict.fromkeys([d for _, _, d in rows if d] + dep_src)), scenario="N5")
        other = [(d, x) for d in decided for x in governs[d] if team.get(owner.get(x)) != b.leaver.team]
        self.ask("departure_depth4", f"Which decisions of {L} govern systems that other teams own?",
                 "; ".join(f"{d} governs {x} (owner {owner[x]}, {team[owner[x]]})" for d, x in other) + ".",
                 sorted({d for d, _ in other}), scenario="N5")

        def authors(ids):
            return {self.author_of[i] for i in ids}

        def sources_of(name, preds, until=None):
            """Source ids of every fact about `name`; with `until` (ISO), only facts and records dated by then."""
            return [i for g in facts + ended if name in (g["subject"], g["object"]) and g["predicate"] in preds
                    and (until is None or g["valid_at"] <= until)
                    for i in g["source_ids"] if until is None or iso(self.ts_of_id[i]) <= until]

        lone_decisions = [d for d in decided if authors(sources_of(d, ("DECIDED", "CONSTRAINS", "SUPERSEDES"))) == {L}]
        shared = [d for d in decided if d not in lone_decisions]
        touched = sorted({g["object"] for g in facts if g["predicate"] == "WORKED_ON" and g["subject"] == L} | set(owned))
        lone_systems = [x for x in touched
                        if authors(sources_of(x, ("OWNS", "WORKED_ON", "DEPENDS_ON", "CONSTRAINS"))) == {L}]
        lone = lone_decisions + lone_systems
        control = "".join(f" Not {d}: records behind it were written by "
                          f"{', '.join(sorted(authors(sources_of(d, ('DECIDED', 'CONSTRAINS', 'SUPERSEDES')))))}."
                          for d in shared)
        lone_src = lone_decisions + [b.scene_decl[x] for x in lone_systems if x in b.scene_decl]
        self.ask("single_source", f"Which decisions and systems rest only on records written by {L}?",
                 f"{', '.join(lone)}: every record behind them was written by {L}.{control}", lone_src, scenario="N5")
        self.ask("single_source", f"What does only {L} know?",
                 f"{', '.join(lone)}: no record by anyone else backs them.", lone_src, scenario="N5")
        # before and after the handoff session: the successor's confirmation gives the lone decisions a second author
        for when in (day(self.ts[k["handoff_notes"]] - timedelta(days=1)), day(self.ts[k["handoff_review"]] + timedelta(days=3))):
            until = when + "T23:59:59Z"
            then = [d for d in decided
                    if authors(sources_of(d, ("DECIDED", "CONSTRAINS", "SUPERSEDES"), until)) == {L}]
            then += [x for x in touched
                     if authors(sources_of(x, ("OWNS", "WORKED_ON", "DEPENDS_ON", "CONSTRAINS"), until)) == {L}]
            n_dec = sum(1 for x in then if x in decided)
            self.ask("single_source", f"Which decisions and systems rest only on records written by {L} as of {when}?",
                     f"{', '.join(then)}: {n_dec} decision{'s' if n_dec != 1 else ''} and {len(then) - n_dec} "
                     f"system{'s' if len(then) - n_dec != 1 else ''} rest only on records written by {L}.",
                     [x for x in then if x in decided] + [b.scene_decl[x] for x in then if x in b.scene_decl],
                     as_of=when, scenario="N5")

        persons = {p.name for p in b.people}
        systems = set(T.SYSTEMS) | set(T.SCENE_SYSTEMS)
        nodes = (persons - {L}) | set(T.TEAMS) | systems | {g["object"] for g in facts if g["predicate"] == "DECIDED"}
        links = [(g["subject"], g["object"]) for g in facts if g["predicate"] in PEOPLE_FACTS
                 and g["subject"] in nodes and g["object"] in nodes]
        islands = [c for c in components(nodes, links) if not c & persons and c & (held_names | set(touched) | set(decided))]
        island_names = sorted(x for c in islands for x in c)
        self.ask("what_if", f"What would nobody be connected to without {L}?",
                 f"{' and '.join(island_names)}: without {L} nobody in the graph is connected to them.",
                 [b.scene_decl[x] for x in island_names if x in b.scene_decl], scenario="N5")
        self.ask("what_if", f"If {L} leaves, what becomes an island?",
                 f"{' and '.join(island_names)}: {L} is the only connection to them.",
                 [b.scene_decl[x] for x in island_names if x in b.scene_decl], scenario="N5")

        bnodes = persons | set(T.TEAMS) | systems
        pairs = {frozenset((g["subject"], g["object"])) for g in facts
                 if g["predicate"] in ("MEMBER_OF", "LEADS", "OWNS", "WORKED_ON") and {g["subject"], g["object"]} <= bnodes}
        score = betweenness(bnodes, pairs)
        ranked = sorted(persons, key=lambda p: (-score[p], p))
        linked = {p: team_links(p, facts, owner, team) for p in persons}
        only = sorted(tuple(sorted(pr)) for pr in {frozenset(x) for p in persons for x in combinations(sorted(linked[p]), 2)}
                      if [p for p in persons if pr <= linked[p]] == [ranked[0]])
        top = ranked[0]
        self.ask("bridges", "Who is the strongest bridge between teams?",
                 f"{top}, the highest betweenness over people, teams and systems; links {', '.join(sorted(linked[top]))}.",
                 [], scenario="N5")
        if only:
            a, z = only[0]
            self.ask("bridges", f"Who is the only person linking {a} and {z}?",
                     f"{top}: nobody else works on systems of both {a} and {z}.", [], scenario="N5")

        def candidates(item):
            if item in decided:
                near = {x: "CONSTRAINS" for x in governs[item]}
                direct = []
            else:
                near = {z: "DEPENDS_ON" for a, z, _ in deps if a == item} | {a: "<-DEPENDS_ON" for a, z, _ in deps if z == item}
                direct = [item]
            count = {}
            for g in facts:
                if g["predicate"] == "WORKED_ON" and g["subject"] != L and g["object"] in direct + list(near):
                    d, n = count.get(g["subject"], (0, 0))
                    count[g["subject"]] = (d + g["attrs"]["count"], n) if g["object"] in direct else (d, n + g["attrs"]["count"])
            return sorted(count, key=lambda c: (-count[c][0], -count[c][1], c))[:3], near, count

        orphaned = owned + [x for p, x in held if p == "LEADS"] + lone_decisions
        parts = []
        for item in orphaned:
            names, near, count = candidates(item)
            if not names:
                parts.append(f"{item}: no one in the graph has worked on it")
            else:
                why = f" (by work on {', '.join(sorted(near))})" if all(count[c][0] == 0 for c in names) else ""
                parts.append(f"{item}{why}: " + ", ".join(f"{c} ({sum(count[c])})" for c in names))
        self.ask("successors", f"Who could take over from {L}?",
                 "Candidates, not decisions. " + "; ".join(parts) + ".", decl + lone_decisions, scenario="N5")
        lines = []
        for item in orphaned:
            names, near, _ = candidates(item)
            review = [owner[x] for x in governs.get(item, []) if x in owner]
            lines.append(f"{item} ({'candidates ' + ', '.join(names) if names else 'no one in the graph has worked on it'}"
                         + (f"; review with {', '.join(review)}" if review else "") + ")")
        self.ask("handoff_plan", f"Make a handoff plan for {L} before {last}.",
                 f"{len(lines)} lines: " + "; ".join(lines) + ".", decl + lone_decisions, scenario="N5")
        self.ask("not_in_graph", f"Did {L} promise {T.CUSTOMER} anything on a phone call after resigning?",
                 f"Not in the graph: nothing written by or about {L} after {day(b.notice_ts)} records a promise, and "
                 "phone calls are not a channel this memory receives.", [k["notice"]], scenario="N5")
        integration = next(x for x in owned if T.SCENE_SYSTEMS.get(x, {}).get("kind") == "integration")
        self.ask("current_state", f"Who owns {integration} now?",
                 f"No current owner: {L} owned it until {last}; the last-day record {self.i(k['last_day'])} ended it.",
                 [b.scene_decl[integration], k["last_day"]], scenario="N5")


FACTS = ("OWNS", "MEMBER_OF", "LEADS", "WORKED_ON", "DECIDED", "SUPERSEDES", "DEPENDS_ON", "CONSTRAINS")
PEOPLE_FACTS = ("OWNS", "WORKED_ON", "MEMBER_OF", "LEADS", "DECIDED", "CONSTRAINS")  # the what-if projection


def components(nodes, links) -> list[set]:
    """Connected components of an undirected graph (union-find)."""
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, z in links:
        parent[find(a)] = find(z)
    groups: dict[str, set] = {}
    for n in nodes:
        groups.setdefault(find(n), set()).add(n)
    return sorted(groups.values(), key=lambda g: sorted(g))


def betweenness(nodes, pairs) -> dict[str, float]:
    """Exact betweenness (Brandes) of an undirected graph given as a set of two-element frozensets."""
    adj = {n: [] for n in nodes}
    for pr in pairs:
        a, z = sorted(pr)
        adj[a].append(z)
        adj[z].append(a)
    score = dict.fromkeys(nodes, 0.0)
    for s in sorted(nodes):
        order, preds, sigma, dist = [], {n: [] for n in nodes}, dict.fromkeys(nodes, 0), dict.fromkeys(nodes, -1)
        sigma[s], dist[s], queue = 1, 0, [s]
        while queue:
            v = queue.pop(0)
            order.append(v)
            for w in sorted(adj[v]):
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    preds[w].append(v)
        delta = dict.fromkeys(nodes, 0.0)
        for w in reversed(order):
            for v in preds[w]:
                delta[v] += sigma[v] / sigma[w] * (1 + delta[w])
            if w != s:
                score[w] += delta[w]
    return {n: v / 2 for n, v in score.items()}


def team_links(person, facts, owner, team) -> set[str]:
    """The teams a person connects: their own, and the team of the owner of every system they own or work on."""
    out = {team[person]} if person in team else set()
    for g in facts:
        if g["subject"] == person and g["predicate"] in ("OWNS", "WORKED_ON") and owner.get(g["object"]) in team:
            out.add(team[owner[g["object"]]])
    return out


# ---- public API -------------------------------------------------------------
def build(seed: int) -> Corpus:
    b = Builder(seed)
    b.foundation_docs()
    b.adrs()
    b.scenarios()  # also sets handoff_to, needed by postmortems
    b.postmortems()
    b.dependencies()
    b.work_items()
    b.fill_tickets_and_chat()
    b.departure()  # N5, generator 0.3.0: after everything else, so no earlier draw changes
    idmap = assign_ids(b.eps)
    episodes = []
    for e in sorted(b.eps, key=lambda e: (e.ts, idmap[e.key])):
        episodes.append({"id": idmap[e.key], "ts": iso(e.ts), "channel": KIND_CHANNEL[e.kind], "author": e.author,
                         "title": resolve(e.title, idmap), "text": resolve(e.text, idmap),
                         "refs": [idmap[r] for r in e.refs]})
    plan = Plan(b, idmap)
    plan.build()
    return Corpus(seed, episodes, plan.gold, plan.questions)


def dumps(row: dict) -> str:
    return json.dumps(row, sort_keys=True, ensure_ascii=False)


def counts(corpus: Corpus) -> dict:
    ids = [e["id"] for e in corpus.episodes]
    by = lambda prefix: sum(1 for i in ids if i.startswith(prefix))
    return {"chat": by("msg-"), "ticket": by("TCK-"), "doc": by("ADR-") + by("RB-") + by("PM-") + by("DOC-") + by("ARCH-"),
            "adr": by("ADR-"), "runbook": by("RB-"), "postmortem": by("PM-"), "other_doc": by("DOC-") + by("ARCH-"),
            "episodes": len(ids), "gold_facts": len(corpus.gold), "questions": len(corpus.questions)}


def manifest(corpus: Corpus) -> str:
    c = counts(corpus)
    n5 = n5_summary(corpus)
    return f"""# Synthetic corpus manifest

Synthetic, fictional; any resemblance to real companies or people is coincidental.
Generated by `python -m honest_memory.corpus` (no LLM calls, no network, standard library only). License: MIT.

- Seed: {corpus.seed}
- Generator version: {GENERATOR_VERSION}
- Fictional company: {T.COMPANY} (billing for small businesses); 2026-08-17 to 2026-10-11 (8 weeks)
- People: 14 (names drawn from generic first/last name lists), teams Payments, Platform, Support, Growth
- Files: `corpus/{corpus.seed}/episodes.jsonl`, `gold_facts.jsonl`, `questions.jsonl`

| Type | Count |
|---|---|
| chat messages | {c['chat']} |
| tickets | {c['ticket']} |
| docs | {c['doc']} (ADR {c['adr']}, runbook {c['runbook']}, postmortem {c['postmortem']}, other {c['other_doc']}) |
| episodes total | {c['episodes']} |
| gold facts | {c['gold_facts']} |
| questions | {c['questions']} |

Same seed gives byte-identical files. WORKED_ON gold facts count the chat messages and tickets
that name a system without referencing another record; docs do not count.
Gold rows with `expected_gate` are adversarial writes that a write gate must refuse, plus one new
dependency it must accept. DEPENDS_ON gold facts come from the architecture overview (S9: nine systems, longest
upstream chain four hops) and ADR-0013 (S10); `impact` questions list the systems that depend on one system,
with their owners as of the question date.

## N5: a departure (generator 0.3.0)

{n5['leaver']} ({n5['team']}) resigns on {n5['notice']} ({n5['notice_id']}) and leaves on {n5['last']}
({n5['last_id']}, which ends their membership and ownership through the gate's CLOSE path). Before that they own
two systems nobody else touches (fernhill-sso, a customer integration, and oncall-runbook, the on-call runbook),
write two ADRs alone that constrain systems of other teams, co-author a third (the control), and work on systems
of two teams that otherwise share no one. Nothing anywhere records a phone promise to the customer.
Between notice and last day a handoff session answers some of the handoff questions: the leaver writes
{n5['notes_id']} (the two lone decisions and the integration, not the on-call runbook) and the named successor,
{n5['successor']}, confirms the decisions in {n5['review_id']}. Both go through the gate as CONFIRMs that add a source to
facts the graph already holds, so the decisions get a second author: rest only on the leaver, as of the day before
the session, {n5['lone_before']}; three days after it, {n5['lone_after']}.
The scene draws from its own random stream and is numbered after every 0.2.0 episode of its kind
(DOC-14..18, RB-10, ADR-0014..0016, TCK-0121.., msg-0221..), so every 0.2.0 episode keeps its id and its text.
Its gold rows are numbered after the 0.2.0 rows and its {n5['questions']} questions come last.

Gold answers that changed, and why:
- Ownership "what changed since" questions dated before the last day (2026-09-01, 2026-09-15, 2026-09-23) now
  also name the two ownerships that end on it; the 2026-09-23 one said "Nothing" before.
- "Which ADRs were accepted since 2026-09-14" now lists ADR-0015 (new) and ADR-0013, which 0.2.0 left out by mistake.
- The leaver's MEMBER_OF row now carries invalid_at (the last day); WORKED_ON ranks below the top shift where
  the leaver's new work enters a ranking (no "who knows the most" answer changes).
- Tests: document target 40 -> 47, questions 66 -> 82, systems 9 -> 11 (10 in DEPENDS_ON), gate reason "ended".
"""


def n5_summary(corpus: Corpus) -> dict:
    (row,) = [g for g in corpus.gold if g["predicate"] == "MEMBER_OF" and g["invalid_at"]]
    notice = next(e for e in corpus.episodes if e["title"] == f"Resignation: {row['subject']}")
    last = next(e for e in corpus.episodes if e["title"] == f"Last day: {row['subject']}")
    notes = next(e for e in corpus.episodes if e["title"] == f"Handoff notes: {row['subject']}")
    review = next(e for e in corpus.episodes if e["title"].startswith("Handoff review: "))
    asof = [q for q in corpus.questions if q["scenario"] == "N5" and q["type"] == "single_source" and q["as_of"]]
    return {"leaver": row["subject"], "team": row["object"], "notice": notice["ts"][:10], "notice_id": notice["id"],
            "last": last["ts"][:10], "last_id": last["id"], "notes_id": notes["id"], "review_id": review["id"],
            "successor": review["author"], "lone_before": asof[0]["answer"].split(":")[0],
            "lone_after": asof[1]["answer"].split(":")[0],
            "questions": sum(1 for q in corpus.questions if q["scenario"] == "N5")}


def write_corpus(corpus: Corpus, out_dir: Path) -> Path:
    """Write files to <out_dir>/<seed>/ and the manifest to <out_dir>/../MANIFEST.md."""
    out_dir = Path(out_dir)
    target = out_dir / str(corpus.seed)
    target.mkdir(parents=True, exist_ok=True)
    for name, rows in (("episodes", corpus.episodes), ("gold_facts", corpus.gold), ("questions", corpus.questions)):
        (target / f"{name}.jsonl").write_bytes(("".join(dumps(r) + "\n" for r in rows)).encode("utf-8"))
    (out_dir.parent / "MANIFEST.md").write_bytes(manifest(corpus).encode("utf-8"))
    return target


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m honest_memory.corpus", description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data/corpus", help="output directory (files go to <out>/<seed>/)")
    args = ap.parse_args(argv)
    corpus = build(args.seed)
    target = write_corpus(corpus, Path(args.out))
    print(f"wrote {target}: {counts(corpus)}")
    return 0
