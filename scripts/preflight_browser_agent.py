#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ruhu.browser_tasks import load_browser_task_pack_registry
from ruhu.runtime_config import RuntimeSettings


@dataclass(slots=True)
class BrowserAgentPreflightResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def run_preflight(settings: RuntimeSettings | None = None) -> BrowserAgentPreflightResult:
    settings = settings or RuntimeSettings.from_env()
    result = BrowserAgentPreflightResult()

    environment = settings.environment.strip().lower()
    worker_enabled = settings.browser_task_worker_enabled
    adapter = settings.browser_task_worker_adapter.strip().lower()
    isolation_mode = settings.browser_task_worker_isolation_mode.strip().lower()
    allowed_pack_ids = set(settings.browser_task_allowed_packs)

    try:
        registry = load_browser_task_pack_registry(settings.browser_task_pack_path)
    except Exception as exc:
        result.errors.append(f"browser task pack registry failed to load: {exc}")
        registry = None

    if not worker_enabled:
        result.warnings.append("browser task worker is disabled")
        return result

    if adapter == "disabled":
        result.errors.append(
            "RUHU_BROWSER_TASK_WORKER_ENABLED=true requires "
            "RUHU_BROWSER_TASK_WORKER_ADAPTER to be a real adapter"
        )

    if environment == "production" and isolation_mode == "local":
        result.errors.append(
            "production browser workers must not use local isolation; set "
            "RUHU_BROWSER_TASK_WORKER_ISOLATION_MODE=cloud"
        )

    if environment == "production" and not allowed_pack_ids:
        result.errors.append(
            "production browser workers must set RUHU_BROWSER_TASK_ALLOWED_PACKS"
        )

    if registry is None:
        return result

    packs = registry.list_packs()
    registered_ids = {pack.pack_id for pack in packs}
    missing_pack_ids = sorted(allowed_pack_ids - registered_ids)
    if missing_pack_ids:
        result.errors.append(
            "RUHU_BROWSER_TASK_ALLOWED_PACKS contains unknown pack ids: "
            + ", ".join(missing_pack_ids)
        )

    enabled_packs = [pack for pack in packs if not allowed_pack_ids or pack.pack_id in allowed_pack_ids]
    for pack in enabled_packs:
        if not pack.allowed_domains:
            result.errors.append(f"{pack.pack_id}@{pack.version} has no allowed domains")
        if pack.performs_write and "change_confirmation" not in pack.approval_policy.approval_kinds:
            result.errors.append(
                f"{pack.pack_id}@{pack.version} performs writes without change_confirmation approval"
            )
        if pack.execution_policy.allow_downloads:
            if "download" not in pack.artifact_policy.allowed_artifacts:
                result.errors.append(
                    f"{pack.pack_id}@{pack.version} allows downloads without download artifacts"
                )
            if not pack.artifact_policy.allowed_download_content_types:
                result.errors.append(
                    f"{pack.pack_id}@{pack.version} allows downloads without MIME allowlist"
                )
        if pack.execution_policy.allow_uploads:
            upload_actions = []
            if pack.browser_plan is not None:
                upload_actions = [
                    action for action in pack.browser_plan.actions if action.kind == "upload"
                ]
            if not upload_actions:
                result.warnings.append(
                    f"{pack.pack_id}@{pack.version} allows uploads but has no structured upload action"
                )

    return result


def main() -> int:
    result = run_preflight()
    if result.warnings:
        print("Browser agent preflight warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    if result.errors:
        print("Browser agent preflight failed:")
        for error in result.errors:
            print(f"- {error}")
        return 1
    print("Browser agent preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
