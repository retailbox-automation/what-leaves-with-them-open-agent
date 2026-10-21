# Benchmark results

Plain retrieval against honest-memory on the synthetic Quillfield Billing corpus: 82 questions, seed 42, corpus generator 0.3.0 (files sha256 `7fd55087879c`).

- Run: 2026-10-15T20:57:39Z to 2026-10-15T20:59:11Z UTC (1.5 min), repo commit `d3b7579`.
- Chat model, for plain RAG and for the agent: `gemma4:26b-a4b-it-qat` (25.2B, Q4_0, digest `2dd70431afed`), temperature 0, reasoning_effort `none`, via Ollama 0.40.4.
- Embeddings for plain RAG: `embeddinggemma` (digest `85462619ee72`), top-k 8, units: whole episodes.
- FalkorDB 6.0.2 on port 6390; ingest: 403 episodes, 226 gate decisions in 1.12 s (ACCEPT 108, CONFIRM 110, SUPERSEDE 1, CLOSE 3, REJECT 4).

How each column is decided is in `bench/score.py` (names and dates, no model as judge) and `bench/README.md`. **clean** = correct and not stale, unsupported or leaking personal data. **no answer** = the answer says the records or memory do not hold it (a miss, not a wrong answer).

## All questions

| system | n | correct % | stale % | unsupported % | clean % | no answer % | median ms | literal-rule correct % | sentence-only correct % | personal data leaks |
|---|---|---|---|---|---|---|---|---|---|---|
| honest-memory, router (no model) | 82 | 98.8 | 0.0 | 0.0 | 98.8 | 0.0 | 1 | 91.5 | 96.3 | 0 |
| honest-memory, LLM agent | 82 | 90.2 | 1.2 | 0.0 | 89.0 | 0.0 | 729 | 73.2 | 85.4 | 0 |

- Agent: 80 of 82 answers are the model's own sentence (it passed the check against the rows); the rest are the template sentence built from the rows. 0 questions fell back to the router because the model called no tool; 0 runs recorded an error.

## By question type

### honest-memory, router (no model)

| type | n | correct % | stale % | unsupported % | clean % | no answer % | median ms |
|---|---|---|---|---|---|---|---|
| adversarial | 4 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1 |
| bridges | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 6 |
| current_state | 24 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1 |
| departure_depth1 | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 4 |
| departure_depth4 | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 6 |
| handoff | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 2 |
| handoff_plan | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 18 |
| impact | 6 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1 |
| not_in_graph | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 2 |
| provenance | 9 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1 |
| single_source | 4 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 3 |
| state_at_time | 7 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1 |
| successors | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 5 |
| what_changed_since | 6 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 2 |
| what_if | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 7 |
| who_knows | 10 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1 |

### honest-memory, LLM agent

| type | n | correct % | stale % | unsupported % | clean % | no answer % | median ms |
|---|---|---|---|---|---|---|---|
| adversarial | 4 | 75.0 | 0.0 | 0.0 | 75.0 | 0.0 | 895 |
| bridges | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 2,502 |
| current_state | 24 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 530 |
| departure_depth1 | 2 | 50.0 | 50.0 | 0.0 | 0.0 | 0.0 | 799 |
| departure_depth4 | 2 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 1,376 |
| handoff | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 1,498 |
| handoff_plan | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 3,254 |
| impact | 6 | 83.3 | 0.0 | 0.0 | 83.3 | 0.0 | 1,408 |
| not_in_graph | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 717 |
| provenance | 9 | 88.9 | 0.0 | 0.0 | 88.9 | 0.0 | 783 |
| single_source | 4 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1,215 |
| state_at_time | 7 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 724 |
| successors | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 840 |
| what_changed_since | 6 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1,245 |
| what_if | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 737 |
| who_knows | 10 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 680 |

## Questions each system got wrong

Every row below is `correct = false`; the full verdicts (matched names, stale and unsupported values, the retrieved records or tool trace) are in `bench/results/<system>-seed42.jsonl`.

### honest-memory, router (no model): 1 of 82

