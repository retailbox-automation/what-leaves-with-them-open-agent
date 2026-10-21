# Architecture

honest-memory is a shared memory for AI agents and a company brain on FalkorDB. Its one opinion: a memory that accepts everything is more dangerous than one that forgets, so every write passes a gate in code before it becomes a fact, and every answer carries the graph path back to its source.

## Flow

```
source record (chat message, ticket, document)            agent conclusion ("remember this")
        |                                                          |
        v                                                          v
   Episode node  --->  extractor proposes candidate facts  --->  WRITE GATE (code, deterministic)
                       (rules; optional LLM, proposal only)        |  schema? names resolve? source exists
                                                                   |  and mentions both ends? personal data?
                                                                   |  duplicate? conflicts with the present?
                                                                   v
                        GateDecision node (receipt) --CITES--> Episode
                                  |
              ACCEPT / CONFIRM / SUPERSEDE: typed edge written, old edge moved to WAS
              CLOSE: a fact ended without a replacement (a last day), edge moved to WAS
              REJECT: nothing written except the receipt
                                  |
   agents (support, ops, company brain) <--- fixed tool catalogue (human-written Cypher) <--- FalkorDB
   via MCP or HTTP; the model picks a tool and fills arguments, never writes Cypher
```

## Graph model

One FalkorDB graph per tenant (`hm_<tenant>`). A tenant is a different key, not a `WHERE` clause.

| Node | Key | Meaning |
|---|---|---|
| `Person` | name | employee of the (fictional) company; a resignation record sets `notice_at`, `last_day`, `notice_source` |
| `Team` | name | Payments, Platform, Support, Growth |
| `System` | name | service such as `billing-api`; `kind` is `integration` (a customer integration) or `runbook` when a document says so |
| `Decision` | id | ADR, e.g. `ADR-0009`; `status` accepted or superseded |
| `Episode` | id | a source record; `ts`, `channel`, `author`, `title`, `text` |
| `GateDecision` | id | receipt for every attempted write, accepted or not |
| `Agent` | name | an agent that reads and writes the memory |
| `Finding` | id | something an agent concluded and the gate let it keep |
| `Handoff` | id | work passed from one agent or team to another |

Current facts are typed edges. Each carries `valid_at` (epoch seconds), `source_ids`, `gate_id`.

