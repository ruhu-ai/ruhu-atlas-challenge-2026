from __future__ import annotations

import sys
import types

import pytest

from ruhu.secret_sources import load_text_secret, normalize_gcp_secret_version


def test_normalize_gcp_secret_version_requires_explicit_version() -> None:
    with pytest.raises(ValueError):
        normalize_gcp_secret_version("projects/ruhu-dev/secrets/jwt-private-key/versions/latest")


def test_load_text_secret_reads_gcp_secret_manager_payload(monkeypatch) -> None:
    google_module = types.ModuleType("google")
    cloud_module = types.ModuleType("google.cloud")
    secretmanager_module = types.ModuleType("google.cloud.secretmanager_v1")

    class FakeSecretManagerServiceClient:
        def access_secret_version(self, request: dict[str, str]):
            assert request["name"] == "projects/ruhu-dev/secrets/jwt-private-key/versions/5"
            return types.SimpleNamespace(payload=types.SimpleNamespace(data=b"demo-secret-value"))

    secretmanager_module.SecretManagerServiceClient = FakeSecretManagerServiceClient
    cloud_module.secretmanager_v1 = secretmanager_module
    google_module.cloud = cloud_module
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_module)
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager_v1", secretmanager_module)

    assert (
        load_text_secret("projects/ruhu-dev/secrets/jwt-private-key/versions/5")
        == "demo-secret-value"
    )
