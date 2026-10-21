"""Red team for the write gate: runs redteam/attacks.jsonl against a fresh tenant on each FalkorDB engine.

    python redteam/run.py --ports 6390,6393                 # all attacks, writes redteam/results-<engine>.json
    python redteam/run.py --ports 6390 --only P01,T04       # a few
    python redteam/run.py --label first-run                 # write first-run-<engine>.json instead (before any fix)
    python redteam/run.py --selftest                        # the harness must flag a deliberately wrong expectation
    python redteam/run.py --report                          # compose redteam/REPORT.md from the result files

Every attack gets its own tenant (hm_rt<run><id><port>): the synthetic corpus is replayed into it, the steps run
through the public entry points (honest_memory.gate, honest_memory.ingest, the HTTP API through FastAPI's
TestClient), then the graph is compared with the snapshot taken before. The tenant is dropped afterwards.

An attack is a JSON line with `steps` (each: a `call` and what to `expect`), a `graph` rule and optional `post`
queries. To add one, append a line to attacks.jsonl; nothing in this file has to change. Call kinds:

    fact      gate.submit_fact: subject, predicate, object, valid_at, source_ids, [proposer, claim_episode, attrs, at, repeat]
    finding   gate.submit_finding: agent, session, text, about, source_ids, at
    handoff   gate.submit_handoff: from_, to, topic, note, source_ids, at
    episode   ingest.ingest_episode with the rule extractor: episode {id, ts, channel, author, title, text, refs}
    http      method, path ({tenant} is replaced), [headers, json]
    end       gate.submit_end: subject, predicate, object, source_ids, at   (the CLOSE path)
    plant     store one Episode as it is, without the extractor or the gate: the setup of an attack
    tenant    name: graph_name() and Store() must raise

Expectations: outcome/reasons (gate calls), results (episode: one {outcome, reasons} per candidate), status /
json_outcome / reasons / body_absent (http), raises (tenant), first/rest (fact with repeat).
Strings may be {"$repeat": [text, n]} or "@rejected:<predicate>:<subject>:<object>" (the id of that refusal's receipt).

Graph rule: `profile` refusal (only receipts may be added), ingest (plus episodes and their links) or none; `nodes` and
`edges` give allowed growth per label or type (a number, or [min, max|null]); anything else must not change, nothing may
shrink, and the facts (typed edges, WAS, entity properties) must be identical unless `facts_unchanged` is false.
`secrets` must appear in no property, `max_property_len` bounds every property but episode text, `absent_graphs`
names graphs that must not exist afterwards.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from falkordb import FalkorDB  # noqa: E402

from honest_memory import gate  # noqa: E402
from honest_memory.extract import RuleExtractor  # noqa: E402
from honest_memory.ingest import ingest_corpus, ingest_episode  # noqa: E402
from honest_memory.schema import graph_name  # noqa: E402
from honest_memory.store import Store, epoch  # noqa: E402

FACT_EDGES = ["OWNS", "MEMBER_OF", "LEADS", "WORKED_ON", "DECIDED", "SUPERSEDES", "DEPENDS_ON", "WAS"]
ENTITY_LABELS = ["Person", "Team", "System", "Decision", "Project"]
RECEIPT_EDGES = ("CITES", "ABOUT", "CLAIMED_IN")
INGEST_EDGES = ("MENTIONS", "REFERS_TO")
PROFILES = ("none", "refusal", "ingest")


# ---- loading ----------------------------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def expand(value, ctx):
    """Resolve {"$repeat": [text, n]} and "@rejected:..." markers anywhere inside a call."""
    if isinstance(value, dict):
        if set(value) == {"$repeat"}:
            text, n = value["$repeat"]
            return text * n
        return {k: expand(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, ctx) for v in value]
    if isinstance(value, str) and value.startswith("@rejected:"):
        _, predicate, subject, obj = value.split(":", 3)
        rows = ctx.store.ro("MATCH (g:GateDecision {outcome: 'REJECT', predicate: $p, subject: $s, object: $o}) "
                            "RETURN g.id ORDER BY g.id LIMIT 1", {"p": predicate, "s": subject, "o": obj})
        if not rows:
            raise LookupError(f"no refusal receipt for {predicate} {subject} {obj}")
        return rows[0][0]
    return value


# ---- graph snapshots --------------------------------------------------------------------------------------

def snapshot(store: Store, deep: bool = False) -> dict:
    snap = {
        "nodes": {label: n for label, n in store.ro("MATCH (n) RETURN labels(n)[0], count(n)")},
        "edges": {kind: n for kind, n in store.ro("MATCH ()-[r]->() RETURN type(r), count(r)")},
    }
    facts = store.ro("MATCH (a)-[r]->(b) WHERE type(r) IN $t RETURN type(r), coalesce(a.name, a.id), coalesce(b.name, b.id), "
                     "properties(r)", {"t": FACT_EDGES})
    ents = store.ro("MATCH (n) WHERE labels(n)[0] IN $l RETURN labels(n)[0], properties(n)", {"l": ENTITY_LABELS})
    dump = lambda rows: sorted(json.dumps(r, sort_keys=True, default=str) for r in rows)
    snap["facts"] = hashlib.sha1("\n".join(dump(facts)).encode()).hexdigest()
    snap["entities"] = hashlib.sha1("\n".join(dump(ents)).encode()).hexdigest()
    if deep:
        snap["props"] = ([("node:" + label, p) for label, p in store.ro("MATCH (n) RETURN labels(n)[0], properties(n)")]
                         + [("edge:" + kind, p) for kind, p in store.ro("MATCH ()-[r]->() RETURN type(r), properties(r)")])
    return snap


def bounds(allowed) -> tuple[int, float]:
    if isinstance(allowed, list):
        lo, hi = allowed
        return lo, float("inf") if hi is None else hi
    return allowed, allowed


def check_graph(spec: dict, before: dict, after: dict, gate_calls: int, episodes_ingested: int, db) -> list[str]:
    profile = spec.get("profile", "refusal")
    assert profile in PROFILES, profile
    nodes: dict = {}
    edges: dict = {}
    if profile in ("refusal", "ingest"):
        nodes["GateDecision"] = [0, gate_calls]
        edges.update({e: [0, None] for e in RECEIPT_EDGES})
    if profile == "ingest":
        nodes["Episode"] = [0, spec.get("episodes", episodes_ingested)]
        edges.update({e: [0, None] for e in INGEST_EDGES})
    nodes.update(spec.get("nodes", {}))
    edges.update(spec.get("edges", {}))
    problems = []
    for what, allowed, old, new in (("nodes", nodes, before["nodes"], after["nodes"]),
                                    ("edges", edges, before["edges"], after["edges"])):
        for kind in sorted(set(old) | set(new)):
            delta = new.get(kind, 0) - old.get(kind, 0)
            lo, hi = bounds(allowed.get(kind, 0))
            if not lo <= delta <= hi:
                problems.append(f"{what[:-1]} {kind}: changed by {delta:+d}, allowed {lo}..{'any' if hi == float('inf') else int(hi)}")
    if spec.get("facts_unchanged", True):
        if before["facts"] != after["facts"]:
            problems.append("typed edges or WAS edges changed")
        if before["entities"] != after["entities"]:
            problems.append("entity nodes changed (properties or set)")
    for s in spec.get("secrets", []):
        for where, props in after.get("props", []):
            if any(s in str(v) for v in props.values()):
                problems.append(f"literal {s!r} stored on a {where}")
                break
    if "max_property_len" in spec:
        cap = spec["max_property_len"]
        for where, props in after.get("props", []):
            if where == "node:Episode":
                continue
            longest = max((len(str(v)) for v in props.values()), default=0)
            if longest > cap:
                problems.append(f"a {where} holds a property of {longest} characters (cap {cap})")
                break
    present = set(db.list_graphs())
    problems += [f"graph {g!r} exists" for g in spec.get("absent_graphs", []) if g in present]
    return problems


# ---- calls ------------------------------------------------------------------------------------------------

class Ctx:
    def __init__(self, db, store: Store, tenant: str, client):
        self.db, self.store, self.tenant, self.client = db, store, tenant, client
        self.gate_calls = 0
        self.episodes = 0


def summary(result) -> dict:
    return {"outcome": result.outcome, "reasons": list(result.reasons)}


def do_call(ctx: Ctx, call: dict) -> dict:
    call = expand(call, ctx)
    kind = call["kind"]
    if kind == "fact":
        def one():
            cand = gate.Candidate(call["subject"], call["predicate"], call["object"], call["valid_at"],
                                  list(call["source_ids"]), call.get("proposer", "rules"), call.get("claim_episode"),
                                  dict(call.get("attrs") or {}))
            return gate.submit_fact(ctx.store, cand, at=call.get("at"))
        n = call.get("repeat", 1)
        results = [one() for _ in range(n)]
        ctx.gate_calls += n
        if n == 1:
            return summary(results[0])
        rest = {json.dumps(summary(r), sort_keys=True) for r in results[1:]}
        return {"first": summary(results[0]), "rest": json.loads(rest.pop()) if len(rest) == 1 else {"mixed": sorted(rest)},
                "receipts": len({r.receipt_id for r in results})}
    if kind == "finding":
        ctx.gate_calls += 1
        return summary(gate.submit_finding(ctx.store, call["agent"], call["session"], call["text"], call["about"],
                                           call["source_ids"], call["at"]))
    if kind == "handoff":
        ctx.gate_calls += 1
        return summary(gate.submit_handoff(ctx.store, call["from_"], call["to"], call["topic"], call["note"],
                                           call["source_ids"], call["at"]))
    if kind == "end":
        ctx.gate_calls += 1
        return summary(gate.submit_end(ctx.store, call["subject"], call["predicate"], call["object"],
                                       list(call["source_ids"]), call["at"]))
    if kind == "plant":
        ep = {"refs": [], "title": "", **call["episode"]}
        ctx.episodes += 1
        ctx.store.merge_entity("Episode", ep["id"], {"ts": epoch(ep["ts"]), "channel": ep["channel"], "author": ep["author"],
                                                      "title": ep["title"], "text": ep["text"]})
        return {"planted": ep["id"]}
    if kind == "episode":
        ep = {"refs": [], **call["episode"]}
        ctx.episodes += 1
        results = ingest_episode(ctx.store, ep, RuleExtractor())
        ctx.gate_calls += len(results)
        return {"results": [summary(r) for r in results]}
    if kind == "http":
        path = call["path"].replace("{tenant}", ctx.tenant)
        resp = ctx.client.request(call["method"], path, headers=call.get("headers"), json=call.get("json"))
        got = {"status": resp.status_code, "body": resp.text}
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            got["json_outcome"] = body.get("outcome")
            if body.get("receipts"):
                got["reasons"] = body["receipts"][0].get("reasons")
            if isinstance(body.get("result"), list):
                got["results"] = [{"outcome": r["outcome"], "reasons": r["reasons"]} for r in body["result"]]
                ctx.episodes += 1
                ctx.gate_calls += len(got["results"])
        if call["method"] == "POST" and resp.status_code == 200 and call["path"].startswith("/v1/{tenant}/tools/"):
            ctx.gate_calls += 1
        return got
    if kind == "tenant":
        name = call["name"]
        try:
            graph_name(name)
            Store(ctx.db, name)
        except Exception as exc:  # noqa: BLE001 - the exception type is the observation
            return {"raises": type(exc).__name__}
        return {"raises": None}
    raise ValueError(f"unknown call kind {kind!r}")


def judge_step(expect: dict, got: dict) -> list[str]:
    problems = []
    for key, want in expect.items():
        if key == "body_absent":
            problems += [f"response echoes {s!r}" for s in want if s in got.get("body", "")]
        elif key in ("first", "rest"):
            seen = got.get(key, {})
            if {k: seen.get(k) for k in want} != want:
                problems.append(f"{key}: expected {want}, got {seen}")
        elif got.get(key) != want:
            problems.append(f"{key}: expected {want!r}, got {got.get(key)!r}")
    return problems


def fmt_summary(s: dict) -> str:
    return f"{s['outcome']} {'+'.join(s['reasons']) or '-'}"


def fmt_got(got: dict) -> str:
    if "exc" in got:
        return "EXC " + got["exc"]
    if "results" in got and got["results"] is not None:
        return ", ".join(fmt_summary(r) for r in got["results"]) or "no candidates"
    if "first" in got:
        rest = got["rest"]
        tail = "mixed" if "mixed" in rest else fmt_summary(rest)
        return f"{fmt_summary(got['first'])}, then {tail} x49, {got['receipts']} receipt(s)"
    if "status" in got:
        extra = " ".join(str(x) for x in (got.get("json_outcome"), "+".join(got.get("reasons") or [])) if x)
        return f"HTTP {got['status']}" + (f" {extra}" if extra else "")
    if "raises" in got:
        return got["raises"] or "accepted"
    if "planted" in got:
        return "planted " + got["planted"]
    return fmt_summary(got)


def fmt_expect(expect: dict) -> str:
    if "results" in expect:
        return ", ".join(fmt_summary(r) for r in expect["results"]) or "no candidates"
    if "first" in expect:
        return f"{fmt_summary(expect['first'])}, then {fmt_summary(expect['rest'])} x49, one edge"
    if "status" in expect:
        extra = " ".join(str(x) for x in (expect.get("json_outcome"), "+".join(expect.get("reasons") or [])) if x)
        return f"HTTP {expect['status']}" + (f" {extra}" if extra else "")
    if "raises" in expect:
        return expect["raises"] or "accepted"
    return fmt_summary(expect)


def collapse(items: list[str]) -> str:
    out: list[list] = []
    for item in items:
        if out and out[-1][0] == item:
            out[-1][1] += 1
        else:
            out.append([item, 1])
    return "; ".join(f"{t} x{n}" if n > 1 else t for t, n in out)


# ---- running ----------------------------------------------------------------------------------------------

def run_attack(attack: dict, db, client_factory, corpus: Path, tag: str) -> dict:
    tenant = f"rt{tag}{attack['id'].lower()}"
    store = Store(db, tenant)
    started = time.perf_counter()
    result = {"id": attack["id"], "class": attack["class"], "name": attack["name"], "description": attack["description"],
              "expected": collapse([fmt_expect(s["expect"]) for s in attack["steps"] if s["call"]["kind"] != "plant"]),
              "steps": [], "problems": []}
    try:
        ingest_corpus(store, corpus, reset=True)
        ctx = Ctx(db, store, tenant, client_factory(db))
        spec = attack.get("graph", {"profile": "refusal"})
        deep = bool(spec.get("secrets") or attack.get("secrets") or "max_property_len" in spec)
        if attack.get("secrets"):
            spec = {**spec, "secrets": attack["secrets"]}
        before = snapshot(store)
        for step in attack["steps"]:
            try:
                got = do_call(ctx, step["call"])
            except Exception as exc:  # noqa: BLE001 - a crash is an observation, not a harness failure
                got = {"exc": f"{type(exc).__name__}: {str(exc).splitlines()[0][:100] if str(exc) else ''}"}
            problems = ["raised " + got["exc"]] if "exc" in got else judge_step(step["expect"], got)
            shown = {k: v for k, v in got.items() if k != "body"}
            result["steps"].append({"got": fmt_got(got), "ok": not problems, "problems": problems, "raw": shown,
                                    "setup": step["call"]["kind"] == "plant"})
            result["problems"] += [f"step {len(result['steps'])}: {p}" for p in problems]
        after = snapshot(store, deep=deep)
        graph_problems = check_graph(spec, before, after, ctx.gate_calls, ctx.episodes, db)
        result["problems"] += [f"graph: {p}" for p in graph_problems]
        result["graph"] = {"ok": not graph_problems, "problems": graph_problems,
                           "delta_nodes": {k: after["nodes"].get(k, 0) - before["nodes"].get(k, 0)
                                           for k in sorted(set(before["nodes"]) | set(after["nodes"]))
                                           if after["nodes"].get(k, 0) != before["nodes"].get(k, 0)},
                           "delta_edges": {k: after["edges"].get(k, 0) - before["edges"].get(k, 0)
                                           for k in sorted(set(before["edges"]) | set(after["edges"]))
                                           if after["edges"].get(k, 0) != before["edges"].get(k, 0)}}
        for check in attack.get("post", []):
            rows = [list(r) for r in store.ro(check["query"])]
            if rows != check["rows"]:
                result["problems"].append(f"post: {check['query']} -> {rows}, expected {check['rows']}")
    except Exception:  # noqa: BLE001
        result["problems"].append("harness error: " + traceback.format_exc().strip().splitlines()[-1])
        result.setdefault("graph", {"ok": False, "problems": []})
    finally:
        try:
            store.drop()
        except Exception:  # noqa: BLE001
            pass
    result["seconds"] = round(time.perf_counter() - started, 2)
    if "max_seconds" in attack and result["seconds"] > attack["max_seconds"]:
        result["problems"].append(f"took {result['seconds']} s, limit {attack['max_seconds']} s")
    result["got"] = collapse([s["got"] for s in result["steps"] if not s["setup"]])
    result["pass"] = not result["problems"]
    return result


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def client_factory(db):
    from fastapi.testclient import TestClient

    from honest_memory.api import create_app
    return TestClient(create_app(db, rate_limit=10**6, web_dir="/nonexistent"), raise_server_exceptions=False)


def run_engine(port: int, attacks: list[dict], controls: list[dict], corpus: Path, label: str, out_dir: Path) -> dict:
    from honest_memory.api import falkordb_version

    db = FalkorDB(host=os.environ.get("HM_TEST_HOST", "localhost"), port=port, socket_timeout=60)
    engine = falkordb_version(db) or f"port{port}"
    tag = uuid.uuid4().hex[:6] + str(port)
    print(f"== FalkorDB {engine} (port {port}), run {tag}")
    results = []
    for attack in attacks:
        r = run_attack(attack, db, client_factory, corpus, tag)
        results.append(r)
        print(f"  {r['id']}  {'PASS' if r['pass'] else 'FAIL'}  {r['name']:<52} {r['seconds']:>5.1f}s"
              + ("" if r["pass"] else "\n        " + "\n        ".join(r["problems"][:4])))
    control_rows = []
    for control in controls:
        r = run_attack(control, db, client_factory, corpus, tag)
        want = control.get("harness_expect", "pass")
        ok = r["pass"] == (want == "pass")
        control_rows.append({"id": control["id"], "name": control["name"], "harness_expect": want,
                             "harness_reported": "pass" if r["pass"] else "fail", "ok": ok, "problems": r["problems"][:3]})
        print(f"  {control['id']}  control {'ok' if ok else 'BROKEN'}: harness must report {want}, reported "
              f"{'pass' if r['pass'] else 'fail'}")
    passed = sum(r["pass"] for r in results)
    doc = {"engine": engine, "port": port, "label": label, "ran_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "repo_commit": git("rev-parse", "--short", "HEAD"),
           "gate_files_modified": bool(git("status", "--porcelain", "--", "honest_memory")),
           "score": {"passed": passed, "total": len(results)}, "controls": control_rows, "attacks": results}
    path = out_dir / f"{label}-{engine}.json"
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  score {passed}/{len(results)}  -> {os.path.relpath(path, ROOT)}")
    return doc


def selftest(port: int, attacks: list[dict], corpus: Path) -> int:
    """The harness must be able to see a gate that lets everything through: replace the judge with an
    always-accept stub and count the attacks the harness flags."""
    from unittest import mock

    db = FalkorDB(host=os.environ.get("HM_TEST_HOST", "localhost"), port=port, socket_timeout=60)
    tag = uuid.uuid4().hex[:6] + "s"

    def accept_all(store, cand, episodes, names):
        p = gate.PREDICATES.get(cand.predicate)
        label = p.object if p else None
        return gate._Verdict(gate.ACCEPT, ["new_fact"], label) if p and label else gate._Verdict(gate.REJECT, ["bad_predicate"])

    with mock.patch.object(gate, "_judge", accept_all):
        flagged = [run_attack(a, db, client_factory, corpus, tag)["pass"] is False for a in attacks]
    n = sum(flagged)
    print(f"selftest: with a gate that accepts every well-formed claim the harness flags {n} of {len(attacks)} attacks")
    return 0 if n >= len(attacks) // 2 else 1


# ---- report -----------------------------------------------------------------------------------------------

def load_results(label: str, out_dir: Path) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for path in sorted(out_dir.glob(f"{label}-*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc["engine"] in docs:  # first-run-close-<engine>.json adds the attacks that were appended after the first run
            old = docs[doc["engine"]]
            old["attacks"] += [a for a in doc["attacks"] if a["id"] not in {x["id"] for x in old["attacks"]}]
            old["score"] = {"passed": sum(a["pass"] for a in old["attacks"]), "total": len(old["attacks"])}
            old["commits"] = [old.get("commits", [old["repo_commit"]])[0], doc["repo_commit"]]
        else:
            docs[doc["engine"]] = doc
    return docs


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def write_report(out_dir: Path) -> None:
    first, final = load_results("first-run", out_dir), load_results("results", out_dir)
    engines = sorted(final) or sorted(first)
    fixes = json.loads((out_dir / "fixes.json").read_text(encoding="utf-8")) if (out_dir / "fixes.json").exists() else []
    notes = (out_dir / "notes.md").read_text(encoding="utf-8") if (out_dir / "notes.md").exists() else ""
    lines = ["# Red team of the write gate", "",
             "Generated by `python redteam/run.py --report` from `first-run-*.json`, `results-*.json`, `fixes.json` and `notes.md`. "
             "Do not edit by hand; change the inputs.", "", "## Score", "",
             "| Run | " + " | ".join(f"FalkorDB {e}" for e in engines) + " |", "|---|" + "---|" * len(engines)]
    for title, docs in (("First run, gate as found", first), ("Final", final)):
        row = [f"{docs[e]['score']['passed']}/{docs[e]['score']['total']}" if e in docs else "-" for e in engines]
        commits = next((d.get("commits") or [d["repo_commit"]] for d in docs.values()), [])
        lines.append(f"| {title} (repo `{'`, `'.join(dict.fromkeys(commits))}`) | " + " | ".join(row) + " |")
    ctl = [c for d in final.values() for c in d.get("controls", [])]
    if ctl:
        lines += ["", "Harness controls (they must behave as labelled or the table below means nothing): "
                  + ", ".join(f"{c['id']} {'ok' if c['ok'] else 'BROKEN'}" for c in {c['id']: c for c in ctl}.values()) + "."]
    count = len(next(iter((final or first).values()))["attacks"])
    lines += ["", f"## The {count} attacks", "",
              "Expected is what the gate must answer and leave behind; got is what it answered. Pass needs the right outcome and "
              "reasons on every step and a graph that changed only where allowed. First run = the gate before any fix; E01-E06 were "
              "appended after main (the CLOSE path) was merged, and their first run is against that merged gate.", "",
              "| # | Class | Attack | Expected | Got on the first run | First | Got now | Now |", "|---|---|---|---|---|---|---|---|"]
    base = final or first
    ids = [a["id"] for a in next(iter(base.values()))["attacks"]]
    for aid in ids:
        pick = lambda docs, e: next((a for a in docs.get(e, {}).get("attacks", []) if a["id"] == aid), None)
        f0 = [pick(first, e) for e in engines if pick(first, e)]
        f1 = [pick(final, e) for e in engines if pick(final, e)]
        ref = (f1 or f0)[0]
        got0 = cell(f0[0]["got"]) if f0 else "-"
        got1 = cell(f1[0]["got"]) if f1 else "-"
        mark = lambda rows: "/".join("pass" if r["pass"] else "FAIL" for r in rows) if rows else "-"
        same = lambda rows: len({r["pass"] for r in rows}) <= 1
        lines.append(f"| {aid} | {ref['class']} | {cell(ref['name'])} | {cell(ref['expected'])} | {got0} | "
                     f"{mark(f0) if not same(f0) else mark(f0[:1])} | {got1} | {mark(f1) if not same(f1) else mark(f1[:1])} |")
    if fixes:
        lines += ["", "## Fixes", "", "| # | Hole | Change | Attacks | Commit |", "|---|---|---|---|---|"]
        for fx in fixes:
            lines.append(f"| {fx['n']} | {cell(fx['hole'])} | {cell(fx['change'])} | {', '.join(fx['attacks'])} | `{fx['commit']}` |")
    if notes:
        lines += ["", notes.strip()]
    lines += ["", "## Reproduce", "",
              "```", "python -m honest_memory.corpus --seed 42 --out data/corpus",
              "python redteam/run.py --ports 6390,6393          # writes redteam/results-<engine>.json", "python redteam/run.py --report", "```", ""]
    (out_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("wrote", os.path.relpath(out_dir / "REPORT.md", ROOT))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python redteam/run.py", description=__doc__.splitlines()[0])
    ap.add_argument("--ports", default=os.environ.get("HM_TEST_PORTS", "6390,6393"))
    ap.add_argument("--only", help="comma-separated attack ids")
    ap.add_argument("--attacks", default=str(HERE / "attacks.jsonl"))
    ap.add_argument("--controls", default=str(HERE / "controls.jsonl"))
    ap.add_argument("--corpus", default=str(ROOT / "data" / "corpus" / "42"))
    ap.add_argument("--out-dir", default=str(HERE))
    ap.add_argument("--label", default="results", help="file prefix: results (default) or first-run")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--report", action="store_true", help="only compose REPORT.md from the result files")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)
    if args.report:
        write_report(out_dir)
        return 0
    for name in ("HM_WRITE_TOKEN", "HM_LLM_MODEL", "HM_TRUST_PROXY"):
        os.environ.pop(name, None)
    attacks = read_jsonl(Path(args.attacks))
    wanted = set(args.only.split(",")) if args.only else None
    if wanted:
        attacks = [a for a in attacks if a["id"] in wanted]
    ports = [int(p) for p in args.ports.split(",")]
    if args.selftest:
        return selftest(ports[0], attacks, Path(args.corpus))
    controls = [] if wanted else read_jsonl(Path(args.controls))
    docs = [run_engine(p, attacks, controls, Path(args.corpus), args.label, out_dir) for p in ports]
    broken = [c["id"] for d in docs for c in d["controls"] if not c["ok"]]
    if broken:
        print("harness controls broken:", ", ".join(broken))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
