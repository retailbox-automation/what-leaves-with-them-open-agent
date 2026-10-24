# Open Agent Hackathon 2026: what was built in the window

Entry: What Leaves With Them, track 02 "The Agent That Can Explain Why". Build window 22 Oct 00:00 UTC to
27 Oct 23:45 UTC. Everything under `honest_memory/` (the memory graph on FalkorDB, its tools, the write gate, the
deterministic router) existed before the window and is declared in [PRE-EXISTING.md](../PRE-EXISTING.md). This page
covers what was written in the window: `zetaris_spike/` (22 Oct) and `open_agent/` (24 Oct).

## What it does

Three source systems of a synthetic company (Quillfield Billing) sit behind one Zetaris SQL endpoint: team chat in
Postgres, support tickets in a CSV file, documents behind a REST API. Two agents answer a question about them and
check each other:

```
 Postgres chat ─┐
 tickets CSV ───┼─ Zetaris (Lightning SQL) ─┬─ 3 SELECTs ─> ingest ─> memory graph (FalkorDB, honest_memory)
 docs REST API ─┘                           │                                   │
                                            │                     Investigator: tools over the graph,
                                            │                     answer + citations (record id + quote)
                                            │                                   │  draft
                                            │                                   v
                                            └── re-read by id ───────── Verifier: quote in the record? (hard gate)
                                                                         model: supports / contradicts / unrelated
                                                                                │
                                              rejected: notes + the record's current text go back to the
                                              Investigator, which revises (at most 2 revisions)
                                                                                │ accepted
                                                                                v
                                                     honest_memory write gate (Finding citing only verified records)
```

- **Investigator** (`agent:investigator`) is the pre-existing honest_memory agent: a model picks tools over the graph
  and writes one sentence that the sentence check accepts or replaces. New in the window: it then backs the answer
  with citations, a record id and a sentence quoted from that record as the memory holds it, and it revises the answer
  from the Verifier's notes.
- **Verifier** (`agent:verifier`) re-reads every cited record by id through Zetaris, from the table of its own
  source system, and checks deterministically that the quoted sentence is in it (this check cannot be overruled).
  Then its own model, with its own prompt, judges whether the record as it reads now supports the answer. Any
  rejection goes back to the Investigator with the reason and the record's current text. An answer that cites nothing
  because a name is not in the memory is checked against all three systems directly (one `UNION ALL` query).
- Only citations the Verifier accepted reach the write gate. An answer with no verified source is not written.

The loop is a real exchange, not a pipeline: in the tampered-source example below the Verifier rejects twice and the
Investigator sends a different set of citations each time: two records, then the whole ADR as one quote, then one
line of it (trace `logs/trace-20261024T113445Z-3ae8.jsonl`).

## Files (new or changed in the window)

| File | What |
|---|---|
| `zetaris_spike/` | 22 Oct stop-gate: registers the three sources in a local Zetaris, checks counts, a federated question, a cross-source JOIN and quote re-reads by id ([its README](../zetaris_spike/README.md)). Changed 24 Oct: `zclient.py` takes a per-call timeout. |
| `open_agent/sources.py` | The SQL: one SELECT per source for the ingest, a point read by id, the absence query. Two endpoints with the same statements: `ZetarisSource` (timeout, one retry, then `SourceUnavailable`) and `LocalSample` (SAMPLE_MODE, in-process SQLite). Tamper/restore of a chat record (Postgres via `docker exec psql`, or the local copy). |
| `open_agent/agents.py` | `Investigator` and `Verifier`, their prompts, the quote rules, the deterministic paths used without a model. |
| `open_agent/llm.py` | The model by environment only (`OA_LLM_*`); a client wrapper that writes every call into the trace with endpoint, model asked and model that answered, latency, tokens, response headers and any non-OpenAI body field (where a provider receipt is). |
| `open_agent/run.py` | Command line and the loop: build the graph from the sources if needed, optional tamper, Investigator, Verifier, revisions, write gate, JSON result. |
| `open_agent/trace.py` | `logs/trace-<run_id>.jsonl`, one event per step, echoed to stderr. |
| `open_agent/stats.py` | Recomputes the numbers on this page from trace files. |
| `honest_memory/ingest.py` | `ingest_episodes()`: the existing ingest loop, taking episodes from any reader (here, Zetaris rows); `ingest_corpus` calls it, so the jsonl path is unchanged. |
| `sample_data/sources/` | The three source tables as files (225 chat rows, 127 tickets, 51 docs), the same files that load Postgres, the CSV table and the docs API; generated by `python -m zetaris_spike.sources` from the seed-42 corpus. |
| `input_examples/`, `output_examples/`, `logs/` | Three questions; their outputs from real runs, live and in SAMPLE_MODE; the matching traces. |
| `docs/open-agent-runs-2026-10-24/` | Five more live runs of each example (outputs and traces), for the numbers below. |
| `tests/test_open_agent.py` | 20 tests: fake model and local endpoint; rejection then revision, the revision cap, unreadable source, model down, quote rules, absence with and without hits, ingest through the sample tables equal to the corpus ingest, trace completeness and no key in it, the command line end to end in SAMPLE_MODE. |

