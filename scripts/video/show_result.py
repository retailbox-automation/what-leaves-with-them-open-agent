"""Print the key fields of an open_agent.run result (used on screen in the recorded takes)."""
import json, sys, textwrap
d = json.load(open(sys.argv[1]))
W = 150
print(f"status: {d['status']}   revisions: {d.get('revisions')}   run: {d['run_id']}")
L = d['llm']
print(f"Investigator: {L['investigator']['model']} via Nightshift app {L['investigator']['app']}")
print(f"Verifier:     {L['verifier']['model']} via Nightshift app {L['verifier']['app']}")
for c in d.get('dropped_citations', []):
    print(f"rejected in round {c['round']}: {c['id']}  reason={c['reason']}  verdict={c.get('verdict')}")
    print(textwrap.fill(f"  at its source now: \"{c.get('text_at_source_now')}\"", W, subsequent_indent='  '))
for c in d.get('citations', []):
    print(f"accepted: {c['id']}  quote_found={c['quote_found']}  verdict={c['verdict']}")
print(textwrap.fill('answer: ' + d.get('answer', ''), W, subsequent_indent='        '))
g = d.get('write_gate') or {}
print(f"write gate: written={g.get('written')} {g.get('finding', '')} · H-MEM {(g.get('memory') or {}).get('memory_id', '-')}")
