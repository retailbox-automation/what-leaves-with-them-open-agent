"""Print the last entries of a tenant's H-MEM ledger (.state/hmem/<tenant>/ledger.jsonl), one line each."""
import datetime, json, sys
rows = [json.loads(l) for l in open(sys.argv[1])][-int(sys.argv[2] if len(sys.argv) > 2 else 4):]
for r in rows:
    s = r.get('newState') or r.get('previousState') or {}
    t = datetime.datetime.fromtimestamp(r['timestamp'] / 1000, datetime.timezone.utc).strftime('%H:%M:%S')
    conf = (r.get('newState') or {}).get('confidence')
    print(f"{t}Z  {r['memoryId']:<6} {r['action']:<9} {('confidence ' + str(round(conf, 2))) if conf is not None else '':<16} \"{s.get('content', '')[:90]}\"")
