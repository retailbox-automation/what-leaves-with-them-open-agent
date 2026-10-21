"""Make sure the demo tenant is loaded before the API starts.

Waits for FalkorDB, then replays the synthetic corpus into the tenant graph unless that graph already holds
this corpus ingested by this code: a fingerprint of episodes.jsonl and the package source is kept in a plain
key next to the graph. A restart with the same code reuses the stored graph; a deploy with new code
re-ingests from scratch. Safe to run on every start.

    python deploy/bootstrap.py --corpus data/corpus/42 --tenant quillfield

Env: FALKORDB_* (see honest_memory.store.connect); HM_REINGEST=1 forces a fresh ingest;
HM_BOOTSTRAP_WAIT seconds to wait for FalkorDB (default 180).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # the source tree, not an installed copy

from honest_memory.ingest import format_summary, ingest_corpus, read_episodes  # noqa: E402
from honest_memory.schema import graph_name  # noqa: E402
from honest_memory.store import Store, connect  # noqa: E402


def fingerprint(corpus_dir: Path) -> str:
    """Changes when the corpus or any module of the package changes."""
    h = hashlib.sha256()
    files = [corpus_dir / "episodes.jsonl", *sorted((ROOT / "honest_memory").rglob("*.py"))]
    for path in files:
        h.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def wait_for_falkordb(timeout: float):
    deadline = time.monotonic() + timeout
    while True:
        try:
            db = connect()
            db.connection.ping()
            return db
        except Exception as exc:  # only the class is printed: messages may echo connection details
            if time.monotonic() > deadline:
                raise SystemExit(f"bootstrap: FalkorDB not reachable after {timeout:.0f} s ({type(exc).__name__})")
            print(f"bootstrap: waiting for FalkorDB ({type(exc).__name__})", flush=True)
            time.sleep(3)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python deploy/bootstrap.py", description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", required=True, help="directory with episodes.jsonl, e.g. data/corpus/42")
    ap.add_argument("--tenant", required=True)
    args = ap.parse_args(argv)

    corpus = Path(args.corpus).resolve()
    fp = fingerprint(corpus)
    key = f"hm:deploy:{args.tenant}:fingerprint"
    db = wait_for_falkordb(float(os.environ.get("HM_BOOTSTRAP_WAIT", "180")))

    stored = db.connection.get(key)
    stored = stored.decode() if isinstance(stored, bytes) else stored
    present = graph_name(args.tenant) in db.list_graphs()
    if present and stored == fp and os.environ.get("HM_REINGEST") != "1":
        print(f"bootstrap: tenant {args.tenant} is up to date (fingerprint {fp}), no ingest", flush=True)
        return 0

    why = "forced" if os.environ.get("HM_REINGEST") == "1" else ("new code or corpus" if present else "no graph yet")
    print(f"bootstrap: ingesting {corpus.name} into tenant {args.tenant} ({why}, fingerprint {fp})", flush=True)
    store = Store(db, args.tenant)
    summary = ingest_corpus(store, corpus, reset=True)
    expected = len(read_episodes(corpus))
    stored_episodes = store.ro("MATCH (e:Episode) RETURN count(e)")[0][0]
    if stored_episodes != expected:
        raise SystemExit(f"bootstrap: {stored_episodes} episodes in the graph, expected {expected}")
    db.connection.set(key, fp)
    print(format_summary(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
