from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from math import sqrt
from typing import Any, Protocol, Sequence
import re

import httpx

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "what",
    "when",
    "where",
    "with",
}
_SYNONYM_GROUPS = {
    "pricing": {"pricing", "price", "prices", "cost", "costs", "quote", "quotes", "budget", "billing", "plan", "plans"},
    "workflow": {"workflow", "workflows", "automation", "automations", "automate", "journey", "journeys", "flow", "flows", "process"},
    "channel": {"voice", "phone", "call", "calls", "whatsapp", "webchat", "web", "chat", "channel", "channels"},
    "support": {"support", "help", "faq", "knowledge", "troubleshooting", "assist"},
    "integration": {"integration", "integrations", "api", "apis", "webhook", "webhooks", "connector", "connectors"},
}
_TOKEN_TO_GROUP = {
    token: group
    for group, values in _SYNONYM_GROUPS.items()
    for token in values
}


class EmbeddingProvider(Protocol):
    @property
    def model_key(self) -> str: ...

    @property
    def dimensions(self) -> int | None: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def close(self) -> None: ...


def normalize_embedding(vector: Sequence[float]) -> list[float]:
    norm = sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return [0.0 for _ in vector]
    return [float(value) / norm for value in vector]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def embedding_tokens(text: str) -> list[str]:
    raw_tokens = [token for token in _TOKEN_RE.findall(text.lower()) if token not in _STOPWORDS]
    expanded: list[str] = []
    for token in raw_tokens:
        expanded.append(token)
        group = _TOKEN_TO_GROUP.get(token)
        if group:
            expanded.append(f"group:{group}")
        if len(token) >= 5:
            expanded.extend(f"tri:{token[i:i+3]}" for i in range(len(token) - 2))
    return expanded


@dataclass(slots=True)
class HashingEmbeddingProvider:
    dimensions: int = 192
    _model_key: str = "hashing-v1"

    @property
    def model_key(self) -> str:
        return self._model_key

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def close(self) -> None:
        return None

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = embedding_tokens(text)
        if not tokens:
            return vector
        for token in tokens:
            digest = sha256(token.encode("utf-8")).digest()
            primary_index = int.from_bytes(digest[:4], "big") % self.dimensions
            secondary_index = int.from_bytes(digest[4:8], "big") % self.dimensions
            sign = -1.0 if digest[8] % 2 else 1.0
            weight = 1.0 + (digest[9] / 255.0)
            vector[primary_index] += sign * weight
            vector[secondary_index] += sign * (weight / 2.0)
        return normalize_embedding(vector)


@dataclass(slots=True)
class HostedEmbeddingProvider:
    base_url: str
    model: str
    api_key: str | None = None
    dimensions: int | None = None
    timeout_seconds: float = 20.0
    batch_size: int = 16
    extra_headers: dict[str, str] = field(default_factory=dict)
    _client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = httpx.Client(timeout=self.timeout_seconds)

    @property
    def model_key(self) -> str:
        return f"hosted:{self.model}"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), max(1, self.batch_size)):
            batch = [str(text) for text in texts[start : start + self.batch_size]]
            vectors.extend(self._request_embeddings(batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._request_embeddings([text])[0]

    def close(self) -> None:
        self._client.close()

    def _request_embeddings(self, texts: Sequence[str]) -> list[list[float]]:
        response = self._client.post(
            f"{self.base_url.rstrip('/')}/embeddings",
            json=self._request_payload(texts),
            headers=self._request_headers(),
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("data")
        if not isinstance(items, list):
            raise ValueError("embedding response missing data list")
        ordered = sorted(items, key=lambda item: int(item.get("index", 0)) if isinstance(item, dict) else 0)
        vectors: list[list[float]] = []
        for item in ordered:
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise ValueError("embedding response item missing embedding")
            embedding = [float(value) for value in item["embedding"]]
            vectors.append(normalize_embedding(embedding))
        if len(vectors) != len(texts):
            raise ValueError("embedding response count mismatch")
        return vectors

    def _request_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request_payload(self, texts: Sequence[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": list(texts),
            "encoding_format": "float",
        }
        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions
        return payload
