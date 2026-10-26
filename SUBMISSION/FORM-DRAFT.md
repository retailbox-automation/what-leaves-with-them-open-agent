# HackOS submission form: draft (worksheet of the Submission Form Preview, section 4)

Draft only. Nothing here has been entered into HackOS. Field texts are plain text, ready to paste; each claim is
listed with the file that proves it in the evidence table under the field. Live numbers are from the 20 runs of
24 Oct through Zetaris, Nightshift and H-MEM (docs/OPEN-AGENT-NEW-WORK.md, Measured).

## Project name

```text
What Leaves With Them
```

## Short description

The organizer said there is no separate field for pre-existing work, so the declaration is the second paragraph of
the Description (correspondence/2026-10-23-organizer-reply-helpdesk-RECEIVED.md).

```text
For a team lead whose colleague is leaving. Two agents answer questions like "Who owns billing-api now?" from team chat in Postgres, tickets in a CSV file and documents behind a REST API, all read through Zetaris. The demo company, Quillfield Billing, is made up, and its records come from a script. The Investigator cites a record id and a quoted line for every claim. The Verifier re-reads each cited record in the system it lives in, rejects a quote that is no longer there and sends the draft back, at most twice. What you get is one answer with its sources, a list of what was dropped and why, and a stored finding only when every citation held up. Gaps come back in plain words too, for example "fernhill-sso has no current owner". Model calls go through Meterless Nightshift to NVIDIA Nemotron, one model per agent, with signed receipts, and the Meterless H-MEM engine keeps what passed.

Pre-existing work, declared: the memory graph on FalkorDB, its write gate, the graph tools, the rule router, the benchmark, the red team and the panel were built for Graph Hacks (15 to 18 October 2026) and imported unchanged at upstream commit a2cca9b. PRE-EXISTING.md in the repo has the file hashes and a recipe to check the import. Built in the window from 22 October: the Zetaris integration, the Investigator and Verifier loop, Nightshift as the model provider, H-MEM as the agents' memory, sample mode, docker compose with POST /run, and the Verifier's evidence in the panel.
```

| Claim | Evidence |
|---|---|
| three systems read through Zetaris (Postgres chat, tickets CSV, docs REST API) | open_agent/sources.py (`INGEST`, `ZetarisSource`), zetaris_spike/README.md |
| Quillfield Billing made up, records from a script | README section 9 (`python -m honest_memory.corpus --seed 42`, data/MANIFEST.md) |
| record id and quoted line per claim | open_agent/agents.py; output_examples/01_current_owner.live.json `citations` |
| "fernhill-sso has no current owner" | docs/deck-runs-2026-10-25/fernhill-sso.sample.json `answer` (sample mode, run 20261025T135528Z-6fd1) |
| Verifier re-reads in the source system, rejects a missing quote, at most two revisions | open_agent/agents.py `Verifier`; open_agent/run.py `MAX_REVISIONS = 2`; live tamper run logs/trace-20261024T133813Z-5253.jsonl seq 23-28 (re-read msg-0148, quote_not_in_record, feedback, revise); the same on the earlier Ollama provider: docs/open-agent-runs-2026-10-24/trace-20261024T123154Z-720b.jsonl seq 18-23 |
| list of what was dropped and why | open_agent/run.py `dropped()`, `notices`; output_examples/02_tampered_source.sample.json |
| stored only when every citation held up | open_agent/run.py `write_finding` ("the Verifier did not accept every citation of this answer; nothing written"); docs/open-agent-runs-2026-10-24/trace-20261024T123209Z-8b24.jsonl seq 48 (write_gate skipped) |
| Nightshift to Nemotron, one model per agent, signed receipts | docs/METERLESS.md (table "Which agent uses which Nemotron model"); logs/receipts-20261024T133813Z-5253.jsonl; `open_agent.stats` `routed` (Lightning 59, Nano 36) and `receipts` (95 signed) |
| H-MEM keeps what passed | honest_memory/tools.py (`HM_MEMORY_BACKEND`), open_agent/hmem_bridge.py; logs/meterless-live-proof-wlwt-proof-20261024T115709Z-c6aed4.json |
| pre-existing list, Graph Hacks dates, a2cca9b unchanged | PRE-EXISTING.md; `git log` first commit b086588 |
| built in the window | docs/OPEN-AGENT-NEW-WORK.md, docs/METERLESS.md, serve.py, docker-compose.yml, web/app.js `verifiedEvidence` |

