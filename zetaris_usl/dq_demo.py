"""Show the USL's data quality rule and content hash at work on the live sources, then put everything back.

1. RUN DQ (expect 0 invalid).
2. Blank the text of one chat message in Postgres (msg-0002, a "Great work this week" note no answer cites),
   RUN DQ again (expect 1 invalid for `quotable`), SHOW DQ INVALID RECORD names it, restore, RUN DQ (0 again).
3. Read content_hash of msg-0148 through the USL, apply the same edit the tamper example makes
   (input_examples/02_tampered_source.json), read the hash again, restore, read it a third time.
Everything goes to zetaris_usl/logs/dq-demo.json. Postgres is changed with psql inside the wlwt-chat-pg container.

usage: python3 -m zetaris_usl.dq_demo
"""
import json
import subprocess
import time

from zetaris_spike.zclient import Zetaris

T = "lightning.metastore.wlwt.wlwt_usl.record"
OUT = "zetaris_usl/logs/dq-demo.json"


def psql(sql):
    return subprocess.run(["docker", "exec", "-i", "wlwt-chat-pg", "psql", "-U", "wlwt", "-d", "wlwt", "-At", "-c", sql],
                          capture_output=True, text=True, check=True).stdout.strip()


def q(s):
    return "'" + s.replace("'", "''") + "'"


def main():
    z = Zetaris()
    log = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "steps": []}

    def step(name, sql):
        rows, secs, _ = z.run(sql)
        log["steps"].append({"step": name, "sql": sql, "rows": rows[:20], "seconds": round(secs, 3)})
        print(name, json.dumps(rows[:5])[:300], flush=True)
        return rows

    tamper = json.load(open("input_examples/02_tampered_source.json"))
    blank_id, keep = "msg-0002", psql("SELECT text FROM chat WHERE id = 'msg-0002'")
    try:
        step("dq_before", f"RUN DQ TABLE {T}")
        psql(f"UPDATE chat SET text = '' WHERE id = {q(blank_id)}")
        step("dq_with_blank", f"RUN DQ TABLE {T}")
        step("invalid_records", f"SHOW DQ INVALID RECORD quotable TABLE {T}")
    finally:
        psql(f"UPDATE chat SET text = {q(keep)} WHERE id = {q(blank_id)}")
    step("dq_after_restore", f"RUN DQ TABLE {T}")
    log["blank_restored"] = psql(f"SELECT text FROM chat WHERE id = {q(blank_id)}") == keep

    rid = tamper.get("tamper") or "msg-0148"
    orig = psql(f"SELECT text FROM chat WHERE id = {q(rid)}")
    new_text = tamper.get("tamper_text")
    step("hash_before", f"SELECT record_id, content_hash FROM {T} WHERE record_id = {q(rid)}")
    try:
        if new_text:
            psql(f"UPDATE chat SET text = {q(new_text)} WHERE id = {q(rid)}")
            step("hash_after_edit", f"SELECT record_id, content_hash FROM {T} WHERE record_id = {q(rid)}")
    finally:
        psql(f"UPDATE chat SET text = {q(orig)} WHERE id = {q(rid)}")
    step("hash_after_restore", f"SELECT record_id, content_hash FROM {T} WHERE record_id = {q(rid)}")
    log["tamper_restored"] = psql(f"SELECT text FROM chat WHERE id = {q(rid)}") == orig
    log["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    json.dump(log, open(OUT, "w"), indent=1, default=str)
    print("restored:", log["blank_restored"], log["tamper_restored"])


if __name__ == "__main__":
    main()
