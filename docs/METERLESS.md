# Meterless in What Leaves With Them

Two separate things, both live in this repo:

1. **Nightshift is the primary model provider.** Every model call of the agents goes to Meterless Nightshift running on the
   Mac, which serves NVIDIA Nemotron from local GGUF files. Cost on every receipt so far: 0 USD. Each call returns a
   receipt signed by the Nightshift daemon.
2. **The H-MEM engine is the agents' memory.** The Meterless H-MEM reference engine (pinned in `vendor/hmem`) holds what
   the agents learned. It stores answers that passed the write gate, puts matching memories in front of the model
   before it answers, and turns the Verifier's verdict into feedback.

Everything below was measured on 2026-10-24 on a Mac Studio (Apple M5 Max, 128 GB, macOS 27.0.1) with Nightshift 1.2.1.

## Which agent uses which Nemotron model

| Agent | Nightshift app id | Lane | Model id (Nightshift registry) | GGUF file | Tool calls |
|---|---|---|---|---|---|
| Investigator (also `honest_memory.agent` with `HM_LLM_PROVIDER=nightshift`) | `wlwt-investigator` | `nemotron-lightning` | `local-runtime/nemotron-3.5-lightning-30b-a3b` | `NVIDIA-Nemotron-3.5-Lightning-30B-A3B-Q4_0.gguf` (ggml-org, 18.90 GB) | native OpenAI `tools` |
| Verifier | `wlwt-verifier` | `nemotron-nano` | `local-runtime/nemotron-3-nano-30b-a3b` | `nvidia_Nemotron-3-Nano-30B-A3B-Q4_K_M.gguf` (24.27 GB) | none: JSON verdict in the text (the registry marks this model `tools:false`) |

`open_agent/nightshift.py` (`route_for`) holds this table. `NIGHTSHIFT_VERIFIER_LANE=nemotron-lightning` moves the
Verifier to Lightning when the Nano lane cannot be loaded. The headless script then sets the allowlist to match.

### Why each agent has its own app id

In Nightshift 1.2.1 an explicit model id does not pin routing. The engine records it as `requested_model`, then
routes by mode: `openai.mjs` sets `forceModel: bind.bound ? null : ...`, and `proxy.js` `bindAppMode` binds every app
to a mode on first contact. Our first receipt shows it: `requested_mode: "meterless/auto"`,
`phases[0].source_policy: "seated-fleet"`.

What does pin a model is the per-app **lane allowlist**, written over the daemon's control socket
(`POST /admin/app-policy`). `wlwt-investigator` may use only `nemotron-lightning`, and `wlwt-verifier` only
`nemotron-nano`.

Negative control, 11:27Z: `wlwt-investigator` sent a Nano model id while its Lightning lane was down. Nightshift refused
it with `400 no_provider_configured` and did not substitute Nano.

Every response is checked by the client (`check_routing`):

- `routed_model` must equal the agent's model id.
- The receipt must show `source_policy: "local-only"` with `app_policy.lanes == [the agent's lane]`, or
  `pinned-model:<id>` (if a later build pins by id).
- The cost must be 0.

If any check fails, the client raises `RoutingMismatch`. That error is not an `OpenAIError`, so the agent does not
quietly fall back to its rule router.

In the Investigator ↔ Verifier loop (`python -m open_agent.run`, client `open_agent/llm.py` `NightshiftClient`) there
is no fallback at all: Nightshift down, a lane not admitted (after the warming wait) or a routing mismatch stops the
run with exit code 4 and `error.type` `ProviderDown` / `RoutingMismatch`, naming the agent, app, lane and model. Ollama
is used only when a developer sets `OA_LLM_PROVIDER=ollama`. (The standalone `python -m honest_memory.agent ask` with
`HM_LLM_PROVIDER=nightshift` keeps lane B's behaviour: Nightshift down falls back to the router and says so in
`errors`.)

