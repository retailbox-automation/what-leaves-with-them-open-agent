# Judge image: POST /run (the Investigator <-> Verifier loop, serve.py), the HTTP API and the panel on port 8000.
# SAMPLE_MODE=true by default: the three source tables come from sample_data/sources/, no Zetaris, no model, no key.
# Needs FalkorDB (the memory graph): docker-compose.yml starts both. No GPU, nothing installed at run time.

# Node for the Meterless H-MEM engine sidecar (vendor/hmem/dist/hmem.mjs needs Node >= 20; no npm install).
FROM public.ecr.aws/docker/library/node:22.23.3-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS node

# Base from the ECR Public mirror of the official image (no Docker Hub rate limit on shared builders).
FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HM_WEB_DIR=/app/web \
    HM_HMEM_DIR=/app/.state/hmem \
    SAMPLE_MODE=true \
    PORT=8000
WORKDIR /app

COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN node --version

# Dependencies first, with the exact versions pinned in pyproject.toml (core + the api and agent extras).
COPY pyproject.toml ./
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; \
print('\n'.join(p['dependencies'] + p['optional-dependencies']['api'] + p['optional-dependencies']['agent']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

COPY . .
# The synthetic company is generated at build time (deterministic, no network) for the panel's tenant.
# output_examples/ starts empty in the image (never read by a run); scripts/cross_session_proof.py writes its result there.
RUN python -m honest_memory.corpus --seed 42 --out data/corpus \
 && groupadd --gid 10001 hm \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin hm \
 && mkdir -p /app/logs /app/.state/hmem /app/output_examples \
 && chown -R hm:hm /app/logs /app/.state /app/output_examples

USER hm
EXPOSE 8000
CMD ["python", "serve.py"]
