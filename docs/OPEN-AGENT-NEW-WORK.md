# Open Agent Hackathon 2026: what was built in the window

Entry: What Leaves With Them, track 02 "The Agent That Can Explain Why". Build window 22 Oct 00:00 UTC to
27 Oct 23:45 UTC. Everything under `honest_memory/` (the memory graph on FalkorDB, its tools, the write gate, the
deterministic router) existed before the window and is declared in [PRE-EXISTING.md](../PRE-EXISTING.md). This page
covers what was written in the window: `zetaris_spike/` (22 Oct), `open_agent/` and `vendor/hmem` (24 Oct), and the
small changes to `honest_memory/` listed below. Meterless specifics (Nightshift, receipts, H-MEM) are in
[METERLESS.md](METERLESS.md).

## What it does

Three source systems of a synthetic company (Quillfield Billing) sit behind one Zetaris SQL endpoint: team chat in
Postgres, support tickets in a CSV file, documents behind a REST API. Two agents answer a question about them and
check each other. Both think on NVIDIA Nemotron models served on this Mac by Meterless Nightshift, the primary (and,
unless a developer asks otherwise, the only) model provider; what they learn is kept in the Meterless H-MEM engine.

```
 Postgres chat ─┐
 tickets CSV ───┼─ Zetaris (Lightning SQL) ─┬─ 3 SELECTs ─> ingest ─> memory graph (FalkorDB, honest_memory)
 docs REST API ─┘                           │                                   │
                                            │   H-MEM recall ──> Investigator (Nightshift: Nemotron 3.5 Lightning,
                                            │   (verified           app wlwt-investigator): tools over the graph,
                                            │    answers of          answer + citations (record id, quoted line, claim)
                                            │    earlier runs)                  │  draft
                                            │                                   v
                                            └── re-read by id ───────── Verifier (Nightshift: Nemotron 3 Nano,
                                                (cited records and       app wlwt-verifier): quote in the record?
                                                 the uncited ones the    (hard gate), then supports / contradicts /
                                                 answer rests on)        unrelated
                                                                                │
                              rejected: notes + each record as its source holds it now go back to the Investigator,
                              which revises (at most 2 revisions); a record that changed at its source or contradicts
                              the answer gets its own "Conflict: ... Flagged for a human." line
                                                                                │ every citation accepted
                                                                                v
                      honest_memory write gate (a Finding citing the verified records) ──> H-MEM, layer long_term
                      the Verifier's verdict ──> H-MEM feedback on the memories that were recalled
                      every model call ──> a receipt signed by the Nightshift daemon ──> logs/receipts-<run_id>.jsonl
```

- **Investigator** (`agent:investigator`) is the pre-existing honest_memory agent: a model picks tools over the graph
  and writes one sentence that the sentence check accepts or replaces. New in the window: before it answers, H-MEM
  puts verified answers of earlier runs that match the question in front of its model; it backs the answer with
  citations (a record id, one line quoted from that record, and the claim that line backs); it revises from the
  Verifier's notes.
- **Verifier** (`agent:verifier`) re-reads every cited record by id through Zetaris, from the table of its own
  source system, and checks deterministically that the quoted line is in it (this check cannot be overruled). Then
  its own model, with its own prompt, judges whether the record as it reads now supports the claim. It also re-reads
  the records the memory's answer rests on that the draft does not cite: one that changed at its source must be
  named. An answer that cites nothing because a name is not in the memory is checked against all three systems
  directly (one `UNION ALL` query).
