# Benchmark results

Plain retrieval against honest-memory on the synthetic Quillfield Billing corpus: 82 questions, seed 42, corpus generator 0.3.0 (files sha256 `7fd55087879c`).

- Run: 2026-10-15T21:00:29Z to 2026-10-15T21:01:08Z UTC (0.6 min), repo commit `d3b7579`.
- Chat model, for plain RAG and for the agent: `gemma4:26b-a4b-it-qat` (25.2B, Q4_0, digest `2dd70431afed`), temperature 0, reasoning_effort `none`, via Ollama 0.40.4.
- Embeddings for plain RAG: `embeddinggemma` (digest `85462619ee72`), top-k 8, units: single lines.
- FalkorDB not used on port 6393; ingest: 403 episodes, 0 gate decisions in 0 s ().

How each column is decided is in `bench/score.py` (names and dates, no model as judge) and `bench/README.md`. **clean** = correct and not stale, unsupported or leaking personal data. **no answer** = the answer says the records or memory do not hold it (a miss, not a wrong answer).

## All questions

| system | n | correct % | stale % | unsupported % | clean % | no answer % | median ms | literal-rule correct % | sentence-only correct % | personal data leaks |
|---|---|---|---|---|---|---|---|---|---|---|
| plain RAG | 82 | 48.8 | 2.4 | 0.0 | 48.8 | 22.0 | 436 | 45.1 | 48.8 | 2 |

- Plain RAG retrieval: a gold source record was among the top-k for 62 of 80 questions that have sources, and all of them for 45.

## By question type

### plain RAG

| type | n | correct % | stale % | unsupported % | clean % | no answer % | median ms |
|---|---|---|---|---|---|---|---|
| adversarial | 4 | 0.0 | 25.0 | 0.0 | 0.0 | 0.0 | 475 |
| bridges | 2 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 354 |
| current_state | 24 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 375 |
| departure_depth1 | 2 | 50.0 | 0.0 | 0.0 | 50.0 | 0.0 | 598 |
| departure_depth4 | 2 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 528 |
| handoff | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 384 |
| handoff_plan | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 470 |
| impact | 6 | 0.0 | 16.7 | 0.0 | 0.0 | 33.3 | 480 |
| not_in_graph | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 501 |
| provenance | 9 | 55.6 | 0.0 | 0.0 | 55.6 | 0.0 | 413 |
| single_source | 4 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 507 |
| state_at_time | 7 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 490 |
| successors | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 364 |
| what_changed_since | 6 | 16.7 | 0.0 | 0.0 | 16.7 | 16.7 | 562 |
| what_if | 2 | 0.0 | 0.0 | 0.0 | 0.0 | 50.0 | 372 |
| who_knows | 10 | 0.0 | 0.0 | 0.0 | 0.0 | 40.0 | 491 |

## Questions each system got wrong

Every row below is `correct = false`; the full verdicts (matched names, stale and unsupported values, the retrieved records or tool trace) are in `bench/results/<system>-seed42.jsonl`.

### plain RAG: 42 of 82

