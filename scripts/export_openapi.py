#!/usr/bin/env python3
"""Export the FastAPI OpenAPI schema (RP-4.2).

The schema is the single source of truth for frontend API types:
``frontend/src/api/generated/openapi.d.ts`` is generated from this output
(``make openapi-types``) and CI fails when the generated file drifts from
the backend's actual schema.

Builds a lightweight app (in-memory kernel, no database) — route and model
declarations are import-time, so the schema is complete without
infrastructure.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "openapi.json",
        help="Where to write the schema (default: ./openapi.json)",
    )
    args = parser.parse_args()

    from ruhu.api import create_app
    from ruhu.composition import build_minimal_runtime
    from ruhu.kernel import ConversationKernel
    from ruhu.registry import FileAgentRegistry

    app = create_app(
        build_minimal_runtime(
            kernel=ConversationKernel(),
            agent_registry=FileAgentRegistry(REPO_ROOT / "tests" / "_fixtures" / "data" / "agents"),
        )
    )
    schema = app.openapi()
    args.output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(
        f"wrote {args.output} — {len(schema.get('paths', {}))} paths, "
        f"{len(schema.get('components', {}).get('schemas', {}))} schemas"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
