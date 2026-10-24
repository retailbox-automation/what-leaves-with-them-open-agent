# Live runs of 24 Oct 2026 on the earlier provider (history)

Made by the Investigator/Verifier lane with the code at commit 266e330, before the integration with Meterless
Nightshift and H-MEM. Model for both agents: NVIDIA Nemotron 3 Nano 30B-A3B (`nemotron-3-nano:30b-a3b-q4_K_M`) on a
local **Ollama**, `reasoning_effort=none`. Not through Nightshift, no receipts, no H-MEM.

- `<example>.run0.json`: the run that was in `output_examples/<example>.live.json` until the integrated runs replaced it;
  its trace was in `logs/` and is now here (the `trace_file` field inside still says `logs/`).
- `<example>.run1..5.json` and the other traces: five more runs of each example.

The integrated runs (Nightshift primary provider, H-MEM memory) are in `output_examples/*.live.json`, `logs/` and
`docs/open-agent-runs-2026-10-24-nightshift/`.
