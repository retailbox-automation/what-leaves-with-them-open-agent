# Deck: speaker notes and the source of every number

`index.html` is the deck (8 slides, 1280x720, one per required point of the Submission Form Preview, section 3.6);
`what-leaves-with-them-deck.pdf` is it printed (headless Chromium, `--print-to-pdf`, no header or footer).
Rebuild: print `deck/index.html` to PDF at 1280x720 with no margins. Live numbers are lane C's 20 runs through
Zetaris, Nightshift and H-MEM (code `9b6f7a7`), recomputed with `python -m open_agent.stats` (command in docs/OPEN-AGENT-NEW-WORK.md, Measured).

| Slide | Point | Number or claim on the slide | Source |
|---|---|---|---|
| 1 | Problem and users | 67% / 64% of 1,000 IT managers | Sinequa press release, August 2022 (link in README, first screen) |
| 2 | Solution | at most two revisions | `MAX_REVISIONS = 2`, open_agent/run.py |
| 2 | Solution | panel screenshot, DOC-14 and DOC-16, round 1, quote found | SUBMISSION/panel-after-answer-card.png (serve.py at c5b859c, sample mode) |
| 3 | Workflow | 21 graph tools | `GET /health` -> `"tools": 21` (SUBMISSION/FRESH-CLONE-TEST.md) |
| 3 | Workflow | 3 rounds at most, UNION ALL absence query | open_agent/run.py, open_agent/sources.py `mentions()` |
| 4 | Stack | Zetaris 2.4.3.1; Nightshift 1.2.1; FalkorDB 6.0.2 | zetaris_spike/README.md; docs/METERLESS.md; docker-compose.yml (pinned by digest) |
| 4 | Stack | fresh clone: 4 examples + 1 new question, all success | SUBMISSION/FRESH-CLONE-TEST.md |
| 5 | Sponsors | 403 records, 0 field differences | docs/OPEN-AGENT-NEW-WORK.md, Measured |
| 5 | Sponsors | re-read p50 0.20 s chat (10 calls), 0.38 s docs (26 calls) | docs/OPEN-AGENT-NEW-WORK.md, Measured; `open_agent.stats` `source_reads_s` |
| 5 | Sponsors | JOIN 45/45, tampered quotes 8/8 rejected | zetaris_spike/README.md, evidence/gate-20261022T153154Z.json |
| 5 | Sponsors | one app id per agent, routed model / lane / cost checked, Ed25519 receipts, one file per run | docs/METERLESS.md; open_agent/nightshift.py; logs/receipts-20261024T133813Z-5253.jsonl (6 receipts: 3 Lightning, 3 Nano) |
| 5 | Sponsors | 95 model calls, 95 signed receipts, $0 each, 0 routing mismatches | `open_agent.stats`: `receipts` (95 / 95 signed / 20 files), `cost_usd` 0.0, `routed`; docs/OPEN-AGENT-NEW-WORK.md, Measured |
| 5 | Sponsors | "Primary provider for both agents"; Lightning 59 of 59, Nano 36 of 36 | `open_agent.stats` `routed`; output_examples/*.live.json `llm.provider: nightshift` |
| 5 | Sponsors | H-MEM confidence 0.70 -> 0.75 | docs/OPEN-AGENT-NEW-WORK.md, cross-session proof; logs/hmem-ledger-oa_xs_live_20261024T140313Z.jsonl; output_examples/05_cross_session_recall.live.json |
| 6 | Unique | the misquote the quote check overruled (earlier provider: Nemotron 3 Nano on Ollama) | docs/open-agent-runs-2026-10-24/trace-20261024T123225Z-c454.jsonl. In lane C's 20 runs every rejected quote was also judged `contradicts` (`check_verdicts` 31 supports / 5 contradicts; `check_reasons` 31 ok / 5 quote_not_in_record) |
| 6 | Unique | tamper: 5 of 5 runs round 1 rejected msg-0148, round 2 verified, restored 5 of 5; p50 164.5 s | `open_agent.stats` per_question ("tamper=True": verified 5, rounds 2 in 5, p50 164.450); docs/OPEN-AGENT-NEW-WORK.md, Measured |
| 6 | Unique | speed: thinking on every call, one lane at a time | docs/OPEN-AGENT-NEW-WORK.md, Measured and limits (Nightshift 1.2.1 cannot switch thinking off; `NIGHTSHIFT_SWAP=1`) |
| 7 | Visuals | the card crop | deck/assets/card-after.png, cropped (sips) from SUBMISSION/panel-after-answer-card.png; nothing drawn over it |
| 7 | Visuals | trace lines (seq 4, 12, 19, 20, 21, 23, 26, 27, 33, 36, 37, 38, 40; 19.4 s, 0.332 s, 2.0 s, 39.5 s, 0.85 -> 0.90, 118.71 s; receipt ids) | docs/open-agent-runs-2026-10-24-nightshift/trace-20261024T135040Z-1dba.jsonl (tamper run 3), values from the trace, rounded to 0.1 s where shown so; seq 27 text shortened |
| 8 | Future | each item's current limit; p50 18 s (03) to 164.5 s (02) | README section 10; docs/OPEN-AGENT-NEW-WORK.md "Not done here, and limits" and Measured |

Speaker lines (one per slide, about 20 seconds each):

1. When someone leaves, the team lead has to work out who owns what now and why, from records that disagree. Two thirds of IT leaders worry about exactly that.
2. Two agents. One answers and cites a line from a record for every claim. The other goes back to the source and checks that the line is still there.
3. It is a loop: a rejection goes back with the record as it reads now, the Investigator revises, at most twice, and only a fully verified answer is stored.
4. Everything runs with one command and no keys in sample mode; live it uses Zetaris, Nightshift and Nemotron on one machine.
5. Zetaris is the only way the agents read the three systems; Nightshift serves every model call with a signed receipt; H-MEM keeps what passed and learns from the Verifier.
6. The Verifier trusts the source, not the memory and not a model: in an earlier run a model said a misquote supported the claim, and the quote check still rejected it. In all five live tamper runs it caught the edited message in round one.
7. The same evidence is in the panel's answer card and in the trace, line by line.
8. Next: refresh the graph when a source changes, a read-only semantic layer in Zetaris, and a pilot on a real team's records.
