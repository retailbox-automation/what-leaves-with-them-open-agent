# Deck: speaker notes and the source of every number

`index.html` is the deck (8 slides, 1280x720, one per required point of the Submission Form Preview, section 3.6);
`what-leaves-with-them-deck.pdf` is it printed (headless Chromium, `--print-to-pdf`, no header or footer).
Rebuild: print `deck/index.html` to PDF at 1280x720 with no margins. Live numbers are lane C's 20 runs through
Zetaris, Nightshift and H-MEM (code `9b6f7a7`, 24 Oct 13:38Z to 14:03Z), recomputed with `python -m open_agent.stats`
(command in docs/OPEN-AGENT-NEW-WORK.md, Measured); recomputed again on 25 Oct from the same 20 traces, same values.

Two different checks carry "8 of 8" and "5 of 5", and they are labelled on the slides:
- **8 of 8** (slide 5): the Zetaris stop-gate script of 22 Oct, run before the agents were written. It re-read 8 cited
  quotes by id and then the same 8 with one word changed; all 8 changed quotes were rejected. No model, no agents.
- **5 of 5** (slides 1 and 6): the 5 live runs of example 02 on 24 Oct, the full system (Zetaris, Nightshift, H-MEM),
  where msg-0148 was edited in Postgres after the memory read it.

H-MEM confidence (slide 5): a new memory starts at 0.70 (`confidence=0.7`, open_agent/hmem_bridge.py); each `helpful`
verdict adds 0.05 (docs/METERLESS.md, "verified / supported -> helpful (+0.05)"). In the cross-session proof `mem-1`
went 0.70 -> 0.75 after one verdict. The earlier deck also showed "0.85 -> 0.90" on slide 7: that was a memory in the
20-run series which had already had three helpful verdicts; slide 7 now shows another example, so the number is gone.

