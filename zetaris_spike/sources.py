"""Split the synthetic corpus into three source systems for Zetaris.

chat    -> Postgres table chat (loaded with psql COPY inside the wlwt-chat-pg container)
tickets -> CSV file (copied into the Zetaris customer-data volume)
docs    -> JSON object {"docs": [...]} served by docs_api.py

Usage: python3 -m zetaris_spike.sources <episodes.jsonl> <out_dir>
"""
import csv
import json
import sys
from pathlib import Path

COLUMNS = ["id", "ts", "author", "title", "text", "refs"]


def split(episodes):
    out = {"chat": [], "ticket": [], "doc": []}
    for row in episodes:
        rec = {c: row.get(c) or "" for c in COLUMNS}
        rec["refs"] = " ".join(row.get("refs") or [])
        out[row["channel"]].append(rec)
    return out


def main(src, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    parts = split(json.loads(line) for line in open(src, encoding="utf-8"))

    with open(out / "chat.tsv", "w", encoding="utf-8") as f:
        for r in parts["chat"]:
            f.write("\t".join(r[c].replace("\\", "\\\\").replace("\t", " ").replace("\n", "\\n") for c in COLUMNS) + "\n")
    (out / "chat.sql").write_text(
        "DROP TABLE IF EXISTS chat;\n"
        "CREATE TABLE chat (id text PRIMARY KEY, ts text, author text, title text, text text, refs text);\n"
        "\\copy chat FROM '/load/chat.tsv'\n",
        encoding="utf-8",
    )

    with open(out / "tickets.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in parts["ticket"]:
            w.writerow({**r, "text": r["text"].replace("\n", " ")})

    (out / "docs.json").write_text(json.dumps({"docs": parts["doc"]}, ensure_ascii=False), encoding="utf-8")
    print({k: len(v) for k, v in parts.items()})


if __name__ == "__main__":
    main(*sys.argv[1:3])