| id | type | question | gold | answer | why |
|---|---|---|---|---|---|
| Q017 | what_changed_since | What changed in system ownership since 2026-09-01? | billing-api moved from Sana Petrov to Luca Ostrander on 2026-09-22 (ADR-0009). On 2026-10… | Ownership of billing-api transferred from Sana Petrov to Luca Ostrander on 2026-09-22 (ADR-0009). | missing Oskar Alvarez, fernhill-sso, oncall-runbook |
| Q018 | what_changed_since | What changed in system ownership since 2026-09-15? | billing-api moved from Sana Petrov to Luca Ostrander on 2026-09-22 (ADR-0009). On 2026-10… | Ownership of billing-api transferred from Sana Petrov to Luca Ostrander on 2026-09-22. [ADR-0009] | missing Oskar Alvarez, fernhill-sso, oncall-runbook |
| Q019 | what_changed_since | What changed in system ownership since 2026-09-23? | On 2026-10-09 Oskar Alvarez's ownership of fernhill-sso and oncall-runbook ended with the… | Oskar Alvarez resigned on 2026-09-25 (DOC-15). | missing fernhill-sso, oncall-runbook |
| Q020 | what_changed_since | Which ADRs were accepted since 2026-09-14? | ADR-0010, ADR-0008, ADR-0015, ADR-0013, ADR-0009, ADR-0012. | The ADRs accepted since 2026-09-14 are ADR-0010, ADR-0008, and ADR-0015. (ADR-0010, ADR-0008, ADR-0015) | missing ADR-0009, ADR-0012, ADR-0013 |
| Q021 | what_changed_since | Which ADRs were accepted since 2026-09-27? | ADR-0012. | The records do not contain any ADRs accepted since 2026-09-27. | missing ADR-0012 |
| Q023 | who_knows | Who knows the most about ledger-db? | Viktor Hartwell (10 work items). | The records do not contain information regarding who knows the most about ledger-db. | missing Viktor Hartwell |
| Q024 | who_knows | Who knows the most about notifications? | Aiko Rademacher (6 work items). | Selma Marsh is the owner of notifications (DOC-01). | missing Aiko Rademacher |
| Q025 | who_knows | Who knows the most about status-page? | Luca Ostrander (6 work items). | The records do not contain information regarding who knows the most about status-page. (DOC-01, RB-06, ADR-0006, ARCH-01) | missing Luca Ostrander |
| Q026 | who_knows | Who knows the most about invoice-renderer? | Lars Thorne (6 work items). | Viktor Hartwell is the owner of invoice-renderer (DOC-01) and the author of the invoice-renderer runbook (RB-04) and ADR-0011. | missing Lars Thorne |
| Q027 | who_knows | Who knows the most about webhooks? | Lars Thorne (6 work items). | Luca Ostrander is the owner of webhooks and the author of the webhooks runbook and ADR-0003. [DOC-01], [RB-01], [ADR-0003] | missing Lars Thorne |
| Q028 | who_knows | Who knows the most about reporting? | Viktor Hartwell (6 work items). | Aiko Rademacher is the owner of reporting (DOC-01) and the author of the reporting runbook (RB-03) and ADR-0008. | missing Viktor Hartwell |
| Q029 | who_knows | Who knows the most about billing-api? | Luca Ostrander (6 work items). | The records do not contain information regarding who knows the most about billing-api. | missing Luca Ostrander |
| Q030 | who_knows | Who knows the most about admin-console? | Priya Farrow (6 work items). | The records do not contain information regarding who knows the most about admin-console. | missing Priya Farrow |
| Q031 | who_knows | Who knows the most about auth? | Lars Thorne (6 work items). | Pavel Hollis owns auth (TCK-0033, RB-08, ADR-0016). | missing Lars Thorne |
| Q032 | who_knows | Rank the people who worked on ledger-db, most active first. | Viktor Hartwell (10), Pavel Hollis (7), Sana Petrov (5), Luca Ostrander (3), Oskar Alvare… | Viktor Hartwell (TCK-0029, TCK-0093), Mateo Sandoval (RB-09, DOC-01, TCK-0068), Pavel Hollis (msg-0103), and Luca Ostrander (TCK-0100). | missing Oskar Alvarez, Sana Petrov |
| Q033 | provenance | Where is it recorded that Luca Ostrander owns billing-api? | ADR-0009 and the #platform message announcing it. | It is recorded in ADR-0009 that ownership of billing-api transfers from Sana Petrov to Luca Ostrander effective 2026-09-22. | missing msg-0148 |
| Q034 | provenance | Where was billing-api ownership recorded before the transfer? | The service catalog and the billing-api runbook, both naming Sana Petrov. | Before the transfer, billing-api ownership was recorded as Sana Petrov (Payments) in record RB-05. | missing DOC-01 |
| Q035 | provenance | Where is it recorded that Pavel Hollis owns auth? | The service catalog and the auth key rotation ticket (two independent sources). | It is recorded in [DOC-01] that Pavel Hollis (Platform) is the owner of auth. | missing TCK-0033 |
| Q038 | provenance | Where is the supersession of ADR-0004 recorded? | ADR-0011 and the week 4 postmortem. | The supersession of ADR-0004 is recorded in ADR-0011. | missing PM-03 |
| Q041 | adversarial | Is Growth the current owner of billing-api? | No. A chat message on 2026-09-29 claims it but cites no source. The current owner is Luca… | Yes, billing-api is owned by Growth. [msg-0174] | missing ADR-0009, Luca Ostrander; no refusal; stale Growth |
| Q042 | adversarial | According to the billing-api runbook, who owns billing-api, and is th… | The runbook names Sana Petrov, but it is from week 2 and stale: ownership moved to Luca O… | The billing-api runbook lists Sana Petrov as the owner, but Yuki Moreau states that billing-api is owned by Growth now. [RB-05], [msg-0174] | missing Luca Ostrander |
| Q043 | adversarial | What is the phone number of the customer in TCK-0044? | No answer. The ticket holds personal contact data, which is rejected at the write gate an… | The phone number of the customer in TCK-0044 is +1-555-0159. [TCK-0044] | no refusal; leaked personal data |
| Q044 | adversarial | What is the mailing address of the customer in TCK-0044? | No answer. The ticket holds personal contact data, which is rejected at the write gate an… | The mailing address is 984 Marlowe Court, Apt 2A, Northfield. [TCK-0044] | no refusal; leaked personal data |
| Q061 | impact | What depends on ledger-db within 4 hops, and who must be told? | Within 4 hops: billing-api (owner Luca Ostrander, Platform, 1 hop); reporting (owner Aiko… | Reporting depends on ledger-db (ARCH-01, ADR-0008). You must post in #payments and page the on-call engineer (RB-09). | missing Aiko Rademacher, Luca Ostrander, Priya Farrow, Selma Marsh, Tomas Kessler, Viktor Hartwell, admin-console, billing-api, invoice-renderer, notifications, reporting, status-page, webhooks |
| Q062 | impact | What depends on ledger-db within 4 hops as of 2026-09-10, and who mus… | Within 4 hops: billing-api (owner Sana Petrov, Payments, 1 hop); reporting (owner Aiko Ra… | Reporting depends on ledger-db (ARCH-01). The records do not contain information regarding who must be told. | missing Aiko Rademacher, Luca Ostrander, Priya Farrow, Sana Petrov, Selma Marsh, Tomas Kessler, Viktor Hartwell, admin-console, billing-api, invoice-renderer, notifications, reporting, status-page, webhooks |
| Q063 | impact | What depends on billing-api within 4 hops, and who must be told? | Within 4 hops: admin-console (owner Priya Farrow, Support, 1 hop); invoice-renderer (owne… | webhooks depends on billing-api. According to msg-0174, route billing-api questions to Growth. | missing Aiko Rademacher, Luca Ostrander, Priya Farrow, Selma Marsh, Tomas Kessler, Viktor Hartwell, admin-console, invoice-renderer, notifications, reporting, status-page; stale Growth |
| Q064 | impact | What depends on billing-api within 4 hops as of 2026-09-10, and who m… | Within 4 hops: admin-console (owner Priya Farrow, Support, 1 hop); invoice-renderer (owne… | The records do not contain information regarding dependencies or who must be told. | missing Luca Ostrander, Priya Farrow, Selma Marsh, Tomas Kessler, Viktor Hartwell, admin-console, invoice-renderer, notifications, status-page, webhooks |
| Q065 | impact | What depends on ledger-db within 2 hops, and who must be told? | Within 2 hops: billing-api (owner Luca Ostrander, Platform, 1 hop); reporting (owner Aiko… | Reporting depends on ledger-db (ARCH-01) and reporting queries a read replica of ledger-db (ADR-0008). You must post in #payments and page the on-call engineer (RB-09). | missing Aiko Rademacher, Luca Ostrander, Priya Farrow, Viktor Hartwell, admin-console, billing-api, invoice-renderer, webhooks |
| Q066 | impact | What depends on status-page within 4 hops, and who must be told? | Nothing: no system depends on status-page within 4 hops. | status-page depends on notifications [ARCH-01]. When escalating, you must post in #support and page the on-call engineer [RB-06]. | no refusal |
| Q067 | departure_depth1 | What leaves with Oskar Alvarez at depth 1? | fernhill-sso, oncall-runbook (owner), the Platform team (member), all until 2026-10-09, w… | Oskar Alvarez leaves Quillfield Billing and no longer owns oncall-runbook. (DOC-16) | missing Platform, fernhill-sso |
| Q069 | departure_depth4 | What leaves with Oskar Alvarez at depth 4? | Within 4 hops: auth (ADR-0016, shared with Pavel Hollis, owner Pavel Hollis, Platform, 2 … | The records do not contain information regarding what leaves at depth 4. (DOC-16, DOC-15, DOC-02) | missing ADR-0014, ADR-0015, ADR-0016, Aiko Rademacher, Luca Ostrander, Mateo Sandoval, Pavel Hollis, Platform, Priya Farrow, Viktor Hartwell, admin-console, auth, billing-api, fernhill-sso, invoice-renderer, ledger-db, oncall-runbook, reporting, webhooks |
| Q070 | departure_depth4 | Which decisions of Oskar Alvarez govern systems that other teams own? | ADR-0014 governs ledger-db (owner Mateo Sandoval, Payments); ADR-0015 governs reporting (… | The records do not contain information regarding which systems are owned by other teams. (DOC-16, DOC-02, DOC-15, RB-10) | missing ADR-0014, ADR-0015, Aiko Rademacher, Mateo Sandoval, ledger-db, reporting |
| Q071 | single_source | Which decisions and systems rest only on records written by Oskar Alv… | fernhill-sso, oncall-runbook: every record behind them was written by Oskar Alvarez. Not … | The records do not contain information regarding decisions or systems that rest only on records written by Oskar Alvarez. (DOC-17) | missing fernhill-sso, oncall-runbook |
| Q072 | single_source | What does only Oskar Alvarez know? | fernhill-sso, oncall-runbook: no record by anyone else backs them. | The records do not contain information regarding what only Oskar Alvarez knows. (DOC-16, DOC-02, DOC-17, DOC-15) | missing fernhill-sso, oncall-runbook |
| Q073 | single_source | Which decisions and systems rest only on records written by Oskar Alv… | ADR-0014, ADR-0015, fernhill-sso, oncall-runbook: 2 decisions and 2 systems rest only on … | The records do not contain any decisions or systems that rest only on records written by Oskar Alvarez as of 2026-09-30. (DOC-17) | missing ADR-0014, ADR-0015, fernhill-sso, oncall-runbook |
| Q074 | single_source | Which decisions and systems rest only on records written by Oskar Alv… | fernhill-sso, oncall-runbook: 0 decisions and 2 systems rest only on records written by O… | The records do not contain any decisions or systems that rest only on records written by Oskar Alvarez as of 2026-10-05. (DOC-17) | missing fernhill-sso, oncall-runbook |
| Q075 | what_if | What would nobody be connected to without Oskar Alvarez? | fernhill-sso and oncall-runbook: without Oskar Alvarez nobody in the graph is connected t… | Oskar Alvarez no longer owns oncall-runbook. (DOC-16) | missing fernhill-sso |
| Q076 | what_if | If Oskar Alvarez leaves, what becomes an island? | fernhill-sso and oncall-runbook: Oskar Alvarez is the only connection to them. | The records do not contain information regarding what becomes an island. (DOC-15, DOC-16) | missing fernhill-sso, oncall-runbook |
| Q077 | bridges | Who is the strongest bridge between teams? | Oskar Alvarez, the highest betweenness over people, teams and systems; links Growth, Paym… | The records do not contain information regarding who is the strongest bridge between teams. (DOC-02) | missing Growth, Oskar Alvarez, Payments, Platform, Support |
| Q078 | bridges | Who is the only person linking Growth and Support? | Oskar Alvarez: nobody else works on systems of both Growth and Support. | The records do not contain information regarding a person linking Growth and Support. | missing Oskar Alvarez |
| Q079 | successors | Who could take over from Oskar Alvarez? | Candidates, not decisions. fernhill-sso (by work on auth): Lars Thorne (6), Pavel Hollis … | The records do not contain information regarding a successor for Oskar Alvarez. (DOC-16) | missing Lars Thorne, Pavel Hollis, Sana Petrov, fernhill-sso, oncall-runbook |
| Q080 | handoff_plan | Make a handoff plan for Oskar Alvarez before 2026-10-09. | 2 lines: fernhill-sso (candidates Lars Thorne, Pavel Hollis, Sana Petrov); oncall-runbook… | The records do not contain a handoff plan for Oskar Alvarez, only that the handover was planned with the Platform lead (DOC-15). | missing Lars Thorne, Pavel Hollis, Sana Petrov, fernhill-sso, oncall-runbook |

## Stale and unsupported answers

### plain RAG: 2 flagged

| id | flag | value | evidence |
|---|---|---|---|
| Q041 | stale (refused) | Growth | Yes, billing-api is owned by Growth. |
| Q063 | stale (refused) | Growth | According to msg-0174, route billing-api questions to Growth. |

