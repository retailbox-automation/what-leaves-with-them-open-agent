"""The baseline: plain retrieval-augmented generation over the same episodes.

Every episode is embedded once (local Ollama embeddings, cached on disk). A question is embedded, the eight
nearest episodes by cosine similarity go into the prompt, and the same local model that drives the honest-memory
agent answers from those episodes only, at temperature 0. There is no graph, no gate, no history, no tools and no
check on the answer: this is what most teams ship first. `--k` and `--chunk lines` make it stronger, for the
sensitivity runs.
"""

from __future__ import annotations

from .common import EMBED_MODEL, EMBED_PREFIXES, Corpus, EmbeddingCache, Ollama, Stopwatch, normalize

K = 8

# The agent's prompt style (honest_memory/agent.py SYSTEM_PROMPT), with records in place of tool results. Two
# additions help the baseline rather than hinder it: the records carry their dates and today's date is stated, and
# it is told to name the record ids it relied on (the agent's tool results carry sources on their own).
SYSTEM_PROMPT = """You are agent:support, a support agent answering from retrieved company records. You know \
nothing about the company except what the records below say. Today is {today}.
- Use only the records. Never guess or correct a name.
- If the records do not contain the answer, say so.
- Reply in one or two plain sentences (a list of names is fine when the question asks for several) that use only \
names, dates and record ids that appear in the records. Never invent systems, people, ids or dates.
- Name the record ids your answer rests on."""


def render_record(ep: dict, text: str | None = None) -> str:
    head = f"[{ep['id']}] {ep['ts']} | {ep['channel']} | by {ep['author']}"
    if ep.get("title") and ep["channel"] != "chat":
        head += f" | {ep['title']}"
    return f"{head}\n{ep['text'] if text is None else text}"


def units(episodes: list[dict], chunk: str) -> list[tuple[dict, str]]:
    """What gets embedded: whole episodes (the default, as specified), or one unit per non-empty line ("lines": a
    sturdier baseline that can find one entry of a long catalogue)."""
    if chunk == "episode":
        return [(ep, ep["text"]) for ep in episodes]
    out = []
    for ep in episodes:
        out += [(ep, line.strip()) for line in ep["text"].split("\n") if line.strip()] or [(ep, ep["text"])]
    return out


class PlainRag:
    def __init__(self, corpus: Corpus, ollama: Ollama, chat_model: str, embed_model: str = EMBED_MODEL, k: int = K,
                 reasoning: str | None = None, chunk: str = "episode"):
        self.corpus, self.ollama, self.chat_model, self.embed_model, self.k = corpus, ollama, chat_model, embed_model, k
        self.reasoning, self.chunk = reasoning, chunk
        self.query_prefix, doc_prefix = EMBED_PREFIXES.get(embed_model, ("", ""))
        self.cache = EmbeddingCache(ollama, embed_model)
        self.units = units(corpus.episodes, chunk)
        docs = [f"{doc_prefix}{ep['title']}\n{text}" if ep.get("title") and ep["channel"] != "chat"
                else f"{doc_prefix}{text}" for ep, text in self.units]
        self.vectors = [normalize(v) for v in self.cache.vectors(docs)]

    def retrieve(self, question: str) -> list[tuple[dict, str, float]]:
        q = normalize(self.cache.vectors([self.query_prefix + question])[0])
        scored = [(sum(a * b for a, b in zip(q, v)), i) for i, v in enumerate(self.vectors)]
        scored.sort(key=lambda s: (-s[0], self.units[s[1]][0]["id"], s[1]))
        return [(*self.units[i], round(score, 4)) for score, i in scored[:self.k]]

    def ask(self, question: str) -> dict:
        with Stopwatch() as total:
            with Stopwatch() as embed:
                hits = self.retrieve(question)
            records = "\n\n".join(render_record(ep, text) for ep, text, _ in hits)
            messages = [{"role": "system", "content": SYSTEM_PROMPT.format(today=self.corpus.now[:10])},
                        {"role": "user", "content": f"Records:\n\n{records}\n\nQuestion: {question}"}]
            with Stopwatch() as llm:
                answer, usage = self.ollama.chat(self.chat_model, messages, self.reasoning)
        return {"answer": answer, "retrieved": [{"id": ep["id"], "score": sc} for ep, _, sc in hits], "evidence": records,
                **usage, "ms": total.ms, "ms_retrieve": embed.ms, "ms_llm": llm.ms}
