# Zetaris spike: three sources, one SQL endpoint, quotes re-read by id

Built 22 October 2026, inside the Open Agent Hackathon window. It answers one question before the
agents get written: can a local Zetaris serve the synthetic Quillfield Billing corpus from three
different systems, and can a Verifier re-read a cited quote by its id through Zetaris?

| Source | System | Zetaris object |
|---|---|---|
| 225 chat messages | Postgres 16 (container `wlwt-chat-pg`, 127.0.0.1:55432) over JDBC | `WLWT_CHAT.chat` |
| 127 tickets | CSV file in the driver's `customer-data` volume | `wlwt_tickets.tickets` |
| 51 docs | our REST JSON API (`docs_api.py`, 127.0.0.1:18090) | `wlwt_docs.docs_raw`, exploded with `LATERAL VIEW explode(docs)` |

The Zetaris containers reach the host as `host.docker.internal` (Docker Desktop on macOS). The
platform bundle is not modified.

## Run

```
python3 -m honest_memory.corpus --seed 42 --out data/corpus
python3 -m zetaris_spike.sources data/corpus/42/episodes.jsonl zetaris_spike/.work
docker run -d --name wlwt-chat-pg -e POSTGRES_USER=wlwt -e POSTGRES_DB=wlwt -e POSTGRES_PASSWORD=... \
  -p 127.0.0.1:55432:5432 -v "$PWD/zetaris_spike/.work:/load:ro" postgres:16-alpine
docker exec wlwt-chat-pg psql -U wlwt -d wlwt -f /load/chat.sql
python3 -m zetaris_spike.docs_api zetaris_spike/.work/docs.json 18090 &
docker exec zetaris-driver-1 mkdir -p /home/zetaris/customer-data/shared/wlwt
docker cp zetaris_spike/.work/tickets.csv zetaris-driver-1:/home/zetaris/customer-data/shared/wlwt/tickets.csv
export ZETARIS_BUNDLE_ENV=~/zetaris-openagent/zetaris-platform/.env   # local admin sign-in, never printed
CHATPG_PASSWORD=... python3 -m zetaris_spike.register
python3 -m zetaris_spike.gate data/corpus/42/episodes.jsonl zetaris_spike/evidence
```

`zclient.py` signs in through the UI proxy on :3000 as the local admin and takes the organisation id
from the login response, like the bundle's `verify.sh`. No Zetaris API key is created or used.
SELECTs go to `/api/proxy/query/sql/start`; DDL and SHOW go to `/api/proxy/sql-editor/sqls/run-query`
(the first route rejects anything that is not a SELECT).

## What the gate checks (`gate.py`)

1. Row counts through Zetaris match the corpus file, per source.
2. One federated question ("who and what touched fernhill-sso?") as a UNION over all three sources
   returns exactly the records that mention it, from all three sources.
3. A cross-source JOIN (chat message → the ticket or doc it references) returns the expected 45 rows.
4. Verifier: every cited record is re-read by id through Zetaris and the quoted sentence must be in
   it. Controls: the same quote with one word changed must not be found, and an unknown id must
   return no rows.
5. Point-read latency per source, 20 reads each.

## Result on 22.10.2026 (Zetaris 2.4.3.1, Apple M5 Max, Docker 7.75 GiB, amd64 images under emulation)

Two runs, both PASS: `evidence/gate-20261022T153154Z.json`, `evidence/gate-20261022T153232Z.json`.
Counts 225 / 127 / 51 match; fernhill-sso answer 8 records (2 chat, 3 tickets, 3 docs) in about 2 s;
JOIN 45 of 45; Verifier 8 of 8 quotes found, 8 of 8 tampered quotes rejected, unknown id 0 rows;
point read by id p50 0.10–0.21 s, max under 0.9 s.

Findings worth keeping:
- A CSV in `/home/zetaris/customer-data/shared/...` registers with a plain path in `PATH`; no S3 or
  MinIO needed (the getting-started docs only show `s3a://`).
- `REGISTER DATASOURCE TABLES` imports nothing from a non-`public` Postgres schema; keep tables in `public`.
- A JOIN of raw tables from three different sources worked directly; the USL workaround in the
  docs was not needed for this data.
- The REST table is fetched again on every query (the docs API log shows a GET per SQL); fine at
  51 docs, cache it before the benchmark.
