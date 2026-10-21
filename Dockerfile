# Demo image: the HTTP API (honest_memory/api.py) over the synthetic corpus, seed 42. See docs/DEPLOY.md.
# Base from the ECR Public mirror of the official image (no Docker Hub rate limit on shared builders).
FROM public.ecr.aws/docker/library/python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HM_WEB_DIR=/app/web
WORKDIR /app

# Dependencies first, with the exact versions pinned in pyproject.toml (core + the api extra).
COPY pyproject.toml ./
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; \
print('\n'.join(p['dependencies'] + p['optional-dependencies']['api']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

COPY . .
# The corpus is generated at build time (deterministic, no network); the panel is served from web/ when the
# repo has one, otherwise a short index page lists the endpoints.
RUN python -m honest_memory.corpus --seed 42 --out data/corpus \
 && if [ ! -d web ]; then mkdir web && cp deploy/index.html web/index.html; fi \
 && groupadd --gid 10001 hm \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin hm

EXPOSE 8080
CMD ["sh", "deploy/start.sh"]
