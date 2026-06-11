#!/bin/bash
set -euo pipefail

if [ "${1:-}" = "" ]; then
  echo "usage: $0 <venv-path>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PATH="$1"

rm -rf "${VENV_PATH}"
python3 -m venv "${VENV_PATH}"

"${VENV_PATH}/bin/pip" install -U pip
"${VENV_PATH}/bin/pip" install -e "${REPO_ROOT}[api,dev,browser-e2e]"

cd "${REPO_ROOT}"
"${VENV_PATH}/bin/python" <<'PY'
import asyncio
from pathlib import Path

import httpx

from ruhu.api import create_app
from ruhu.knowledge import KnowledgeRuntime, KnowledgeService, InMemoryKnowledgeStore
from ruhu.kernel import ConversationKernel
from ruhu.registry import FileGraphRegistry
from ruhu.tools.production import ProductionToolBackend


class _StubDemoLeadStore:
    def create_or_get(self, **kwargs):
        raise NotImplementedError


async def main() -> None:
    repo_root = Path.cwd()
    graph_root = repo_root / "tests" / "_fixtures" / "data" / "graphs"
    knowledge_runtime = KnowledgeRuntime(
        service=KnowledgeService(InMemoryKnowledgeStore()),
        default_organization_id="public",
        seed_path=repo_root / "tests" / "_fixtures" / "data" / "knowledge" / "sales.json",
        auto_seed=True,
        auto_reindex_on_startup=False,
    )
    knowledge_runtime.startup()
    app = create_app(
        kernel=ConversationKernel(),
        graph_registry=FileGraphRegistry(graph_root),
        knowledge_runtime=knowledge_runtime,
        tool_backend=ProductionToolBackend(
            knowledge_service=knowledge_runtime.service,
            demo_leads=_StubDemoLeadStore(),
        ),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        health = await client.get("/ready")
        assert health.status_code == 200, health.text

        upload = await client.post(
            "/knowledge/documents/upload",
            data={
                "title": "Release Hygiene Notes",
                "status": "published",
                "tags": '["release","smoke"]',
            },
            files={"file": ("release-notes.txt", b"release hygiene smoke check", "text/plain")},
        )
        assert upload.status_code == 201, upload.text
        payload = upload.json()
        assert payload["title"] == "Release Hygiene Notes"
        assert payload["source_ref"] == "release-notes.txt"
        assert payload["source_kind"] == "file"

    knowledge_runtime.shutdown()


asyncio.run(main())
PY

echo "release hygiene smoke completed successfully"
