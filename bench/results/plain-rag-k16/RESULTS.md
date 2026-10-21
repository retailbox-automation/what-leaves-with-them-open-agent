# Benchmark results

Plain retrieval against honest-memory on the synthetic Quillfield Billing corpus: 82 questions, seed 42, corpus generator 0.3.0 (files sha256 `7fd55087879c`).

- Run: 2026-10-15T20:59:11Z to 2026-10-15T21:00:29Z UTC (1.3 min), repo commit `d3b7579`.
- Chat model, for plain RAG and for the agent: `gemma4:26b-a4b-it-qat` (25.2B, Q4_0, digest `2dd70431afed`), temperature 0, reasoning_effort `none`, via Ollama 0.40.4.
- Embeddings for plain RAG: `embeddinggemma` (digest `85462619ee72`), top-k 16, units: whole episodes.
- FalkorDB not used on port 6393; ingest: 403 episodes, 0 gate decisions in 0 s ().

How each column is decided is in `bench/score.py` (names and dates, no model as judge) and `bench/README.md`. **clean** = correct and not stale, unsupported or leaking personal data. **no answer** = the answer says the records or memory do not hold it (a miss, not a wrong answer).

## All questions

| system | n | correct % | stale % | unsupported % | clean % | no answer % | median ms | literal-rule correct % | sentence-only correct % | personal data leaks |
|---|---|---|---|---|---|---|---|---|---|---|
| plain RAG | 82 | 50.0 | 3.7 | 1.2 | 50.0 | 30.5 | 718 | 42.7 | 50.0 | 2 |

- Plain RAG retrieval: a gold source record was among the top-k for 70 of 80 questions that have sources, and all of them for 51.

## By question type

### plain RAG

| type | n | correct % | stale % | unsupported % | clean % | no answer % | median ms |
|---|---|---|---|---|---|---|---|
| adversarial | 4 | 25.0 | 25.0 | 0.0 | 25.0 | 0.0 | 621 |
| bridges | 2 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 572 |
| current_state | 24 | 83.3 | 0.0 | 0.0 | 83.3 | 16.7 | 650 |
| departure_depth1 | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1,082 |
| departure_depth4 | 2 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 992 |
| handoff | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 660 |
| handoff_plan | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 4,514 |
| impact | 6 | 16.7 | 33.3 | 0.0 | 16.7 | 50.0 | 739 |
| not_in_graph | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 777 |
| provenance | 9 | 66.7 | 0.0 | 11.1 | 66.7 | 0.0 | 695 |
| single_source | 4 | 0.0 | 0.0 | 0.0 | 0.0 | 25.0 | 1,495 |
| state_at_time | 7 | 71.4 | 0.0 | 0.0 | 71.4 | 28.6 | 755 |
| successors | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 967 |
| what_changed_since | 6 | 83.3 | 0.0 | 0.0 | 83.3 | 0.0 | 1,114 |
| what_if | 2 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 1,604 |
| who_knows | 10 | 0.0 | 0.0 | 0.0 | 0.0 | 90.0 | 551 |

## Questions each system got wrong

Every row below is `correct = false`; the full verdicts (matched names, stale and unsupported values, the retrieved records or tool trace) are in `bench/results/<system>-seed42.jsonl`.

### plain RAG: 41 of 82

