# Benchmark results

Plain retrieval against honest-memory on the synthetic Quillfield Billing corpus: 82 questions, seed 42, corpus generator 0.3.0 (files sha256 `7fd55087879c`).

- Run: 2026-10-15T21:16:43Z to 2026-10-15T21:18:55Z UTC (2.2 min), repo commit `caa9143`.
- Chat model, for plain RAG and for the agent: `gemma4:26b-a4b-it-qat` (25.2B, Q4_0, digest `2dd70431afed`), temperature 0, reasoning_effort `none`, via Ollama 0.40.4.
- Embeddings for plain RAG: `embeddinggemma` (digest `85462619ee72`), top-k 8, units: whole episodes.
- FalkorDB 4.22.0 on port 6393; ingest: 403 episodes, 226 gate decisions in 1.4 s (ACCEPT 108, CONFIRM 110, SUPERSEDE 1, CLOSE 3, REJECT 4).

How each column is decided is in `bench/score.py` (names and dates, no model as judge) and `bench/README.md`. **clean** = correct and not stale, unsupported or leaking personal data. **no answer** = the answer says the records or memory do not hold it (a miss, not a wrong answer).

## All questions

| system | n | correct % | stale % | unsupported % | clean % | no answer % | median ms | literal-rule correct % | sentence-only correct % | personal data leaks |
|---|---|---|---|---|---|---|---|---|---|---|
| plain RAG | 82 | 46.3 | 4.9 | 0.0 | 46.3 | 36.6 | 512 | 39.0 | 46.3 | 2 |
| honest-memory, router (no model) | 82 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 2 | 92.7 | 97.6 | 0 |
| honest-memory, LLM agent | 82 | 95.1 | 0.0 | 0.0 | 95.1 | 1.2 | 784 | 79.3 | 90.2 | 0 |

- Plain RAG retrieval: a gold source record was among the top-k for 63 of 80 questions that have sources, and all of them for 39.
- Agent: 79 of 82 answers are the model's own sentence (it passed the check against the rows); the rest are the template sentence built from the rows. 0 questions fell back to the router because the model called no tool; 0 runs recorded an error.

## By question type

### plain RAG

| type | n | correct % | stale % | unsupported % | clean % | no answer % | median ms |
|---|---|---|---|---|---|---|---|
| adversarial | 4 | 25.0 | 25.0 | 0.0 | 25.0 | 0.0 | 509 |
| bridges | 2 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 646 |
| current_state | 24 | 79.2 | 0.0 | 0.0 | 79.2 | 20.8 | 409 |
| departure_depth1 | 2 | 50.0 | 50.0 | 0.0 | 50.0 | 0.0 | 791 |
| departure_depth4 | 2 | 0.0 | 0.0 | 0.0 | 0.0 | 50.0 | 750 |
| handoff | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 447 |
| handoff_plan | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 812 |
| impact | 6 | 16.7 | 33.3 | 0.0 | 16.7 | 33.3 | 601 |
| not_in_graph | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 707 |
| provenance | 9 | 55.6 | 0.0 | 0.0 | 55.6 | 22.2 | 478 |
| single_source | 4 | 25.0 | 0.0 | 0.0 | 25.0 | 25.0 | 951 |
| state_at_time | 7 | 57.1 | 0.0 | 0.0 | 57.1 | 42.9 | 586 |
| successors | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 524 |
| what_changed_since | 6 | 66.7 | 0.0 | 0.0 | 66.7 | 16.7 | 880 |
| what_if | 2 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 976 |
| who_knows | 10 | 0.0 | 0.0 | 0.0 | 0.0 | 90.0 | 408 |

### honest-memory, router (no model)

| type | n | correct % | stale % | unsupported % | clean % | no answer % | median ms |
|---|---|---|---|---|---|---|---|
| adversarial | 4 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1 |
| bridges | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 12 |
| current_state | 24 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1 |
| departure_depth1 | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 6 |
| departure_depth4 | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 8 |
| handoff | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 4 |
| handoff_plan | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 24 |
| impact | 6 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 3 |
| not_in_graph | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 2 |
| provenance | 9 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 2 |
| single_source | 4 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 4 |
| state_at_time | 7 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 2 |
| successors | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 8 |
| what_changed_since | 6 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 3 |
| what_if | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 10 |
| who_knows | 10 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1 |

### honest-memory, LLM agent

