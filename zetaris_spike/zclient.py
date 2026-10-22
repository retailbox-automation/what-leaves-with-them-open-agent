"""Minimal Zetaris client over the UI proxy (:3000), signing in as the local admin.

Credentials come from the environment (ZETARIS_USERNAME / ZETARIS_PASSWORD) or, when
ZETARIS_BUNDLE_ENV points at the platform bundle's .env, from its ZETARIS_ADMIN_* lines.
They are never printed. No API key is created or used: the organisation id comes back
with the login, as in the bundle's verify.sh.
"""
import json
import os
import re
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = os.environ.get("ZETARIS_BASE_URL", "http://localhost:3000").rstrip("/")


def _credentials():
    user, pw = os.environ.get("ZETARIS_USERNAME"), os.environ.get("ZETARIS_PASSWORD")
    bundle = os.environ.get("ZETARIS_BUNDLE_ENV")
    if (not user or not pw) and bundle:
        env = dict(re.findall(r'^(ZETARIS_ADMIN_\w+)=["\']?(.*?)["\']?\s*$', open(bundle).read(), re.M))
        user, pw = env.get("ZETARIS_ADMIN_EMAIL"), env.get("ZETARIS_ADMIN_PASSWORD")
    if not user or not pw:
        raise RuntimeError("set ZETARIS_USERNAME/ZETARIS_PASSWORD or ZETARIS_BUNDLE_ENV")
    return user, pw


def _call(path, body=None, headers=None, timeout=300):
    h = {"Content-Type": "application/json", "X-Request-ID": str(uuid.uuid4()), **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    req = Request(BASE + path, data=data, headers=h, method="POST" if data else "GET")
    try:
        with urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except HTTPError as e:
        raise RuntimeError(f"{path}: HTTP {e.code} {e.read(600).decode('utf-8', 'replace')}") from None


class Zetaris:
    def __init__(self):
        user, pw = _credentials()
        login = _call("/api/auth/login", {"username": user, "password": pw}) or {}
        text = json.dumps(login)
        token = re.search(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", text)
        org = os.environ.get("ZETARIS_ORG_ID") or (re.search(r'"orgId"\s*:\s*"?(\d+)', text) or [None, None])[1]
        if not token or not org:
            raise RuntimeError("login returned no token or orgId")
        self.headers = {"Authorization": "Bearer " + token.group(0), "X-Org-ID": str(org)}
        self.org_id = str(org)

    def sql(self, statement, limit=1000):
        """Run one Lightning SQL statement; returns (rows, seconds, raw response)."""
        t0 = time.perf_counter()
        out = _call("/api/proxy/query/sql/start", {"select": statement, "pageLimit": limit}, self.headers)
        return rows_of(out), time.perf_counter() - t0, out

    def run(self, statement, limit=1000):
        """Run any statement (DDL, SHOW, SELECT) through the SQL editor route."""
        t0 = time.perf_counter()
        out = _call("/api/proxy/sql-editor/sqls/run-query", {"sql": statement, "limit": limit}, self.headers)
        return rows_of(out), time.perf_counter() - t0, out


def rows_of(out):
    """Find the list of row dicts in a query response (shape checked live 22.10.2026)."""
    if isinstance(out, list):
        return out if all(isinstance(r, dict) for r in out) else []
    if isinstance(out, dict) and isinstance(out.get("headers"), list) and isinstance(out.get("data"), list):
        return [dict(zip(out["headers"], row)) for row in out["data"]]
    if isinstance(out, dict):
        for value in out.values():
            rows = rows_of(value)
            if rows:
                return rows
    return []
