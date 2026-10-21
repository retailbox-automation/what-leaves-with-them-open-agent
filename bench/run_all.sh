#!/usr/bin/env bash
# Every result set under bench/results, in the order they were produced. About 15 minutes on a laptop.
#   PORT_422=6393 PORT_602=6390 bash bench/run_all.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
P422=${PORT_422:-6393}
P602=${PORT_602:-6390}

$PY -m bench.run --port "$P422"                                                                           # RESULTS.md
$PY -m bench.run --systems honest-router,honest-agent --port "$P602" --out bench/results/falkordb-6.0.2   # other engine
$PY -m bench.run --systems plain-rag --k 16 --out bench/results/plain-rag-k16                              # stronger baselines
$PY -m bench.run --systems plain-rag --chunk lines --out bench/results/plain-rag-lines-k8
$PY -m bench.run --systems plain-rag --chunk lines --k 16 --out bench/results/plain-rag-lines-k16
$PY -m bench.run --systems honest-agent --reasoning default --port "$P422" --out bench/results/agent-thinking-default
$PY -m bench.report                                                                                       # SENSITIVITY.md
