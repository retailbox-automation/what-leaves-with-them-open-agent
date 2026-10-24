# Deck: speaker notes and the source of every number

`index.html` is the deck (8 slides, 1280x720, one per required point of the Submission Form Preview, section 3.6);
`what-leaves-with-them-deck.pdf` is it printed (headless Chromium, `--print-to-pdf`, no header or footer).
Rebuild: print `deck/index.html` to PDF at 1280x720 with no margins. Lines marked `C-UPDATE` change when the
final live runs through Nightshift are committed.

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
| 5 | Sponsors | re-read p50 0.11 s chat (21 calls), 0.18 s docs (97 calls) `C-UPDATE` | docs/OPEN-AGENT-NEW-WORK.md, Measured (28 traces, code at 266e330) |
| 5 | Sponsors | JOIN 45/45, tampered quotes 8/8 rejected | zetaris_spike/README.md, evidence/gate-20261022T153154Z.json |
| 5 | Sponsors | one app id per agent, routed model / lane / cost checked, Ed25519 receipts | docs/METERLESS.md; open_agent/nightshift.py; logs/receipts-wlwt-proof-20261024T115709Z-c6aed4.jsonl (3 receipts) `C-UPDATE` |
| 5 | Sponsors | "Primary provider for both agents" | docs/METERLESS.md (smoke runs, cross-session proof). `C-UPDATE`: the loop's committed live runs (lane A) used Nemotron 3 Nano on Ollama; confirm once lane C commits the loop's runs through Nightshift |
| 5 | Sponsors | H-MEM confidence 0.70 -> 0.75 | docs/METERLESS.md, cross-session proof |
| 6 | Unique | the misquote the quote check overruled | docs/open-agent-runs-2026-10-24/trace-20261024T123225Z-c454.jsonl; docs/OPEN-AGENT-NEW-WORK.md |
| 6 | Unique | tamper: round 1 rejected msg-0148 in 6 of 6 live runs; 4 verified, 2 unverified `C-UPDATE` | docs/OPEN-AGENT-NEW-WORK.md, Measured table and "What happened" |
| 6 | Unique | "live run (Zetaris + Nemotron)" | logs/trace-20261024T123154Z-720b.jsonl: model `nemotron-3-nano:30b-a3b-q4_K_M` on local Ollama `C-UPDATE` |
| 7 | Visuals | the card crop | deck/assets/card-after.png, cropped (sips) from SUBMISSION/panel-after-answer-card.png; nothing drawn over it |
| 7 | Visuals | trace lines (seq 3, 12, 14, 18, 20, 22, 23, 30, 34, 36, 37, 38; 0.188 s, 0.36 s, 12.28 s) `C-UPDATE` | logs/trace-20261024T123154Z-720b.jsonl, values copied, layout abridged |
| 8 | Future | each item's current limit | README section 10; docs/OPEN-AGENT-NEW-WORK.md "Not done here, and limits" |

Speaker lines (one per slide, about 20 seconds each):

1. When someone leaves, the team lead has to work out who owns what now and why, from records that disagree. Two thirds of IT leaders worry about exactly that.
2. Two agents. One answers and cites a line from a record for every claim. The other goes back to the source and checks that the line is still there.
3. It is a loop: a rejection goes back with the record as it reads now, the Investigator revises, at most twice, and only a fully verified answer is stored.
4. Everything runs with one command and no keys in sample mode; live it uses Zetaris, Nightshift and Nemotron on one machine.
5. Zetaris is the only way the agents read the three systems; Nightshift serves every model call with a signed receipt; H-MEM keeps what passed and learns from the Verifier.
6. The Verifier trusts the source, not the memory and not a model: in one run a model said a misquote supported the claim, and the quote check still rejected it.
7. The same evidence is in the panel's answer card and in the trace, line by line.
8. Next: refresh the graph when a source changes, a read-only semantic layer in Zetaris, and a pilot on a real team's records.
