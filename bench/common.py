"""Shared pieces of the benchmark: the corpus, the local model client, run metadata.

Everything runs against a local Ollama (OpenAI-compatible endpoint) and a local FalkorDB. No hosted APIs.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "bench"
CACHE = BENCH / ".cache"
RESULTS = BENCH / "results"

BASE_URL = os.environ.get("HM_LLM_BASE_URL", "http://localhost:11434/v1")
API_KEY = os.environ.get("HM_LLM_API_KEY", "ollama")
EMBED_MODEL = os.environ.get("BENCH_EMBED_MODEL", "embeddinggemma")

# (query prefix, document prefix) the embedding model was trained with.
EMBED_PREFIXES = {
    "nomic-embed-text": ("search_query: ", "search_document: "),
    "embeddinggemma": ("task: search result | query: ", "title: none | text: "),
}


# --- corpus -----------------------------------------------------------------------------------

@dataclass
class Corpus:
    seed: int
    dir: Path
    episodes: list[dict]
    questions: list[dict]
    gold: list[dict]
    generator_version: str
    now: str                        # ISO timestamp of the end of the corpus: "currently" in a question means this

    @property
    def sha256(self) -> str:
        """Hash of the three corpus files, so a rerun can show it used the same bytes."""
        h = hashlib.sha256()
        for name in ("episodes", "gold_facts", "questions"):
            h.update((self.dir / f"{name}.jsonl").read_bytes())
        return h.hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_corpus(seed: int = 42, corpus_dir: str | Path | None = None) -> Corpus:
    """Read a corpus directory, or build the seed's corpus fresh with the repo's generator.

    With no directory the corpus is generated into bench/.cache/corpus/<version>-<seed>/ every run (deterministic,
    no network, a second), so a changed generator is never hidden behind a stale data/corpus folder.
    """
    from honest_memory.corpus import GENERATOR_VERSION, build
    from honest_memory.corpus.generator import dumps

    if corpus_dir is None:
        target = CACHE / "corpus" / f"{GENERATOR_VERSION}-{seed}"
        target.mkdir(parents=True, exist_ok=True)
        built = build(seed)
        for name, rows in (("episodes", built.episodes), ("gold_facts", built.gold), ("questions", built.questions)):
            (target / f"{name}.jsonl").write_bytes("".join(dumps(r) + "\n" for r in rows).encode("utf-8"))
    else:
        target = Path(corpus_dir)
    episodes = _read_jsonl(target / "episodes.jsonl")
    try:
        from honest_memory.corpus.generator import END

        now = END.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ImportError:
        now = max(e["ts"] for e in episodes)
    return Corpus(seed, target, episodes, _read_jsonl(target / "questions.jsonl"),
                  _read_jsonl(target / "gold_facts.jsonl"), GENERATOR_VERSION, now)


# --- local model client -----------------------------------------------------------------------

class Ollama:
    """Chat and embeddings through Ollama's OpenAI-compatible endpoint."""

    def __init__(self, base_url: str = BASE_URL, api_key: str = API_KEY, timeout: float = 300.0):
        from openai import OpenAI

        self.base_url = base_url
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=1)

    def chat(self, model: str, messages: list[dict], reasoning: str | None = None, max_tokens: int = 1500) -> tuple[str, dict]:
        """One completion at temperature 0 with a fixed seed: the text and the token counts. `reasoning` is Ollama's
        reasoning_effort ("none" turns thinking off); thinking tokens are counted but not returned. A completion cut off
        by max_tokens comes back empty, with finish_reason "length" in the usage."""
        kwargs = {"reasoning_effort": reasoning} if reasoning else {}
        out = self.client.chat.completions.create(model=model, messages=messages, temperature=0, seed=42,
                                                  max_tokens=max_tokens, **kwargs)
        choice = out.choices[0]
        usage = {"finish_reason": choice.finish_reason}
        if out.usage:
            usage.update(prompt_tokens=out.usage.prompt_tokens, completion_tokens=out.usage.completion_tokens)
        return (choice.message.content or "").strip(), usage

    def embed(self, model: str, texts: list[str], batch: int = 32) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), batch):
            out = self.client.embeddings.create(model=model, input=texts[i:i + batch])
            vectors += [d.embedding for d in sorted(out.data, key=lambda d: d.index)]
        return vectors


class EmbeddingCache:
    """Embeddings on disk, keyed by model and exact text, so a rerun does not embed the corpus again."""

    def __init__(self, ollama: Ollama, model: str, root: Path = CACHE):
        self.ollama, self.model = ollama, model
        self.path = root / f"embeddings-{model.replace(':', '_').replace('/', '_')}.json"
        self.data: dict[str, list[float]] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def vectors(self, texts: list[str]) -> list[list[float]]:
        missing = list(dict.fromkeys(t for t in texts if self.key(t) not in self.data))
        if missing:
            for text, vec in zip(missing, self.ollama.embed(self.model, missing)):
                self.data[self.key(text)] = vec
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data), encoding="utf-8")
        return [self.data[self.key(t)] for t in texts]


def normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


# --- metadata ---------------------------------------------------------------------------------

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_head() -> str:
    try:
        out = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             timeout=10)
        dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no", "--", "honest_memory", "bench/*.py", "tests"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return out.stdout.strip() + (" (uncommitted changes)" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def ollama_models(names: list[str]) -> dict[str, dict]:
    """Digest and size of the named local models, from Ollama's native API."""
    import urllib.request

    root = BASE_URL.rsplit("/v1", 1)[0]
    try:
        with urllib.request.urlopen(f"{root}/api/tags", timeout=10) as r:
            tags = json.load(r)["models"]
    except (OSError, ValueError, KeyError):
        return {n: {} for n in names}
    out = {}
    for n in names:
        match = next((m for m in tags if m["name"] == n or m["name"] == f"{n}:latest"), None)
        out[n] = ({"digest": match["digest"][:12], "parameters": match.get("details", {}).get("parameter_size"),
                   "quantization": match.get("details", {}).get("quantization_level")} if match else {})
    return out


def falkordb_version(db) -> str:
    """'4.22.0' / '6.0.2' from the graph module's version number."""
    for module in db.connection.execute_command("MODULE", "LIST"):
        fields = dict(zip(module[::2], module[1::2]))
        if fields.get(b"name", fields.get("name")) in (b"graph", "graph"):
            v = int(fields.get(b"ver", fields.get("ver")))
            return f"{v // 10000}.{v // 100 % 100}.{v % 100}"
    return "unknown"


class Stopwatch:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = round((time.perf_counter() - self.t0) * 1000, 1)
