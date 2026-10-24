"""Print the signed Nightshift receipts of one run (the daemon's own export, logs/receipts-<run>.jsonl)."""
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1])]
print(f"{len(rows)} receipts, signed by {rows[0]['body']['signer']} (Ed25519)\n")
for x in rows:
    r = x['body']['receipt']
    print(f"{r['receipt_id']}  app={r['app']:<17} lane={r['app_policy']['lanes'][0]:<18} routed_model={r['winning_route']['model']}")
    print(f"    cost_usd={r['cost']['actual_usd']}  privacy={r['privacy']['path']}  tokens={r['usage']['input_tokens']}+{r['usage']['output_tokens']}"
          f"  latency_ms={r['usage']['latency_ms']}  sig={x['sig_b64'][:24]}…")
