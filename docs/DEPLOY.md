# Deploy

The public demo is two containers in one Zeabur project on a dedicated server:

| Service | What | Limits |
|---|---|---|
| `falkordb` | `falkordb/falkordb:v4.22.0` (the FalkorDB Cloud free version), from `deploy/zeabur-falkordb.yaml`; internal network only, no domain, port forwarding off; volume on `/var/lib/falkordb/data`, AOF every second, `maxmemory 512mb` with `noeviction` | 500m CPU, 768 MB |
| `api` | this repo's `Dockerfile`: `honest_memory/api.py` under uvicorn, built from GitHub `main` | 500m CPU, 512 MB |

Nothing secret is stored in the repo or as a literal in the service environment. The FalkorDB password is the Zeabur-generated `${PASSWORD}` of the `falkordb` service, exposed to the project as `FALKORDB_PASSWORD`.

## Environment (names only)

| Service | Variable | Meaning |
|---|---|---|
| falkordb | `FALKORDB_PASSWORD` = `${PASSWORD}`, exposed | `requirepass` of the server |
| falkordb | `FALKORDB_HOST` = `${CONTAINER_HOSTNAME}`, `FALKORDB_PORT` = `6379`, exposed | where the API connects |
| falkordb | `BROWSER` = `0`, `FALKORDB_ARGS` | bundled browser UI off; two query threads, other arguments as in the image |
| api | `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_PASSWORD` | from the `falkordb` service |
| api | `HM_RATE_LIMIT_PER_MIN` = `600`, `HM_TRUST_PROXY` = `1` | per client IP behind the platform proxy; a panel of about 12 people behind one NAT stays well under it |
| api | `HM_PUBLIC_WRITES` (unset), `HM_WRITE_TOKEN` (unset) | writes are locked: with neither set, `deploy/start.sh` sets a random write token that nobody holds, so every write returns 401 |
| api | `HM_REINGEST`, `HM_DEMO_TENANT` (optional) | `1` forces a fresh ingest on the next start; tenant name, default `quillfield` |

No LLM is configured on the demo (`HM_LLM_MODEL` unset), so `/v1/{tenant}/ask` uses the rule router.

## Data

The image generates the synthetic corpus at build time (`python -m honest_memory.corpus --seed 42`, no network). On every start `deploy/bootstrap.py` waits for FalkorDB and replays the corpus into tenant `quillfield` unless the graph already holds this corpus ingested by this code (a fingerprint of `episodes.jsonl` and the package source, kept in the key `hm:deploy:quillfield:fingerprint`). A restart reuses the stored graph; a deploy with new code re-ingests (about 1 s for 382 episodes). The FalkorDB volume and AOF keep the graph across FalkorDB restarts.

The root path serves `web/` when the repo has one; otherwise the image copies `deploy/index.html`, a short page that links the endpoints.

## Run locally

```sh
export FALKORDB_PASSWORD="$(openssl rand -hex 16)"   # throwaway, this shell only
docker network create hm-net
docker run -d --name hm-falkordb --network hm-net -e FALKORDB_PASSWORD -e BROWSER=0 \
  --entrypoint /bin/sh falkordb/falkordb:v4.22.0 \
  -c 'export REDIS_ARGS="--requirepass $FALKORDB_PASSWORD --appendonly yes"; exec /var/lib/falkordb/bin/run.sh'
docker build -t what-leaves-api .
docker run -d --name hm-api --network hm-net -p 127.0.0.1:8080:8080 \
  -e FALKORDB_HOST=hm-falkordb -e FALKORDB_PASSWORD what-leaves-api
curl -s -X POST localhost:8080/v1/quillfield/ask -H 'content-type: application/json' \
  -d '{"question": "Who owns billing-api?"}'
```

## Redeploy from main

```sh
HM_ZEABUR_API_SERVICE_ID=<api service id> HM_ZEABUR_ENV_ID=<environment id> sh deploy/redeploy.sh
```

It clones `main` fresh, writes the commit into `deploy/COMMIT`, uploads it to the `api` service, waits for the new deployment to reach RUNNING, then checks that the runtime log shows `start: code from commit <sha>` and that `/health` on the public URL is ok.
