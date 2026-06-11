from __future__ import annotations

from pathlib import Path
import sys

from ruhu.runtime_config import RuntimeSettings

import importlib.util


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "preflight_browser_agent.py"
SPEC = importlib.util.spec_from_file_location("preflight_browser_agent", SCRIPT_PATH)
assert SPEC is not None
preflight = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = preflight
SPEC.loader.exec_module(preflight)


def _settings(**overrides: object) -> RuntimeSettings:
    values = {
        "environment": "production",
        "browser_task_worker_enabled": True,
        "browser_task_worker_adapter": "playwright",
        "browser_task_worker_isolation_mode": "cloud",
        "browser_task_allowed_packs": ("invoice_lookup",),
    }
    values.update(overrides)
    return RuntimeSettings(**values)


def test_preflight_passes_for_cloud_worker_with_allowed_pack() -> None:
    result = preflight.run_preflight(_settings())

    assert result.ok
    assert result.errors == []


def test_preflight_rejects_local_isolation_in_production() -> None:
    result = preflight.run_preflight(
        _settings(browser_task_worker_isolation_mode="local")
    )

    assert not result.ok
    assert any("must not use local isolation" in error for error in result.errors)


def test_preflight_rejects_missing_allowed_packs_in_production() -> None:
    result = preflight.run_preflight(_settings(browser_task_allowed_packs=()))

    assert not result.ok
    assert any("RUHU_BROWSER_TASK_ALLOWED_PACKS" in error for error in result.errors)


def test_preflight_rejects_unknown_allowed_pack() -> None:
    result = preflight.run_preflight(
        _settings(browser_task_allowed_packs=("missing_pack",))
    )

    assert not result.ok
    assert any("unknown pack ids" in error for error in result.errors)


def test_preflight_warns_when_worker_disabled() -> None:
    result = preflight.run_preflight(
        _settings(browser_task_worker_enabled=False, browser_task_allowed_packs=())
    )

    assert result.ok
    assert "browser task worker is disabled" in result.warnings
