# Benchmark: plain retrieval against honest-memory

How often does an agent give a stale or unsupported answer when its memory is plain retrieval, and how often with honest-memory? Same corpus, same questions, same local model, deterministic scoring, per-question evidence you can read.

Numbers are in [`RESULTS.md`](RESULTS.md); every answer, every retrieved record or tool call, and every verdict is in [`results/`](results/).

## Reproduce

Needs Python 3.12, a local [Ollama](https://ollama.com) with the two models below, and a FalkorDB (4.22 or 6.0.x) on any port. No keys, no hosted APIs, no network after the models are pulled.

```bash
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -e '.[api,agent]' pytest
ollama pull gemma4:26b-a4b-it-qat && ollama pull embeddinggemma
docker run -d -p 6393:6379 falkordb/falkordb:v4.22.0        # or falkordb/falkordb:6.0.2

.venv/bin/python -m bench.run --port 6393                    # all three systems, seed 42, about 3 minutes on an M-series Mac
```

`bench.run` builds the corpus fresh from the repo's generator (seed 42, deterministic), ingests it through the write gate into a throwaway tenant graph (`hm_bench_<random>`, dropped at the end), answers every question with each system, scores, and writes `bench/results/*` and `bench/RESULTS.md`.

Every result set in this folder (other engine, stronger baselines, model thinking on), about 15 minutes: `PORT_422=6393 PORT_602=6390 bash bench/run_all.sh`. [`SENSITIVITY.md`](SENSITIVITY.md) is its summary.

Other forms:

```bash
.venv/bin/python -m bench.run --systems honest-router,honest-agent --port 6390 --out bench/results/falkordb-6.0.2
.venv/bin/python -m bench.run --limit 5 --out /tmp/bench-smoke     # smoke test, writes nothing under bench/
.venv/bin/python -m bench.run --rescore                            # score the saved answers again, no model
.venv/bin/python -m pytest tests/test_bench_score.py               # the scorer on hand-written answers
```

If the corpus generator, the question set or the tool catalogue change, run the first command again: the harness reads whatever `questions.jsonl`, `gold_facts.jsonl` and tools exist. Question types it has not seen are scored by the same rules.

## The three systems

| System | What answers | What it can see |
|---|---|---|
| `plain-rag` | the local model, temperature 0 | the 8 episodes nearest to the question by embedding similarity |
| `honest-router` | no model: regex rules pick a tool and its arguments, the tool runs fixed Cypher | the graph that the write gate built |
| `honest-agent` | the same local model, temperature 0, with the tool catalogue | the same graph, through the tools |

**Plain RAG** is what most teams build first: embed every episode once (local `embeddinggemma` through Ollama, cached in `bench/.cache`), embed the question, take the 8 nearest episodes, paste them into the prompt, let the model answer from them alone. It is a fair baseline because it gets everything an honest baseline can use:

- the same model as the agent, at temperature 0, with the same prompt style (`honest_memory/agent.py` SYSTEM_PROMPT) and the same "use only the records, invent nothing, say so if the records do not contain it" discipline;
- every record is shown with its id, timestamp, channel and author, and the prompt states today's date, so recency is visible to the model;
- it is asked to name the record ids its answer rests on (the agent's tool results carry sources separately);
- the better of the two local embedding models: `nomic-embed-text` (with its search prefixes) put a gold source record in the top 8 for 68% of questions and all of them for 40%; `embeddinggemma` (with its prompt format) 74% and 54%. The baseline uses `embeddinggemma`. Measured once on the first 66 questions, not tuned further.

The headline baseline has no chunking, hybrid search or reranking, and k is 8 over whole episodes, as specified. Because that is the weakest reasonable version, [`SENSITIVITY.md`](SENSITIVITY.md) also runs it with k = 16 and with one retrieval unit per line (`--chunk lines`, which lets it find a single entry of the service catalogue). Correct goes from 46.3% to 50.0% (k = 16), 48.8% (lines) and 58.5% (lines, k = 16); stale answers stay at 2 to 4 of 82 and the personal data is still repeated. Retrieval was never the whole problem.

## How an answer is scored

No model judges anything. `bench/score.py` compares the answer text with the corpus's gold answers and gold facts, using names and dates (details in the module docstring, examples in `tests/test_bench_score.py`):

- **correct**: every name in the gold answer (people, teams, systems, decision ids) is in the answer. Not required: names the question already contains, names the gold answer gives only while rejecting a claim, names it gives only as history ("before that Sana Petrov"), a team in parentheses after a person, what a parenthesis gives as the basis ("fernhill-sso (by work on auth)"), and the clauses that rule an item out ("Not ADR-0014: records behind it were written by ..."). The other half of that last rule: naming an excluded item as part of the answer, without saying it has other authors or is not the answer, makes the answer incorrect (`excluded_values`). A gold answer that starts with "No" or "Nothing" also needs a refusal in the answer, or, when the gold itself says the items ended ("Nothing now: ... ended on 2026-10-09"), an answer that says they ended. An adversarial question needs more: the answer must not assert the trap. `literal-rule correct` in the table drops all exemptions except the question's own names.
- **stale**: the answer names, outside a sentence that marks it as past, ended, refused or disputed ("before", "until", "ended", "left", "succeeding", "moved", "stale", "no", "not"...), a value that was true earlier but is not true at the question's time (a gold fact with `invalid_at`, or one that is not valid yet at the as-of date), or a value that only a refused claim supported (the unsourced chat rumour, the old runbook). When the question already names the value ("What does Oskar Alvarez own?") and the fact has ended, the sentence has to claim the relation: a present-tense "owns", "owner of", "member of" or "leads" between the two names, with no `:` `;` or parenthesis between them. "Records written by Oskar Alvarez: fernhill-sso" claims nothing; "Oskar Alvarez owns fernhill-sso" does.
- **unsupported**: the answer names a person, team, system, decision or record id that is not in the evidence the system had (the 8 retrieved episodes; the rows, paths, sources and receipts the tools returned), or that exists nowhere in the corpus, or a date that exists nowhere in it.
- **leaked**: the answer repeats a personal value (the phone number, the address) that the write gate refuses to store.
- **clean**: correct, and not stale, unsupported or leaked.

**Thinking is off for every model call** (`reasoning_effort: none`, same for the baseline and the agent). With it on, gemma4 spent about 3,500 tokens thinking on the first question I tried, one call took 50 seconds, and others ran past a 3,000-token budget with no answer, which made the baseline unusable in a run of dozens of questions. The agent was also run with the model's own thinking: 93.9% correct instead of 90.2% (three more questions; one run each, so not a finding), median 3.1 s instead of 0.75 s ([`SENSITIVITY.md`](SENSITIVITY.md)).

Latency is wall-clock per question: query embedding, retrieval and the model for plain RAG; the tool call for the router; the model and the tool calls for the agent. The Mac was shared with other work, so treat latency as an order of magnitude.

## What the numbers say, and where honest-memory lost

Corpus 0.3.0, 82 questions: the 66 original ones plus 16 about one person leaving (`departure_depth1`, `departure_depth4`, `single_source`, `what_if`, `bridges`, `successors`, `handoff_plan`, `not_in_graph` and one `current_state`).

Plain RAG is correct on 46.3% (38 of 82). Of its 44 misses, 30 are "the records do not contain it" (a miss, not a wrong answer) and 14 are confident wrong or incomplete answers, including 4 stale ones (Q041, Q063 and Q064 give Growth as the owner of billing-api from the unsourced chat message, and Q064 also lists `reporting` as depending on billing-api before ADR-0013; Q068 says Oskar Alvarez "owns fernhill-sso" after his last day) and 2 that repeat a phone number and a mailing address. On the 16 departure questions it is correct on 4. The router answers 81 of 82 correctly and the LLM agent 74 (90.2%), with 0 unsupported and 0 leaked answers for both; the agent states an ended relation as current on some runs (Q067, below).

**What the scorer got wrong on the new questions, and what changed.** The first run on corpus 0.3.0 gave plain RAG 46.3%, router 97.6% (1.2% stale), agent 87.8%. Reviewing every miss on the 16 new questions found five scorer artefacts and five real misses of the agent (one of them shared by the router); a sixth artefact, a past-marker gap, turned up later in a run with the model's own thinking. The rules in "How an answer is scored" above are the fixes, each with a test in `tests/test_bench_score.py` that fails on the previous scorer:

| id | system | what the scorer did | verdict |
|---|---|---|---|
| Q068 | router | "ended 2026-10-09" was not read as "nothing now" | artefact, fixed |
| Q070 | router | flagged stale on "Platform (member, ended 2026-10-09)": the team-label rule erased the "ended" inside the parenthesis, and "ended" was not a past marker | artefact (a bug of mine), fixed |
| Q071 | agent | right answer (fernhill-sso, oncall-runbook) marked wrong for not naming the items the gold rules out ("Not ADR-0014: ...") | artefact, fixed |
| Q079 | agent | all three candidates named; marked wrong for not saying "auth", the basis in "(by work on auth)" | artefact, fixed |
| Q071 | plain RAG | would have flipped to correct under the exclusion rule alone, because it names fernhill-sso and oncall-runbook; it also claims ADR-0014 and ADR-0015, which the gold rules out | stays wrong, by the paired rule |
| Q068 | plain RAG | "Oskar Alvarez is a Security Engineer in the Platform team and owns fernhill-sso" was not flagged stale: a value the question names was never checked | artefact in plain RAG's favour, fixed |
| Q042 | agent (thinking on) | "succeeding Sana Petrov" flagged stale; "succeeding" was not a past marker | artefact, fixed |

The net effect on the first run's own answers, scored again: plain RAG 46.3% correct and 4.9% stale (was 3.7%), router 98.8% and 0.0% (was 97.6%, 1.2%), agent 90.2% and 1.2% (was 87.8%, 0.0%). Two changes count against honest-memory or the baseline (Q068 plain RAG stale, Q067 agent stale); the others remove misses from honest-memory's side. They do not favour it by construction: the scorer takes no system name, and the same sentence gets the same verdict from whichever system wrote it.

**Real misses of the agent** (`results/honest-agent-seed42.jsonl` has the tool calls; the router misses only Q080). Product feedback, nothing here was changed in `honest_memory/`:

| id | what happened |
|---|---|
| Q034 | Right records (DOC-01, RB-05) but the sentence does not name Sana Petrov, whom the gold names. A harsh miss, the answer is not wrong. |
| Q042 | The agent called `current_owner` only, so the answer is the current owner and never mentions that the runbook names Sana Petrov. Incomplete, not stale. The router calls `history` and shows both. |
| Q045 | `recall` returned the handoff, the sentence says "from Support" and leaves out Priya Farrow, who handed it over. Incomplete. |
| Q063 | The impact question asks "and who must be told". The agent called `impact` and then `escalation_path` of the failing system and named two of the six owners: a plausible tool, the wrong reading of the question. Q061 and Q062 failed the same way on the 66-question corpus and pass on 0.3.0 in all four thinking-off runs; Q063 fails in all four and passes with the model's own thinking. |
| Q067 | `what_leaves` returned the relations with `ended` dates, and in 3 of the 4 thinking-off runs kept on disk the sentence is "Oskar Alvarez owns the systems fernhill-sso and oncall-runbook and is a member of the Platform team": present tense for relations that ended two days before. The fourth run says "leaves". Flagged stale when the wording is present tense. |
| Q068 | "Oskar Alvarez owned fernhill-sso and oncall-runbook and was a member of the Platform team": past tense, the tool's `ended 2026-10-09` dropped, and no statement that nothing is current. Kept as a miss (soft); the router's sentence carries the date. |
| Q069 | `what_leaves` at depth 4 returned ten items; the sentence names three (admin-console, invoice-renderer, webhooks) and no owners. |
| Q070 | Over-claims: "ADR-0014 ... governs ledger-db, reporting, admin-console, and invoice-renderer". The tool shows ADR-0014 constraining ledger-db; the others are hops away. The scorer flags only the missing owners; it has no check for a wrong relation between right names, so this miss is invisible to the numbers. |
| Q080 | Router and agent: `handoff_plan` holds three `candidates` for fernhill-sso but the action text names only Lars Thorne, and the gold lists three. Kept as a miss (soft): a plan may name one person, but nothing in the text says the other two exist. |

Plain RAG misses 12 of the 16 new questions, mostly by refusing (bridges, what-if, successors, handoff plan, depth 4) or by listing what the question rules out (Q071). The 10 `who_knows` questions: plain RAG answers none correctly (9 refusals, 1 wrong person).

**After the product fixes (commit `caa9143`).** The misses above were then fixed in the product, not in the scorer: `handoff_plan` names every candidate in its action text (Q080), and the agent's system prompt got three general rules: say that an ended fact ended and when, name every item the rows list, and attach no relation the rows do not show (Q067, Q068, Q069, Q070). The rerun on the same 82 questions (`RESULTS.md`, FalkorDB 4.22): plain RAG 46.3% correct and 4.9% stale (unchanged), router 100.0% and 0.0%, agent 95.1% and 0.0%. Q069 and Q070 are still agent misses. These prompt rules were written after reading the agent's misses on this question set, so the agent's 95.1% is closer to an upper bound for this corpus than the 90.2% before the fixes; both runs are in the git history. `SENSITIVITY.md` still reflects commit `d3b7579`.

## Limits

- **Synthetic corpus.** One fictional company, 403 episodes written by a seeded script, with the failure modes (rumour, stale runbook, personal data, ownership transfer, dependency added later, a person leaving) put in on purpose. It shows that the mechanism works where it should; it does not predict a rate on real data.
- **82 questions, one seed, one model, one run per setting.** A question is 1.2 percentage points. Plain RAG gave byte-identical answers on every rerun; the agent changes 3 to 6 of 82 sentences between identical runs (Ollama is not bit-exact even at temperature 0 with a fixed seed), and in four runs the only verdict that moved was Q067 (stale in three, not in one). There are no confidence intervals. Do not read differences of a few points as real.
- **The scorer was tuned after seeing the answers.** Seven rule changes came from reading answers: six from the first 0.3.0 run, one from a later run. Each is general and tested on both sides, but a reader who wants an unbiased number should score a new corpus with the rules as they stand, not edit them again.
- **The router was written against this question set.** Its rules match the corpus's question templates, so `honest-router` is close to an upper bound for questions phrased like these and says little about other phrasings. `honest-agent` is the less biased path: the model, not a regex, chooses the tool and fills the arguments.
- **Some question types cannot be answered from 8 retrieved episodes at all.** "Who knows the most about X" is a count over 6 to 28 work items, "what becomes an island" and "strongest bridge" need a graph algorithm, impact questions need up to four dependency hops. A better embedding does not fix those; aggregation is something retrieval does not do, which is the point of the graph and also why this is not a like-for-like contest of retrieval quality.
- **Name-level scoring.** A sentence can contain the right names and still be wrong (an owner attached to the wrong system, Q070), and a correct answer worded differently can miss a name. The table shows the choice: `correct` applies the exemptions above; `literal-rule correct` exempts nothing but the question's own names (agent 73.2%, router 91.5%, plain RAG 39.0%, against 90.2%, 98.8% and 46.3%); `sentence-only correct` scores honest-memory's sentence without the source ids its tools returned with it (agent 85.4%, router 96.3%). The `extra_names`, `stale_values`, `excluded_values` and `unsupported_values` fields keep the matched text.
- **Known leniencies, none of them in honest-memory's favour.** (1) A gold that says "Nothing" or "No current owner" is met by any refusal, so plain RAG's "the records do not contain it" counts as correct for Q066, Q081 and Q082 although "unknown" is weaker than "no owner". (2) Q074 ("as of 2026-10-05: 0 decisions and 2 systems") has no exclusion lines in its gold, so plain RAG's answer that also lists ADR-0014 and ADR-0015 is scored correct. (3) Q070's gold names the owners in a parenthesis; plain RAG states the right decisions and systems but not the owners and is scored wrong. I left (3) strict because relaxing it would also credit the agent's over-claiming answer.
- **Unsupported is 0 for honest-memory and nearly so for plain RAG.** The scorer only flags names, ids and dates that are not in the evidence or not in the corpus. The one flag in the sensitivity runs is a plain RAG answer citing a record it had not retrieved (k = 16, Q038).
- **No LLM judge** also means no credit for a correct answer that avoids the expected names.
