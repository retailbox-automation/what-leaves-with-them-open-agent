#!/bin/sh
# Container entry point for the demo: drop root, load the demo tenant if needed, serve the HTTP API.
# Env: FALKORDB_* (connection), HM_DEMO_TENANT (default quillfield), PORT (default 8080),
# HM_PUBLIC_WRITES=1 to accept writes without a token (default: writes are locked, see docs/DEPLOY.md).
set -eu

# The platform may ignore the Dockerfile USER line, so the switch happens here.
if [ "$(id -u)" = "0" ]; then
    exec setpriv --reuid=10001 --regid=10001 --clear-groups sh "$0" "$@"
fi

cd /app
if [ -f deploy/COMMIT ]; then
    echo "start: code from commit $(cat deploy/COMMIT)"
fi

python deploy/bootstrap.py --corpus data/corpus/42 --tenant "${HM_DEMO_TENANT:-quillfield}"

# Public demo: writes need a token nobody holds, unless explicitly opened or a token is configured.
if [ -z "${HM_WRITE_TOKEN:-}" ] && [ "${HM_PUBLIC_WRITES:-0}" != "1" ]; then
    HM_WRITE_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    export HM_WRITE_TOKEN
    echo "start: writes are locked (HM_PUBLIC_WRITES is not 1)"
fi

exec python -m uvicorn honest_memory.api:app --host 0.0.0.0 --port "${PORT:-8080}"
