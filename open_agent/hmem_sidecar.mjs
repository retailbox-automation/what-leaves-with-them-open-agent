// H-MEM sidecar: the Meterless H-MEM reference engine (vendor/hmem, pinned) behind JSON lines on stdin/stdout.
//
//   node open_agent/hmem_sidecar.mjs --persist-dir <dir> [--actor wlwt]
//
// One request per line: {"id": 1, "op": "add", "args": {...}}; one reply per line: {"id": 1, "ok": true,
// "result": {...}} or {"id": 1, "ok": false, "error": "..."}. No network, no port: the Python side owns the pipe.
// State lives in <dir>/state.json and <dir>/ledger.jsonl (written by the engine itself on every change).
// One process per directory: <dir>/sidecar.lock holds the owner's pid, because two engines on one state.json
// would overwrite each other.
//
// Ops: ping, add, query, recall (query over one tag), feedback, get, list, stats, import (add records whose
// provenance ref is not stored yet).

import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const ENGINE = process.env.HMEM_ENGINE || path.join(here, "..", "vendor", "hmem", "dist", "hmem.mjs");

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i > 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}

const persistDir = arg("--persist-dir");
const actor = arg("--actor", "wlwt");
if (!persistDir) {
  process.stderr.write("hmem_sidecar: --persist-dir is required\n");
  process.exit(2);
}
fs.mkdirSync(persistDir, { recursive: true });

// --- single owner per directory --------------------------------------------------------------
const lockFile = path.join(persistDir, "sidecar.lock");
function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === "EPERM";
  }
}
function takeLock() {
  try {
    fs.writeFileSync(lockFile, String(process.pid), { flag: "wx" });
    return;
  } catch (e) {
    if (e.code !== "EEXIST") throw e;
  }
  const owner = Number(fs.readFileSync(lockFile, "utf-8").trim());
  if (owner && owner !== process.pid && alive(owner)) {
    process.stderr.write(`hmem_sidecar: ${persistDir} is held by pid ${owner}\n`);
    process.exit(3);
  }
  fs.writeFileSync(lockFile, String(process.pid));   // stale lock from a dead process
}
function dropLock() {
  try {
    if (Number(fs.readFileSync(lockFile, "utf-8").trim()) === process.pid) fs.unlinkSync(lockFile);
  } catch {}
}
takeLock();
process.on("exit", dropLock);
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => process.exit(0));

const { HMEM } = await import(pathToFileURL(ENGINE).href);
const hmem = new HMEM({ persistDir, actor });

// --- helpers ----------------------------------------------------------------------------------
function plain(rec) {
  if (!rec) return null;
  const { embedding, ...rest } = rec;   // 64 floats of the mock embedder; nobody downstream needs them
  return rest;
}
function refsOf(rec) {
  return (rec.provenance && Array.isArray(rec.provenance.refs)) ? rec.provenance.refs : [];
}
function findByRef(ref) {
  return hmem.store.all().find((r) => refsOf(r)[0] === ref) || null;
}

const OPS = {
  ping: () => ({ engine: path.relative(path.join(here, ".."), ENGINE), persistDir, records: hmem.store.all().length,
                 ledger: hmem.ledger.all().length, pid: process.pid }),

  add: (a) => plain(hmem.add(a)),

  // Records keyed by provenance.refs[0]: a ref already stored is skipped, so an import can be repeated.
  import: (a) => {
    const added = [], skipped = [];
    for (const rec of a.records || []) {
      const ref = refsOf(rec)[0];
      if (!ref) throw new Error("import: every record needs provenance.refs[0]");
      const have = findByRef(ref);
      if (have) {   // already held: only widen its tags (a finding about two entities is imported once per entity)
        const tags = [...new Set([...have.tags, ...(rec.tags || [])])];
        if (tags.length !== have.tags.length) hmem.store.update(have.id, { tags });
        skipped.push(ref);
        continue;
      }
      added.push(plain(hmem.add(rec)).id);
    }
    return { added, skipped };
  },

  query: (a) => {
    const res = hmem.query(a.text, { topN: a.topN, threshold: a.threshold, chatId: a.chatId });
    hmem.store.persist();   // query touches lastAccessed/accessCount in memory; keep them across restarts
    return { memories: res.memories.map((m) => ({ memory: plain(m.memory), relevance: m.relevance })),
             context: res.context, trace: res.trace };
  },

  // The engine's query() over records that carry one tag (answers vs findings share a store), optionally only
  // those whose provenance.refs[0] is in allowRefs: same score (retrieval.scoreOne), same read entry in the
  // ledger, same [MEMORY CONTEXT] formatter.
  recall: (a) => {
    const threshold = a.threshold ?? 0.35, topN = a.topN ?? 5;
    const allow = Array.isArray(a.allowRefs) ? new Set(a.allowRefs) : null;
    const scored = hmem.store.all()
      .filter((r) => !a.tag || r.tags.includes(a.tag))
      .filter((r) => !allow || allow.has(refsOf(r)[0]))
      .map((m) => ({ memory: m, relevance: hmem.retrieval.scoreOne(m, a.text) }))
      .filter((x) => x.relevance >= threshold)
      .sort((x, y) => y.relevance - x.relevance || x.memory.id.localeCompare(y.memory.id))
      .slice(0, topN);
    for (const { memory } of scored) {
      hmem.store.touch(memory.id);
      hmem.ledger.log(memory.id, "read", actor, { details: { query: a.text, tag: a.tag ?? null } });
    }
    hmem.store.persist();
    return { memories: scored.map((m) => ({ memory: plain(m.memory), relevance: m.relevance })),
             context: hmem.retrieval.formatReinjection(scored) };
  },

  feedback: (a) => {
    hmem.feedback(a.id, a.kind);
    return plain(hmem.store.get(a.id));
  },

  get: (a) => plain(hmem.store.get(a.id)) ?? (a.ref ? plain(findByRef(a.ref)) : null),

  list: (a) => {
    let recs = hmem.store.all();
    if (a && a.tag) recs = recs.filter((r) => r.tags.includes(a.tag));
    return recs.map(plain);
  },

  stats: () => ({ records: hmem.store.all().length, ledger: hmem.ledger.stats() }),
};

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
const out = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
out({ id: 0, ok: true, result: { ready: true, ...OPS.ping() } });

rl.on("line", (line) => {
  if (!line.trim()) return;
  let req;
  try {
    req = JSON.parse(line);
  } catch (e) {
    out({ id: null, ok: false, error: `bad json: ${e.message}` });
    return;
  }
  const fn = OPS[req.op];
  if (!fn) {
    out({ id: req.id, ok: false, error: `unknown op ${req.op}` });
    return;
  }
  try {
    out({ id: req.id, ok: true, result: fn(req.args || {}) });
  } catch (e) {
    out({ id: req.id, ok: false, error: String(e && e.message ? e.message : e) });
  }
});
rl.on("close", () => process.exit(0));
