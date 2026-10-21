# Benchmark results

Plain retrieval against honest-memory on the synthetic Quillfield Billing corpus: 82 questions, seed 42, corpus generator 0.3.0 (files sha256 `7fd55087879c`).

- Run: 2026-10-15T21:02:20Z to 2026-10-15T21:08:40Z UTC (6.3 min), repo commit `d3b7579`.
- Chat model, for plain RAG and for the agent: `gemma4:26b-a4b-it-qat` (25.2B, Q4_0, digest `2dd70431afed`), temperature 0, reasoning_effort `default`, via Ollama 0.40.4.
- Embeddings for plain RAG: `embeddinggemma` (digest `85462619ee72`), top-k 8, units: whole episodes.
- FalkorDB 4.22.0 on port 6393; ingest: 403 episodes, 226 gate decisions in 1.66 s (ACCEPT 108, CONFIRM 110, SUPERSEDE 1, CLOSE 3, REJECT 4).

How each column is decided is in `bench/score.py` (names and dates, no model as judge) and `bench/README.md`. **clean** = correct and not stale, unsupported or leaking personal data. **no answer** = the answer says the records or memory do not hold it (a miss, not a wrong answer).

## All questions

| system | n | correct % | stale % | unsupported % | clean % | no answer % | median ms | literal-rule correct % | sentence-only correct % | personal data leaks |
|---|---|---|---|---|---|---|---|---|---|---|
| honest-memory, LLM agent | 82 | 93.9 | 0.0 | 0.0 | 93.9 | 0.0 | 3,059 | 80.5 | 87.8 | 0 |

- Agent: 81 of 82 answers are the model's own sentence (it passed the check against the rows); the rest are the template sentence built from the rows. 0 questions fell back to the router because the model called no tool; 0 runs recorded an error.

## By question type

### honest-memory, LLM agent

| type | n | correct % | stale % | unsupported % | clean % | no answer % | median ms |
|---|---|---|---|---|---|---|---|
| adversarial | 4 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 5,969 |
| bridges | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 2,591 |
| current_state | 24 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1,865 |
| departure_depth1 | 2 | 50.0 | 0.0 | 0.0 | 50.0 | 0.0 | 2,147 |
| departure_depth4 | 2 | 50.0 | 0.0 | 0.0 | 50.0 | 0.0 | 2,764 |
| handoff | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 19,290 |
| handoff_plan | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 3,177 |
| impact | 6 | 83.3 | 0.0 | 0.0 | 83.3 | 0.0 | 9,977 |
| not_in_graph | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 4,970 |
| provenance | 9 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 4,121 |
| single_source | 4 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 3,134 |
| state_at_time | 7 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1,634 |
| successors | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 2,273 |
| what_changed_since | 6 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 5,709 |
| what_if | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 1,532 |
| who_knows | 10 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 3,567 |

## Questions each system got wrong

Every row below is `correct = false`; the full verdicts (matched names, stale and unsupported values, the retrieved records or tool trace) are in `bench/results/<system>-seed42.jsonl`.

### honest-memory, LLM agent: 5 of 82

| id | type | question | gold | answer | why |
|---|---|---|---|---|---|
| Q045 | handoff | Who received the duplicate invoice emails incident from Support? | Selma Marsh (Platform on-call), handed over by Priya Farrow in TCK-0077. | Support is led by Priya Farrow (since 2026-08-17; DOC-02); Support has member Priya Farrow (since 2026-08-17; DOC-02); Support has member Tomas Kessler (since 2026-08-17… | missing Selma Marsh |
| Q065 | impact | What depends on ledger-db within 2 hops, and who must be told? | Within 2 hops: billing-api (owner Luca Ostrander, Platform, 1 hop); reporting (owner Aiko… | Within 2 hops, `billing-api`, `reporting`, `admin-console`, `invoice-renderer`, and `webhooks` depend on `ledger-db`, and Mateo Sandoval (owner and lead of the Payments … | missing Aiko Rademacher, Luca Ostrander, Priya Farrow, Viktor Hartwell |
| Q068 | departure_depth1 | What does Oskar Alvarez own, lead or belong to? | Nothing now: fernhill-sso, oncall-runbook (owner), the Platform team (member) ended on 20… | Oskar Alvarez owned fernhill-sso and oncall-runbook and was a member of Platform. | no refusal |
| Q069 | departure_depth4 | What leaves with Oskar Alvarez at depth 4? | Within 4 hops: auth (ADR-0016, shared with Pavel Hollis, owner Pavel Hollis, Platform, 2 … | Oskar Alvarez's departure on 2026-10-09 leaves behind ownership of fernhill-sso, oncall-runbook, and membership in the Platform team, as well as decisions like ADR-0016 … | missing Aiko Rademacher, Luca Ostrander, Mateo Sandoval, Pavel Hollis, Priya Farrow, Viktor Hartwell |
| Q080 | handoff_plan | Make a handoff plan for Oskar Alvarez before 2026-10-09. | 2 lines: fernhill-sso (candidates Lars Thorne, Pavel Hollis, Sana Petrov); oncall-runbook… | The handoff plan for Oskar Alvarez before 2026-10-09 includes the following: * **fernhill-sso (system):** Ask Lars Thorne (candidate) to take over fernhill-sso; Oskar Al… | missing Pavel Hollis, Sana Petrov |

## Stale and unsupported answers

### honest-memory, LLM agent: 0 flagged

