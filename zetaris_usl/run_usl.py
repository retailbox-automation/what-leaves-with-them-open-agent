"""Run a USL SQL file against the local Zetaris one statement at a time and log every response.

Statements are split on ';' at the end of a line (the multi-table COMPILE USL is one statement: its inner
CREATE TABLE blocks end with ');' and are joined back to the COMPILE that opens them). Each statement goes
through the SQL editor route (DDL, SHOW, SELECT); the response, row count and seconds are written to
<log_dir>/NN.json. Credentials come from ZETARIS_BUNDLE_ENV, as in zetaris_spike.register; nothing secret is logged.

usage: python3 -m zetaris_usl.run_usl zetaris_usl/sql/01_wlwt_usl_create.sql zetaris_usl/logs/create [--from N] [--only N]
"""
import json
import os
import re
import sys

from zetaris_spike.zclient import Zetaris


def statements(path):
    lines = [re.sub(r"\s+--.*$", "", l) for l in open(path).read().splitlines() if not l.lstrip().startswith("--")]
    body = "\n".join(lines)   # trailing "-- expect ..." comments are cut (no "--" inside our string literals)
    parts = [p.strip() for p in re.split(r";\s*\n", body + "\n") if p.strip()]
    out, i = [], 0
    while i < len(parts):
        p = parts[i]
        if p.upper().startswith("COMPILE USL"):          # glue the CREATE TABLE blocks back onto their COMPILE
            while i + 1 < len(parts) and parts[i + 1].upper().startswith("CREATE TABLE"):
                i += 1
                p += ";\n" + parts[i]
        out.append(p)
        i += 1
    return out


def main():
    src, log_dir = sys.argv[1], sys.argv[2]
    start = int(sys.argv[sys.argv.index("--from") + 1]) if "--from" in sys.argv else 1
    only = int(sys.argv[sys.argv.index("--only") + 1]) if "--only" in sys.argv else None
    os.makedirs(log_dir, exist_ok=True)
    z = Zetaris()
    for n, st in enumerate(statements(src), 1):
        if n < start or (only and n != only):
            continue
        try:
            rows, secs, raw = z.run(st)
            rec = {"n": n, "statement": st, "ok": True, "rows": len(rows), "seconds": round(secs, 3), "sample": rows[:20], "raw": raw}
        except RuntimeError as e:
            rec = {"n": n, "statement": st, "ok": False, "error": str(e)[:2000]}
        json.dump(rec, open(os.path.join(log_dir, f"{n:02d}.json"), "w"), indent=1, default=str)
        print(f"{n:02d} {'OK ' if rec['ok'] else 'ERR'} {st.splitlines()[0][:90]}"
              + (f"  rows={rec['rows']} {rec['seconds']}s" if rec["ok"] else f"  {rec['error'][:160]}"), flush=True)


if __name__ == "__main__":
    main()
