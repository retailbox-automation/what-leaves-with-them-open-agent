"""Time the Verifier's two reads both ways on the live Zetaris: the hand-coded per-system SQL from
open_agent/sources.py (by_id, mentions) against the same read through the USL table. N runs each, alternating,
median and p90 to zetaris_usl/logs/measure.json. Read-only.

usage: python3 -m zetaris_usl.measure [N]
"""
import json
import statistics
import sys
import time

from open_agent.sources import by_id, mentions
from zetaris_spike.zclient import Zetaris

T = "lightning.metastore.wlwt.wlwt_usl.record"
CASES = {
    "point_read_chat": (by_id("chat", "msg-0148"), f"SELECT * FROM {T} WHERE record_id = 'msg-0148'"),
    "point_read_doc": (by_id("doc", "ADR-0009"), f"SELECT * FROM {T} WHERE record_id = 'ADR-0009'"),
    "absence_check": (mentions("payroll-export"), f"SELECT system, record_id FROM {T} WHERE instr(body, 'payroll-export') > 0"),
}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    z, out = Zetaris(), {"n": n, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cases": {}}
    for name, (hand, usl) in CASES.items():
        t = {"hand_coded": [], "usl": []}
        rows = {}
        for _ in range(n):
            for k, sql in (("hand_coded", hand), ("usl", usl)):
                r, s, _ = z.sql(sql)
                t[k].append(s)
                rows[k] = len(r)
        out["cases"][name] = {k: {"median_s": round(statistics.median(v), 3), "p90_s": round(sorted(v)[int(0.9 * (n - 1))], 3),
                                  "rows": rows[k]} for k, v in t.items()}
        out["cases"][name]["sql"] = {"hand_coded": hand, "usl": usl}
        print(name, {k: out["cases"][name][k] for k in ("hand_coded", "usl")}, flush=True)
    out["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    json.dump(out, open("zetaris_usl/logs/measure.json", "w"), indent=1)


if __name__ == "__main__":
    main()