This check fired for real on 24 Oct at 13:09Z: with both lanes loaded and the daemon at "admission posture Pause
(memory pressure critical, swap 33 GB)", Nightshift answered `wlwt-verifier` from the **Lightning** lane although its
allowlist names only `nemotron-nano` (receipt `rcpt_req_bm9j53u7`: `app_policy.lanes ["nemotron-nano"]`,
`winning_route` Lightning). The run stopped with `RoutingMismatch`. Since then the runs load one lane at a time
(below), so no other Nemotron model is there to answer.

## What runs where

| Piece | Where | Notes |
|---|---|---|
| Nightshift daemon `meterlessd` 1.2.1 | from `/Applications/Nightshift.app/Contents/MacOS`, started by `scripts/nightshift_headless.sh` | no app window and no phone page (`:8089` belongs to the app shell, which we never start) |
| Nightshift engine (bundled Node v22.23.2, `Resources/engine`) | spawned by the daemon | OpenAI-compatible API on `127.0.0.1:8088/v1` |
| Lane fabric | `127.0.0.1:11445` | not `11435`, so it does not collide with other tools on the Mac |
| Lanes (bundled llama.cpp `llama-server`, ref `08659901c43b`) | `127.0.0.1:11446` (Lightning), `127.0.0.1:11447` (Nano); 16384 tokens, 1 slot each (`scripts/nightshift_lanes.toml`) | the daemon refused 32768 x 2 for Lightning: it wanted 47,462 MB and saw 28,366 MB free |
| Nightshift state | `~/nightshift-openagent/headless/state` (own proxy token, signing key, `lanes.toml`, `receipts.jsonl`) | the app's own data folder is not touched |
| Models | `~/nightshift-openagent/models` | Nano = APFS clone (`cp -c`) of the Ollama blob `sha256-a70437c4...d1ce` (0 extra bytes on disk). Lightning downloaded from Hugging Face; sha256 `61f87e75974e4b53...7a00e3c` matches the repo's LFS id |
| H-MEM sidecar | `node open_agent/hmem_sidecar.mjs --persist-dir .state/hmem/<tenant>` | JSON lines over a pipe, no port; one process per folder (`sidecar.lock`) |
| H-MEM state | `.state/hmem/<tenant>/state.json`, `ledger.jsonl` (gitignored) | `HM_HMEM_DIR` moves it |

Only `127.0.0.1` is ever bound. After every start and every lane start, the script checks the listeners of the
daemon and all its children. If any listener is not on loopback, the script stops everything. The API token is read
from the state folder on every request and is never printed or logged.

## Commands

```bash
scripts/nightshift_headless.sh start                       # daemon + engine + app allowlists + both lanes
scripts/nightshift_headless.sh start nemotron-lightning    # only one lane (a Mac short on memory)
scripts/nightshift_headless.sh lane nemotron-nano start    # waits until the lane is ready (or fails)
scripts/nightshift_headless.sh status                      # pid, listeners, lanes, models (no token)
scripts/nightshift_headless.sh stop                        # daemon, engine and lanes; checks nothing is left

python -m open_agent.nightshift smoke --agents investigator,verifier --out logs/nightshift-smoke.json
HM_LLM_PROVIDER=nightshift NIGHTSHIFT_TRACE=logs/trace.jsonl \
  python -m honest_memory.agent ask "Who currently owns billing-api?" --tenant <tenant>
python -m open_agent.nightshift export --trace logs/trace.jsonl --out logs/receipts-<run>.jsonl
FALKORDB_PORT=6395 python scripts/meterless_live_proof.py --tenant <tenant> --swap   # the whole chain below
```

## Findings from running the loop on Nightshift 1.2.1 (24 Oct)