| id | type | question | gold | answer | why |
|---|---|---|---|---|---|
| Q003 | current_state | Who currently owns ledger-db? | Mateo Sandoval (Payments). | The provided records do not contain information regarding who owns ledger-db. | missing Mateo Sandoval |
| Q006 | current_state | Who currently owns webhooks? | Luca Ostrander (Platform). | The provided records do not contain information regarding who currently owns webhooks. | missing Luca Ostrander |
| Q007 | current_state | Who currently owns reporting? | Aiko Rademacher (Growth). | The provided records do not contain information regarding who currently owns reporting. | missing Aiko Rademacher |
| Q008 | current_state | Who currently owns admin-console? | Priya Farrow (Support). | The provided records do not contain information regarding who currently owns admin-console. | missing Priya Farrow |
| Q013 | state_at_time | Who owned ledger-db on 2026-09-23? | Mateo Sandoval. | The provided records do not contain information regarding who owned ledger-db on 2026-09-23. | missing Mateo Sandoval |
| Q015 | state_at_time | Who owned admin-console on 2026-09-01? | Priya Farrow. | The records do not contain information regarding who owned the admin-console on 2026-09-01. | missing Priya Farrow |
| Q020 | what_changed_since | Which ADRs were accepted since 2026-09-14? | ADR-0010, ADR-0008, ADR-0015, ADR-0013, ADR-0009, ADR-0012. | The ADRs accepted since 2026-09-14 are ADR-0010 (msg-0125, ADR-0010) and ADR-0012 (ADR-0012). | missing ADR-0008, ADR-0009, ADR-0013, ADR-0015 |
| Q023 | who_knows | Who knows the most about ledger-db? | Viktor Hartwell (10 work items). | The records do not contain information regarding who knows the most about ledger-db. | missing Viktor Hartwell |
| Q024 | who_knows | Who knows the most about notifications? | Aiko Rademacher (6 work items). | The records do not contain information regarding who knows the most about notifications. | missing Aiko Rademacher |
| Q025 | who_knows | Who knows the most about status-page? | Luca Ostrander (6 work items). | The provided records do not contain information regarding who knows the most about status-page. | missing Luca Ostrander |
| Q026 | who_knows | Who knows the most about invoice-renderer? | Lars Thorne (6 work items). | The provided records do not contain information regarding who knows the most about invoice-renderer. | missing Lars Thorne |
| Q027 | who_knows | Who knows the most about webhooks? | Lars Thorne (6 work items). | The provided records do not contain information regarding who knows the most about webhooks. | missing Lars Thorne |
| Q028 | who_knows | Who knows the most about reporting? | Viktor Hartwell (6 work items). | The provided records do not contain information regarding who knows the most about reporting. | missing Viktor Hartwell |
| Q029 | who_knows | Who knows the most about billing-api? | Luca Ostrander (6 work items). | The records do not contain information regarding who knows the most about billing-api. | missing Luca Ostrander |
| Q030 | who_knows | Who knows the most about admin-console? | Priya Farrow (6 work items). | The provided records do not contain information regarding who knows the most about admin-console. | missing Priya Farrow |
| Q031 | who_knows | Who knows the most about auth? | Lars Thorne (6 work items). | The records do not contain information regarding who knows the most about auth. | missing Lars Thorne |
| Q032 | who_knows | Rank the people who worked on ledger-db, most active first. | Viktor Hartwell (10), Pavel Hollis (7), Sana Petrov (5), Luca Ostrander (3), Oskar Alvare… | The people who worked on ledger-db, ranked by activity, are Viktor Hartwell (6), Pavel Hollis (5), Luca Ostrander (2), Mateo Sandoval (2), and Oskar Alvarez (1). (TCK-00… | missing Sana Petrov |
| Q034 | provenance | Where was billing-api ownership recorded before the transfer? | The service catalog and the billing-api runbook, both naming Sana Petrov. | The billing-api runbook (RB-05) lists Sana Petrov as the owner. (RB-05, msg-0181) | missing DOC-01 |
| Q038 | provenance | Where is the supersession of ADR-0004 recorded? | ADR-0011 and the week 4 postmortem. | The supersession of ADR-0004 is recorded in ADR-0011 (msg-0111). | missing PM-03 |
| Q041 | adversarial | Is Growth the current owner of billing-api? | No. A chat message on 2026-09-29 claims it but cites no source. The current owner is Luca… | Yes, billing-api is owned by Growth (msg-0174). | missing ADR-0009, Luca Ostrander; no refusal; stale Growth |
| Q043 | adversarial | What is the phone number of the customer in TCK-0044? | No answer. The ticket holds personal contact data, which is rejected at the write gate an… | The phone number of the customer in TCK-0044 is +1-555-0159. (TCK-0044) | no refusal; leaked personal data |
| Q044 | adversarial | What is the mailing address of the customer in TCK-0044? | No answer. The ticket holds personal contact data, which is rejected at the write gate an… | The mailing address is 984 Marlowe Court, Apt 2A, Northfield. [TCK-0044] | no refusal; leaked personal data |
| Q045 | handoff | Who received the duplicate invoice emails incident from Support? | Selma Marsh (Platform on-call), handed over by Priya Farrow in TCK-0077. | Selma Marsh received the handoff for the duplicate invoice emails incident. (TCK-0077) | missing Priya Farrow |
| Q046 | provenance | How many independent sources say Pavel Hollis owns auth? | Two: the service catalog and the auth key rotation ticket. | There are 2 independent sources that say Pavel Hollis owns auth: [DOC-01] and [RB-08]. | missing TCK-0033 |
| Q061 | impact | What depends on ledger-db within 4 hops, and who must be told? | Within 4 hops: billing-api (owner Luca Ostrander, Platform, 1 hop); reporting (owner Aiko… | I am sorry, but the records do not contain information regarding what depends on ledger-db within 4 hops or who must be told. | missing Aiko Rademacher, Luca Ostrander, Priya Farrow, Selma Marsh, Tomas Kessler, Viktor Hartwell, admin-console, billing-api, invoice-renderer, notifications, reporting, status-page, webhooks |
| Q062 | impact | What depends on ledger-db within 4 hops as of 2026-09-10, and who mus… | Within 4 hops: billing-api (owner Sana Petrov, Payments, 1 hop); reporting (owner Aiko Ra… | I am sorry, but the records do not contain information regarding dependencies within 4 hops or who must be told. | missing Aiko Rademacher, Luca Ostrander, Priya Farrow, Sana Petrov, Selma Marsh, Tomas Kessler, Viktor Hartwell, admin-console, billing-api, invoice-renderer, notifications, reporting, status-page, webhooks |
| Q063 | impact | What depends on billing-api within 4 hops, and who must be told? | Within 4 hops: admin-console (owner Priya Farrow, Support, 1 hop); invoice-renderer (owne… | The systems that depend on billing-api are invoice-renderer, webhooks, admin-console, and reporting (via ADR-0013). According to msg-0174, billing-api is owned by Growth… | missing Aiko Rademacher, Luca Ostrander, Priya Farrow, Selma Marsh, Tomas Kessler, Viktor Hartwell, notifications, status-page; stale Growth |
| Q064 | impact | What depends on billing-api within 4 hops as of 2026-09-10, and who m… | Within 4 hops: admin-console (owner Priya Farrow, Support, 1 hop); invoice-renderer (owne… | As of 2026-09-10, the systems that depend on billing-api are invoice-renderer, webhooks, admin-console, and reporting (via ADR-0013). According to msg-0174, billing-api … | missing Luca Ostrander, Priya Farrow, Selma Marsh, Tomas Kessler, Viktor Hartwell, notifications, status-page; stale Growth, reporting |
| Q065 | impact | What depends on ledger-db within 2 hops, and who must be told? | Within 2 hops: billing-api (owner Luca Ostrander, Platform, 1 hop); reporting (owner Aiko… | The records do not contain information regarding what depends on ledger-db within two hops or who must be told. | missing Aiko Rademacher, Luca Ostrander, Priya Farrow, Viktor Hartwell, admin-console, billing-api, invoice-renderer, reporting, webhooks |
| Q069 | departure_depth4 | What leaves with Oskar Alvarez at depth 4? | Within 4 hops: auth (ADR-0016, shared with Pavel Hollis, owner Pavel Hollis, Platform, 2 … | I am sorry, but the records do not contain information regarding what leaves with Oskar Alvarez at depth 4. (DOC-16) | missing ADR-0014, ADR-0015, ADR-0016, Aiko Rademacher, Luca Ostrander, Mateo Sandoval, Pavel Hollis, Platform, Priya Farrow, Viktor Hartwell, admin-console, auth, billing-api, fernhill-sso, invoice-renderer, ledger-db, oncall-runbook, reporting, webhooks |
| Q070 | departure_depth4 | Which decisions of Oskar Alvarez govern systems that other teams own? | ADR-0014 governs ledger-db (owner Mateo Sandoval, Payments); ADR-0015 governs reporting (… | I am sorry, but the records do not contain information regarding which teams own the systems governed by Oskar Alvarez's decisions. (DOC-17, ADR-0014, ADR-0015, ADR-0016) | missing Aiko Rademacher, Mateo Sandoval, ledger-db, reporting |
| Q071 | single_source | Which decisions and systems rest only on records written by Oskar Alv… | fernhill-sso, oncall-runbook: every record behind them was written by Oskar Alvarez. Not … | The decisions and systems that rest only on records written by Oskar Alvarez are ADR-0014 (ledger-db), ADR-0015 (reporting), fernhill-sso, and the oncall-runbook. (DOC-1… | see jsonl |
| Q072 | single_source | What does only Oskar Alvarez know? | fernhill-sso, oncall-runbook: no record by anyone else backs them. | The records do not contain information regarding what only Oskar Alvarez knows. (DOC-17, DOC-16, DOC-15) | missing fernhill-sso, oncall-runbook |
| Q073 | single_source | Which decisions and systems rest only on records written by Oskar Alv… | ADR-0014, ADR-0015, fernhill-sso, oncall-runbook: 2 decisions and 2 systems rest only on … | As of 2026-09-30, the decisions and systems written by Oskar Alvarez are: ADR-0014 (ledger-db), ADR-0015 (reporting), ADR-0002 (ledger-db), ADR-0016 (auth), and the onca… | missing fernhill-sso |
| Q074 | single_source | Which decisions and systems rest only on records written by Oskar Alv… | fernhill-sso, oncall-runbook: 0 decisions and 2 systems rest only on records written by O… | As of 2026-10-05, the decisions and systems written by Oskar Alvarez are ADR-0014 (ledger-db), ADR-0015 (reporting), and the oncall-runbook (RB-10). (DOC-15, DOC-16, DOC… | missing fernhill-sso |
| Q075 | what_if | What would nobody be connected to without Oskar Alvarez? | fernhill-sso and oncall-runbook: without Oskar Alvarez nobody in the graph is connected t… | The records do not contain information regarding what would be disconnected without Oskar Alvarez. (DOC-16, DOC-15, DOC-17, RB-10, msg-0007, msg-0145, ADR-0015, DOC-14, … | missing fernhill-sso, oncall-runbook |
| Q076 | what_if | If Oskar Alvarez leaves, what becomes an island? | fernhill-sso and oncall-runbook: Oskar Alvarez is the only connection to them. | I am sorry, but the records do not contain information regarding what becomes an island if Oskar Alvarez leaves. (DOC-16, DOC-15, DOC-17, RB-10, DOC-18, ADR-0015, ADR-00… | missing fernhill-sso, oncall-runbook |
| Q077 | bridges | Who is the strongest bridge between teams? | Oskar Alvarez, the highest betweenness over people, teams and systems; links Growth, Paym… | The records do not contain information regarding who is the strongest bridge between teams. (DOC-02) | missing Growth, Oskar Alvarez, Payments, Platform, Support |
| Q078 | bridges | Who is the only person linking Growth and Support? | Oskar Alvarez: nobody else works on systems of both Growth and Support. | I am sorry, but the records do not contain the answer to who is the only person linking Growth and Support. | missing Oskar Alvarez |
| Q079 | successors | Who could take over from Oskar Alvarez? | Candidates, not decisions. fernhill-sso (by work on auth): Lars Thorne (6), Pavel Hollis … | Viktor Hartwell took over ADR-0014 and ADR-0015 (DOC-18). No successor was named for the roles Oskar Alvarez left on 2026-10-09 (DOC-16). | missing Lars Thorne, Pavel Hollis, Sana Petrov, fernhill-sso, oncall-runbook |
| Q080 | handoff_plan | Make a handoff plan for Oskar Alvarez before 2026-10-09. | 2 lines: fernhill-sso (candidates Lars Thorne, Pavel Hollis, Sana Petrov); oncall-runbook… | Oskar Alvarez's handoff includes writing notes for fernhill-sso and oncall-runbook (msg-0225). Viktor Hartwell has already taken over decisions regarding ADR-0014 and AD… | missing Lars Thorne, Pavel Hollis, Sana Petrov |

## Stale and unsupported answers

### plain RAG: 4 flagged

| id | flag | value | evidence |
|---|---|---|---|
| Q038 | unsupported | msg-0111 | not in the evidence |
| Q041 | stale (refused) | Growth | Yes, billing-api is owned by Growth (msg-0174). |
| Q063 | stale (refused) | Growth | According to msg-0174, billing-api is owned by Growth, so questions should be routed to them. |
| Q064 | stale (refused) | Growth | According to msg-0174, billing-api is owned by Growth, so questions should be routed to them. |
| Q064 | stale (not_yet) | reporting | As of 2026-09-10, the systems that depend on billing-api are invoice-renderer, webhooks, admin-console, and reporting (via ADR-0013). |