## Track

```text
[ ] Solving Fragmented Intelligence
[x] The Agent That Can Explain Why
[ ] Reasoning Architecture
[ ] Wildcard [Tinkerer]
```

Same track named in README (first line), deck (slide 1), metadata.json `track`. The video must say it too.

## Team (solo)

```text
Team member 1:
Full name: Michael Oskola
HackOS account email: oskola7@gmail.com
GitHub username: retailbox-automation
Role: Everything: product, agents, Zetaris and Meterless integration, packaging
Primary contact: Yes
```

Name decided 26 Oct (Mikhail, "as now"): this field and the HackOS profile say Michael Oskola; the deck and the video keep
"prepared by Mikh[ai]l Oskola" (the RetailBox brand line).

## GitHub repository URL

```text
https://github.com/retailbox-automation/what-leaves-with-them-open-agent
```

Currently PRIVATE. Making it public is the human's decision; after that, open the link in a private window.

## Colab links

```text
Not used.
```

## Demo video (1 to 4 minutes)

```text
<pending>
```

## Slide deck

```text
deck/what-leaves-with-them-deck.pdf (8 slides; source deck/index.html, notes and sources deck/NOTES.md)
```

## Sponsor technology explanation

```text
Zetaris: Zetaris is the only way the agents read the three source systems. Team chat sits in Postgres (registered over JDBC), tickets in a CSV file in the driver's data volume, and documents behind our REST JSON API. All three are Zetaris objects, queried with one SQL dialect on a local Zetaris 2.4.3.1. The memory graph is built from three SELECTs through Zetaris: 403 records, the same row counts as the source files (225 chat messages, 127 tickets, 51 documents). The Verifier re-reads each cited record by id from the table of its own system, and checks an absence with one UNION ALL over all three. Without Zetaris the graph cannot be built and no citation can be accepted. Two things we found on the platform: a CSV in the shared customer-data folder registers with a plain path, no S3 needed, and REGISTER DATASOURCE TABLES imports nothing from a Postgres schema other than public.

Meterless: Nightshift is the primary model provider. Both agents send every model call to Nightshift running headless on the same machine, and each response carries a receipt signed by the Nightshift daemon. In our 20 live runs Nightshift answered 95 model calls, each with a signed receipt at zero cost and no routing mismatch; one more call came back empty, and the code took that step. The client checks each one: the routed model has to be the agent's model, the receipt has to show the agent's own lane allowlist, and the cost has to be zero, otherwise the run stops instead of falling back to another model. In Nightshift 1.2.1 an explicit model id did not pin routing, so each agent has its own app id with a one-lane allowlist. The H-MEM engine, the Meterless reference implementation pinned in vendor/hmem, is the agents' memory: an answer that passed the Verifier and the write gate is stored as a long-term memory, matching memories go in front of the model before it answers, and the Verifier's verdict becomes feedback on the memory it used.

Cursor: Not used; the organizer says it is optional. The code was written with other AI coding assistants under the author's direction.

NVIDIA: NVIDIA Nemotron open models run through Nightshift from local GGUF files, one per agent. The Investigator uses Nemotron 3.5 Lightning 30B-A3B for its native tool calls over the graph tools. The Verifier uses Nemotron 3 Nano 30B-A3B for short JSON verdicts, so it is a different model from the one it checks. In the 20 live runs the receipts show the Investigator on Lightning for all 59 of its answered calls and the Verifier on Nano for all 36 of its calls. The README has the table and links the receipt log (logs/receipts-20261024T133813Z-5253.jsonl). The runs are slow, 18.4 to 164.5 seconds per question at the median, because the models think on every call and, on our shared machine, only one of the two models was loaded at a time. The video shows a later run of the tampered-source example (367 seconds), with the waits between outputs shortened.

What was unique: The Verifier does not trust the Investigator's memory or a model. Before an answer counts, it goes back through Zetaris to the system that owns each cited record and re-reads the quoted line. That check is plain code and no model can overrule it. On 24 October, in a development run before we moved to Nightshift (Nemotron 3 Nano on a local Ollama), the Investigator's model quoted a chat sentence as if it came from a decision record, the Verifier's model called it supported, and the quote check still rejected it. In all five live runs where we edited a chat message after the memory read it, the Verifier caught the change in the first round and the answer was revised. Only answers that survive this reach the H-MEM memory. A chat assistant over uploaded files answers from the files it was given, and wiki search leaves the reconciling to the reader. Neither reads each quoted line again at its source before the answer counts; here that check decides.
```

