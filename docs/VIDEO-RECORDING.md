# Demo video: what the live shots show

The demo video (3:20) has 8 illustrated cards and 6 live shots. The live shots are screen recordings of real runs of
this repository on 24 October 2026, 14:33 to 14:44 UTC: Zetaris 2.4.3.1 local, Meterless Nightshift 1.2.1 headless
(Investigator on Nemotron 3.5 Lightning 30B-A3B, Verifier on Nemotron 3 Nano 30B-A3B), H-MEM memory. Synthetic company
data (Quillfield Billing). The files each shot shows are committed here.

| Shot | What it shows | Run id | Files |
|---|---|---|---|
| 4 | the panel (`web/`, served by `serve.py` on 127.0.0.1), the pre-existing graph core: pick a person, depth, Remove | — | `web/` |
| 7, 8 | `python -m open_agent.run --input input_examples/02_tampered_source.json`: msg-0148 is edited in Postgres after the memory read it; round 1 cites ADR-0009 and msg-0148; the Verifier re-reads msg-0148 through Zetaris and rejects it (`quote_not_in_record`); round 2 keeps ADR-0009 and reports the edit on its own conflict line; verified, stored; msg-0148 restored | `20261024T143516Z-7f03` (367.1 s on a shared machine) | `logs/trace-20261024T143516Z-7f03.jsonl`, `logs/receipts-20261024T143516Z-7f03.jsonl`, `logs/video-take-02.json` |
| 9 | the 7 signed Nightshift receipts of that run: app, lane, routed model, $0, signature | `20261024T143516Z-7f03` | `logs/receipts-20261024T143516Z-7f03.jsonl` |
| 10 | H-MEM across processes: a new process asks a paraphrase and recalls the answer the earlier run stored | `20261024T144200Z-b108` | `logs/trace-20261024T144200Z-b108.jsonl`, `logs/receipts-20261024T144200Z-b108.jsonl`, `logs/video-take-05.json` |
| 11 | a name that is in none of the three systems: one UNION ALL through Zetaris, `absence_confirmed` | `20261024T144248Z-e71d` | `logs/trace-20261024T144248Z-e71d.jsonl`, `logs/receipts-20261024T144248Z-e71d.jsonl`, `logs/video-take-03.json` |

`20261024T143316Z-b06d` is a run of the example 01 question ("Who owns billing-api now?") at 14:33:16Z on the same tenant, before the recorded takes; it is not shown in the video (trace and receipts committed).

## What was shortened

Only the waits between outputs were shortened; no output line was changed, reordered or removed. The voice in shot 7
says the waits are shortened.

| Shot | Real | Shown |
|---|---|---|
| 7 | 200.4 s | 20.9 s |
| 8 | 168.9 s | 25.8 s |
| 10 | 41.4 s | 11.9 s |
| 11 | 6.8 s | 5.4 s |

Shots 4 and 9 are not shortened. In shots 7 and 8 the camera zooms onto two lines (the rejection and the conflict
line) over the same recording.

The display helpers typed in the takes (`show_result.py`, `show_receipts.py`, `show_ledger.py`) and the re-timing
script for the terminal recordings (`retime.py`, asciicast v2) are in `scripts/video/`. In the takes they were run
from the project folder next to the repository, so the command line on screen shows a different path.
