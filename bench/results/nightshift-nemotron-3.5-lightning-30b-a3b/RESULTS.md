# Benchmark results

Plain retrieval against honest-memory on the synthetic Quillfield Billing corpus: 82 questions, seed 42, corpus generator 0.3.0 (files sha256 `7fd55087879c`).

- Run: 2026-10-24T14:06:49Z to 2026-10-24T14:24:56Z UTC (18.1 min), repo commit `bc9453a`.
- Chat model, for plain RAG and for the agent: `local-runtime/nemotron-3.5-lightning-30b-a3b` (?, ?, digest `?`), temperature 0, reasoning_effort `default`, via Ollama not used.
- Embeddings for plain RAG: `embeddinggemma` (digest `?`), top-k 8, units: whole episodes.
- FalkorDB 6.0.2 on port 6395; ingest: 403 episodes, 226 gate decisions in 1.53 s (ACCEPT 108, CONFIRM 110, SUPERSEDE 1, CLOSE 3, REJECT 4).

How each column is decided is in `bench/score.py` (names and dates, no model as judge) and `bench/README.md`. **clean** = correct and not stale, unsupported or leaking personal data. **no answer** = the answer says the records or memory do not hold it (a miss, not a wrong answer).

## All questions

| system | n | correct % | stale % | unsupported % | clean % | no answer % | median ms | literal-rule correct % | sentence-only correct % | personal data leaks |
|---|---|---|---|---|---|---|---|---|---|---|
| honest-memory, LLM agent | 82 | 97.6 | 0.0 | 0.0 | 97.6 | 0.0 | 10,385 | 89.0 | 95.1 | 0 |

- Agent: 74 of 82 answers are the model's own sentence (it passed the check against the rows); the rest are the template sentence built from the rows. 0 questions fell back to the router because the model called no tool; 0 runs recorded an error.

## By question type

### honest-memory, LLM agent

| type | n | correct % | stale % | unsupported % | clean % | no answer % | median ms |
|---|---|---|---|---|---|---|---|
| adversarial | 4 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 9,900 |
| bridges | 2 | 50.0 | 0.0 | 0.0 | 50.0 | 0.0 | 6,680 |
| current_state | 24 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 4,627 |
| departure_depth1 | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 21,704 |
| departure_depth4 | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 24,794 |
| handoff | 1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 48,206 |
| handoff_plan | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 14,571 |
| impact | 6 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 19,213 |
| not_in_graph | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 8,701 |
| provenance | 9 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 13,886 |
| single_source | 4 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 9,303 |
| state_at_time | 7 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 9,585 |
| successors | 1 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 12,791 |
| what_changed_since | 6 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 25,148 |
| what_if | 2 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 10,359 |
| who_knows | 10 | 100.0 | 0.0 | 0.0 | 100.0 | 0.0 | 11,652 |

## Questions each system got wrong

Every row below is `correct = false`; the full verdicts (matched names, stale and unsupported values, the retrieved records or tool trace) are in `bench/results/<system>-seed42.jsonl`.

### honest-memory, LLM agent: 2 of 82

| id | type | question | gold | answer | why |
|---|---|---|---|---|---|
| Q045 | handoff | Who received the duplicate invoice emails incident from Support? | Selma Marsh (Platform on-call), handed over by Priya Farrow in TCK-0077. | Support has member Priya Farrow (since 2026-08-17; DOC-02); Support has member Tomas Kessler (since 2026-08-17; DOC-02); Support has member Zara Lowell (since 2026-08-17… | missing Selma Marsh |
| Q078 | bridges | Who is the only person linking Growth and Support? | Oskar Alvarez: nobody else works on systems of both Growth and Support. | Omar Fenwick is the only person linking Growth and Support. | missing Oskar Alvarez |

## Stale and unsupported answers

### honest-memory, LLM agent: 0 flagged

