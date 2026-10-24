# The 82-question benchmark through Meterless Nightshift (24 Oct 2026)

One run of the pre-existing benchmark (`bench/`, corpus 0.3.0, seed 42, scorer unchanged) for the honest-memory LLM
agent, with the model served by **Meterless Nightshift 1.2.1** instead of Ollama:

```
HM_MEMORY_CONTEXT=off HM_MEMORY_BACKEND=graph NIGHTSHIFT_SWAP=1 \
  python -m bench.run --systems honest-agent --provider nightshift --port 6395 \
  --out bench/results/nightshift-nemotron-3.5-lightning-30b-a3b
```

- Model: NVIDIA Nemotron 3.5 Lightning 30B-A3B, `local-runtime/nemotron-3.5-lightning-30b-a3b` (GGUF Q4_0), the
  Investigator's lane `nemotron-lightning`, app `wlwt-investigator`, temperature 0. Its thinking is on: Nightshift
  1.2.1 cannot switch it off from outside, so `reasoning` reads `default` in `summary.json`.
- 171 model calls, 171 receipts, all routed to that model, `cost_usd` 0 on every one: `trace-bench-*.jsonl` here has
  one `llm_call` line per call with its `receipt_id`, `routed_model`, lane, latency and tokens. 69 replies had U+2011
  hyphens made ASCII (`reply_normalised`).
- Memory settings as in the published run, which predates H-MEM: no memory context before the model, recall from the
  graph. FalkorDB 6.0.2, Mac Studio M5 Max shared with other work.
- The generated `RESULTS.md` names Ollama fields it does not have ("via Ollama not used", "?" for size and digest)
  because `bench/report.py` was written for Ollama; the facts are above and in `summary.json`. Its "repo commit
  `bc9453a`" is the commit at the end of the run; the code that ran was `a3ed69e`. The commit after it, `e582d0c`, only stops
  re-asking after an empty reply and renames a trace detail; no reply was empty here (171 of 171 calls ok).

| Run | Model, provider | correct | stale | unsupported | clean | median |
|---|---|---|---|---|---|---|
| this one, 24 Oct | Nemotron 3.5 Lightning 30B-A3B, Meterless Nightshift, thinking on | **97.6%** (80 of 82) | 0.0% | 0.0% | 97.6% | 10.4 s |
| `bench/RESULTS.md`, 15 Oct | gemma4:26b-a4b-it-qat, Ollama, thinking off, FalkorDB 4.22 | 95.1% | 0.0% | 0.0% | 95.1% | 0.78 s |

Different models on different days, one run each: the 2-question difference is not a finding. What carries over is
that the guarantees hold on the new provider: 0 stale, 0 unsupported, 0 personal-data leaks, 0 errors, 0 fallbacks to
the router. Misses: Q045 (handoff) and Q078 (bridges, "Omar Fenwick is the only person linking Growth and Support").
Literal-rule correct 89.0% (was 79.3%), sentence-only correct 95.1% (was 90.2%).