- **A changed or contradicting source is reported, not blended in.** The Investigator's revision keeps what the cited
  records support (a clause that repeats a flagged record's wording is taken out) and adds one deterministic line per
  flagged record, e.g. `Luca Ostrander owns billing-api, with ownership transferred effective 2026-09-22 [ADR-0009].
  Conflict: msg-0148 (chat message) was edited after the memory read it and now says "...Sana Petrov remains the owner
  of billing-api until further notice."; the earlier quote "Per ADR-0009, I am now the owner of billing-api." is no
  longer in the record. Flagged for a human.` The result lists the same in `conflicts`. The Verifier judges the claim
  without the conflict lines, and the memory keeps the claim only.
- **Only a fully verified answer is written.** The write gate gets a Finding only when every citation was accepted
  and no flagged record is left unmentioned; H-MEM then keeps it (long_term). Otherwise the result says why nothing
  was written.
- **No silent fallback to another model.** If Nightshift is down, a lane is not admitted, or a reply comes from a
  model or route other than the agent's (`routed_model`, the lane allowlist, cost 0), the run stops with exit code 4
  and says which agent, app, lane and model, and how to start it. Ollama is used only when a developer sets
  `OA_LLM_PROVIDER=ollama`.

## Which agent uses which Nemotron model

| Agent | Nightshift app | Lane | Model (Nightshift registry id) | How it answers |
|---|---|---|---|---|
| Investigator | `wlwt-investigator` | `nemotron-lightning` | `local-runtime/nemotron-3.5-lightning-30b-a3b` (GGUF Q4_0) | native OpenAI tool calls over 21 graph tools; JSON for cite / revise |
| Verifier | `wlwt-verifier` | `nemotron-nano` | `local-runtime/nemotron-3-nano-30b-a3b` (GGUF Q4_K_M) | JSON verdict in text |

Every `llm_call` line in `logs/trace-<run_id>.jsonl` carries `provider`, `app`, `lane`, `requested_model`,
`routed_model`, `lanes` (the allowlist the receipt reports), `source_policy`, `receipt_id`, `cost_usd`, `from_cache`,
`latency_ms` and tokens. The run's signed receipts are exported to `logs/receipts-<run_id>.jsonl`, and the output
JSON names that file in `receipts.file`.

## Files (new or changed in the window)

| File | What |
|---|---|
| `zetaris_spike/` | 22 Oct stop-gate: registers the three sources in a local Zetaris, checks counts, a federated question, a cross-source JOIN and quote re-reads by id ([its README](../zetaris_spike/README.md)). `zclient.py` takes a per-call timeout (24 Oct). |
| `open_agent/sources.py` | The SQL: one SELECT per source for the ingest, a point read by id, the absence query. Two endpoints with the same statements: `ZetarisSource` (timeout, one retry, then `SourceUnavailable`) and `LocalSample` (SAMPLE_MODE, in-process SQLite). Tamper/restore of a chat record. |
| `open_agent/agents.py` | `Investigator` and `Verifier`, their prompts, the quote and contradiction rules, the uncited-evidence re-read, the conflict line, the H-MEM recall before the Investigator answers, the deterministic paths used without a model. |
| `open_agent/llm.py` | Provider by environment (`OA_LLM_PROVIDER`: nightshift default, ollama, none); `NightshiftClient` (each agent's route, receipt fields per call, token budget, loud failures); the OpenAI-compatible `TracedClient` for the ollama override. |
| `open_agent/nightshift.py` | Lane B's Nightshift client (routes, receipt extraction, the routing check, retries, receipt export with a secrets/PII scan) plus, in this lane, one lane at a time over the daemon's control socket (`NIGHTSHIFT_SWAP=1`). |
| `open_agent/hmem_bridge.py`, `open_agent/hmem_sidecar.mjs`, `vendor/hmem/` | The Meterless H-MEM reference engine, pinned, behind a Node sidecar (lane B). |
| `open_agent/run.py` | Command line and the loop: provider check, graph from the sources if needed, optional tamper, Investigator, Verifier, revisions, write gate, H-MEM feedback, receipt export, JSON result. |
| `open_agent/trace.py`, `open_agent/stats.py` | `logs/trace-<run_id>.jsonl`, one event per step; the numbers on this page recomputed from traces. |
| `honest_memory/agent.py`, `tools.py`, `ingest.py` | Small changes: `HM_LLM_PROVIDER=nightshift`, H-MEM context before the model's first call (`MEMORY_PREFIX`), `remember`/`recall` on H-MEM behind `HM_MEMORY_BACKEND`, `ingest_episodes()` for rows from any reader. |
| `scripts/nightshift_headless.sh`, `scripts/nightshift_lanes.toml` | Nightshift without its window, on 127.0.0.1 only; per-app lane allowlist and `raceWidth 1`. |
| `scripts/live_series.py`, `scripts/cross_session_proof.py` | The live runs below; the two-process memory proof. |
| `input_examples/` | Five questions: a normal one, a tampered source, a name in none of the three systems, two decision records that disagree, a paraphrase asked in a new process after 01. |
| `output_examples/`, `logs/` | Outputs of real runs, live (`*.live.json`) and SAMPLE_MODE (`*.sample.json`), with their traces, receipt logs and the H-MEM ledger of the cross-session run. |
| `docs/open-agent-runs-2026-10-24-nightshift/` | Runs 2 to 5 of each example on the integrated stack (outputs, traces, receipts, `series.log`). |
| `docs/open-agent-runs-2026-10-24/` | History: the earlier runs on Ollama (before the integration), with their own README. |
| `tests/test_open_agent.py`, `tests/test_loop_integration.py` | The loop with a fake model and with a fake Nightshift on loopback (see Tests). |

## Run it

All modes need FalkorDB (the memory graph): `docker run -d --rm -p 127.0.0.1:6379:6379 falkordb/falkordb:6.0.2`, then
`pip install -e '.[agent]'`. H-MEM needs Node >= 20 on the PATH (or `HM_NODE`); without it the memory falls back to
the graph and the result says so.

**SAMPLE_MODE** (no Zetaris, no model, no credentials, no GPU):

```
python -m open_agent.run --sample --input input_examples/02_tampered_source.json
python scripts/cross_session_proof.py --sample          # H-MEM across two processes, no model
```

What runs: the same SQL text against the three tables loaded from `sample_data/sources/` into SQLite (endpoint
`local-sample`; the one rewrite is `LATERAL VIEW explode(docs)`, which SQLite does not have). The graph is built
from those rows by the real ingest and write gate on first use (`--ingest` rebuilds it). No model: the Investigator
uses the deterministic router and quotes, from each source record, the line that names the most of what the answer
names; the Verifier's re-reads and quote check run as live, its model judgement is reported as
`skipped: no model in SAMPLE_MODE`. H-MEM recall, store and feedback run as live (they need no model; a recall that
no model reads is labelled so). Nothing in sample mode replays a recorded output. Example 04 is worded for a model;
the router has no rule for it and says so (`no_claim`).

**Live** (local Zetaris with the three sources registered, see `zetaris_spike/README.md`; Nightshift headless):

```
scripts/nightshift_headless.sh start                       # daemon, engine, app policies, both Nemotron lanes
export ZETARIS_BUNDLE_ENV=~/zetaris-openagent/zetaris-platform/.env        # admin sign-in, never printed
export NIGHTSHIFT_SWAP=1                                   # optional: one lane loaded at a time (shared Mac)
python -m open_agent.run --ingest --input input_examples/01_current_owner.json
python -m open_agent.run --input input_examples/02_tampered_source.json    # edits msg-0148 in Postgres, restores it
python scripts/cross_session_proof.py                       # process A answers 01, process B the paraphrase
python -m open_agent.stats logs/trace-*.jsonl docs/open-agent-runs-2026-10-24-nightshift/trace-*.jsonl
scripts/nightshift_headless.sh stop
```

Other settings: `OA_LLM_MAX_TOKENS` (per Nightshift call, thinking included, default 6144), `OA_LLM_TIMEOUT`
(default 180 s), `OA_ZETARIS_TIMEOUT` (30 s), `NIGHTSHIFT_<AGENT>_LANE` (e.g. the Verifier on Lightning when Nano
cannot be admitted), `OA_TENANT`, `OA_LOG_DIR`, `HM_HMEM_DIR`, `FALKORDB_HOST`/`FALKORDB_PORT`. Development only:
`OA_LLM_PROVIDER=ollama` with `OA_LLM_BASE_URL`, `OA_LLM_MODEL`, `OA_VERIFIER_MODEL`; `OA_LLM_PROVIDER=none` for the
deterministic paths against live Zetaris. `OA_LLM_MODEL` with the default provider is refused, so a leftover setting
cannot pick the provider.

## Rules the Verifier applies

| Reason | When |
|---|---|
| `quote_not_in_record` | the quoted line is not in the record as its source system holds it now (whitespace and a trailing full stop ignored) |
| `quote_not_a_sentence` | the quote has a line break or is over 300 characters: a citation points at a line, not a whole record |
| `quote_too_short` | under 4 words |
| `record_missing` | the source system has no record with that id |
| `model_contradicts`, `model_unrelated` | the Verifier's model says the record as it reads now does not support the claim |
| `contradiction_dropped` | a record that contradicted the answer in an earlier round is neither cited nor named now (draft-level) |
| `evidence_changed` | a record the memory's answer rests on, not cited, changed at its source since the memory read it, and the answer does not name it (draft-level) |
| `unreadable` | Zetaris did not answer after one retry; the citation is not accepted and no revision is asked for |

Accepted means the quote is found, has the right shape and the model said `supports` (with the ollama override or
no model: or the judgement was skipped, which the verdict says). At most 2 revisions, so at most 3 verification
rounds. Statuses: `verified`, `absence_confirmed`, `no_claim`, `partially_verified`, `unverified`, `contradicted`.
The result lists citations dropped along the way with the record's current text (`dropped_citations`, `notices`) and
the flagged records (`conflicts`).

Failures: Nightshift down, lane not admitted, or a reply from another model: the run stops (exit code 4,
`error.type` `ProviderDown` or `RoutingMismatch`); a tampered record is restored either way. An empty model reply
(Nightshift `..._empty_response`) is not "down": the trace marks it and the deterministic path takes that step.
Zetaris down on the ingest: the run stops with a structured error that points to `--sample`.

## Measured (24 Oct 2026, the integrated system)

Mac Studio M5 Max (128 GB, shared with other work), Zetaris 2.4.3.1 in Docker under emulation, FalkorDB 6.0.2,
Nightshift 1.2.1 headless with `raceWidth 1` and one lane loaded at a time (`NIGHTSHIFT_SWAP=1`), H-MEM reference
engine at `1742dec5`. Investigator on Nemotron 3.5 Lightning, Verifier on Nemotron 3 Nano, both through Nightshift.
Code at commit `9b6f7a7`. Fresh tenant `openagent_c2`, its graph built through Zetaris on the first run. 20 runs,
5 of each example, one after another, 13:38:13Z to 14:03:13Z (`scripts/live_series.py`); run 1 of each is in
`output_examples/*.live.json` and `logs/`, runs 2 to 5 in `docs/open-agent-runs-2026-10-24-nightshift/`. All numbers
below come from those 20 traces:

```
python -m open_agent.stats logs/trace-20261024T133813Z-5253.jsonl logs/trace-20261024T134340Z-4bd2.jsonl \
  logs/trace-20261024T134427Z-8408.jsonl logs/trace-20261024T134456Z-4547.jsonl docs/open-agent-runs-2026-10-24-nightshift/trace-*.jsonl
```

| Example | Runs | Result | Rounds | Wall time p50 / max | Signed receipts per run |
|---|---|---|---|---|---|
| 01 Who owns billing-api now? | 5 | verified 5, written 5 | 1 in 5 | 45.9 s / 46.7 s | 5 |
| 02 same question, msg-0148 edited at the source | 5 | verified 5, written 5, edit restored 5 | 2 in 5 | 164.5 s / 326.2 s | 6 to 8 |
| 03 Who owns payroll-export? | 5 | absence_confirmed 5 | 1 in 5 | 18.4 s / 28.8 s | 2 |
| 04 What did the team decide about invoice rendering? | 5 | verified 5, written 5 | 1 in 5 | 54.7 s / 64.5 s | 5 |
| 05 cross-session: A asks 01, then B (a new process) the paraphrase | 1 + 1 | A verified, B verified; B recalled A's memory | 1 / 1 | A 85.8 s, B 63.8 s | 5 + 5 |

What happened, from the traces:
- 02: in every run round 1 rejected msg-0148 (`quote_not_in_record`; the Verifier's model also said `contradicts`,
  e.g. "The record states Sana Petrov remains the owner of billing-api"), the revision kept the ADR-0009 answer, the
  conflict line flagged msg-0148 for a human, and round 2 verified. In 1 of the 5 runs (run 1) Lightning's revise
  call came back empty (`..._empty_response`, the thinking used the 6,144-token budget) and the deterministic
  revision took that step; the answer is the same shape. In none of the 5 runs did the model blend the edited chat
  into the answer the way Nemotron 3 Nano on Ollama did earlier.
- 04: both decision records cited and accepted in every run; all five answers name ADR-0004 (synchronous,
  2026-08-25) and ADR-0011 (asynchronous, 2026-09-08) with their dates; none says in words that ADR-0011 replaced
  ADR-0004.
- 03: the absence query over the three systems returned 0 rows every time; the answer is the template sentence.
- The Verifier also re-read 50 uncited records the answers rested on; none had changed (in all 5 tamper runs the
  model cited msg-0148 itself, so the evidence path did not have to catch it here; the tests cover it).
- 7 model replies carried U+2011 hyphens and were made ASCII (`reply_normalised`).

| Step (live) | Calls | p50 | max |
|---|---|---|---|
| Zetaris re-read by id, Postgres chat | 10 | 0.20 s | 0.38 s |
| Zetaris re-read by id, docs REST API | 26 | 0.38 s | 1.20 s |
| Zetaris absence query over the three sources | 5 | 0.30 s | 1.31 s |
| Zetaris ingest SELECT chat / tickets / docs (225 / 127 / 51 rows) | 1 each | 0.30 / 0.35 / 1.27 s | |
| Investigator tool-loop call, Lightning (prompt about 5.8k tokens) | 40 | 4.27 s | 10.59 s |
| Investigator cite call, Lightning (about 1.1k) | 15 | 11.94 s | 31.96 s |
| Investigator revise call, Lightning (about 2.0k) | 4 (+1 empty) | 44.12 s | 58.60 s |
| Verifier judge call, Nano (about 0.7k) | 36 | 2.52 s | 9.83 s |
| Switching the loaded lane | 40 | 5.48 s | 7.31 s |

Model-call times are the answered request (the receipt's `latency_ms`). Where a run's time goes, p50 per run: 02 has
76.6 s in model calls, 23.2 s switching lanes plus 35.1 s waiting for a just-switched lane to answer, 1.7 s in
Zetaris; 01 has 22.5 s in model calls and about 20 s for its 2 lane switches. With both lanes resident (a Mac with
room for them) that switching time goes away.

Nightshift receipts: 95 model calls, 95 signed receipts, `cost_usd` 0 on every one, 0 from cache; every call routed
to its agent's model (Investigator to Lightning 59, Verifier to Nano 36), 0 routing mismatches in the series. 20
receipt files, each holding exactly the run's receipts (0 missing, 0 redacted).

H-MEM: 15 verified answers stored (layer long_term); 12 of the 20 recalls put an earlier answer in front of the
Investigator's model, 8 found nothing; 9 verdicts went back as `helpful` (+0.05 confidence each). Three recalls for
03 matched a billing-api memory by word overlap; an absence answer gives no feedback.

Cross-session proof (`scripts/cross_session_proof.py`, tenant `oa_xs_live_20261024T140313Z`): process A (pid 95910,
run `20261024T140314Z-76b1`) answered 01, verified, finding FND-00001, stored as H-MEM `mem-1`. Process B (pid
97394, run `20261024T140440Z-409c`) asked "Which person is in charge of the billing-api system at the moment?":
H-MEM put `mem-1` (relevance 0.22) in front of the Investigator's model before its first call, the answer was
verified through Zetaris, and the verdict raised `mem-1` from 0.70 to 0.75. Ledger
`logs/hmem-ledger-oa_xs_live_20261024T140313Z.jsonl`: `mem-1` create 14:04:40.121Z, read 14:04:40.611Z, feedback
14:05:44.299Z. Output: `output_examples/05_cross_session_recall.live.json` (`cross_session_proof` block).

SAMPLE_MODE (tenant `openagent_sample_c`, no model): 01 verified 1 round 1.35 s with the ingest; 02 verified after 2
rounds 0.12 s with the conflict line; 03 absence_confirmed 0.10 s; 04 no_claim 0.09 s. The sample cross-session run
(`oa_xs_sample_20261024T133946Z`) recalled A's memory in B (ledger create and read); B is `no_claim` on the router,
so no feedback, and the output says so.

Tests: 519 passed, 1 skipped (`HM_TEST_PORTS=<falkordb port> python -m pytest -q`; 503 before this lane). Mutation
checks on the new guards: Nightshift-down turned into an OpenAIError fails 1 test, the conflict lines removed fail 6,
no H-MEM feedback fails 2.

Commits after `9b6f7a7` change only an error path (an empty reply is not asked again) and a trace field
(`removed_from_answer` is set only when a clause was taken out; in the 20 runs above it also shows the answer before
the `[ADR-0009]` marker was added in 3 runs, where nothing was removed).

### The same examples on the earlier provider (history)

Before the integration the loop ran on NVIDIA Nemotron 3 Nano on a local Ollama for both agents, reasoning off,
code `266e330` (lane A; 24 live runs, `docs/open-agent-runs-2026-10-24/`): 01 verified 6/6 (p50 12.0 s), 02 verified
4/6 and unverified 2/6 (12.2 s), 03 absence_confirmed 6/6 (1.7 s), 04 partially_verified 6/6 (15.4 s). The
integrated runs verify 02 and 04 in every run but are 4 to 13 times slower. The Investigator's model, the thinking
(on, not switchable through Nightshift), the conflict line and the evidence rules all changed together, so the
difference is not attributable to any one of them.

<<BENCH>>

## Not done here, and limits

- The memory graph is not refreshed after a source edit: the Verifier's catch is reported (and flagged for a human),
  the graph keeps the old copy.
- Tamper works on chat records (Postgres) only.
- Nightshift 1.2.1 cannot switch the Nemotron lanes' thinking off from outside (the engine forwards
  `chat_template_kwargs` only from its own jobs plane), so each call carries a token budget and the Investigator's
  cite and revise calls take tens of seconds. On this shared Mac the two lanes did not fit next to other work, so the
  runs here load one lane at a time (`NIGHTSHIFT_SWAP=1`, about 7 s per switch).
- H-MEM recall is lexical (the engine's deterministic hash embedder), not semantic; a paraphrase is recalled when it
  shares enough words with the stored answer.
- The absence query uses `instr`, case-sensitive in both engines; a name written in another case is not found.
- A record that backed an earlier answer is re-judged from the text read in that round, not read again; uncited
  evidence is re-read once, in round 1, at most 12 records.
- Answer quality is bounded by the model. The loop's guarantee is about what gets written, not that every question
  is answered.