| Edge | From → To | Functional |
|---|---|---|
| `OWNS` | Person → System | one current owner per system |
| `MEMBER_OF` | Person → Team | one current team per person |
| `LEADS` | Person → Team | one current lead per team |
| `WORKED_ON` | Person → System | many; `count` of work items |
| `DECIDED` | Person → Decision | many |
| `SUPERSEDES` | Decision → Decision | many |
| `DEPENDS_ON` | System → System | many |
| `CONSTRAINS` | Decision → System | many; the decision governs how that system must work (an ADR's `Constrains:` line) |

A customer integration and an on-call runbook are `System` nodes with a `kind`, so ownership, work, dependencies
and constraints need no new edge types.

Record edges: `(Episode)-[:MENTIONS]->(entity)`, `(Episode)-[:REFERS_TO]->(Episode)`, `(GateDecision)-[:CITES]->(Episode)`, `(GateDecision)-[:ABOUT]->(entity)`, `(Agent)-[:WROTE]->(Finding)`, `(Finding)-[:ABOUT]->(entity)`, `(Finding)-[:DERIVED_FROM]->(Episode|GateDecision)`, `(Handoff)-[:FROM]->(Agent|Team)`, `(Handoff)-[:TO]->(Agent|Person)`, `(Handoff)-[:CITES]->(Episode)`.

**History is structural.** Closing a fact deletes the typed edge and writes `(s)-[:WAS {predicate, valid_at, invalid_at, source_ids, gate_id, closed_by}]->(o)`. Queries about the present never see history; queries about the past read `WAS` plus the current edge. Measured on 200k edges, 4 hops: 1.3–1.5 ms structural vs 2.2–4.3 ms for a pushed-down `r.invalid_at IS NULL` filter vs 9–15 ms for a filter over the path (FalkorDB 4.22 and 6.0.2; reproduce with `python spike/spike_local.py --nodes 20000 --edges 200000 --hops 4`).

## Write gate

Input: a candidate `{subject, predicate, object, valid_at, source_ids, proposer}`. Checks run in this order; the first failing check decides. Every outcome writes a `GateDecision` with `outcome`, `reasons`, the candidate, and `CITES` edges to the episodes it was given.

| # | Check | Outcome when it fails |
|---|---|---|
| 1 | predicate is in the schema; forbidden predicates (`HAS_PHONE`, `HAS_ADDRESS`, …); time parses; names at most 200 characters | REJECT `bad_predicate` / `pii` / `bad_time` / `bad_name` |
| 2 | subject, object or attrs look like personal data (phone, email, street address, card number, also dotted or spelled-out forms); attrs only `role`, `title`, `note`, short strings | REJECT `pii` / `bad_attrs` |
| 3 | at least one source id given | REJECT `no_source` (plus `conflicts_current` when it would contradict the present) |
| 4 | every source id is an existing Episode, and none is newer than the episode the claim was read in | REJECT `source_not_found` / `source_after_claim` |
| 5 | one line of a cited episode mentions both subject and object by name and, for `OWNS`, `MEMBER_OF`, `LEADS`, `DECIDED`, `SUPERSEDES`, `DEPENDS_ON`, carries the relation's word; the edge keeps only the sources that do | REJECT `ungrounded` |
| 6 | subject and object resolve to known entities of the right label (new entities only from documents) | REJECT `unknown_entity` |
| 7 | same fact already current | CONFIRM: source added to the edge |
| 8 | functional predicate with a different current value: candidate newer → SUPERSEDE (old edge to `WAS`), older → REJECT `older_than_current`. Before that: a claim dated after all the sources that state it → REJECT `valid_after_source`; a fact that was closed and is asserted again on evidence that is not newer than the closing, or only points at the old document → REJECT `closed_fact`; a superseded decision superseding another → REJECT `superseded_decision` | |
| 9 | the same fact was ended after the time the candidate speaks of (replayed old evidence) | REJECT `already_ended` |
| 10 | otherwise | ACCEPT |

**Ending a fact (CLOSE).** `submit_end(store, subject, predicate, object, sources, at)` ends a current fact
without a replacement, for a person's last day. Checks 1-6 run unchanged (source exists, mentions both names, no
personal data, known entities), except that the line naming both ends must also say "no longer" (a document that
merely asserts the fact is not its end) and a title cannot stand in for the person; then the fact must be current
(else REJECT `not_current`, or `already_ended` when history holds it), `at` must come after the fact began (else
REJECT `older_than_current`), and it must be dated at the record that states it: later is REJECT `valid_after_source`,
more than a day earlier REJECT `end_before_source`. Outcome CLOSE: the
typed edge moves to `WAS` with `invalid_at = at`, `closed_by` = the receipt id, and the receipt CITES the record.
Ingest sends every line of a "last day" record (`- X is no longer a member of the T team.`, `- X no longer owns
S.`, `- X no longer leads P.`) down this path. Afterwards `current_owner` of an orphaned system answers "no current
owner; last owner X until D (closed by GD-..., citing DOC-..)" instead of guessing.

**Proposed handoffs.** `submit_handoff(..., status="proposed")` writes a Handoff a person still has to approve
(`handoff_plan` with `propose=true`); nothing is sent anywhere.

Findings and handoffs use the same door: agent ids must look like `agent:name`, personal data is looked for in every text and name they carry, a refused receipt is not a source (`source_rejected`), and a record may not be dated before the sources it cites. An exact repeat of a current fact (same proposer, sources the edge already has) writes no new receipt; it bumps `repeats` on the first one. Episodes dated more than an hour ahead of the clock are refused before anything is stored. The attacks that found these holes, and how each was closed, are in `redteam/REPORT.md`.

The gate is plain code. A model may propose candidates; it never decides what is stored.

## Tool catalogue

Agents call tools; each tool is a parameterised, human-written Cypher query. Arguments that name things are resolved against the graph first, and a name that does not resolve stops the call with outcome `unknown_entity`. Twenty-one tools: fourteen for systems, decisions and agent memory (`honest_memory/tools.py`), seven for a departure (`honest_memory/departure.py`). The same list reaches MCP, the HTTP API and the agent from one registry.

