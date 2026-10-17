"""
brain/embeddings.py

Turns text into embeddings (lists of numbers representing meaning) for
the brain's semantic search.

- OllamaEmbedder: local, free, via Ollama's nomic-embed-text (ported
  from kmare-code's chatbox/embeddings.py, batched).
- HashEmbedder: deterministic bag-of-words hashing. No model, no
  network; used by tests and as a crude stand-in.

embed() returns None when the embedder isn't available (Ollama not
running, model not pulled). The index then falls back to keyword search
only, rather than failing.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from typing import Protocol
from .. import config

DEFAULT_MODEL = "nomic-embed-text"


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]] | None: ...


class OllamaEmbedder:
    def __init__(self, model: str = DEFAULT_MODEL, base_url: str | None = None, timeout: float = 10):
        self.name = f"ollama:{model}"
        self._model = model
        self._base_url = (base_url or config.get_ollama_base_url()).rstrip("/")
        self._timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not texts:
            return []
        body = json.dumps({"model": self._model, "input": texts}).encode()
        request = urllib.request.Request(f"{self._base_url}/api/embed", data=body,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                data = json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError):
            return None
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            return None
        return vectors


class HashEmbedder:
    """Feature hashing over lower-cased words: texts sharing words land
    close together. Only for tests and when nothing better exists."""

    def __init__(self, dimensions: int = 256):
        self.name = f"hash:{dimensions}"
        self._dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        vectors = []
        for text in texts:
            vector = [0.0] * self._dimensions
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                digest = hashlib.blake2b(word.encode(), digest_size=4).digest()
                vector[int.from_bytes(digest, "big") % self._dimensions] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([v / norm for v in vector])
        return vectors


class NoEmbedder:
    name = "none"

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        return None