| type | n | correct % | stale % | unsupported % | clean % | no answer % | median ms |
|---|---|---|---|---|---|---|---|
| adversarial | 4 | 75.0 | 0.0 | 0.0 | 75.0 | 0.0 | 942 |
| bridges | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 622 |
| current_state | 24 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 515 |
| departure_depth1 | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1,127 |
| departure_depth4 | 2 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 1,585 |
| handoff | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 3,260 |
| handoff_plan | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 3,126 |
| impact | 6 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1,686 |
| not_in_graph | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 793 |
| provenance | 9 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 878 |
| single_source | 4 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1,395 |
| state_at_time | 7 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 779 |
| successors | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1,067 |
| what_changed_since | 6 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1,337 |
| what_if | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 696 |
| who_knows | 10 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 769 |

## Questions each system got wrong

Every row below is `correct = false`; the full verdicts (matched names, stale and unsupported values, the retrieved records or tool trace) are in `bench/results/<system>-seed42.jsonl`.

### plain RAG: 44 of 82

| id | type | question | gold | answer | why |
|---|---|---|---|---|---|
| Q003 | current_state | Who currently owns ledger-db? | Mateo Sandoval (Payments). | The records do not contain information regarding who owns ledger-db. | missing Mateo Sandoval |
| Q005 | current_state | Who currently owns notifications? | Selma Marsh (Platform). | The records do not contain information regarding who currently owns notifications. | missing Selma Marsh |
| Q006 | current_state | Who currently owns webhooks? | Luca Ostrander (Platform). | The provided records do not contain information regarding who currently owns webhooks. | missing Luca Ostrander |
| Q007 | current_state | Who currently owns reporting? | Aiko Rademacher (Growth). | The records do not contain information regarding who currently owns reporting. | missing Aiko Rademacher |
| Q008 | current_state | Who currently owns admin-console? | Priya Farrow (Support). | The provided records do not contain information regarding who currently owns admin-console. | missing Priya Farrow |
| Q013 | state_at_time | Who owned ledger-db on 2026-09-23? | Mateo Sandoval. | The records do not contain information regarding who owned ledger-db on 2026-09-23. | missing Mateo Sandoval |
| Q015 | state_at_time | Who owned admin-console on 2026-09-01? | Priya Farrow. | The records do not contain information regarding who owned admin-console on 2026-09-01. | missing Priya Farrow |
| Q016 | state_at_time | Who owned webhooks on 2026-09-30? | Luca Ostrander. | The records do not contain information regarding who owned webhooks on 2026-09-30. | missing Luca Ostrander |
| Q020 | what_changed_since | Which ADRs were accepted since 2026-09-14? | ADR-0010, ADR-0008, ADR-0015, ADR-0013, ADR-0009, ADR-0012. | ADR-0010 was accepted on 2026-09-15. [msg-0125] | missing ADR-0008, ADR-0009, ADR-0012, ADR-0013, ADR-0015 |
| Q021 | what_changed_since | Which ADRs were accepted since 2026-09-27? | ADR-0012. | The records do not contain any ADRs accepted since 2026-09-27. | missing ADR-0012 |
| Q023 | who_knows | Who knows the most about ledger-db? | Viktor Hartwell (10 work items). | The records do not contain information regarding who knows the most about ledger-db. | missing Viktor Hartwell |
| Q024 | who_knows | Who knows the most about notifications? | Aiko Rademacher (6 work items). | The records do not contain information regarding who knows the most about notifications. | missing Aiko Rademacher |
| Q025 | who_knows | Who knows the most about status-page? | Luca Ostrander (6 work items). | The provided records do not contain information regarding who knows the most about status-page. | missing Luca Ostrander |
| Q026 | who_knows | Who knows the most about invoice-renderer? | Lars Thorne (6 work items). | The records do not contain information regarding who knows the most about invoice-renderer. | missing Lars Thorne |
| Q027 | who_knows | Who knows the most about webhooks? | Lars Thorne (6 work items). | The records do not contain information regarding who knows the most about webhooks. | missing Lars Thorne |
| Q028 | who_knows | Who knows the most about reporting? | Viktor Hartwell (6 work items). | The provided records do not contain information regarding who knows the most about reporting. | missing Viktor Hartwell |
| Q029 | who_knows | Who knows the most about billing-api? | Luca Ostrander (6 work items). | The records do not contain information regarding who knows the most about billing-api. | missing Luca Ostrander |
| Q030 | who_knows | Who knows the most about admin-console? | Priya Farrow (6 work items). | The provided records do not contain information regarding who knows the most about admin-console. | missing Priya Farrow |
| Q031 | who_knows | Who knows the most about auth? | Lars Thorne (6 work items). | The records do not contain information regarding who knows the most about auth. | missing Lars Thorne |
| Q032 | who_knows | Rank the people who worked on ledger-db, most active first. | Viktor Hartwell (10), Pavel Hollis (7), Sana Petrov (5), Luca Ostrander (3), Oskar Alvare… | Viktor Hartwell, Mateo Sandoval, Pavel Hollis, Luca Ostrander, and Oskar Alvarez. (TCK-0029, TCK-0093, RB-09, TCK-0068, TCK-0100, TCK-0120, msg-0103, msg-0206) | missing Sana Petrov |
| Q034 | provenance | Where was billing-api ownership recorded before the transfer? | The service catalog and the billing-api runbook, both naming Sana Petrov. | The billing-api runbook (RB-05) lists Sana Petrov as the owner. [msg-0181] | missing DOC-01 |
| Q035 | provenance | Where is it recorded that Pavel Hollis owns auth? | The service catalog and the auth key rotation ticket (two independent sources). | Pavel Hollis is listed as the owner of auth in [DOC-01]. | missing TCK-0033 |
| Q037 | provenance | Where is it recorded that Selma Marsh owns notifications? | The service catalog. | The provided records do not contain information stating that Selma Marsh owns notifications. (TCK-0077, ADR-0007, msg-0126, msg-0070, msg-0195, msg-0056, msg-0156, TCK-0… | missing DOC-01 |
| Q038 | provenance | Where is the supersession of ADR-0004 recorded? | ADR-0011 and the week 4 postmortem. | The records do not contain information regarding the supersession of ADR-0004. | missing ADR-0011, PM-03 |
| Q041 | adversarial | Is Growth the current owner of billing-api? | No. A chat message on 2026-09-29 claims it but cites no source. The current owner is Luca… | Yes, billing-api is owned by Growth (msg-0174). | missing ADR-0009, Luca Ostrander; no refusal; stale Growth |
| Q043 | adversarial | What is the phone number of the customer in TCK-0044? | No answer. The ticket holds personal contact data, which is rejected at the write gate an… | The phone number of the customer in TCK-0044 is +1-555-0159. [TCK-0044] | no refusal; leaked personal data |
| Q044 | adversarial | What is the mailing address of the customer in TCK-0044? | No answer. The ticket holds personal contact data, which is rejected at the write gate an… | The mailing address is 984 Marlowe Court, Apt 2A, Northfield. [TCK-0044] | no refusal; leaked personal data |
| Q061 | impact | What depends on ledger-db within 4 hops, and who must be told? | Within 4 hops: billing-api (owner Luca Ostrander, Platform, 1 hop); reporting (owner Aiko… | I am sorry, but the records do not contain information regarding what depends on ledger-db within 4 hops or who must be told. | missing Aiko Rademacher, Luca Ostrander, Priya Farrow, Selma Marsh, Tomas Kessler, Viktor Hartwell, admin-console, billing-api, invoice-renderer, notifications, reporting, status-page, webhooks |
| Q062 | impact | What depends on ledger-db within 4 hops as of 2026-09-10, and who mus… | Within 4 hops: billing-api (owner Sana Petrov, Payments, 1 hop); reporting (owner Aiko Ra… | I am sorry, but the records do not contain information regarding dependencies within 4 hops or who must be told. | missing Aiko Rademacher, Luca Ostrander, Priya Farrow, Sana Petrov, Selma Marsh, Tomas Kessler, Viktor Hartwell, admin-console, billing-api, invoice-renderer, notifications, reporting, status-page, webhooks |
| Q063 | impact | What depends on billing-api within 4 hops, and who must be told? | Within 4 hops: admin-console (owner Priya Farrow, Support, 1 hop); invoice-renderer (owne… | invoice-renderer, webhooks, admin-console, and reporting depend on billing-api (ARCH-01, ADR-0013). Route billing-api questions to Growth (msg-0174). | missing Aiko Rademacher, Luca Ostrander, Priya Farrow, Selma Marsh, Tomas Kessler, Viktor Hartwell, notifications, status-page; stale Growth |
| Q064 | impact | What depends on billing-api within 4 hops as of 2026-09-10, and who m… | Within 4 hops: admin-console (owner Priya Farrow, Support, 1 hop); invoice-renderer (owne… | As of 2026-09-10, the systems that depend on billing-api are invoice-renderer, webhooks, admin-console, and reporting (per ARCH-01 and ADR-0013). According to msg-0148 a… | missing Priya Farrow, Selma Marsh, Tomas Kessler, Viktor Hartwell, notifications, status-page; stale Growth, reporting |
| Q065 | impact | What depends on ledger-db within 2 hops, and who must be told? | Within 2 hops: billing-api (owner Luca Ostrander, Platform, 1 hop); reporting (owner Aiko… | Reporting queries a read replica of ledger-db (ADR-0008). If there are common alerts, you must post in #payments and page the on-call engineer (RB-09). | missing Aiko Rademacher, Luca Ostrander, Priya Farrow, Viktor Hartwell, admin-console, billing-api, invoice-renderer, reporting, webhooks |
| Q068 | departure_depth1 | What does Oskar Alvarez own, lead or belong to? | Nothing now: fernhill-sso, oncall-runbook (owner), the Platform team (member) ended on 20… | Oskar Alvarez is a Security Engineer in the Platform team and owns fernhill-sso and oncall-runbook [DOC-16, DOC-14, RB-10]. | no refusal; stale Oskar Alvarez |
| Q069 | departure_depth4 | What leaves with Oskar Alvarez at depth 4? | Within 4 hops: auth (ADR-0016, shared with Pavel Hollis, owner Pavel Hollis, Platform, 2 … | I am sorry, but the records do not contain information regarding what leaves at "depth 4". (DOC-16) | missing ADR-0014, ADR-0015, ADR-0016, Aiko Rademacher, Luca Ostrander, Mateo Sandoval, Pavel Hollis, Platform, Priya Farrow, Viktor Hartwell, admin-console, auth, billing-api, fernhill-sso, invoice-renderer, ledger-db, oncall-runbook, reporting, webhooks |
| Q070 | departure_depth4 | Which decisions of Oskar Alvarez govern systems that other teams own? | ADR-0014 governs ledger-db (owner Mateo Sandoval, Payments); ADR-0015 governs reporting (… | The decisions are ADR-0014, which constrains ledger-db, and ADR-0015, which constrains reporting (DOC-17, ADR-0014, ADR-0015). | missing Aiko Rademacher, Mateo Sandoval |
| Q071 | single_source | Which decisions and systems rest only on records written by Oskar Alv… | fernhill-sso, oncall-runbook: every record behind them was written by Oskar Alvarez. Not … | The decisions and systems that rest only on records written by Oskar Alvarez are ADR-0014 (ledger-db), ADR-0015 (reporting), fernhill-sso, and oncall-runbook. (DOC-15, D… | see jsonl |
| Q072 | single_source | What does only Oskar Alvarez know? | fernhill-sso, oncall-runbook: no record by anyone else backs them. | The records do not contain information regarding what only Oskar Alvarez knows. (DOC-16, DOC-15, DOC-17, RB-10, ADR-0015, DOC-14, msg-0007, msg-0145) | missing fernhill-sso, oncall-runbook |
| Q073 | single_source | Which decisions and systems rest only on records written by Oskar Alv… | ADR-0014, ADR-0015, fernhill-sso, oncall-runbook: 2 decisions and 2 systems rest only on … | The decisions and systems are ADR-0014 (ledger-db), ADR-0015 (reporting), and fernhill-sso (auth) [DOC-17, ADR-0014, ADR-0015]. | missing oncall-runbook |
| Q075 | what_if | What would nobody be connected to without Oskar Alvarez? | fernhill-sso and oncall-runbook: without Oskar Alvarez nobody in the graph is connected t… | I am sorry, but the records do not contain information regarding what would be disconnected without Oskar Alvarez. (DOC-16, DOC-15, DOC-17, RB-10, msg-0007, msg-0145, AD… | missing fernhill-sso, oncall-runbook |
| Q076 | what_if | If Oskar Alvarez leaves, what becomes an island? | fernhill-sso and oncall-runbook: Oskar Alvarez is the only connection to them. | The records do not contain information regarding what becomes an island if Oskar Alvarez leaves. (DOC-16, DOC-15, DOC-17, RB-10, DOC-18, ADR-0015, ADR-0014, msg-0007) | missing fernhill-sso, oncall-runbook |
| Q077 | bridges | Who is the strongest bridge between teams? | Oskar Alvarez, the highest betweenness over people, teams and systems; links Growth, Paym… | The records do not contain information regarding who is the strongest bridge between teams. (DOC-02, ARCH-01, msg-0039, msg-0044, msg-0074, msg-0166, msg-0022, msg-0076) | missing Growth, Oskar Alvarez, Payments, Platform, Support |
| Q078 | bridges | Who is the only person linking Growth and Support? | Oskar Alvarez: nobody else works on systems of both Growth and Support. | The records do not contain information regarding a person linking Growth and Support. | missing Oskar Alvarez |
| Q079 | successors | Who could take over from Oskar Alvarez? | Candidates, not decisions. fernhill-sso (by work on auth): Lars Thorne (6), Pavel Hollis … | The records do not contain information regarding a successor for Oskar Alvarez (DOC-16). | missing Lars Thorne, Pavel Hollis, Sana Petrov, fernhill-sso, oncall-runbook |
| Q080 | handoff_plan | Make a handoff plan for Oskar Alvarez before 2026-10-09. | 2 lines: fernhill-sso (candidates Lars Thorne, Pavel Hollis, Sana Petrov); oncall-runbook… | The records do not contain a handoff plan for Oskar Alvarez before 2026-10-09. (DOC-15, DOC-16, DOC-17) | missing Lars Thorne, Pavel Hollis, Sana Petrov, fernhill-sso, oncall-runbook |

### honest-memory, router (no model): 0 of 82

### honest-memory, LLM agent: 4 of 82

| id | type | question | gold | answer | why |
|---|---|---|---|---|---|
| Q042 | adversarial | According to the billing-api runbook, who owns billing-api, and is th… | The runbook names Sana Petrov, but it is from week 2 and stale: ownership moved to Luca O… | Luca Ostrander owns billing-api since 2026-09-22. | missing Sana Petrov |
| Q045 | handoff | Who received the duplicate invoice emails incident from Support? | Selma Marsh (Platform on-call), handed over by Priya Farrow in TCK-0077. | I am sorry, but I cannot find any record of a "duplicate invoice emails incident" in the memory graph. | missing Priya Farrow, Selma Marsh |
| Q069 | departure_depth4 | What leaves with Oskar Alvarez at depth 4? | Within 4 hops: auth (ADR-0016, shared with Pavel Hollis, owner Pavel Hollis, Platform, 2 … | Oskar Alvarez's ownership of fernhill-sso, oncall-runbook, and membership in Platform ended on 2026-10-09. | missing Aiko Rademacher, Luca Ostrander, Mateo Sandoval, Pavel Hollis, Priya Farrow, Viktor Hartwell, admin-console, auth, billing-api, invoice-renderer, ledger-db, reporting, webhooks |
| Q070 | departure_depth4 | Which decisions of Oskar Alvarez govern systems that other teams own? | ADR-0014 governs ledger-db (owner Mateo Sandoval, Payments); ADR-0015 governs reporting (… | ADR-0014 governs ledger-db, reporting, billing-api, admin-console, and invoice-renderer, while ADR-0015 governs reporting. | missing Aiko Rademacher, Mateo Sandoval |

## Stale and unsupported answers

### plain RAG: 4 flagged

| id | flag | value | evidence |
|---|---|---|---|
| Q041 | stale (refused) | Growth | Yes, billing-api is owned by Growth (msg-0174). |
| Q063 | stale (refused) | Growth | Route billing-api questions to Growth (msg-0174). |
| Q064 | stale (refused) | Growth | According to msg-0148 and msg-0174, you must route questions or escalations to Luca Ostrander or Growth. |
| Q064 | stale (not_yet) | reporting | As of 2026-09-10, the systems that depend on billing-api are invoice-renderer, webhooks, admin-console, and reporting (per ARCH-01 and ADR-0013). |
| Q068 | stale (closed) | Oskar Alvarez | Oskar Alvarez is a Security Engineer in the Platform team and owns fernhill-sso and oncall-runbook [DOC-16, DOC-14, RB-10]. |

### honest-memory, router (no model): 0 flagged

### honest-memory, LLM agent: 0 flagged

