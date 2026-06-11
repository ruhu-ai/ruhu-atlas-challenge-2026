from __future__ import annotations

import json
from pathlib import Path

from .models import SeedKnowledgeDocument


def load_seed_documents(path: str | Path) -> list[SeedKnowledgeDocument]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    documents = payload.get("documents", [])
    result: list[SeedKnowledgeDocument] = []
    for item in documents:
        result.append(
            SeedKnowledgeDocument(
                external_id=str(item["id"]),
                title=str(item["title"]),
                content=str(item["content"]),
                tags=[str(tag) for tag in item.get("tags", [])],
                category=None if item.get("category") is None else str(item["category"]),
                summary=None if item.get("summary") is None else str(item["summary"]),
            )
        )
    return result
