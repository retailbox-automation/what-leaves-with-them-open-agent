"""Minimal repro for getzep/graphiti#1934 without any LLM or embedder.

Every FalkorDriver.clone(database=...) schedules another un-awaited
build_indices_and_constraints() task. This script clones per "group" the way
Graphiti does for scoped calls, runs a trivial query on each clone, and counts
connection errors and pending index-building tasks.

Run: python spike/graphiti_clone_repro.py --port 6390 --groups 4 --calls 40
"""
import argparse
import asyncio

from graphiti_core.driver.falkordb_driver import FalkorDriver


async def main(port, groups, calls):
    root = FalkorDriver(host="localhost", port=port)
    errors, ok = 0, 0

    async def one(i):
        nonlocal errors, ok
        clone = root.clone(database=f"repro_group_{i % groups}")
        try:
            await clone.execute_query("RETURN 1 AS x")
            ok += 1
        except Exception as exc:  # report every failure class, do not hide it
            errors += 1
            print(f"call {i}: {type(exc).__name__}: {str(exc)[:100]}")

    await asyncio.gather(*(one(i) for i in range(calls)))
    pending = [t for t in asyncio.all_tasks() if "build_indices_and_constraints" in repr(t.get_coro())]
    print(f"port={port} groups={groups} calls={calls} ok={ok} errors={errors} "
          f"pending_index_tasks={len(pending)}")
    await asyncio.sleep(3)
    failures = {}
    for t in pending:
        if t.done() and t.exception() is not None:
            key = f"{type(t.exception()).__name__}: {str(t.exception())[:160]}"
            failures[key] = failures.get(key, 0) + 1
    print(f"index tasks finished with an exception after 3s: {sum(failures.values())} of {len(pending)}")
    for key, count in failures.items():
        print(f"  {count} x {key}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=6390)
    ap.add_argument("--groups", type=int, default=4)
    ap.add_argument("--calls", type=int, default=40)
    a = ap.parse_args()
    asyncio.run(main(a.port, a.groups, a.calls))
