"""Stop-gate spike (Open Agent Hackathon 2026, 22.10): Zetaris answers over our three sources,
cites them by id, and a Verifier re-reads each cited quote by id through Zetaris.

Sources (registered beforehand, see README.md in this folder):
  WLWT_CHAT.chat          Postgres over JDBC   (225 chat messages)
  wlwt_tickets.tickets    CSV file             (127 tickets)
  wlwt_docs.docs_raw      our REST JSON API    (51 docs, exploded)

Usage: python3 -m zetaris_spike.gate <episodes.jsonl> <evidence_dir> [topic]
Writes <evidence_dir>/gate-<UTC>.json and prints a summary. Exit 0 only if every check passes.
"""
import json
import statistics
import sys
import time
from pathlib import Path

from .zclient import Zetaris

SOURCES = {
    "chat": "SELECT id, author, ts, text, refs FROM WLWT_CHAT.chat",
    "ticket": "SELECT id, author, ts, text, refs FROM wlwt_tickets.tickets",
    "doc": "SELECT d.id AS id, d.author AS author, d.ts AS ts, d.text AS text, d.refs AS refs "
           "FROM wlwt_docs.docs_raw LATERAL VIEW explode(docs) x AS d",
}


def q(s):
    return "'" + s.replace("'", "''") + "'"


def by_id(source, record_id):
    return f"SELECT * FROM ({SOURCES[source]}) s WHERE s.id = {q(record_id)}"


def quote_of(text, topic):
    """The sentence of the source text that mentions the topic: what an agent would cite."""
    for sentence in text.replace("\n", " ").split(". "):
        if topic in sentence:
            return sentence.strip()[:200]
    return text[:120]


def tamper(quote):
    words = quote.split(" ")
    i = len(words) // 2
    words[i] = words[i] + "X"
    return " ".join(words)


def main(episodes_path, evidence_dir, topic="fernhill-sso"):
    corpus = {}
    for line in open(episodes_path, encoding="utf-8"):
        r = json.loads(line)
        corpus[r["id"]] = r
    z = Zetaris()
    checks, log = {}, []

    def sql(statement, kind="select"):
        rows, secs, _ = (z.sql if kind == "select" else z.run)(statement)
        log.append({"sql": statement, "seconds": round(secs, 3), "rows": len(rows)})
        return rows, secs

    version, _ = sql("SHOW SERVER VERSION", "run")
    checks["server_version"] = version[0]["version"] if version else None

    # 1. Each source answers, and its row count matches the corpus file (independent count).
    expected = {ch: sum(1 for r in corpus.values() if r["channel"] == ch) for ch in SOURCES}
    counts = {ch: int(sql(f"SELECT count(*) AS n FROM ({s}) s")[0][0]["n"]) for ch, s in SOURCES.items()}
    checks["counts"] = {"zetaris": counts, "corpus_file": expected, "ok": counts == expected}

    # 2. One federated question over all three sources: who and what touched <topic>?
    union = " UNION ALL ".join(
        f"SELECT '{ch}' AS source, s.id, s.author, s.ts, s.text FROM ({s}) s WHERE s.text LIKE {q('%' + topic + '%')}"
        for ch, s in SOURCES.items())
    hits, secs = sql(f"SELECT * FROM ({union}) u ORDER BY u.ts")
    want = sorted(r["id"] for r in corpus.values() if topic in r["text"])
    checks["federated_answer"] = {
        "topic": topic, "seconds": round(secs, 3), "ids": [h["id"] for h in hits],
        "by_source": {ch: sum(h["source"] == ch for h in hits) for ch in SOURCES},
        "ok": sorted(h["id"] for h in hits) == want and len({h["source"] for h in hits}) == 3,
    }

    # 3. Cross-source JOIN: chat messages that reference a ticket or a doc, with the referenced title line.
    join = ("SELECT c.id AS chat_id, c.refs AS ref, t.id AS ticket_id, d.id AS doc_id "
            f"FROM ({SOURCES['chat']}) c "
            f"LEFT JOIN ({SOURCES['ticket']}) t ON c.refs = t.id "
            f"LEFT JOIN ({SOURCES['doc']}) d ON c.refs = d.id "
            "WHERE c.refs <> '' AND (t.id IS NOT NULL OR d.id IS NOT NULL)")
    joined, secs = sql(join)
    want_join = sum(1 for r in corpus.values() if r["channel"] == "chat" and len(r["refs"]) == 1
                    and r["refs"][0] in corpus and corpus[r["refs"][0]]["channel"] in ("ticket", "doc"))
    checks["federated_join"] = {"rows": len(joined), "expected": want_join, "seconds": round(secs, 3),
                                "sample": joined[:3], "ok": len(joined) == want_join}

    # 4. Verifier: re-read every cited record by id through Zetaris and check the quote is really there.
    verified, controls = [], []
    for h in hits:
        quote = quote_of(corpus[h["id"]]["text"], topic)
        rows, secs = sql(by_id(h["source"], h["id"]))
        verified.append({"source": h["source"], "id": h["id"], "quote": quote, "seconds": round(secs, 3),
                         "found": bool(rows) and quote in rows[0]["text"].replace("\n", " ")})
        bad = tamper(quote)
        controls.append({"id": h["id"], "tampered_quote_found": bool(rows) and bad in rows[0]["text"].replace("\n", " ")})
    ghost, _ = sql(by_id("doc", "ADR-9999"))
    checks["verifier"] = {
        "cited": len(verified), "verified": sum(v["found"] for v in verified), "items": verified,
        "control_tampered_rejected": sum(not c["tampered_quote_found"] for c in controls),
        "control_unknown_id_rows": len(ghost),
        "ok": all(v["found"] for v in verified) and not any(c["tampered_quote_found"] for c in controls) and not ghost,
    }

    # 5. Point-read latency per source (20 reads each), what the Verifier pays per citation.
    lat = {}
    for ch in SOURCES:
        ids = [i for i, r in corpus.items() if r["channel"] == ch][:20]
        times = [sql(by_id(ch, i))[1] for i in ids]
        lat[ch] = {"n": len(times), "p50": round(statistics.median(times), 3),
                   "max": round(max(times), 3)}
    checks["point_read_latency_s"] = lat

    ok = all(v.get("ok", True) for v in checks.values() if isinstance(v, dict))
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = Path(evidence_dir) / f"gate-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"utc": stamp, "pass": ok, "checks": checks, "sql_log": log},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    for name, v in checks.items():
        print(name, "->", json.dumps(v if name != "verifier" else {k: v[k] for k in v if k != "items"},
                                      ensure_ascii=False)[:400])
    print("PASS" if ok else "FAIL", out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