| Tool | Question it answers | Cypher (the query constant, or the pattern) |
|---|---|---|
| `current_owner(system)` | who owns X now, and why do we believe it; for an orphan, the last owner and the receipt that ended it | `(p:Person)-[r:OWNS]->(s:System {name: $system})` + `(s)<-[:ABOUT]-(g:GateDecision)-[:CITES]->(e:Episode)`; orphan: `_LAST_OWNER` |
| `owner_at(system, date)` | who owned X on a date | `_OWNERS_AT`: `OWNS` ∪ `WAS {predicate: 'OWNS'}` by interval |
| `history(system)` | every owner change with sources | `_OWNERS_AT` over all time |
| `escalation_path(system)` | whom to call about X | `(s)<-[:OWNS]-(o)-[:MEMBER_OF]->(t:Team)<-[:LEADS]-(l)` |
| `impact(system, depth, as_of)` | what depends on X and who must hear about it | `_IMPACT`: `(u:System)-[:DEPENDS_ON*1..{depth}]->(s)`, owners of each `u` |
| `who_knows(system)` | who has worked on X | `(p:Person)-[w:WORKED_ON]->(s)`, ranked by `w.count` |
| `decision_trail(decision)` | how a decision evolved | `_TRAIL`: `SUPERSEDES*1..6` both ways + `DECIDED` |
| `why(subject, predicate, object)` | why the memory believes or refused a fact | `_WHY_BY_NAME` / `_WHY_BY_EPISODE`: receipts + the current fact |
| `what_changed(since)` | what the memory learned, closed or refused since a date; closed rows name the closing receipt | typed edges with `valid_at > $t`, `WAS` with `invalid_at > $t`, REJECT receipts |
| `path_between(a, b)` | how two things are connected | `algo.SPpaths` over current fact types, `relDirection: 'both'` |
| `recall(entity)` | what agents concluded before, and when | `_FINDINGS`, `_HANDOFFS` |
| `facts_about(entity)` | every current fact about an entity | `(n)-[r:OWNS|MEMBER_OF|...]-(m)` |
| `remember(...)`, `handoff(...)` | write a finding or a handoff | through the gate |
| `what_leaves(person, depth, as_of)` | depth 1: what they own, lead, belong to (also what a recorded last day ended); depth 2-4: decisions they made that govern systems others own, and what depends on those | `_HELD`; `_DECIDED_REACH`: `(me)-[:DECIDED]->(d)-[:CONSTRAINS]->(x)` then `(s)-[:DEPENDS_ON*0..{depth-2}]->(x)`; `_OWNED_REACH`; owners `_OWNER_AT` |
| `single_source(person, as_of)` | live decisions and systems whose every source record this person wrote | `_LONE_DECISIONS`, `_LONE_SYSTEMS`: every fact about the item, `UNWIND r.source_ids`, `collect(DISTINCT e.author)`, size 1 |
| `without(person)` | what nobody is connected to once they are gone (islands) | `_P_NODES`/`_P_LINKS` copied without the person into a temporary graph, `_WCC`: `algo.WCC`; components with no Person |
| `bridges(top)` | who links teams; the only per-person score | projection written both ways into a temporary graph, `_BETWEENNESS`: `algo.betweenness` with `samplingSize` = node count; `_TEAM_LINKS` |
| `successors(person)` | for each orphaned item, who worked on it or next to it (candidates) | `_NEAR` (`DEPENDS_ON` either way), `_GOVERNS` (`CONSTRAINS`), `_WORKERS` (`WORKED_ON` counts) |
| `handoff_plan(person, last_day, propose)` | one list: item, why at risk (path), sources, candidates, action; plus the questions to ask the person before they go (one per single-source item, per orphan nobody else has worked on, and for promises the records cannot hold), each with why and the records so far; `propose=true` writes proposed Handoffs through the gate | the queries above, combined; `_EPISODES` for who wrote each record and when; writes via `submit_handoff(status="proposed")` |
| `promises(person, since, to)` | promises in the written records since a date (default: the resignation) | `_RECORDS_SINCE`: episodes authored by or `MENTIONS` the person after `$since`; none → `not_in_graph` naming the channels searched |

The departure tools work the same way before a vacation, a parental leave or a team move: ask before the person goes.

**The graph gets richer from the handoff.** Answers to the plan's questions arrive as ordinary records (handoff notes by the person, a review by the successor) and go through the same gate: each line restates a fact the graph holds, so the gate CONFIRMs it and adds the record as a source. A decision whose records now have a second author is no longer single-source; `single_source` and `what_leaves` with `as_of` show the change (seed 42: two decisions rest only on the leaver the day before the session, none three days after; docs/DEMO-N5.md, section 10).