| What we saw | Evidence | What the code does now |
|---|---|---|
| A tool-free prompt is planned by keywords in the non-system text (engine `classify.js`: "test", "fix", "plan", "json"...). The Investigator's cite call, with the records in the user turn, became `planning` + `code_patch` + a 3-way `moa_synthesis` on the same lane: 154 s, 8,929 output tokens, and no JSON. | receipt `rcpt_req_kin0ai9g` phases | JSON prompts carry their data in the system message and the user turn is a short question (`NightshiftClient.ASK`): one `final_synthesis` phase, 14.4 s, 1,116 tokens (`rcpt_req_v3nn18jr`). |
| A local app races 2 candidates by default (`orchestrator.js:2058`), on a 1-slot lane. | `app_policy.race_width` on receipts | `scripts/nightshift_headless.sh` sets `raceWidth: 1` per app. |
| The lanes think before they answer and nothing in the public API turns that off (`chat_template_kwargs` is forwarded only from the engine's own jobs plane, `local-runtime.js:1272`; `reasoning_effort` goes to OpenAI Responses rows only). The local default `max_tokens` is 2048; the cite call used it all inside the thinking and Nightshift returned `502 ..._empty_response`. | lane log 12:58Z: 2 x 2,048 decoded tokens | Each call sends `max_tokens` (`OA_LLM_MAX_TOKENS`, default 6144). An empty reply is marked in the trace and the deterministic path takes that step; it is not treated as "down". |
| Lightning writes ids and dates with U+2011 non-breaking hyphens ("ADR\u20110004"), which never match the records. | output of run 20261024T133400Z-fc26 (dev, not kept) | The client makes U+2010/U+2011/U+00A0/U+202F ASCII in text and tool arguments, and writes a `reply_normalised` event. |
| Both lanes next to other work pushed the Mac into swap; latency rose and the routing check above fired. | daemon log 13:13Z | `NIGHTSHIFT_SWAP=1`: before a call, `ensure_lane` stops the other lane and starts the agent's own over the control socket (trace event `lane_swap`, about 5 to 8 s). |

## Receipts

Each response carries a `meterless` block with these fields:

- `receipt_id`, `requested_model`, `routed_model`;
- the phase and its `source_policy`;
- `app_policy` (the lanes);
- cost, cache, privacy path, usage;
- `receipt_envelope`: `{body, sig_b64, pubkey_b64}`, signed Ed25519 by the daemon, whose key never leaves it.

`open_agent.nightshift` keeps one `Receipt` per call (`client.receipts`, and `NIGHTSHIFT_TRACE` if set). After a run,
`export_receipts(ids, out)` reads the daemon's `GET /v1/meterless/receipts` and keeps the run's receipts by
`receipt_id`. The signed body has no `meterless-scope`, so the run is named by its ids.

Before writing, the export scans every receipt:

- for secret patterns and the literal token;
- for personal data, with the write gate's `detect_pii` (email, phone, address, card).

A clean receipt is written exactly as the daemon returned it. If a receipt's `prompt_preview` trips the scan, that
field is replaced and `redacted: [...]` is added; the signature then covers the original text, not the written one. A
receipt that trips the scan anywhere else is left out.

Receipt logs in this repo:

- **One per loop run:** `logs/receipts-<run_id>.jsonl` next to `logs/trace-<run_id>.jsonl`, written by
  `open_agent.run` after every live run (`export_run_receipts`, by the receipt ids of the run's `llm_call` events), and
  named in the run's output JSON as `receipts.file` (with `calls`, `signed_receipts`, `redacted`, `missing`). Runs 2 to
  5 of each example keep theirs in `docs/open-agent-runs-2026-10-24-nightshift/`.
- `logs/receipts-wlwt-proof-20261024T115709Z-c6aed4.jsonl`: 3 signed receipts, scan clean (lane B's first proof).
- Per-call blocks: `logs/nightshift-smoke-investigator.json`, `logs/nightshift-smoke-verifier.json`,
  `logs/nightshift-agent-trace.jsonl`, `logs/meterless-live-proof-wlwt-proof-20261024T115709Z-c6aed4.json`.

## H-MEM in the product

- **Engine.** `vendor/hmem` is the Meterless reference implementation at commit `1742dec5`, unchanged. Its pin, review
  and file hashes are in `vendor/hmem/README-PIN.md`. The sidecar runs `vendor/hmem/dist/hmem.mjs`, the same source
  bundled to plain JS. Retrieval uses the engine's formula and its own embedder, a deterministic hash: recall is
  lexical overlap, not semantic search.
- **Write.** `remember` still goes through the write gate, which decides what is kept and writes its Finding and
  receipt. A finding the gate accepts is then stored in H-MEM with `layer: long_term`. Its tags are `finding`,
  `about:<entity>` and `src:<id>`. Its provenance holds the gate's finding id and receipt.
- **Recall.** The `recall` tool reads findings from H-MEM, ranked by the engine and carrying `confidence`,
  `relevance`, `needs_review` and `memory_id`. Each recall writes a `read` entry to the ledger. A memory is served
  only while the gate's record of it is still in the graph, so a rebuilt graph does not bring old memories back.
  Findings written by other paths (fixtures, red team) are imported into H-MEM the first time they are recalled.
  Handoffs still come from the graph.
- **Before answering.** `Agent._ask_model` asks H-MEM for findings that match the question
  (`hmem_bridge.memory_context`). It puts the `[MEMORY CONTEXT]` block in front of the model as a system message and
  records which memories it used in `run.memory_ids`.
- **Verdict to feedback.** `hmem_bridge.verdict_feedback(store, run.memory_ids, verdict)` maps the Verifier's verdict:
  - verified / supported → `helpful` (+0.05)
  - unsupported / insufficient → `not_helpful` (−0.03)
  - refuted / contradicted → `wrong` (−0.20, tagged `review`)
- **Switch.** `HM_MEMORY_BACKEND=auto` (default) uses H-MEM when Node and the engine are present. `graph` keeps the
  old path. If the engine fails, recall answers from the graph and the answer's `note` says so.
  `HM_MEMORY_CONTEXT=off` stops the injection before answering.

Wiring for the Investigator ↔ Verifier loop:

```python
from open_agent import nightshift as N
from open_agent.hmem_bridge import verdict_feedback

# Investigator: the honest_memory agent, with AgentConfig(provider="nightshift", model=N.LIGHTNING) or
# HM_LLM_PROVIDER=nightshift. Agent.ask already puts matching memories in front of the model (run.memory_ids).
reply = N.chat(messages, agent="verifier", scope=run_scope, max_tokens=600)   # Verifier on Nano, JSON in text
verdict_feedback(store, run.memory_ids, verdict)                              # verdict -> H-MEM feedback
T.call(store, "remember", {"text": answer, "about": [...], "source_ids": cited}, ctx)  # approved: gate, then H-MEM
```

## Measured in the integrated loop (24 Oct, 13:38Z to 14:06Z)

20 live runs of the four examples (5 each) plus the two-process memory proof, code `9b6f7a7`, Nightshift 1.2.1 with
`raceWidth 1` and one lane loaded at a time. Full table and method in
[OPEN-AGENT-NEW-WORK.md](OPEN-AGENT-NEW-WORK.md#measured-24-oct-2026-the-integrated-system); from the 20 traces:

| What | Number |
|---|---|
| Model calls / signed receipts | 95 / 95, `cost_usd` 0 on every one, 0 from cache |
| Routed to the agent's own model | Investigator → `local-runtime/nemotron-3.5-lightning-30b-a3b` 59 of 59, Verifier → `local-runtime/nemotron-3-nano-30b-a3b` 36 of 36; 0 routing mismatches |
| Receipt files | 20 (`logs/receipts-<run_id>.jsonl` for run 1 of each example, the rest next to their traces), each with exactly the run's receipts |
| Investigator tool-loop call (Lightning, native tools, ~5.8k prompt tokens) | p50 4.27 s, max 10.59 s (n=40) |
| Investigator cite call (Lightning, JSON) | p50 11.94 s, max 31.96 s (n=15) |
| Investigator revise call (Lightning, JSON) | p50 44.12 s, max 58.60 s (n=4); 1 more came back empty after 2 x 6,144 tokens of thinking |
| Verifier judge call (Nano, JSON in text, ~0.7k) | p50 2.52 s, max 9.83 s (n=36) |
| Switching the loaded lane (`NIGHTSHIFT_SWAP=1`) | p50 5.48 s, max 7.31 s (n=40), plus about 5 s until the engine sees the new lane |
| H-MEM | 15 verified answers stored long_term, 12 recalls with a memory, 9 `helpful` feedbacks; cross-session: B recalled A's `mem-1` and raised it 0.70 → 0.75 |

Where to find them for the README: `output_examples/02_tampered_source.live.json` → `receipts.file` =
`logs/receipts-20261024T133813Z-5253.jsonl` (the tamper run: 6 signed receipts, 3 on Lightning for
the two tool-loop calls and the cite call, 3 on Nano for the three judge calls; the revise call came back empty and
has none); `logs/receipts-20261024T140440Z-409c.jsonl` (the cross-session run B).

## Measured by lane B before the integration (2026-10-24)

| What | Number | Evidence |
|---|---|---|
| Nano lane cold start to ready (weights already in page cache) | about 11 s | journal 11:25:41Z |
| Lightning lane start to ready | 9 s | journal 11:37:44Z |
| Nano, one-word answer (curl) | 1.35 s, then 0.52 s | `rcpt_req_0tzcokt3`, journal |
| Lightning, one native tool call (curl, load 6) | 2.24 s | journal 11:38Z |
| Lightning, same call through `open_agent.nightshift` | 11.5 s (11:48Z, load 12); 4.98 s (12:05Z) | journal; `logs/nightshift-smoke-investigator.json` (12:05Z run) |
| Nano verifier verdict through `open_agent.nightshift` | 4.07 s (11:50Z); 2.61 s (12:06Z) | journal; `logs/nightshift-smoke-verifier.json` (12:06Z run) |
| `honest_memory.agent ask` through Nightshift (tool call + checked sentence) | 26.6 s (16.3 s + 10.3 s; 5,558 and 6,113 prompt tokens) | `logs/nightshift-agent-ask-1.json` |
| Cross-session proof, session B (memory injected, tool call, checked sentence) | 29.5 s (23.5 s + 5.9 s) | `logs/meterless-live-proof-...c6aed4.json` |
| Cross-session proof, Verifier on Nano | 9.1 s, verdict `verified` | same file |
| Cost on every receipt | 0 USD | all receipt files |
| H-MEM reference tests / conformance (source) / conformance (bundle) | 10/10, 13/13, 13/13 | `logs/hmem-*.txt` |

Latency depends on the load from other work on this shared Mac. The 2.24 s, 11.5 s and 4.98 s numbers are the same request.

## Cross-session proof (what `scripts/meterless_live_proof.py` showed)

Scope `wlwt-proof-20261024T115709Z-c6aed4`:

1. Process A (pid 82287) remembered "billing-api is owned by Luca Ostrander (Platform) since 2026-09-22, per
   ADR-0009." The gate accepted it as `FND-00002`, and H-MEM stored it as `mem-2`, long_term.
2. Process B (pid 83156) asked "Which person is in charge of the billing-api system at the moment?" through Nightshift
   Lightning:
   - H-MEM put `mem-2` and `mem-1` in front of the model (`mem-1` is the same text, from a first attempt that stopped
     at the Verifier step);
   - the model called `current_owner`;
   - its sentence passed the check.
3. The Verifier on Nano returned `{"verdict": "verified"}` on ADR-0009 and msg-0148. H-MEM recorded `helpful` on both
   memories: confidence went from 0.70 to 0.75.
4. The ledger for `mem-2` shows `create`, `read`, `feedback(helpful)`.

## H-MEM in the Investigator ↔ Verifier loop

- **Recall:** before the Investigator answers, `Investigator.recall_memory` asks H-MEM for findings that match the
  question (`hmem_bridge.memory_context`, only findings the graph still holds). The trace gets a `memory_recall` event
  with the memory ids, relevance, confidence and who reads them: the model (a system message before its first call) or
  nobody (the router in SAMPLE_MODE; recorded so the feedback still lands).
- **Store:** only a fully verified answer goes to the write gate (`remember`), and only what the gate accepts goes to
  H-MEM, `layer: long_term`. The output's `write_gate.memory` and `memory.stored` give the memory id; conflict lines are
  not stored (`write_gate.conflicts_not_written`).
- **Feedback:** after the last Verifier round, the run's status becomes feedback on the recalled memories
  (`memory_feedback` event): verified → helpful, partially_verified / unverified → not_helpful, contradicted → wrong
  (tagged review); absence_confirmed and no_claim give none.
- **Across processes:** `scripts/cross_session_proof.py` runs example 01 in process A and the paraphrase
  `input_examples/05_cross_session_recall.json` in a new process B on a fresh tenant, then copies that tenant's
  `ledger.jsonl` to `logs/hmem-ledger-<tenant>.jsonl`. Its output `output_examples/05_cross_session_recall.live.json`
  carries a `cross_session_proof` block (both pids and run ids, A's memory id, B's recalled ids, B's feedback, the
  ledger lines of that memory). `--sample` does the same without Zetaris or a model.

## Tests

- `tests/test_nightshift.py` (18): run against a fake Nightshift on a loopback port. They cover:
  - the token is read on each request;
  - the headers;
  - receipt extraction;
  - every routing-mismatch case;
  - the retry rules;
  - the honest_memory agent with tool calls through Nightshift;
  - a mismatch is not hidden behind the router;
  - Nightshift down falls back to the router and says so;
  - the export filter and scan.
- `tests/test_hmem.py` (9): needs Node and FalkorDB. It covers:
  - an approved answer stored by one process is recalled from a paraphrase by another, with `create` and `read` in
    the ledger;
  - one sidecar per folder;
  - verdict to feedback;
  - remember to H-MEM and recall from H-MEM;
  - the graph backend;
  - a broken engine falls back to the graph;
  - a rebuilt graph does not serve old memories;
  - a new session's model gets the memory before its first call, and a `refuted` verdict flags it.
- `tests/test_nightshift_live.py`: runs only with `NIGHTSHIFT_LIVE=1` and Nightshift up.
- `tests/test_loop_integration.py` (15): the whole loop against a fake Nightshift on loopback that answers like the
  real one (OpenAI body plus `meterless` receipt). It covers: Nightshift is the default and each agent gets its own
  Nemotron route; contradicting provider settings are refused; every `llm_call` carries the receipt fields; the run
  exports its signed receipts and names the file; another model answering stops the run (exit 4, no retry, no other
  model); Nightshift down stops the run and no other provider is constructed; a lane going down mid-run stops the run
  and the tamper is restored; an empty reply is visible and not "down"; the conflict line; the uncited-evidence
  re-read (changed and unchanged); H-MEM recall/store/feedback across runs without a model; the model gets the memory
  before its first call; no feedback for an answer that states no result.

## Limits found

- **Explicit model ids do not pin routing** in 1.2.1 (see above). We use the per-app allowlist.
- **Memory.** Lanes are admitted against the daemon's estimate of free memory. On this shared Mac, Lightning and Nano
  together did not fit next to Ollama's own copy of Nano (25.3-25.9 GB resident) and another Ollama model (28.7-34.9 GB resident). The proof
  ran one lane at a time (`--swap`).
- **Lane start is asynchronous.** Right after the daemon reports `ready`, the engine can still answer
  `400 no_provider_configured` or `503 local_runtime_not_ready` ("still listing its models") for several seconds.
  The client waits up to 45 s for that, checking every 5 s, and does not count it as its one retry.
- **Nano has `tools:false` in the registry**, so the Verifier works with JSON in text.

## Cleanup after the event

1. `scripts/nightshift_headless.sh stop`
2. Remove `~/nightshift-openagent/` (models, state, logs). The Nano file is a clone; removing it does not touch
   Ollama's blob.
3. Remove `.state/` in the repo.