| Claim | Evidence |
|---|---|
| Postgres over JDBC, CSV in the data volume, REST JSON API; Zetaris 2.4.3.1 | zetaris_spike/README.md (table, Result) |
| 403 records, row counts 225 / 127 / 51 | logs/trace-20261024T133813Z-5253.jsonl seq 3-6 (Ingest select through Zetaris, graph_built 403); data/MANIFEST.md |
| re-read by id per system; UNION ALL absence | open_agent/sources.py `by_id`, `mentions` |
| without Zetaris no graph and no accepted citation | open_agent/run.py (`SourceUnavailable` on ingest), docs/OPEN-AGENT-NEW-WORK.md Rules table (`unreadable`) |
| CSV plain path; non-public schema imports nothing | zetaris_spike/README.md, "Findings worth keeping" |
| every model call of both agents to Nightshift headless; 95 answered calls, 95 signed receipts, zero cost, no mismatch; 1 empty reply | docs/METERLESS.md; docs/OPEN-AGENT-NEW-WORK.md, Measured (Nightshift receipts); `open_agent.stats` |
| signed receipts; routed model, lane allowlist, zero cost checked; a routing mismatch or Nightshift being down stops the run; an empty model reply is logged and the code template takes that step | open_agent/llm.py (empty_response is not "down"); logs/trace-20261024T133813Z-5253.jsonl seq 30-31; docs/METERLESS.md (Receipts, check_routing, "no fallback at all" in the loop); tests/test_nightshift.py |
| 1.2.1 explicit id does not pin; per-app one-lane allowlist | docs/METERLESS.md, "Why each agent has its own app id" |
| H-MEM pinned, long-term store, context before answering, verdict feedback | docs/METERLESS.md "H-MEM in the product"; vendor/hmem/README-PIN.md; tests/test_hmem.py |
| Cursor optional per organizer 23 Oct | correspondence/2026-10-23-organizer-reply-helpdesk-RECEIVED.md (project dir) |
| Lightning for the Investigator (tool calls), Nano for the Verifier (JSON); 59 of 59, 36 of 36; 18.4 to 164.5 s median; thinking on, one lane at a time; waits shortened in the video | docs/METERLESS.md table; `open_agent.stats` `routed`, per_question p50 (03 18.36 s, 02 164.45 s); docs/OPEN-AGENT-NEW-WORK.md limits; docs/VIDEO-RECORDING.md "What was shortened" |
| README table and receipt log link | README.md section 4, NVIDIA |
| quote check cannot be overruled; the misquote run on 24 Oct before Nightshift (Nemotron 3 Nano on Ollama); 5 of 5 tamper runs caught in round 1 and revised | docs/open-agent-runs-2026-10-24/trace-20261024T123225Z-c454.jsonl (12:32Z); `open_agent.stats` per_question tamper=True (verified 5, rounds 2 in 5); docs/OPEN-AGENT-NEW-WORK.md "What happened" |
| chat assistant over exported files, wiki page as current as its last edit | how those tools work by default; no measurement of ours (same comparison as deck slide 6) |

## Pre-existing components

Same text as the second paragraph of the Description (the form has no separate field; keep both identical if a
field appears).

```text
The memory graph on FalkorDB, its write gate, the graph tools, the rule router, the benchmark, the red team and the panel were built for Graph Hacks (15 to 18 October 2026) and imported unchanged at upstream commit a2cca9b. PRE-EXISTING.md in the repo has the file hashes and a recipe to check the import. Built in the window from 22 October: the Zetaris integration, the Investigator and Verifier loop, Nightshift as the model provider, H-MEM as the agents' memory, sample mode, docker compose with POST /run, and the Verifier's evidence in the panel.
```

## Before pasting (checks, not form fields)

- The humanizer pass was run over every field text above (no em dashes, plain text, no markdown inside fields).
- Roster locks at the first submission. HackOS account: Google SSO oskola7@gmail.com (registered 24.09, team "RetailBox", profile name Michael Oskola); GitHub: retailbox-automation, the author of every commit in the repo (`git log --format=%an`).
