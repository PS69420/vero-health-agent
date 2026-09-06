"""
Semantic (content-searchable) memory of what patients have actually said.

agent/memory.py answers "did we call this patient, and when" (episodic).
This module answers "what did they say" -- so the next call or doctor email
can reference it instead of starting from zero each time. This is the RAG
layer that knowledge_base.py's docstring flagged as a future upgrade path.

`LocalTfidfVectorStore` is a real, working vector search implementation --
term-frequency/inverse-document-frequency vectors + cosine similarity,
computed with nothing but the standard library. That's the right size for
the current scale (a handful of short transcripts per patient): no API key,
no embedding cost, no extra dependency. Swap it for a real embedding-API
store (OpenAI/Voyage embeddings + Chroma/pgvector/Pinecone) behind the same
VectorStore interface once transcript volume or cross-patient search quality
calls for it -- same mock-now/real-later pattern as every tool in this
codebase (agent/tools/base.py). Nothing in agent/agents/*.py should need to
change when that swap happens.
"""

from __future__ import annotations

import json
import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from pathlib import Path
from typing import Optional

_WORD_RE = re.compile(r"[a-z0-9']+")
# The opening line of every transcript we generate is a greeting
# confirmation ("Yes, this is <name>.") -- never the informative part, so
# extract_patient_quote() skips it rather than returning it as "the quote."
_GREETING_PREFIXES = ("yes, this is",)


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def extract_patient_quote(transcript_text: str) -> Optional[str]:
    """Pulls the most informative patient line out of a stored "speaker:
    text" transcript (see agents/compliance_agent.py's ingestion format) --
    skipping the opening greeting confirmation so callers get the actual
    complaint/response instead of "Yes, this is Clay." """
    for line in transcript_text.split("\n"):
        if not line.startswith("patient: "):
            continue
        content = line[len("patient: "):]
        if content.lower().startswith(_GREETING_PREFIXES):
            continue
        return content
    return None


class VectorStore(ABC):
    @abstractmethod
    def add(self, patient_id: str, doc_id: str, text: str, metadata: Optional[dict] = None) -> None:
        """Index one document (e.g. a call transcript) for later retrieval.
        Re-adding the same doc_id replaces the previous version."""

    @abstractmethod
    def search(self, patient_id: str, query: str, top_k: int = 3) -> list[dict]:
        """Return up to top_k documents for this patient most relevant to
        `query`, each as {doc_id, text, metadata, score}, best match first.
        Documents are scoped to one patient by design -- this answers "what
        has THIS patient said before," not a cross-patient search."""


class NullVectorStore(VectorStore):
    """Used when no semantic memory is configured -- every call is a no-op,
    so agents can call this interface unconditionally without a None check
    at every call site."""

    def add(self, patient_id: str, doc_id: str, text: str, metadata: Optional[dict] = None) -> None:
        pass

    def search(self, patient_id: str, query: str, top_k: int = 3) -> list[dict]:
        return []


class LocalTfidfVectorStore(VectorStore):
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, list[dict]] = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            with open(self.path) as f:
                return json.load(f)
        return {}

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)

    def add(self, patient_id: str, doc_id: str, text: str, metadata: Optional[dict] = None) -> None:
        docs = self._data.setdefault(patient_id, [])
        docs[:] = [d for d in docs if d["doc_id"] != doc_id]  # idempotent re-ingestion
        docs.append({"doc_id": doc_id, "text": text, "metadata": metadata or {}})
        self._save()

    @staticmethod
    def _vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
        counts = Counter(tokens)
        total = sum(counts.values()) or 1
        return {term: (count / total) * idf.get(term, 0.0) for term, count in counts.items()}

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        common = set(a) & set(b)
        num = sum(a[t] * b[t] for t in common)
        da = math.sqrt(sum(v * v for v in a.values())) or 1.0
        db = math.sqrt(sum(v * v for v in b.values())) or 1.0
        return num / (da * db)

    def search(self, patient_id: str, query: str, top_k: int = 3) -> list[dict]:
        docs = self._data.get(patient_id, [])
        if not docs:
            return []
        doc_tokens = [_tokenize(d["text"]) for d in docs]

        # IDF over this one patient's document set -- a tiny corpus,
        # recomputed per query, which is cheap at this scale.
        doc_freq: Counter = Counter()
        for tokens in doc_tokens:
            doc_freq.update(set(tokens))
        n_docs = len(docs)
        idf = {term: math.log((n_docs + 1) / (df + 1)) + 1.0 for term, df in doc_freq.items()}

        query_vec = self._vector(_tokenize(query), idf)
        scored = []
        for doc, tokens in zip(docs, doc_tokens):
            score = self._cosine(query_vec, self._vector(tokens, idf))
            if score > 0:
                scored.append({"doc_id": doc["doc_id"], "text": doc["text"], "metadata": doc["metadata"], "score": round(score, 4)})
        scored.sort(key=lambda d: d["score"], reverse=True)
        return scored[:top_k]