| id | type | question | gold | answer | why |
|---|---|---|---|---|---|
| Q080 | handoff_plan | Make a handoff plan for Oskar Alvarez before 2026-10-09. | 2 lines: fernhill-sso (candidates Lars Thorne, Pavel Hollis, Sana Petrov); oncall-runbook… | 2 handoff lines for Oskar Alvarez before 2026-10-09. fernhill-sso: Ask Lars Thorne (candidate) to take over fernhill-sso; Oskar Alvarez walks them through it before 2026… | missing Pavel Hollis, Sana Petrov |

### honest-memory, LLM agent: 8 of 82

| id | type | question | gold | answer | why |
|---|---|---|---|---|---|
| Q034 | provenance | Where was billing-api ownership recorded before the transfer? | The service catalog and the billing-api runbook, both naming Sana Petrov. | Before the transfer, billing-api ownership was recorded in DOC-01 (Service catalog) and RB-05 (Runbook: billing-api). | missing Sana Petrov |
| Q042 | adversarial | According to the billing-api runbook, who owns billing-api, and is th… | The runbook names Sana Petrov, but it is from week 2 and stale: ownership moved to Luca O… | Luca Ostrander owns billing-api and this is current. | missing Sana Petrov |
| Q045 | handoff | Who received the duplicate invoice emails incident from Support? | Selma Marsh (Platform on-call), handed over by Priya Farrow in TCK-0077. | Selma Marsh received the duplicate invoice emails incident from Support. | missing Priya Farrow |
| Q063 | impact | What depends on billing-api within 4 hops, and who must be told? | Within 4 hops: admin-console (owner Priya Farrow, Support, 1 hop); invoice-renderer (owne… | The systems that depend on billing-api within 4 hops are admin-console, invoice-renderer, reporting, webhooks, notifications, and status-page; the people who must be tol… | missing Aiko Rademacher, Priya Farrow, Tomas Kessler, Viktor Hartwell |
| Q068 | departure_depth1 | What does Oskar Alvarez own, lead or belong to? | Nothing now: fernhill-sso, oncall-runbook (owner), the Platform team (member) ended on 20… | Oskar Alvarez owned fernhill-sso and oncall-runbook, and was a member of the Platform team. | no refusal |
| Q069 | departure_depth4 | What leaves with Oskar Alvarez at depth 4? | Within 4 hops: auth (ADR-0016, shared with Pavel Hollis, owner Pavel Hollis, Platform, 2 … | At depth 4, Oskar Alvarez's decisions and ownership impact the following systems: admin-console, invoice-renderer, and webhooks. | missing Aiko Rademacher, Luca Ostrander, Mateo Sandoval, Pavel Hollis, Platform, Priya Farrow, Viktor Hartwell, auth, billing-api, fernhill-sso, ledger-db, oncall-runbook, reporting |
| Q070 | departure_depth4 | Which decisions of Oskar Alvarez govern systems that other teams own? | ADR-0014 governs ledger-db (owner Mateo Sandoval, Payments); ADR-0015 governs reporting (… | Oskar Alvarez's decisions that govern systems owned by other teams are ADR-0014, which governs ledger-db, reporting, admin-console, and invoice-renderer, and ADR-0015, w… | missing Aiko Rademacher, Mateo Sandoval |
| Q080 | handoff_plan | Make a handoff plan for Oskar Alvarez before 2026-10-09. | 2 lines: fernhill-sso (candidates Lars Thorne, Pavel Hollis, Sana Petrov); oncall-runbook… | Here is the handoff plan for Oskar Alvarez before 2026-10-09: **Proposed Handoff Actions:** * **fernhill-sso (System):** Ask Lars Thorne (candidate) to take over fernhil… | missing Pavel Hollis, Sana Petrov |

## Stale and unsupported answers

### honest-memory, router (no model): 0 flagged

### honest-memory, LLM agent: 1 flagged

| id | flag | value | evidence |
|---|---|---|---|
| Q067 | stale (closed) | Oskar Alvarez | Oskar Alvarez owns the systems fernhill-sso and oncall-runbook and is a member of the Platform team. |