| Slide | Point | Number or claim on the slide | Source |
|---|---|---|---|
| 1 | Problem and users | 67% / 64% of 1,000 IT managers | Sinequa press release, August 2022 (link in README, first screen) |
| 1 | Problem and users | edited message caught in 5 of 5 live runs, flagged for a person | `open_agent.stats` per_question "tamper=True": verified 5, rounds 2 in 5; docs/OPEN-AGENT-NEW-WORK.md, Measured, "What happened" (conflict line flagged msg-0148 for a human in every run) |
| 1 | Problem and users | "fernhill-sso has no current owner. ... Nobody has been recorded as taking it over." | docs/deck-runs-2026-10-25/fernhill-sso.sample.json `answer` (run 20261025T135528Z-6fd1) |
| 1 | Problem and users | Quillfield Billing made up, script, seed 42, no real people | README section 9 (`python -m honest_memory.corpus --seed 42`, data/MANIFEST.md) |
| 2 | Solution | at most two more tries | `MAX_REVISIONS = 2`, open_agent/run.py |
| 2 | Solution | panel screenshot, cropped | SUBMISSION/panel-after-answer-card.png (serve.py at c5b859c, sample mode) -> deck/assets/panel-crop.png (`sips`, x 320 to 1600, y 20 to 900) |
| 2 | Solution | "Ask before Oskar leaves.", "8 days left", "Knowledge saved 1 / 4" | web/app.js: headline `Ask before ${first(person)} leaves.`; days start at notice date to last day (2026-09-25 to 2026-10-09 = 14, data/MANIFEST.md) and drop `DAYS_PER_ANSWER = 3` per question asked ("a game rule, not data"): 2 asked, 14 - 6 = 8; saved counts an item only when, after the answer, it no longer rests on one person (`g.saved++` when `found.after.single_source` is false): ADR-0014, confirmed by Viktor Hartwell (DOC-18), green card (`.card.saved`); fernhill-sso was answered but still rests on Oskar |
| 3 | Workflow | 21 graph tools | `GET /health` -> `"tools": 21` (SUBMISSION/FRESH-CLONE-TEST.md) |
| 3 | Workflow | at most 2 revisions, 3 rounds; one query across the three systems for a name in no system; a quote that fails the check is never stored | open_agent/run.py (`write_finding`: nothing written unless every citation was accepted), open_agent/sources.py `mentions()` (one UNION ALL); output_examples/03_not_in_these_systems.live.json (status absence_confirmed) |
| 4 | Stack | Zetaris 2.4.3.1; Nightshift 1.2.1; FalkorDB 6.0.2 | zetaris_spike/README.md; docs/METERLESS.md; docker-compose.yml (pinned by digest) |
| 4 | Stack | fresh clone, empty .env: 5 input examples + 1 new question, all 6 completed: 3 verified, 1 absence_confirmed, 2 no_claim | SUBMISSION/FRESH-CLONE-TEST.md, third run (commit d3f4550): 01 verified, 02 verified, 03 absence_confirmed, 04 no_claim, 05 no_claim, "Who decided ADR-0014?" verified |
| 4 | Stack | Mac Studio, Apple M5 Max, 128 GB; no separate graphics card, no cloud model API | hardware: docs/METERLESS.md line 12; no cloud model API: all 95 receipts local, cost 0 (`open_agent.stats`); "no separate graphics card" is the reviewer's wording for Nadya's point 8 (the Mac has no discrete card) |
| 5 | Sponsors | 403 records, the same row counts as the source files (225 / 127 / 51) | live trace logs/trace-20261024T133813Z-5253.jsonl (ingest select rows 225, 127, 51 through Zetaris; graph_built 403 episodes); data/MANIFEST.md. The earlier "0 field differences" came from lane A's journal outside the repo and is no longer claimed |
| 5 | Sponsors | re-read median 0.20 s chat (10 calls), 0.38 s docs (26 calls) | `open_agent.stats` `source_reads_s` (p50 0.200 / 0.382) |
| 5 | Sponsors | stop-gate 22 Oct: JOIN 45 of 45, 8 of 8 quotes with one word changed rejected | zetaris_spike/README.md "Result on 22.10.2026", evidence/gate-20261022T153154Z.json |
| 5 | Sponsors | own app id per agent, one model allowed; routed model / lane / cost checked; Ed25519 receipts, one file per run | docs/METERLESS.md; open_agent/nightshift.py; logs/receipts-20261024T133813Z-5253.jsonl (6 receipts: 3 Lightning, 3 Nano) |
| 5 | Sponsors | 95 answered model calls, 95 signed receipts, $0 each, 0 routing mismatches; 1 more call came back empty and the code took that step | `open_agent.stats`: `receipts` (95 / 95 signed / 20 files), `cost_usd` 0.0, `routed`, `providers` (nightshift 95, nightshift warning 1); the empty call: logs/trace-20261024T133813Z-5253.jsonl seq 30 (Investigator revise, HTTP 502 empty_response after 216.7 s, no receipt), seq 31 revise fallback; open_agent/llm.py (an empty reply is not "down": warning, the run goes on) |
| 5 | Sponsors | Lightning 59 of 59 answered calls, Nano 36 of 36 | `open_agent.stats` `routed` |
| 5 | Sponsors | H-MEM: starts at 0.70, +0.05 per helpful verdict, 0.70 -> 0.75 in the cross-session proof | open_agent/hmem_bridge.py; docs/METERLESS.md; logs/hmem-ledger-oa_xs_live_20261024T140313Z.jsonl; output_examples/05_cross_session_recall.live.json |
| 5 | Sponsors | medians per call: re-read 0.20 / 0.38 s, Nano judge 2.5 s, Lightning tool step 4.3 s, cite 11.9 s, revise 44.1 s, lane switch 5.5 s | `open_agent.stats` `source_reads_s`, `llm_calls_s` (2.516, 4.273, 11.944, 44.122), `lane_swaps_s` (5.475) |
| 6 | Unique | the misquote the quote check overruled, 24 Oct, before Nightshift (Nemotron 3 Nano on Ollama) | docs/open-agent-runs-2026-10-24/trace-20261024T123225Z-c454.jsonl (12:32Z; the Nightshift integration started after it). In lane C's 20 runs every rejected quote was also judged `contradicts` (`check_verdicts` 31 supports / 5 contradicts; `check_reasons` 31 ok / 5 quote_not_in_record) |
| 6 | Unique | tamper: 5 of 5 runs round 1 rejected msg-0148, round 2 verified, restored 5 of 5; median 164.5 s | `open_agent.stats` per_question ("tamper=True": verified 5, rounds 2 in 5, p50 164.450); docs/OPEN-AGENT-NEW-WORK.md, Measured |
| 6 | Unique | why slow: thinking on every call, one model loaded at a time; the film shows a later run of the same example (367 s) with the waits shortened | docs/OPEN-AGENT-NEW-WORK.md, Measured and limits (`NIGHTSHIFT_SWAP=1`); docs/VIDEO-RECORDING.md (run 20261024T143516Z-7f03, not one of the 20; "What was shortened": only the waits between outputs); logs/trace-20261024T143516Z-7f03.jsonl end 367.1 s |
| 6 | Unique | comparison: a chat assistant over uploaded files (e.g. ChatGPT with file uploads), wiki search (e.g. Confluence search) | only the missing step is claimed (no re-read of each quoted line at its live source before the answer counts); how those tools work by default, no measurement of ours, no numbers |
| 7 | Visuals | the card crop | deck/assets/card-after.png, cropped (sips) from SUBMISSION/panel-after-answer-card.png; nothing drawn over it |
| 7 | Visuals | trace lines (seq 1, 5, 7, 8, 10, 12 to 16, 18, 19; 403 records, 1.23 s, 1.34 s; FND-00001, mem-1) and the answer | docs/deck-runs-2026-10-25/trace-20261025T135528Z-6fd1.jsonl and fernhill-sso.sample.json: the panel's own question "Who owns fernhill-sso now?" in sample mode (tenant oa_deck_20261025, code 4edcd3a, 25 Oct 13:55Z); same records (DOC-14, DOC-16), round 1, quote found, as on the card |
| 8 | Future | each item's current limit; median 18.4 s (03) to 164.5 s (02) | README section 10; docs/OPEN-AGENT-NEW-WORK.md "Not done here, and limits"; `open_agent.stats` per_question (18.360, 164.450) |

Speaker lines (one per slide, about 20 seconds each):

1. When someone leaves, the team lead has to work out who owns what now and why, from records that disagree. Two thirds of IT leaders worry about exactly that. We give the lead an answer with its proof, a warning when a record changed, and the gaps in writing.
2. Two agents. One answers and cites a line from a record for every claim. The other goes back to the source and checks that the line is still there. The panel shows it for a person who is leaving.
3. It is a loop: a rejection goes back with the record as it reads now, the Investigator revises, at most twice, and only a fully verified answer is stored.
4. One command runs it. Sample mode needs no keys and no model; live it uses Zetaris, Nightshift and Nemotron on one Mac.
5. Zetaris is the only way the agents read the three systems; Nightshift answers the model calls with a signed receipt each; H-MEM keeps what passed and learns from the Verifier.
6. The Verifier trusts the source, not the memory and not a model. In all five live tamper runs it caught the edited message in round one. A chat assistant or a wiki does not do that check.
7. One question, the same evidence on the panel's card and in the trace, step by step.
8. Next: refresh the graph when a source changes, a read-only semantic layer in Zetaris, and a pilot on a real team's records.
