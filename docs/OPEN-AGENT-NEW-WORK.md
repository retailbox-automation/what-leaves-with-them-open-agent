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
                                            │                     answer + citations (record id, quoted line, claim)
                                            │                                   │  draft
                                            │                                   v
                                            └── re-read by id ───────── Verifier: quote in the record? (hard gate)
                                                                         model: supports / contradicts / unrelated
                                                                                │
                                              rejected: notes + each record as its source holds it now go back
                                              to the Investigator, which revises (at most 2 revisions)
                                                                                │ every citation accepted
                                                                                v
                                                     honest_memory write gate (a Finding citing the verified records)
```

- **Investigator** (`agent:investigator`) is the pre-existing honest_memory agent: a model picks tools over the graph
  and writes one sentence that the sentence check accepts or replaces. New in the window: it backs the answer with
  citations (a record id, one line quoted from that record, and the claim that line backs), and it revises from the
  Verifier's notes. The records are shown to its model as lists of quotable lines; after a rejection, a record the
  Verifier re-read is shown as its source holds it now, not as the memory holds it.
- **Verifier** (`agent:verifier`) re-reads every cited record by id through Zetaris, from the table of its own
  source system, and checks deterministically that the quoted line is in it (this check cannot be overruled). Then
  its own model, with its own prompt, judges whether the record as it reads now supports the claim. Any rejection
  goes back to the Investigator with the reason and the record's current lines. An answer that cites nothing because
  a name is not in the memory is checked against all three systems directly (one `UNION ALL` query).
- **Contradictions cannot vanish.** A record the Verifier found contradicting the answer may be answered, cited
  again, or named in the answer, but not dropped without a word: the Investigator adds what it says now if its model
  left it out, and the Verifier rejects a draft that still leaves it out. A record that backed an earlier answer and
  is dropped by a revision is judged again against the new answer.
- **Only a fully verified answer is written.** The write gate gets a Finding only when every citation was accepted
  and no contradiction is left unaddressed; otherwise the result says why nothing was written.

The loop is a real exchange, not a pipeline. In `logs/trace-20261024T123154Z-720b.jsonl` (tampered source) the
Verifier rejects the chat message whose text changed at its source, with the model's note "Sana Petrov remains the
owner"; the revision keeps the answer ADR-0009 supports and reports the conflict (its model wrote "but Sana Petrov
remains the owner" without the record id, so the Investigator added what msg-0148 says now). In `docs/open-agent-runs-2026-10-24/trace-20261024T123225Z-c454.jsonl`
nothing was tampered: the Investigator's model quoted "Sana Petrov stays on as backup for 30 days." as ADR-0009 (that
sentence is in msg-0148; the ADR says "stays available"), the Verifier's model said "supports", the quote check
rejected it, and the revision quoted the ADR's own line.

## Files (new or changed in the window)

| File | What |
|---|---|
| `zetaris_spike/` | 22 Oct stop-gate: registers the three sources in a local Zetaris, checks counts, a federated question, a cross-source JOIN and quote re-reads by id ([its README](../zetaris_spike/README.md)). Changed 24 Oct: `zclient.py` takes a per-call timeout. |
| `open_agent/sources.py` | The SQL: one SELECT per source for the ingest, a point read by id, the absence query. Two endpoints with the same statements: `ZetarisSource` (timeout, one retry, then `SourceUnavailable`) and `LocalSample` (SAMPLE_MODE, in-process SQLite). Tamper/restore of a chat record (Postgres via `docker exec psql`, or the local copy). |
| `open_agent/agents.py` | `Investigator` and `Verifier`, their prompts, the quote and contradiction rules, the deterministic paths used without a model. |
| `open_agent/llm.py` | The model by environment only (`OA_LLM_*`, `OA_VERIFIER_MODEL`); a client wrapper that writes every call into the trace with endpoint, model asked and model that answered, latency, tokens, response headers and any non-OpenAI body field (where a provider receipt is). |
| `open_agent/run.py` | Command line and the loop: build the graph from the sources if needed, optional tamper, Investigator, Verifier, revisions, write gate, JSON result. |
| `open_agent/trace.py` | `logs/trace-<run_id>.jsonl`, one event per step, echoed to stderr. |
| `open_agent/stats.py` | Recomputes the numbers on this page from trace files. |
| `honest_memory/ingest.py` | `ingest_episodes()`: the existing ingest loop, taking episodes from any reader (here, Zetaris rows); `ingest_corpus` calls it, so the jsonl path is unchanged. |
| `sample_data/sources/` | The three source tables as files (225 chat rows, 127 tickets, 51 docs), the same files that load Postgres, the CSV table and the docs API; generated by `python -m zetaris_spike.sources` from the seed-42 corpus. |
| `input_examples/` | Four questions: a normal one, a tampered source, a name in none of the three systems, two decision records that disagree. |
| `output_examples/`, `logs/` | Their outputs from real runs, live (`*.live.json`) and in SAMPLE_MODE (`*.sample.json`), and the matching traces. |
| `docs/open-agent-runs-2026-10-24/` | Five more live runs of each example (outputs and traces), for the numbers below. |
| `tests/test_open_agent.py` | 27 tests, fake model and local endpoint: rejection then revision, the revision cap, unreadable source, model down, quote rules, absence with and without hits, contradiction rules (named by the Investigator, rejected by the Verifier, re-judged when dropped, not written), the claim reaching the judge, ingest through the sample tables equal to the corpus ingest, trace completeness and no key in it, the command line end to end in SAMPLE_MODE. |

## Run it

Both modes need FalkorDB (the memory graph): `docker run -d --rm -p 6379:6379 falkordb/falkordb:6.0.2`, then
`pip install -e '.[agent]'`.

**SAMPLE_MODE** (no Zetaris, no model, no credentials, no GPU):

```
python -m open_agent.run --sample --input input_examples/01_current_owner.json
python -m open_agent.run --sample --input input_examples/02_tampered_source.json
python -m open_agent.run --sample --input input_examples/03_not_in_these_systems.json
```

What runs: the same SQL text against the three tables loaded from `sample_data/sources/` into SQLite (endpoint
`local-sample` in the output and trace; the one rewrite is `LATERAL VIEW explode(docs)`, which SQLite does not have,
so the docs table is stored one row per doc). The graph is built from those rows by the real ingest and write gate on
first use (`--ingest` rebuilds it). No model: the Investigator uses the deterministic router and quotes, from each
source record, the line that names the most of what the answer names; the Verifier's re-read and quote check run as
live, its model judgement is reported as `skipped: no model in SAMPLE_MODE`; a rejected citation is dropped and the
answer says why. The tamper changes the row in the in-memory copy. Nothing in sample mode replays a recorded output.
Example 04 is worded for the model; without one the router has no rule for it and says so (`no_claim`).

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

Other settings: `OA_VERIFIER_MODEL` (another model for the Verifier at the same endpoint), `OA_LLM_API_KEY` or
`OA_LLM_API_KEY_FILE`, `OA_LLM_TIMEOUT` (default 120 s), `OA_ZETARIS_TIMEOUT` (30 s), `OA_TENANT`, `OA_LOG_DIR`,
`FALKORDB_HOST`/`FALKORDB_PORT`. Without `OA_LLM_MODEL` the live run uses the deterministic paths too, still reading
through Zetaris. Switching the model provider is environment only.

## Rules the Verifier applies

| Reason | When |
|---|---|
| `quote_not_in_record` | the quoted line is not in the record as its source system holds it now (whitespace and a trailing full stop ignored) |
| `quote_not_a_sentence` | the quote has a line break or is over 300 characters: a citation points at a line, not a whole record |
| `quote_too_short` | under 4 words |
| `record_missing` | the source system has no record with that id |
| `model_contradicts`, `model_unrelated` | the Verifier's model says the record as it reads now does not support the claim |
| `contradiction_dropped` | a record that contradicted the answer in an earlier round is neither cited nor named now (draft-level) |
| `unreadable` | Zetaris did not answer after one retry; the citation is not accepted and no revision is asked for |

Accepted means the quote is found, has the right shape and the model said `supports` (or the model was skipped or
unavailable, which the verdict says). At most 2 revisions, so at most 3 verification rounds. Statuses: `verified`,
`absence_confirmed`, `no_claim`, `partially_verified`, `unverified`, `contradicted`. The result lists citations
dropped along the way with the record's current text (`dropped_citations`, `notices`).

Fallbacks: model down for the tool loop, the router answers; for citing or revising, the deterministic path; for
judging, the quote check alone decides and the verdict says `skipped: model unavailable (...)`. Zetaris down on the
ingest, the run stops with a structured error that points to `--sample`.

## Measured (24 Oct 2026, Mac Studio M5 Max, Zetaris 2.4.3.1 in Docker under emulation, FalkorDB 6.0.2)

Model in every live run: NVIDIA Nemotron 3 Nano 30B-A3B (`nemotron-3-nano:30b-a3b-q4_K_M`) on local Ollama,
temperature 0, `reasoning_effort=none`, for both agents. Not yet through Meterless Nightshift (next step).
Numbers from the 28 traces in `logs/` and `docs/open-agent-runs-2026-10-24/`, all made with the code at commit
266e330 (`python -m open_agent.stats logs/trace-*.jsonl docs/open-agent-runs-2026-10-24/trace-*.jsonl`).
The Mac was shared with other work (load average up to 20 during the runs).

| Example | Mode | Runs | Result | Rounds | Wall time p50 / max |
|---|---|---|---|---|---|
| 01 Who owns billing-api now? | live | 6 | verified 6, written 6 | 1 in 3 runs, 2 in 3 | 12.0 s / 14.0 s |
| 02 same question, msg-0148 edited at the source | live | 6 | verified 4, unverified 2; written 4; edit restored 6 | 2 in 4, 3 in 2 | 12.2 s / 14.9 s |
| 03 Who owns payroll-export? | live | 6 | absence_confirmed 6 | 1 | 1.7 s / 1.9 s |
| 04 What did the team decide about invoice rendering? | live | 6 | partially_verified 6, written 0 | 3 | 15.4 s / 16.6 s |
| 01 / 02 / 03 / 04 | sample | 1 each | verified / verified / absence_confirmed / no_claim | 1 / 2 / 1 / 1 | 1.46 s (with ingest) / 0.05 / 0.01 / 0.01 s |

What happened, from the traces:
- 01: the 2-round runs are the misquote catch described above (the hard gate overruled the model's "supports").
- 02: round 1 rejects msg-0148 in every run (`quote_not_in_record`; model note: the record now says Sana Petrov
  remains the owner). In 4 runs the revision keeps the ADR-0009 answer, the Investigator adds what msg-0148 says now
  (its model left the id out every time), and round 2 verifies it. In 2 runs the revision switched
  the answer to the edited chat message and dropped ADR-0009; the Verifier judged ADR-0009 again against the new
  answer (contradicts), the Investigator named it, and the Verifier's model then judged the chat line against the
  mixed answer as contradicting: unverified after 3 rounds, nothing written.
- 04: the Investigator's model chose `what_changed` rather than `decision_trail`, answered "synchronously
  (ADR-0004)", and the Verifier's model flagged ADR-0011 (asynchronous, it superseded ADR-0004) as contradicting. The
  revisions name both records but keep "synchronously" first; after 3 rounds the status is partially_verified and
  nothing is written. Nemotron 3 Nano as judge also called some lines `contradicts` against answers that report a
  conflict; `OA_VERIFIER_MODEL` lets the Verifier run on a stronger model.

| Step (live) | Calls | p50 | max |
|---|---|---|---|
| Zetaris re-read by id, Postgres chat | 21 | 0.11 s | 0.36 s |
| Zetaris re-read by id, docs REST API | 97 | 0.18 s | 0.69 s |
| Zetaris absence query over the three sources | 6 | 0.35 s | 0.43 s |
| Zetaris ingest SELECT chat / tickets / docs (225 / 127 / 51 rows) | 1 each | 0.14 / 0.18 / 0.25 s | |
| Investigator tool-loop call (prompt about 4.4k tokens) | 48 | 0.74 s | 1.17 s |
| Investigator cite call | 18 | 2.82 s | 3.27 s |
| Investigator revise call | 23 | 1.94 s | 3.26 s |
| Verifier judge call (prompt about 460 tokens) | 120 | 0.60 s | 0.99 s |

The first model call of this work took 66.3 s (cold prompt cache, a development run whose trace was not kept).
Citation checks over all 28 runs: 123, of which 81 accepted; rejections 10 `quote_not_in_record` (7 on the tampered
record, 3 model misquotes on untampered records), 20 `model_contradicts`, 12 `model_unrelated`. Model verdicts: 80
supports, 26 contradicts, 12 unrelated, 5 skipped (sample mode).

Graph built through Zetaris: 403 records, field for field equal to `data/corpus/42/episodes.jsonl` (0 differences;
the tickets CSV comes back with `ts` as `2026-08-17 16:43:00.0` and empty `refs` as null, both normalised), and the
write gate's decisions on it equal the jsonl ingest (ACCEPT 108, CONFIRM 110, SUPERSEDE 1, CLOSE 3, REJECT 4).
A Postgres UPDATE is visible through Zetaris on the next SELECT. From a fresh clone with an empty environment
(`env -i`, only `SAMPLE_MODE=true` and the FalkorDB port), example 02 ran in 1.45 s: graph built, tamper caught,
verified after 2 rounds, restored.

Tests: 474 passed, 1 skipped (`HM_TEST_PORTS=<falkordb port> HM_SKIP_LLM=1 python -m pytest -q`; 447 passed before
this work). Mutation checks: forcing the quote check to pass fails 4 tests; a loop that never revises fails 5;
removing the Investigator's contradiction guard fails 2, the Verifier's fails 1, the re-judging of dropped evidence 1.

## Not done here, and limits

- Meterless Nightshift as the provider and its receipt field: the code takes any OpenAI-compatible endpoint and
  writes headers and extra body fields into the trace (`provider_receipt` picks a field or header whose name says
  receipt); which field Nightshift uses is wired by the Nightshift lane.
- SAMPLE_MODE still needs a FalkorDB; there is no compose file for judges in this change.
- Tamper works on chat records (Postgres) only. The memory graph is not refreshed after an edit: the Verifier's
  catch is reported, the graph keeps the old copy.
- The absence query uses `instr`, case-sensitive in both engines; a name written in another case is not found.
- A record that backed an earlier answer is re-judged from the text read in that round, not read again.
- Answer quality is bounded by the model: with Nemotron 3 Nano the superseded-decision example never reached
  `verified` (see above). The loop's guarantee is about what gets written, not that every question is answered.
