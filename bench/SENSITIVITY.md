# Sensitivity runs

The same 82 questions (corpus generator 0.3.0) with the baseline made stronger, the other FalkorDB engine and the model's own thinking left on. Each row is a full run; the files are under `bench/results/<variant>/`. The rows labelled "the baseline in RESULTS.md" and "the agent in RESULTS.md" are the ones that file reports.

| run | correct % | stale % | unsupported % | no answer % | clean % | median ms | commit |
|---|---|---|---|---|---|---|---|
| plain RAG, whole episodes, k=8 (the baseline in RESULTS.md) | 46.3 | 4.9 | 0.0 | 36.6 | 46.3 | 513 | `d3b7579` |
| plain RAG, whole episodes, k=16 | 50.0 | 3.7 | 1.2 | 30.5 | 50.0 | 718 | `d3b7579` |
| plain RAG, one line per unit, k=8 | 48.8 | 2.4 | 0.0 | 22.0 | 48.8 | 436 | `d3b7579` |
| plain RAG, one line per unit, k=16 | 58.5 | 3.7 | 0.0 | 23.2 | 57.3 | 587 | `d3b7579` |
| honest-memory router, FalkorDB 4.22 | 98.8 | 0.0 | 0.0 | 0.0 | 98.8 | 3 | `d3b7579` |
| honest-memory router, FalkorDB 6.0.2 | 98.8 | 0.0 | 0.0 | 0.0 | 98.8 | 1 | `d3b7579` |
| honest-memory LLM agent, thinking off, FalkorDB 4.22 (the agent in RESULTS.md) | 90.2 | 0.0 | 0.0 | 0.0 | 90.2 | 750 | `d3b7579` |
| honest-memory LLM agent, thinking off, FalkorDB 6.0.2 | 90.2 | 1.2 | 0.0 | 0.0 | 89.0 | 729 | `d3b7579` |
| honest-memory LLM agent, the model's own thinking, FalkorDB 4.22 | 93.9 | 0.0 | 0.0 | 0.0 | 93.9 | 3,059 | `d3b7579` |
