"""Graph model shared by the write gate, the tools and the API.

Current facts are typed edges (OWNS, MEMBER_OF, ...). When the gate closes a fact it
deletes the typed edge and writes a WAS edge with the same endpoints, carrying the
original predicate and both timestamps. Traversals over the present therefore never
touch history (spike Q3: 1.3-1.5 ms vs 9-15 ms for a path filter on invalid_at).
"""

from __future__ import annotations

from dataclasses import dataclass

# Entity labels and the property that identifies each of them.
ENTITY_KEYS = {
    "Person": "name",
    "Team": "name",
    "System": "name",
    "Decision": "id",
    "Agent": "name",
    "Project": "name",      # led by a Person (LEADS); from the project charters
}

# Record labels written by the memory itself.
RECORD_KEYS = {
    "Episode": "id",        # a source record: chat message, ticket, document
    "GateDecision": "id",   # the receipt for every attempted write
    "Finding": "id",        # something an agent concluded and was allowed to keep
    "Handoff": "id",        # work passed from one agent or team to another
}

HISTORY_EDGE = "WAS"

# Edge types between records and entities, written by the gate and the ingest step. CLAIMED_IN points
# from a receipt to the episode a refused claim was read in, when that episode is not one of its sources.
RECORD_EDGES = frozenset({"MENTIONS", "REFERS_TO", "CITES", "ABOUT", "CLAIMED_IN",
                          "WROTE", "DERIVED_FROM", "FROM", "TO"})


@dataclass(frozen=True)
class Predicate:
    name: str
    subject: str
    object: str
    # "object": at most one current subject per object (one owner per system).
    # "subject": at most one current object per subject (one team per person).
    # None: many current edges may coexist (a person works on many systems).
    functional: str | None = None
    # Other labels the object may carry (a Person leads a Team or a Project).
    object_alt: tuple[str, ...] = ()

    @property
    def object_labels(self) -> tuple[str, ...]:
        return (self.object, *self.object_alt)


PREDICATES = {
    p.name: p
    for p in (
        Predicate("OWNS", "Person", "System", functional="object"),
        Predicate("MEMBER_OF", "Person", "Team", functional="subject"),
        Predicate("LEADS", "Person", "Team", functional="object", object_alt=("Project",)),
        Predicate("WORKED_ON", "Person", "System"),
        Predicate("DECIDED", "Person", "Decision"),
        Predicate("SUPERSEDES", "Decision", "Decision"),
        Predicate("DEPENDS_ON", "System", "System"),
        # The decision governs how that system must work (from an ADR's "Constrains:" line).
        Predicate("CONSTRAINS", "Decision", "System"),
    )
}

# Kinds of System nodes (property `kind`, absent on a plain service): a customer integration and a runbook are
# Systems too, so ownership, work, dependencies and constraints need no new edge types.
SYSTEM_KINDS = ("service", "integration", "runbook")

# Facts a person holds only while they work here; a "last day" record ends them through the gate (CLOSE).
ROLE_PREDICATES = ("OWNS", "MEMBER_OF", "LEADS")

# Predicates the gate refuses outright: they would store personal data (hackathon rule 6).
FORBIDDEN_PREDICATES = {"HAS_PHONE", "HAS_ADDRESS", "HAS_EMAIL", "HAS_BIRTHDATE", "HAS_CARD"}

# Range indexes created per tenant graph. Both FalkorDB 4.22 (Cloud free) and 6.0.x accept this form.
INDEXES = [(label, key) for label, key in {**ENTITY_KEYS, **RECORD_KEYS}.items()] + [("GateDecision", "repeat_key")]


def graph_name(tenant: str) -> str:
    """One FalkorDB graph per tenant: isolation is a different key, not a WHERE clause."""
    if not tenant or not tenant.isascii() or not all(c.isalnum() or c in "-_" for c in tenant):
        raise ValueError(f"tenant must be ASCII letters, digits, '-' or '_': {tenant!r}")
    return f"hm_{tenant}"