**Time.** Tools with `as_of` read the facts in force that day (current edges with `valid_at` ≤ the day, `WAS` edges whose interval covers it) and, for `single_source`, only the source records dated by then. Without `as_of`, `what_leaves`, `single_source`, `successors` and `handoff_plan` read the present plus what the person held until a recorded last day (the gate's CLOSE receipts), so the question still has an answer after they have gone.

**Temporary graphs.** `without` and `bridges` need a projection the tenant graph does not have (one person left out; every link both ways, because `algo.betweenness` follows edge direction). `Store.scratch()` creates `hm_<tenant>__<purpose>_<random>`, writes the projection with `UNWIND ... CREATE`, runs the algorithm and deletes the graph in a `finally`, also when a query fails (tested). On the demo corpus the whole `without` call, including building and deleting the copy, takes 10-25 ms on both engines.

**Engine notes.** `algo.betweenness` is exact on 6.0.2 when `samplingSize` covers every node and samples sources on 4.22: `bridges` runs two seeds, reports exact scores when they agree and otherwise the mean of 8 seeded runs, marked as an estimate (docs/sponsor-feedback.md, items 6-7). "Decisions not superseded" is passed in as a list, because filtering it with `OPTIONAL MATCH` + `count()` + `WITH ... WHERE` drops later rows on 6.0.2 (item 8).

Algorithms used only from the set present in both FalkorDB 4.22 (Cloud free) and 6.0.x: `algo.pageRank`, `algo.SPpaths`, `algo.WCC`, `algo.labelPropagation`, `algo.betweenness`.

## Answers

Every answer is one of: `answered_with_path` (rows plus the hops), `answered_from_memory` (a Finding, with the date it was learned), `not_in_graph` (the entity exists, nothing connects the way the question assumed), `unknown_entity` (no such name, no query ran), and for writes `accepted` / `confirmed` / `superseded` / `closed` / `refused` with the receipt. The model writes one sentence from the rows; a check rejects any sentence that names an entity absent from the rows, and the answer falls back to a sentence built from the rows.

## Panel

`web/` is the one-page demo served at `/` (static files, no build step, `web/vendor/` holds cytoscape.js 3.34.3 with its licence). It reads the HTTP API only and never writes. Two read endpoints exist for it, both in `honest_memory/snapshot.py`:

| Endpoint | What | Cypher |
|---|---|---|
| `GET /v1/{tenant}/graph` | people, teams, systems, decisions, projects; current facts in `edges`, facts a last-day record ended (WAS closed by a CLOSE receipt) in `ended`, people with a recorded resignation in `leaving`; `?as_of=YYYY-MM-DD` gives the facts in force that day | `NODES`, `CURRENT`, `ENDED`, `AS_OF` in `snapshot.py`; the trace is in the answer |
| `GET /v1/{tenant}/episodes/{id}` | one source record; a line that looks like personal data is withheld | `MATCH (e:Episode {id: $id}) RETURN …` |

Layout (`web/app.js`, no randomness, the same picture on every load): each team owns a corner (zone order Payments, Platform, Support, Growth) and its nodes sit on a grid in three lanes (people; decisions and projects; systems), a decision that governs another team's system sits in the gap between the two zones, and the outer margins stay free for what drifts off on Remove. At rest only people, systems and ownership lines are drawn; decisions and projects appear when they are on a lit path or a result. When a person is picked, only the lit part moves: one column per step of `what_leaves` (systems above decisions in a column), placed from the depth-4 answer so the slider adds hops without reshuffling, drawn as curved arrows in the overlay SVG. In the handoff game the items in play gather on an arc round the person; at the end they return to their place (handed over) or to the margin (still on one person). Narrow portrait screens get the same corners with steps read top to bottom.

The flow and the tools behind each step: pick a person, `what_leaves(person, depth)` 1–4; Remove, `without(person)`, `single_source(person, as_of = notice day)`, `bridges(top=3)`; Plan the handoff, `handoff_plan(person)` and `promises(person)`; answering a question, the CONFIRM receipts about the item since the notice, the records they cite, and `single_source(person, as_of = the day of those records)`. Show Cypher lists the queries and milliseconds of the last action from each answer's trace. `scripts/capture_panel_fixtures.py` records the same answers into `web/fixtures/`; the page reads them with `?data=fixtures` and falls back to them when the API does not answer.

## Compatibility

FalkorDB Cloud free runs 4.22 (C engine); `falkordb/falkordb:latest` is 6.0.x (Rust engine). Tests run on both. Avoided on purpose: label expressions `n:A|B`, `all(e IN r …)` over a variable-length relationship, `db.idx.fulltext.createNodeIndex`, algorithms missing from 6.0.x.