## Run it

Both modes need FalkorDB (the memory graph): `docker run -d --rm -p 6379:6379 falkordb/falkordb:6.0.2`, then
`pip install -e '.[agent]'`.

**SAMPLE_MODE** (no Zetaris, no model, no credentials, no GPU):

```
python -m open_agent.run --sample --ingest --input input_examples/01_current_owner.json
python -m open_agent.run --sample --input input_examples/02_tampered_source.json
python -m open_agent.run --sample --input input_examples/03_not_in_these_systems.json
```

What runs: the same SQL text against the three tables loaded from `sample_data/sources/` into SQLite (endpoint
`local-sample` in the output and trace; the one rewrite is `LATERAL VIEW explode(docs)`, which SQLite does not have,
so the docs table is stored one row per doc). The graph is built from those rows by the real ingest and write gate.
No model: the Investigator uses the deterministic router and quotes, from each source record, the sentence that names
the most of what the answer names; the Verifier's re-read and quote check run as live, its model judgement is
reported as `skipped: no model in SAMPLE_MODE`; a rejected citation is dropped and the answer says why. The tamper
changes the row in the in-memory copy. Nothing in sample mode replays a recorded output.

**Live** (local Zetaris with the three sources registered, see `zetaris_spike/README.md`):

```
export ZETARIS_BUNDLE_ENV=~/zetaris-openagent/zetaris-platform/.env        # admin sign-in, never printed
export OA_LLM_BASE_URL=http://localhost:11434/v1                           # or Meterless Nightshift
export OA_LLM_MODEL=nemotron-3-nano:30b-a3b-q4_K_M
export OA_LLM_REASONING=none                                               # optional reasoning_effort
python -m open_agent.run --ingest --input input_examples/01_current_owner.json
python -m open_agent.run --input input_examples/02_tampered_source.json    # edits msg-0148 in Postgres, restores it
python -m open_agent.run --question "Who owns payroll-export?"
```

Other settings: `OA_LLM_API_KEY` or `OA_LLM_API_KEY_FILE`, `OA_LLM_TIMEOUT` (default 120 s), `OA_ZETARIS_TIMEOUT`
(30 s), `OA_TENANT`, `OA_LOG_DIR`, `FALKORDB_HOST`/`FALKORDB_PORT`. Without `OA_LLM_MODEL` the live run uses the
deterministic paths too, still reading through Zetaris. Switching the model provider is environment only.

## Rules the Verifier applies

| Reason | When |
|---|---|
| `quote_not_in_record` | the quoted sentence is not in the record as its source system holds it now (whitespace and a trailing full stop ignored) |
| `quote_not_a_sentence` | the quote has a line break or is over 300 characters: a citation points at a sentence, not a whole record |
| `quote_too_short` | under 4 words |
| `record_missing` | the source system has no record with that id |
| `model_contradicts`, `model_unrelated` | the Verifier's model says the record as it reads now does not support the answer |
| `unreadable` | Zetaris did not answer after one retry; the citation is not accepted and no revision is asked for |

Accepted means the quote is found, has the right shape and the model said `supports` (or the model was skipped or
unavailable, which the verdict says). At most 2 revisions, so at most 3 verification rounds. The result lists
citations dropped along the way with the record's current text (`dropped_citations`, `notices`).

Fallbacks: model down for the tool loop, the router answers; for citing or revising, the deterministic path; for
judging, the quote check alone decides and the verdict says `skipped: model unavailable (...)`. Zetaris down on the
ingest, the run stops with a structured error that points to `--sample`.

