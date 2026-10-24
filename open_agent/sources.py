"""The three source systems, read with one set of SQL statements through one of two endpoints.

zetaris       the local Zetaris (Lightning SQL over the UI proxy, zetaris_spike/zclient.py): Postgres chat over
              JDBC, a tickets CSV and a REST docs API, registered by zetaris_spike/register.py.
local-sample  SAMPLE_MODE: the same three tables loaded from the files in sample_data/sources/ into an in-process
              SQLite. The statements are the same text; one rewrite is applied, because SQLite has no
              `LATERAL VIEW explode(...)`: the docs table is stored already exploded, one row per doc.

Every statement has a timeout; the Zetaris endpoint retries once and then raises SourceUnavailable, which the
callers turn into an explicit "unreadable" result instead of a crash.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import subprocess
import time
from pathlib import Path

CHANNELS = ("chat", "ticket", "doc")
SYSTEM_OF = {"chat": "Postgres chat (WLWT_CHAT.chat)", "ticket": "tickets CSV (wlwt_tickets.tickets)",
             "doc": "docs REST API (wlwt_docs.docs_raw)"}
INGEST = {
    "chat": "SELECT id, ts, author, title, text, refs FROM WLWT_CHAT.chat",
    "ticket": "SELECT id, ts, author, title, text, refs FROM wlwt_tickets.tickets",
    "doc": "SELECT d.id AS id, d.ts AS ts, d.author AS author, d.title AS title, d.text AS text, d.refs AS refs "
           "FROM wlwt_docs.docs_raw LATERAL VIEW explode(docs) x AS d",
}
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "sample_data" / "sources"


class SourceUnavailable(RuntimeError):
    """The endpoint did not answer (timeout, refused, HTTP error) after its retries."""


def q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def by_id(channel: str, record_id: str) -> str:
    """Re-read one record by id from the table of its source system."""
    return f"SELECT * FROM ({INGEST[channel]}) s WHERE s.id = {q(record_id)}"


def mentions(term: str) -> str:
    """Every record in the three systems whose text contains `term` (case-sensitive in both engines)."""
    return " UNION ALL ".join(f"SELECT '{ch}' AS source, s.id AS id FROM ({INGEST[ch]}) s WHERE instr(s.text, {q(term)}) > 0"
                              for ch in CHANNELS)


_SPARK_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(?:\.\d+)?$")


def norm_ts(value) -> str:
    """'2026-08-17 16:43:00.0' (the CSV column Zetaris inferred as a timestamp, shown in UTC) -> ISO with Z."""
    text = str(value or "")
    m = _SPARK_TS.match(text)
    return f"{m[1]}T{m[2]}Z" if m else text


def episode(row: dict, channel: str) -> dict:
    """One source row as the episode honest_memory ingests (the shape of data/corpus/*/episodes.jsonl)."""
    return {"id": row["id"], "ts": norm_ts(row.get("ts")), "channel": channel, "author": row.get("author") or "",
            "title": row.get("title") or "", "text": row.get("text") or "", "refs": (row.get("refs") or "").split()}


class ZetarisSource:
    endpoint = "zetaris"

    def __init__(self, timeout: float = 30.0, retries: int = 1, chat_container: str = "wlwt-chat-pg"):
        self.timeout, self.retries, self.chat_container = timeout, retries, chat_container
        self._z = None

    def sql(self, statement: str) -> tuple[list[dict], float]:
        from zetaris_spike.zclient import Zetaris

        last = None
        for attempt in range(self.retries + 1):
            try:
                if self._z is None:
                    self._z = Zetaris(timeout=self.timeout)
                rows, secs, _ = self._z.sql(statement)
                return rows, secs
            except (RuntimeError, OSError) as exc:  # HTTP errors, URLError, socket timeouts
                last, self._z = exc, None  # sign in again on the retry
                if attempt < self.retries:
                    time.sleep(1.0)
        raise SourceUnavailable(f"zetaris: {type(last).__name__}: {str(last)[:200]}")

    # Tamper writes to the source system itself (Postgres), not through Zetaris, which only reads.
    def _psql(self, script: str, **variables) -> str:
        cmd = ["docker", "exec", "-i", self.chat_container, "psql", "-U", "wlwt", "-d", "wlwt", "-tA",
               "-v", "ON_ERROR_STOP=1"]
        for k, v in variables.items():
            cmd += ["-v", f"{k}={v}"]
        try:
            out = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=30, check=True)
        except (subprocess.SubprocessError, OSError) as exc:
            raise SourceUnavailable(f"postgres: {type(exc).__name__}: {str(getattr(exc, 'stderr', '') or exc)[:200]}")
        return out.stdout

    def chat_text(self, record_id: str) -> str | None:
        out = self._psql("SELECT count(*) || ':' || coalesce(max(text), '') FROM chat WHERE id = :'id';", id=record_id)
        n, _, text = out.rstrip("\n").partition(":")
        return text if n == "1" else None

    def set_chat_text(self, record_id: str, text: str) -> str:
        self._psql("UPDATE chat SET text = :'txt' WHERE id = :'id';", id=record_id, txt=text)
        return "UPDATE chat SET text = :'txt' WHERE id = :'id'  (psql in container %s)" % self.chat_container


_EXPLODE = re.compile(r"(\w+\.\w+) LATERAL VIEW explode\(\w+\) \w+ AS (\w+)")


def sqlite_text(statement: str) -> str:
    """The one rewrite SAMPLE_MODE needs: `t LATERAL VIEW explode(docs) x AS d` -> `t d` (docs stored exploded)."""
    return _EXPLODE.sub(r"\1 \2", statement)


def _unescape_copy(value: str) -> str:
    """Postgres COPY text format, as zetaris_spike/sources.py writes chat.tsv."""
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), value)


class LocalSample:
    """SAMPLE_MODE endpoint: the three tables from sample_data/sources/ in an in-process SQLite, schema-qualified
    like the Zetaris objects (WLWT_CHAT.chat, wlwt_tickets.tickets, wlwt_docs.docs_raw)."""
    endpoint = "local-sample"

    def __init__(self, folder: Path | str = SAMPLE_DIR):
        folder = Path(folder)
        self.db = sqlite3.connect(":memory:")
        cols = "id TEXT PRIMARY KEY, ts TEXT, author TEXT, title TEXT, text TEXT, refs TEXT"
        for schema, table in (("WLWT_CHAT", "chat"), ("wlwt_tickets", "tickets"), ("wlwt_docs", "docs_raw")):
            self.db.execute(f"ATTACH DATABASE ':memory:' AS {schema}")
            self.db.execute(f"CREATE TABLE {schema}.{table} ({cols})")
        names = ("id", "ts", "author", "title", "text", "refs")
        with open(folder / "chat.tsv", encoding="utf-8") as f:
            chat = [[_unescape_copy(v) for v in line.rstrip("\n").split("\t")] for line in f if line.strip()]
        with open(folder / "tickets.csv", encoding="utf-8", newline="") as f:
            tickets = [[r[c] for c in names] for r in csv.DictReader(f)]
        docs = [[d.get(c, "") for c in names] for d in json.load(open(folder / "docs.json", encoding="utf-8"))["docs"]]
        for table, rows in (("WLWT_CHAT.chat", chat), ("wlwt_tickets.tickets", tickets), ("wlwt_docs.docs_raw", docs)):
            self.db.executemany(f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?)", rows)

    def sql(self, statement: str) -> tuple[list[dict], float]:
        t0 = time.perf_counter()
        try:
            cur = self.db.execute(sqlite_text(statement))
        except sqlite3.Error as exc:
            raise SourceUnavailable(f"local-sample: {exc}") from exc
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return rows, time.perf_counter() - t0

    def chat_text(self, record_id: str) -> str | None:
        row = self.db.execute("SELECT text FROM WLWT_CHAT.chat WHERE id = ?", (record_id,)).fetchone()
        return row[0] if row else None

    def set_chat_text(self, record_id: str, text: str) -> str:
        self.db.execute("UPDATE WLWT_CHAT.chat SET text = ? WHERE id = ?", (text, record_id))
        return "UPDATE WLWT_CHAT.chat SET text = ? WHERE id = ?  (local SQLite copy)"


def read_all(source, trace) -> list[dict]:
    """One SELECT per source system; every row becomes an episode. Raises SourceUnavailable."""
    episodes = []
    for channel in CHANNELS:
        statement = INGEST[channel]
        try:
            rows, secs = source.sql(statement)
        except SourceUnavailable as exc:
            trace.event("Ingest", "select", status="error", endpoint=source.endpoint, source=SYSTEM_OF[channel],
                        sql=statement, error=str(exc))
            raise
        trace.event("Ingest", "select", endpoint=source.endpoint, source=SYSTEM_OF[channel], sql=statement,
                    rows=len(rows), seconds=round(secs, 3))
        episodes += [episode(r, channel) for r in rows]
    return episodes
