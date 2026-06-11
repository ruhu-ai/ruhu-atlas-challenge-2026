from __future__ import annotations

import re


class SecretSourceConfigurationError(ValueError):
    pass


_GCP_SECRET_VERSION_PATTERN = re.compile(
    r"^projects/[^/]+/secrets/[^/]+/versions/[^/]+$"
)


def normalize_gcp_secret_version(resource_name: str) -> str:
    value = resource_name.strip()
    if not value:
        raise SecretSourceConfigurationError("secret version resource name must not be empty")
    if not _GCP_SECRET_VERSION_PATTERN.fullmatch(value):
        raise SecretSourceConfigurationError(
            "secret version resource must match projects/<project>/secrets/<name>/versions/<version>"
        )
    if value.endswith("/versions/latest"):
        raise SecretSourceConfigurationError(
            "pin an explicit secret version instead of using /versions/latest"
        )
    return value


def load_text_secret(resource_name: str) -> str:
    normalized_name = normalize_gcp_secret_version(resource_name)
    try:
        from google.cloud import secretmanager_v1
    except ImportError as exc:
        raise SecretSourceConfigurationError(
            "google-cloud-secret-manager is required when auth signing material is sourced from Secret Manager"
        ) from exc

    client = secretmanager_v1.SecretManagerServiceClient()
    response = client.access_secret_version(request={"name": normalized_name})
    payload = getattr(getattr(response, "payload", None), "data", None)
    if not isinstance(payload, (bytes, bytearray)):
        raise SecretSourceConfigurationError(
            f"secret manager response for {normalized_name!r} did not contain a text payload"
        )
    try:
        return bytes(payload).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretSourceConfigurationError(
            f"secret manager response for {normalized_name!r} was not valid UTF-8 text"
        ) from exc
