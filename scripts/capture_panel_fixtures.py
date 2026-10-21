"""Record the answers the panel (web/) asks for, from a running API, into web/fixtures/.

The panel reads these when it is opened with ?data=fixtures, and falls back to them when the live API does not
answer. Every file is a real answer of the API; nothing here is typed in by hand.

    python scripts/capture_panel_fixtures.py --base http://127.0.0.1:8731 --tenant quillfield

Calls per person: what_leaves at depth 1-4, without, single_source (now, and as of the notice day and the days of the
handoff records for a person with a recorded resignation), handoff_plan and promises. Shared: the graph, the tool
list, bridges(top=3), the CONFIRM receipts and the records they cite after a notice.
Keys match api.key() in web/app.js: name|arg=value|... with the argument names sorted.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "web" / "fixtures"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def key(name: str, args: dict) -> str:
    return "|".join([name] + [f"{k}={args[k]}" for k in sorted(args)])


class Client:
    def __init__(self, base: str, tenant: str):
        self.base, self.tenant = base.rstrip("/"), tenant

    def get(self, path: str):
        with urllib.request.urlopen(f"{self.base}/v1/{self.tenant}/{path}", timeout=30) as r:
            return json.load(r)

    def tool(self, name: str, args: dict):
        req = urllib.request.Request(f"{self.base}/v1/{self.tenant}/tools/{name}", data=json.dumps(args).encode(),
                                     headers={"content-type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:8731")
    ap.add_argument("--tenant", default="quillfield")
    a = ap.parse_args(argv)
    c = Client(a.base, a.tenant)

    graph = c.get("graph")
    tools = c.get("tools")
    takes_as_of = {t["name"] for t in tools["tools"] if "as_of" in t["parameters"].get("properties", {})}
    receipts = c.get("receipts?outcome=confirm&limit=200")
    common = {"graph": graph, "tools": tools, key("bridges", {"top": 3}): c.tool("bridges", {"top": 3}),
              "receipts|confirm": receipts}
    notices = {r["person"]: r["notice_at"] for r in graph["leaving"] if r.get("notice_at")}
    later = sorted({i for r in receipts["receipts"] for i in r.get("cites", [])
                    if notices and r["ts"] > min(notices.values())})
    episodes = {}
    for eid in later:
        episodes[eid] = c.get(f"episodes/{eid}")
        common[f"episode|{eid}"] = episodes[eid]

    (OUT / "people").mkdir(parents=True, exist_ok=True)
    people = [n["id"] for n in graph["nodes"] if n["kind"] == "person"]
    for person in people:
        book = {}

        def call(name, args):
            book[key(name, args)] = c.tool(name, args)

        for depth in (1, 2, 3, 4):
            call("what_leaves", {"person": person, "depth": depth})
        for name in ("without", "single_source", "handoff_plan", "promises"):
            call(name, {"person": person})
        if person in notices:
            days = {notices[person][:10]} | {e["ts"][:10] for e in episodes.values() if e["ts"] > notices[person]}
            for name in ("without", "single_source"):
                if name in takes_as_of:
                    for d in sorted(days):
                        call(name, {"person": person, "as_of": d})
        (OUT / "people" / f"{slug(person)}.json").write_text(json.dumps(book, separators=(",", ":")))
    (OUT / "common.json").write_text(json.dumps(common, separators=(",", ":")))
    print(f"recorded {len(people)} people and {len(common)} shared answers into {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
