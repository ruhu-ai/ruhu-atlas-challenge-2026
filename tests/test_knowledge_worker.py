from __future__ import annotations

from pathlib import Path

from ruhu import knowledge_worker
from ruhu.knowledge import InMemoryKnowledgeStore, InMemoryKnowledgeVectorIndex, KnowledgeRuntime, KnowledgeService


def _runtime() -> KnowledgeRuntime:
    return KnowledgeRuntime(
        service=KnowledgeService(
            InMemoryKnowledgeStore(),
            vector_index=InMemoryKnowledgeVectorIndex(),
        ),
        default_organization_id="org-worker",
        seed_path=Path(__file__).resolve().parent / "_fixtures" / "data" / "knowledge" / "sales.json",
        auto_seed=True,
        auto_reindex_on_startup=False,
    )


def test_knowledge_worker_status_command_prints_index_health(monkeypatch, capsys) -> None:
    monkeypatch.setattr(knowledge_worker, "_build_runtime", lambda database_url=None: _runtime())

    exit_code = knowledge_worker.main(["status", "--json"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"index_health"' in output
    assert '"guardrails"' in output


def test_knowledge_worker_reindexes_organization(monkeypatch, capsys) -> None:
    monkeypatch.setattr(knowledge_worker, "_build_runtime", lambda database_url=None: _runtime())

    exit_code = knowledge_worker.main(["reindex-organization", "--json", "--timeout-seconds", "5"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert '"status": "completed"' in output
    assert '"indexed_embeddings"' in output
