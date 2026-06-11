from __future__ import annotations

import json

import pytest

from ruhu.runtime_config import RuntimeSettings

TEST_HS256_SECRET = "0123456789abcdef0123456789abcdef"


def test_runtime_settings_parse_agent_interpreters_from_env(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_DATABASE_URL", "postgresql+psycopg://ruhu:secret@localhost:5432/ruhu_runtime_dev")
    monkeypatch.setenv("RUHU_ENVIRONMENT", "staging")
    monkeypatch.setenv("RUHU_INTERPRETER", "gemma_local")
    monkeypatch.setenv("RUHU_CLASSIFIER_MODEL_PATH", "/tmp/custom-gemma")
    monkeypatch.setenv("RUHU_INTENT_TAGS_CLASSIFIER_BASE_URL", "http://classifier.internal:8011")
    monkeypatch.setenv("RUHU_INTENT_TAGS_CLASSIFIER_API_KEY", "classifier-api-key")
    monkeypatch.setenv("RUHU_INTENT_TAGS_CLASSIFIER_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("RUHU_INTENT_TAGS_CLASSIFIER_MAX_RETRIES", "4")
    monkeypatch.setenv("RUHU_INTENT_TAGS_CLASSIFIER_RETRY_BACKOFF_SECONDS", "0.75")
    monkeypatch.setenv("RUHU_AUTH_DATABASE_URL", "postgresql+psycopg://ruhu:secret@localhost:5432/ruhu_auth")
    monkeypatch.setenv("RUHU_AUTH_REQUIRE_ASYMMETRIC_TOKENS", "true")
    monkeypatch.setenv("RUHU_AUTH_JWT_SECRET", TEST_HS256_SECRET)
    monkeypatch.setenv("RUHU_AUTH_JWT_ISSUER", "ruhu-test")
    monkeypatch.setenv("RUHU_AUTH_JWT_PRIVATE_KEY_PEM", "test-private-key-pem-value")
    monkeypatch.setenv("RUHU_AUTH_JWT_PRIVATE_KEY_PATH", "/tmp/ruhu-jwt-private.pem")
    monkeypatch.setenv(
        "RUHU_AUTH_JWT_PRIVATE_KEY_SECRET_VERSION",
        "projects/ruhu-dev/secrets/jwt-private-key/versions/3",
    )
    monkeypatch.setenv("RUHU_AUTH_JWT_ACTIVE_KID", "kid-active")
    monkeypatch.setenv("RUHU_AUTH_JWT_VERIFICATION_JWKS", '{"keys":[{"kty":"RSA","kid":"kid-older"}]}')
    monkeypatch.setenv("RUHU_AUTH_JWT_VERIFICATION_JWKS_PATH", "/tmp/ruhu-jwks.json")
    monkeypatch.setenv(
        "RUHU_AUTH_JWT_VERIFICATION_JWKS_SECRET_VERSION",
        "projects/ruhu-dev/secrets/jwt-jwks/versions/7",
    )
    monkeypatch.setenv("RUHU_FRONTEND_URL", "http://app.example.com")
    monkeypatch.setenv("RUHU_AUTH_ALLOWED_REDIRECT_ORIGINS", '["http://app.example.com","https://console.example.com"]')
    monkeypatch.setenv("RUHU_GOOGLE_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("RUHU_GOOGLE_CLIENT_SECRET", "google-client-secret")
    monkeypatch.setenv("RUHU_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("RUHU_SMTP_PORT", "2525")
    monkeypatch.setenv("RUHU_SMTP_USER", "smtp-user")
    monkeypatch.setenv("RUHU_SMTP_PASSWORD", "smtp-password")
    monkeypatch.setenv("RUHU_SMTP_FROM_EMAIL", "noreply@example.com")
    monkeypatch.setenv("RUHU_SMTP_FROM_NAME", "Ruhu Mailer")
    monkeypatch.setenv("RUHU_SMTP_STARTTLS", "false")
    monkeypatch.setenv("RUHU_SIMULATION_EVAL_WORKERS", "4")
    monkeypatch.setenv("RUHU_JOURNEY_RUNTIME_WORKERS", "3")
    monkeypatch.setenv("RUHU_JOURNEY_RUNTIME_EMBEDDED_WORKER_ENABLED", "false")
    monkeypatch.setenv("RUHU_JOURNEY_RUNTIME_POLL_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("RUHU_JOURNEY_RUNTIME_JOB_LEASE_SECONDS", "180")
    monkeypatch.setenv("RUHU_JOURNEY_RUNTIME_JOB_HEARTBEAT_INTERVAL_SECONDS", "20")
    monkeypatch.setenv("RUHU_JOURNEY_RUNTIME_FAILURE_ALERT_THRESHOLD", "4")
    monkeypatch.setenv("RUHU_JOURNEY_RUNTIME_FAILURE_ALERT_WINDOW_SECONDS", "600")
    monkeypatch.setenv("RUHU_BROWSER_TASK_WORKER_ENABLED", "true")
    monkeypatch.setenv("RUHU_BROWSER_TASK_WORKER_ADAPTER", "cloud-browser")
    monkeypatch.setenv("RUHU_BROWSER_TASK_WORKER_IDENTITY", "browser-worker-a")
    monkeypatch.setenv("RUHU_BROWSER_TASK_WORKER_POLL_INTERVAL_SECONDS", "3.5")
    monkeypatch.setenv("RUHU_BROWSER_TASK_WORKER_BATCH_SIZE", "2")
    monkeypatch.setenv("RUHU_BROWSER_TASK_WORKER_LEASE_SECONDS", "90")
    monkeypatch.setenv("RUHU_BROWSER_TASK_WORKER_HEARTBEAT_INTERVAL_SECONDS", "12")
    monkeypatch.setenv("RUHU_BROWSER_TASK_PACK_PATH", "/tmp/browser-task-packs")
    monkeypatch.setenv("RUHU_BROWSER_TASK_ALLOWED_PACKS", "invoice_lookup, ticket_status_lookup")
    monkeypatch.setenv("RUHU_JOURNEY_ABANDONMENT_SWEEP_ENABLED", "true")
    monkeypatch.setenv("RUHU_JOURNEY_ABANDONMENT_SWEEP_INTERVAL_SECONDS", "45")
    monkeypatch.setenv("RUHU_TICKETING_RETRY_WORKER_ENABLED", "true")
    monkeypatch.setenv("RUHU_TICKETING_RETRY_INTERVAL_SECONDS", "15")
    monkeypatch.setenv("RUHU_TICKETING_RETRY_BATCH_SIZE", "40")
    monkeypatch.setenv("RUHU_SEMANTIC_SUMMARY_WEBHOOK_WORKER_ENABLED", "true")
    monkeypatch.setenv("RUHU_SEMANTIC_SUMMARY_WEBHOOK_INTERVAL_SECONDS", "12.5")
    monkeypatch.setenv("RUHU_SEMANTIC_SUMMARY_WEBHOOK_BATCH_SIZE", "55")
    monkeypatch.setenv(
        "RUHU_AGENT_INTERPRETERS",
        json.dumps(
            {
                "sales": "gemma_local",
                "support_triage": "support_triage",
            }
        ),
    )

    settings = RuntimeSettings.from_env()

    assert settings.database_url == "postgresql+psycopg://ruhu:secret@localhost:5432/ruhu_runtime_dev"
    assert settings.environment == "staging"
    assert settings.interpreter_name == "gemma_local"
    assert str(settings.classifier_model_path) == "/tmp/custom-gemma"
    assert settings.intent_tags_classifier_base_url == "http://classifier.internal:8011"
    assert settings.intent_tags_classifier_api_key == "classifier-api-key"
    assert settings.intent_tags_classifier_timeout_seconds == 7.5
    assert settings.intent_tags_classifier_max_retries == 4
    assert settings.intent_tags_classifier_retry_backoff_seconds == 0.75
    assert settings.auth_database_url == "postgresql+psycopg://ruhu:secret@localhost:5432/ruhu_auth"
    assert settings.auth_require_asymmetric_tokens is True
    assert settings.auth_jwt_secret == TEST_HS256_SECRET
    assert settings.auth_jwt_issuer == "ruhu-test"
    assert settings.auth_jwt_private_key_pem == "test-private-key-pem-value"
    assert str(settings.auth_jwt_private_key_path) == "/tmp/ruhu-jwt-private.pem"
    assert (
        settings.auth_jwt_private_key_secret_version
        == "projects/ruhu-dev/secrets/jwt-private-key/versions/3"
    )
    assert settings.auth_jwt_active_kid == "kid-active"
    assert settings.auth_jwt_verification_jwks == '{"keys":[{"kty":"RSA","kid":"kid-older"}]}'
    assert str(settings.auth_jwt_verification_jwks_path) == "/tmp/ruhu-jwks.json"
    assert (
        settings.auth_jwt_verification_jwks_secret_version
        == "projects/ruhu-dev/secrets/jwt-jwks/versions/7"
    )
    assert settings.frontend_url == "http://app.example.com"
    assert settings.auth_allowed_redirect_origins == [
        "http://app.example.com",
        "https://console.example.com",
    ]
    assert settings.google_client_id == "google-client-id"
    assert settings.google_client_secret == "google-client-secret"
    assert settings.smtp_host == "smtp.example.com"
    assert settings.smtp_port == 2525
    assert settings.smtp_user == "smtp-user"
    assert settings.smtp_password == "smtp-password"
    assert settings.smtp_from_email == "noreply@example.com"
    assert settings.smtp_from_name == "Ruhu Mailer"
    assert settings.smtp_starttls is False
    assert settings.simulation_eval_workers == 4
    assert settings.journey_runtime_workers == 3
    assert settings.journey_runtime_embedded_worker_enabled is False
    assert settings.journey_runtime_poll_interval_seconds == 2.5
    assert settings.journey_runtime_job_lease_seconds == 180.0
    assert settings.journey_runtime_job_heartbeat_interval_seconds == 20.0
    assert settings.journey_runtime_failure_alert_threshold == 4
    assert settings.journey_runtime_failure_alert_window_seconds == 600.0
    assert settings.browser_task_worker_enabled is True
    assert settings.browser_task_worker_adapter == "cloud-browser"
    assert settings.browser_task_worker_identity == "browser-worker-a"
    assert settings.browser_task_worker_poll_interval_seconds == 3.5
    assert settings.browser_task_worker_batch_size == 2
    assert settings.browser_task_worker_lease_seconds == 90
    assert settings.browser_task_worker_heartbeat_interval_seconds == 12.0
    assert str(settings.browser_task_pack_path) == "/tmp/browser-task-packs"
    assert settings.browser_task_allowed_packs == ("invoice_lookup", "ticket_status_lookup")
    assert settings.journey_abandonment_sweep_enabled is True
    assert settings.journey_abandonment_sweep_interval_seconds == 45.0
    assert settings.ticketing_retry_worker_enabled is True
    assert settings.ticketing_retry_interval_seconds == 15.0
    assert settings.ticketing_retry_batch_size == 40
    assert settings.semantic_summary_webhook_worker_enabled is True
    assert settings.semantic_summary_webhook_interval_seconds == 12.5
    assert settings.semantic_summary_webhook_batch_size == 55
    assert settings.agent_interpreters == {
        "sales": "gemma_local",
        "support_triage": "support_triage",
    }


def test_runtime_settings_parse_intent_tags_classifier_api_key_secret_version(monkeypatch) -> None:
    monkeypatch.setenv(
        "RUHU_INTENT_TAGS_CLASSIFIER_API_KEY_SECRET_VERSION",
        "projects/ruhu-dev/secrets/intent-tags-classifier-api-key/versions/4",
    )

    settings = RuntimeSettings.from_env()

    assert (
        settings.intent_tags_classifier_api_key_secret_version
        == "projects/ruhu-dev/secrets/intent-tags-classifier-api-key/versions/4"
    )


def test_runtime_settings_accept_old_smtp_and_frontend_env_names(monkeypatch) -> None:
    monkeypatch.delenv("RUHU_FRONTEND_URL", raising=False)
    monkeypatch.delenv("RUHU_SMTP_HOST", raising=False)
    monkeypatch.delenv("RUHU_SMTP_PORT", raising=False)
    monkeypatch.delenv("RUHU_SMTP_USER", raising=False)
    monkeypatch.delenv("RUHU_SMTP_PASSWORD", raising=False)
    monkeypatch.delenv("RUHU_SMTP_FROM_EMAIL", raising=False)
    monkeypatch.delenv("RUHU_SMTP_FROM_NAME", raising=False)
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3001")
    monkeypatch.setenv("SMTP_HOST", "smtp.resend.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "resend")
    monkeypatch.setenv("SMTP_PASSWORD", "resend-secret")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "hello@ruhu.ai")
    monkeypatch.setenv("SMTP_FROM_NAME", "Ruhu AI")

    settings = RuntimeSettings.from_env()

    assert settings.frontend_url == "http://localhost:3001"
    assert settings.smtp_host == "smtp.resend.com"
    assert settings.smtp_port == 587
    assert settings.smtp_user == "resend"
    assert settings.smtp_password == "resend-secret"
    assert settings.smtp_from_email == "hello@ruhu.ai"
    assert settings.smtp_from_name == "Ruhu AI"


def test_runtime_settings_reads_whatsapp_json_config(monkeypatch) -> None:
    monkeypatch.setenv(
        "RUHU_WHATSAPP_META_CHANNELS",
        json.dumps(
            {
                "new-phone-id": {
                    "agent_id": "json-agent",
                    "phone_number_id": "new-phone-id",
                    "verify_token": "json-verify",
                    "access_token": "json-access",
                    "app_secret": "json-secret",
                }
            }
        ),
    )

    settings = RuntimeSettings.from_env()

    assert settings.whatsapp_meta_channels == {
        "new-phone-id": {
            "agent_id": "json-agent",
            "phone_number_id": "new-phone-id",
            "verify_token": "json-verify",
            "access_token": "json-access",
            "app_secret": "json-secret",
        }
    }


def test_runtime_settings_parse_phone_number_routes_json(monkeypatch) -> None:
    monkeypatch.setenv(
        "RUHU_PHONE_NUMBER_ROUTES",
        json.dumps(
            {
                "support_line": {
                    "phone_number": "+2348012345678",
                    "agent_id": "sales",
                    "organization_id": "org-demo",
                    "provider": "telnyx",
                }
            }
        ),
    )

    settings = RuntimeSettings.from_env()

    assert settings.phone_number_routes == {
        "support_line": {
            "phone_number": "+2348012345678",
            "agent_id": "sales",
            "organization_id": "org-demo",
            "provider": "telnyx",
        }
    }


def test_runtime_settings_parse_telnyx_config(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_TELNYX_API_KEY", "telnyx-live-key")
    monkeypatch.setenv("RUHU_TELNYX_API_BASE_URL", "https://telnyx.example.test/v2")
    monkeypatch.setenv("RUHU_TELNYX_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("RUHU_PHONE_NUMBER_RECONCILIATION_INTERVAL_SECONDS", "180")
    monkeypatch.setenv("RUHU_PHONE_NUMBER_RECONCILIATION_BATCH_SIZE", "25")

    settings = RuntimeSettings.from_env()

    assert settings.telnyx_api_key == "telnyx-live-key"
    assert settings.telnyx_api_base_url == "https://telnyx.example.test/v2"
    assert settings.telnyx_timeout_seconds == 12.5
    assert settings.phone_number_reconciliation_interval_seconds == 180.0
    assert settings.phone_number_reconciliation_batch_size == 25


def test_runtime_settings_reject_latest_secret_manager_versions(monkeypatch) -> None:
    monkeypatch.setenv(
        "RUHU_AUTH_JWT_PRIVATE_KEY_SECRET_VERSION",
        "projects/ruhu-dev/secrets/jwt-private-key/versions/latest",
    )

    try:
        RuntimeSettings.from_env()
    except ValueError as exc:
        assert "pin an explicit secret version" in str(exc)
    else:
        raise AssertionError("expected RuntimeSettings.from_env() to reject /versions/latest")


def test_runtime_settings_default_to_asymmetric_tokens_in_production(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_ENVIRONMENT", "production")

    settings = RuntimeSettings.from_env()

    assert settings.environment == "production"
    assert settings.auth_require_asymmetric_tokens is True


# ─────────────────────────────────────────────────────────────────────────────
# WI-3 of doc 36: master kill switch for LLM move selection.
# ─────────────────────────────────────────────────────────────────────────────


def test_llm_move_selection_enabled_defaults_false(monkeypatch) -> None:
    monkeypatch.delenv("RUHU_LLM_MOVE_SELECTION_ENABLED", raising=False)
    settings = RuntimeSettings.from_env()
    assert settings.llm_move_selection_enabled is False


def test_llm_move_selection_enabled_parses_true(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_LLM_MOVE_SELECTION_ENABLED", "true")
    settings = RuntimeSettings.from_env()
    assert settings.llm_move_selection_enabled is True


def test_llm_move_selection_enabled_parses_false(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_LLM_MOVE_SELECTION_ENABLED", "false")
    settings = RuntimeSettings.from_env()
    assert settings.llm_move_selection_enabled is False


def test_llm_move_selection_enabled_accepts_truthy_synonyms(monkeypatch) -> None:
    for value in ("1", "yes", "on", "TRUE"):
        monkeypatch.setenv("RUHU_LLM_MOVE_SELECTION_ENABLED", value)
        settings = RuntimeSettings.from_env()
        assert settings.llm_move_selection_enabled is True, f"value={value!r} should be True"


def test_llm_move_selection_enabled_accepts_falsy_synonyms(monkeypatch) -> None:
    for value in ("0", "no", "off", "FALSE"):
        monkeypatch.setenv("RUHU_LLM_MOVE_SELECTION_ENABLED", value)
        settings = RuntimeSettings.from_env()
        assert settings.llm_move_selection_enabled is False, f"value={value!r} should be False"


# ─────────────────────────────────────────────────────────────────────────────
# WI-3.3: prefill-first classifier env knobs
# ─────────────────────────────────────────────────────────────────────────────


def test_classifier_settings_default_when_no_env(monkeypatch) -> None:
    for var in (
        "RUHU_CLASSIFIER_BACKEND",
        "RUHU_CLASSIFIER_BASE_URL",
        "RUHU_CLASSIFIER_MODEL",
        "RUHU_CLASSIFIER_TIMEOUT_MS",
        "RUHU_CLASSIFIER_LORA_STORAGE_URI",
        "RUHU_CLASSIFIER_MODEL_PATH",
    ):
        monkeypatch.delenv(var, raising=False)
    settings = RuntimeSettings.from_env()
    assert settings.classifier_backend == "transformers"
    assert settings.classifier_base_url is None
    assert settings.classifier_model is None
    assert settings.classifier_timeout_ms == 500
    assert settings.classifier_lora_storage_uri is None
    assert str(settings.classifier_model_path) == "/tmp/gemma-4-E4B-it"


def test_classifier_settings_parse_full_env(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_CLASSIFIER_BACKEND", "vllm")
    monkeypatch.setenv("RUHU_CLASSIFIER_BASE_URL", "http://vllm.classifier.svc.cluster.local:8000")
    monkeypatch.setenv("RUHU_CLASSIFIER_MODEL", "Qwen/Qwen3-8B")
    monkeypatch.setenv("RUHU_CLASSIFIER_TIMEOUT_MS", "750")
    monkeypatch.setenv("RUHU_CLASSIFIER_LORA_STORAGE_URI", "gs://ruhu-classifier-loras/")
    settings = RuntimeSettings.from_env()
    assert settings.classifier_backend == "vllm"
    assert settings.classifier_base_url == "http://vllm.classifier.svc.cluster.local:8000"
    assert settings.classifier_model == "Qwen/Qwen3-8B"
    assert settings.classifier_timeout_ms == 750
    assert settings.classifier_lora_storage_uri == "gs://ruhu-classifier-loras/"


def test_classifier_backend_is_lowercased_and_trimmed(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_CLASSIFIER_BACKEND", "  VLLM  ")
    settings = RuntimeSettings.from_env()
    assert settings.classifier_backend == "vllm"


def test_classifier_model_path_blank_env_means_none(monkeypatch) -> None:
    """Passing RUHU_CLASSIFIER_MODEL_PATH="" explicitly clears it (vllm-only deployments)."""
    monkeypatch.setenv("RUHU_CLASSIFIER_MODEL_PATH", "")
    settings = RuntimeSettings.from_env()
    assert settings.classifier_model_path is None


def test_classifier_model_path_unset_keeps_dev_default(monkeypatch) -> None:
    monkeypatch.delenv("RUHU_CLASSIFIER_MODEL_PATH", raising=False)
    settings = RuntimeSettings.from_env()
    assert str(settings.classifier_model_path) == "/tmp/gemma-4-E4B-it"


def test_classifier_timeout_ms_invalid_value_raises(monkeypatch) -> None:
    """Strict parse — fail loudly instead of silently masking config errors."""
    monkeypatch.setenv("RUHU_CLASSIFIER_TIMEOUT_MS", "not-an-int")
    with pytest.raises(ValueError):
        RuntimeSettings.from_env()


def test_blob_store_s3_region_defaults_to_london(monkeypatch) -> None:
    """When no region env var is set, the loader defaults to eu-west-2 (London)."""
    monkeypatch.delenv("RUHU_BLOB_STORE_S3_REGION", raising=False)
    monkeypatch.delenv("BLOB_STORE_S3_REGION", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)

    settings = RuntimeSettings.from_env()

    assert settings.blob_store_s3_region == "eu-west-2"


def test_blob_store_s3_region_respects_explicit_override(monkeypatch) -> None:
    """An explicit env override beats the London default."""
    monkeypatch.setenv("RUHU_BLOB_STORE_S3_REGION", "us-east-1")
    settings = RuntimeSettings.from_env()
    assert settings.blob_store_s3_region == "us-east-1"


def test_blob_store_s3_region_picks_up_aws_region_env(monkeypatch) -> None:
    """Standard AWS_REGION env var also wins over the default."""
    monkeypatch.delenv("RUHU_BLOB_STORE_S3_REGION", raising=False)
    monkeypatch.delenv("BLOB_STORE_S3_REGION", raising=False)
    monkeypatch.setenv("AWS_REGION", "ap-southeast-2")

    settings = RuntimeSettings.from_env()

    assert settings.blob_store_s3_region == "ap-southeast-2"


def test_capture_audit_retention_settings_parse_env(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_CAPTURE_AUDIT_WORKER_ENABLED", "true")
    monkeypatch.setenv("RUHU_CAPTURE_AUDIT_WORKER_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("RUHU_CAPTURE_AUDIT_RETENTION_DAYS", "120")
    monkeypatch.setenv("RUHU_CAPTURE_AUDIT_RETENTION_SWEEP_BATCH_SIZE", "750")
    monkeypatch.setenv("RUHU_CAPTURE_AUDIT_OUTBOX_BATCH_SIZE", "25")

    settings = RuntimeSettings.from_env()

    assert settings.capture_audit_worker_enabled is True
    assert settings.capture_audit_worker_interval_seconds == 30
    assert settings.capture_audit_retention_days == 120
    assert settings.capture_audit_retention_sweep_batch_size == 750
    assert settings.capture_audit_outbox_batch_size == 25
