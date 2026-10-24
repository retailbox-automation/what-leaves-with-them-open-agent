# Fresh-clone test, the way a judge runs it

Run on 2026-10-24 (times UTC from `date -u`) on a Mac Studio (Apple M5 Max, Docker Desktop, Compose v5.6.1),
load average about 7 at the start. Commit tested: `be8a71b` on branch `oa/lane-d` (cloned into a new empty folder;
no `.env` in the clone, nothing copied in by hand). Result: **PASS**. Repeated on the final commit of this lane, `8fb046a`, after the bind-host change: **PASS** (section at the end).

Ports 8000 and 18000 were already taken by other programs on this Mac, so the host port was moved with the
documented variable `APP_HOST_PORT` (default 8000). Nothing else was set.

## Commands and what came back

```bash
git clone --branch oa/lane-d <repo> fresh1 && cd fresh1
test ! -e .env && echo "no .env"                         # -> no .env

export APP_HOST_PORT=28000 FALKORDB_HOST_PORT=16379       # only because 8000 was busy here
docker compose -p laned-fresh build                       # 12:50:35Z -> 12:50:49Z, 14.8 s wall
docker run --rm what-leaves-open-agent:local node --version   # -> v22.23.3 (for the H-MEM sidecar)
docker compose -p laned-fresh up -d                       # 12:52:36Z; /health answered by 12:52:39Z
curl -s localhost:28000/health
# {"status":"ok","falkordb":{"ping":true,"version":"6.0.2"},"tools":21,"write_gate":true,"llm":{"configured":false,"model":null}}
```

The 14.8 s build had the two base images (python:3.12-slim and node:22.23.3-bookworm-slim, both pinned by
digest) already in the local Docker cache; a machine without them also downloads them (not measured here).
Image size: 439 MB.

Start-up log of the app container: the panel's tenant was built through the write gate on first start
(`tenant quillfield: 403 episodes, 226 gate decisions in 0.92 s`; ACCEPT 108, CONFIRM 110, SUPERSEDE 1, CLOSE 3,
REJECT 4).

## POST /run on every input example (SAMPLE_MODE, no credentials)

```bash
for f in input_examples/*.json; do
  curl -s -X POST localhost:28000/run -H 'content-type: application/json' -d @$f
done
```

| Input | HTTP status field | Trace id | Result | Rounds | Wall time | Citations kept | Dropped | Written by the gate |
|---|---|---|---|---|---|---|---|---|
| 01_current_owner | success | 20261024T125239Z-c773 | verified | 1 | 1.54 s (first run builds the graph from the 3 tables) | ADR-0009, msg-0148 | none | yes |
| 02_tampered_source | success | 20261024T125241Z-026d | verified | 2 | 0.45 s | ADR-0009 | msg-0148 (quote no longer in the record at its source; tamper restored: true) | yes |
| 03_not_in_these_systems | success | 20261024T125241Z-5e3d | absence_confirmed | 1 | 0.40 s | none | none | no ("keeps a finding only with a verified source") |
| 04_decision_superseded | success | 20261024T125242Z-1588 | no_claim | 1 | 0.39 s | none | none | no (the router has no rule for this wording without a model; documented) |

The organizer's request shape works too:

```bash
curl -s -X POST localhost:28000/run -H 'content-type: application/json' \
  -d '{"run_id":"demo-001","input":{"query":"Who owns oncall-runbook now?"},"options":{"sample_mode":true}}'
# run_id demo-001, status success, verified in 1 round, citations RB-10 and DOC-16, 0.50 s
```

That question is not one of the examples: the answer and its citations come from the loop, not from a stored file.
The four outputs differ and each names its own trace.

Other checks in the same session:

- `GET /` (the panel): 200.
- `GET /run/20261024T125241Z-026d/trace`: 24 JSONL lines (the run's trace).
- In the container: `python -m open_agent.run --sample --input input_examples/02_tampered_source.json` ->
  verified, 2 rounds, `logs/trace-20261024T125254Z-c25c.jsonl`, 0.06 s.
- H-MEM state written in the container: `.state/hmem/<tenant>/state.json` and `ledger.jsonl` (owned by the
  non-root user `hm`).
- Bad input `{"input":{}}` -> 422 with `give a question: ...` (no stack trace).
- Live mode without credentials (`"options":{"sample_mode":false}`) -> `status: error`,
  `SourceUnavailable: set ZETARIS_USERNAME/ZETARIS_PASSWORD or ZETARIS_BUNDLE_ENV`, with the hint to use sample
  mode, in 1.04 s. No crash, no hang.

## Second run: commit 8fb046a (serve.py binds 127.0.0.1 unless HOST is set; compose sets HOST=0.0.0.0 inside the container)

Fresh clone into another empty folder, no `.env`, same two port variables, project name `laned-fresh2`.

- Build 13:05:50Z to 13:05:52Z (base layers and dependency layers cached); `serve.py` in the image has `bind_host`.
- `up -d` 13:06:02Z; `/health` ok by 13:06:08Z. App log: `serve: SAMPLE_MODE=true; POST /run and the panel on 0.0.0.0:8000` (inside the container).
- Host listeners: `127.0.0.1:28000` and `127.0.0.1:16379` only (`lsof -nP -iTCP -sTCP:LISTEN`).

| Input | Status | Trace id | Result | Rounds | Wall time | Kept | Dropped | Stored |
|---|---|---|---|---|---|---|---|---|
| 01_current_owner | success | 20261024T130608Z-3e79 | verified | 1 | 1.11 s | ADR-0009, msg-0148 | none | yes |
| 02_tampered_source | success | 20261024T130610Z-9538 | verified | 2 | 0.41 s | ADR-0009 | msg-0148 (tamper restored) | yes |
| 03_not_in_these_systems | success | 20261024T130610Z-7d0b | absence_confirmed | 1 | 0.37 s | none | none | no |
| 04_decision_superseded | success | 20261024T130610Z-0bc3 | no_claim | 1 | 0.37 s | none | none | no |
| organizer shape, "Who decided ADR-0014?" | success | 20261024T130611Z-890c | verified | 1 | 0.45 s | ADR-0014 | none | yes |

Also: panel `GET /` 200; in the panel (headless browser) the oncall-runbook answer card showed "Verified at the
source: Who owns oncall-runbook now?" with RB-10 and DOC-16, docs REST API, round 1, quote found; CLI in the
container `python -m open_agent.run --sample --input input_examples/03_not_in_these_systems.json` ->
absence_confirmed, 0.01 s.

## Clean-up

The containers, networks and the image these tests created (`laned-fresh-*`, `laned-fresh2-*`,
`what-leaves-open-agent:local`) were removed at the end of the session; the base images were left in the cache.