## Measured (24 Oct 2026, Mac Studio M5 Max, Zetaris 2.4.3.1 in Docker under emulation, FalkorDB 6.0.2)

Model in every live run: NVIDIA Nemotron 3 Nano 30B-A3B (`nemotron-3-nano:30b-a3b-q4_K_M`) on local Ollama,
temperature 0, `reasoning_effort=none`, for both agents. Not yet through Meterless Nightshift (next step).
Numbers from the 21 traces in `logs/` and `docs/open-agent-runs-2026-10-24/` (`python -m open_agent.stats ...`).

| Example | Mode | Runs | Result | Rounds | Wall time p50 / max |
|---|---|---|---|---|---|
| 01 Who owns billing-api now? | live | 6 | verified 6/6 | 1 in all | 11.6 s / 13.0 s |
| 02 same, msg-0148 edited at the source | live | 6 | verified 6/6, edit restored 6/6 | 3 in all | 24.9 s / 27.2 s |
| 03 Who owns payroll-export? | live | 6 | absence_confirmed 6/6 | 1 | 3.65 s / 3.78 s |
| 01 / 02 / 03 | sample | 1 each | verified / verified / absence_confirmed | 1 / 2 / 1 | 1.43 s (with ingest) / 0.03 s / 0.01 s |

Example 02, every live run: round 1 accepts ADR-0009 and rejects msg-0148 (`quote_not_in_record`, model verdict
`contradicts`, note: the record now says Sana Petrov remains the owner); the revision quotes the whole ADR record
and round 2 rejects it (`quote_not_a_sentence`); the second revision quotes one line and round 3 accepts it. The write
gate stores a Finding citing ADR-0009 only.

| Step (live) | Calls | p50 | max |
|---|---|---|---|
| Zetaris re-read by id, Postgres chat | 12 | 0.20 s | 0.65 s |
| Zetaris re-read by id, docs REST API | 24 | 0.34 s | 1.04 s |
| Zetaris absence query over the three sources | 6 | 0.41 s | 1.08 s |
| Zetaris ingest SELECT chat / tickets / docs (225 / 127 / 51 rows) | 1 each | 0.18 / 0.20 / 0.29 s | |
| Investigator tool-loop call (prompt about 4.3k tokens) | 36 | 1.36 s | 2.53 s |
| Investigator cite call | 12 | 4.32 s | 5.60 s |
| Investigator revise call | 12 | 4.62 s | 7.83 s |
| Verifier judge call (prompt about 430 tokens) | 36 | 1.36 s | 2.38 s |

The first model call of this work took 66.3 s (cold prompt cache, a development run whose trace was not kept).
Checks over all runs: 41 citation checks, 28 accepted; rejections 7 `quote_not_in_record`, 6 `quote_not_a_sentence`;
model verdicts 30 supports, 6 contradicts, 5 skipped (sample mode).

Graph built through Zetaris: 403 records, field for field equal to `data/corpus/42/episodes.jsonl` (0 differences;
the tickets CSV comes back with `ts` as `2026-08-17 16:43:00.0` and empty `refs` as null, both normalised), and the
write gate's decisions on it equal the jsonl ingest (ACCEPT 108, CONFIRM 110, SUPERSEDE 1, CLOSE 3, REJECT 4).
A Postgres UPDATE is visible through Zetaris on the next SELECT.

Tests: 467 passed, 1 skipped (`HM_TEST_PORTS=6395 HM_SKIP_LLM=1 python -m pytest -q`; 447 passed before this work).

## Not done here, and limits

- Meterless Nightshift as the provider and its receipt field: the code takes any OpenAI-compatible endpoint and
  writes headers and extra body fields into the trace; which field is the receipt is wired by the Nightshift lane.
- SAMPLE_MODE still needs a FalkorDB; there is no compose file for judges in this change.
- Tamper works on chat records (Postgres) only. The memory graph is not refreshed after an edit: the Verifier's
  catch is reported, the graph keeps the old copy.
- The absence query uses `instr`, case-sensitive in both engines; a name written in another case is not found.
- In example 02 the model's final answer does not mention the edited chat message itself; the result's `notices`
  carry it, built from the Verifier's notes rather than from the model.
