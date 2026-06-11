from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .secret_sources import normalize_gcp_secret_version


@dataclass(slots=True, frozen=True)
class RuntimeSettings:
    database_url: str | None = None
    # ── DB pool configuration ──────────────────────────────────────────────────
    sync_db_pool_size: int = 20
    sync_db_max_overflow: int = 40
    sync_db_pool_recycle: int = 1800
    sync_db_pool_timeout: float = 30.0
    sync_db_statement_timeout_ms: int = 30_000
    async_db_pool_size: int = 20
    async_db_max_overflow: int = 40
    async_db_pool_recycle: int = 1800
    async_db_pool_timeout: float = 30.0
    async_db_statement_timeout_ms: int = 30_000
    environment: str = "development"
    interpreter_name: str | None = None
    classifier_model_path: Path | None = Path("/tmp/gemma-4-E4B-it")
    agent_interpreters: dict[str, str] = field(default_factory=dict)
    # ── Prefill-first classifier (Stage 3+) ───────────────────────────────────
    # See docs/pre-fill-intent-classifier-design/04-runtime-spec.md.
    classifier_backend: str = "transformers"
    classifier_base_url: str | None = None
    classifier_model: str | None = None
    classifier_timeout_ms: int = 500
    classifier_lora_storage_uri: str | None = None
    # WI-5.5: runtime kill-switch. "single" (default) lets the prefill-first
    # classifier run normally. "off" forces the kernel to skip the
    # SemanticInterpreter call entirely — only pre_classified events
    # attached to the turn upstream survive. Used in disaster recovery
    # when the classifier subsystem itself is the failure mode and
    # backend selection (RUHU_CLASSIFIER_BACKEND) can't help.
    classifier_mode: str = "single"
    intent_tags_classifier_base_url: str | None = None
    intent_tags_classifier_api_key: str | None = None
    intent_tags_classifier_api_key_secret_version: str | None = None
    intent_tags_classifier_timeout_seconds: float = 5.0
    intent_tags_classifier_max_retries: int = 2
    intent_tags_classifier_retry_backoff_seconds: float = 0.25
    whatsapp_meta_channels: dict[str, dict[str, object]] = field(default_factory=dict)
    phone_number_routes: dict[str, dict[str, object]] = field(default_factory=dict)
    telnyx_api_key: str | None = None
    telnyx_api_base_url: str = "https://api.telnyx.com/v2"
    telnyx_timeout_seconds: float = 10.0
    africastalking_api_key: str | None = None
    africastalking_username: str | None = None
    africastalking_sandbox: bool = False
    africastalking_timeout_seconds: float = 10.0
    phone_number_reconciliation_interval_seconds: float = 300.0
    phone_number_reconciliation_batch_size: int = 50
    livekit_server_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    livekit_agent_name: str = "ruhu-voice"
    livekit_room_prefix: str = "ruhu"
    livekit_phone_provider: str = "livekit"
    livekit_agents_sdk_version_target: str = "1.5.2"
    livekit_voice_mode: str = "pipeline"
    livekit_dispatch_strategy: str = "hybrid"
    livekit_metadata: dict[str, object] = field(default_factory=dict)
    livekit_control_plane_base_url: str | None = None
    livekit_room_metadata_secret: str = ""
    provider_shared_secret: str | None = None
    internal_api_secret: str | None = None
    auth_database_url: str | None = None
    auth_require_asymmetric_tokens: bool = False
    auth_jwt_secret: str | None = None
    auth_jwt_issuer: str = "ruhu"
    auth_jwt_private_key_pem: str | None = None
    auth_jwt_private_key_path: Path | None = None
    auth_jwt_private_key_secret_version: str | None = None
    auth_jwt_active_kid: str | None = None
    auth_jwt_verification_jwks: str | None = None
    auth_jwt_verification_jwks_path: Path | None = None
    auth_jwt_verification_jwks_secret_version: str | None = None
    frontend_url: str | None = None
    auth_allowed_redirect_origins: list[str] = field(default_factory=list)
    google_client_id: str | None = None
    google_client_secret: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str = "noreply@ruhu.ai"
    smtp_from_name: str = "Ruhu AI"
    smtp_starttls: bool = True
    email_provider: str | None = None
    resend_api_key: str | None = None
    resend_from_email: str | None = None
    resend_from_name: str | None = None
    resend_timeout_seconds: float = 10.0
    blob_store_backend: str = "in_memory"
    blob_store_bucket: str | None = None
    blob_store_s3_region: str | None = None
    blob_store_gcs_project: str | None = None
    blob_store_local_root: str | None = None
    knowledge_default_organization_id: str | None = None
    knowledge_seed_path: Path | None = None
    # Enterprise posture: never auto-seed demo data into any tenant.
    # Seeding is a development convenience that becomes a production
    # liability (cross-tenant data pollution, stale fixtures).  Tests use
    # explicit fixtures; opt in to seeding via RUHU_KNOWLEDGE_AUTO_SEED=true.
    knowledge_auto_seed: bool = False
    knowledge_auto_reindex_on_startup: bool = True
    knowledge_reindex_workers: int = 1
    knowledge_embedding_base_url: str | None = None
    knowledge_embedding_api_key: str | None = None
    knowledge_embedding_model: str | None = None
    knowledge_embedding_dimensions: int | None = None
    knowledge_embedding_timeout_seconds: float = 20.0
    knowledge_max_file_bytes: int = 5 * 1024 * 1024
    knowledge_max_chunks_per_document: int = 128
    knowledge_chunk_max_words: int = 120
    knowledge_chunk_overlap_words: int = 24
    knowledge_weaviate_enabled: bool = False
    knowledge_weaviate_host: str = "localhost"
    knowledge_weaviate_port: int = 8080
    knowledge_weaviate_grpc_port: int = 50051
    knowledge_weaviate_collection: str = "KnowledgeChunk"
    knowledge_weaviate_retry_attempts: int = 2
    knowledge_weaviate_backoff_base_seconds: float = 0.1
    knowledge_weaviate_backoff_max_seconds: float = 1.0
    attachments_max_file_bytes: int = 10 * 1024 * 1024
    attachments_workers: int = 4
    # Retention sweep: opt-in per-deployment.  The canonical spec §"Storage
    # Impact" and implementation plan item 9 require this worker for
    # compliant retention enforcement.
    attachments_retention_sweep_enabled: bool = False
    attachments_retention_sweep_interval_seconds: float = 600.0
    attachments_retention_sweep_batch_size: int = 100
    # Grace period between soft-delete (deleted_at set) and hard-delete
    # (row + blob + views removed).  Default: 30 days.  Set to 0 for
    # immediate hard-delete in test environments.
    attachments_retention_hard_delete_grace_seconds: float = 30 * 24 * 3600.0
    # Default retention applied at upload time when the caller doesn't
    # supply an explicit ``retention_expires_at``.  ``None`` means
    # retain indefinitely (matches pre-rebuild behavior).
    attachments_default_retention_days: int | None = None
    capture_audit_worker_enabled: bool = False
    capture_audit_worker_interval_seconds: float = 600.0
    capture_audit_retention_days: int = 90
    capture_audit_retention_sweep_batch_size: int = 500
    capture_audit_outbox_batch_size: int = 100
    # View-ready worker: opt-in per-deployment.  Dispatches synthetic
    # system_event turns to the kernel when an attachment view becomes ready.
    attachments_view_ready_worker_enabled: bool = False
    attachments_view_ready_worker_interval_seconds: float = 5.0
    attachments_view_ready_worker_batch_size: int = 50
    # API-embedded view-ready dispatch is legacy opt-in; the sweep runs in
    # the worker process (view_ready.tick) by default.
    attachments_view_ready_embedded_worker_enabled: bool = False
    # Vision producer: opt-in.  When enabled and a Gemini API key is present,
    # process_attachment() writes native_file_uri + vision views for images.
    attachments_vision_enabled: bool = False
    simulation_eval_workers: int = 4
    journey_runtime_workers: int = 4
    # Embedded journey threads in the API process are legacy; journey jobs
    # drain in the worker process (journey_runtime.tick) by default.
    journey_runtime_embedded_worker_enabled: bool = False
    journey_runtime_worker_enabled: bool = True
    journey_runtime_poll_interval_seconds: float = 1.0
    journey_runtime_job_lease_seconds: float = 300.0
    journey_runtime_job_heartbeat_interval_seconds: float = 30.0
    journey_runtime_failure_alert_threshold: int = 3
    journey_runtime_failure_alert_window_seconds: float = 900.0
    # Embedded tool-integration threads in the API process are legacy;
    # deferred tool jobs drain in the worker process (tool_integration.tick)
    # by default.  The API always constructs the worker runtime object —
    # webhook-callback processing needs it — but its thread loop only spawns
    # when the embedded flag is opted into.
    tool_integration_embedded_worker_enabled: bool = False
    tool_integration_worker_enabled: bool = True
    tool_integration_worker_poll_interval_seconds: float = 2.0
    tool_integration_worker_batch_size: int = 10
    browser_task_worker_enabled: bool = False
    browser_task_worker_adapter: str = "disabled"
    browser_task_worker_identity: str = "ruhu-browser-worker"
    browser_task_worker_poll_interval_seconds: float = 2.0
    browser_task_worker_batch_size: int = 1
    browser_task_worker_lease_seconds: int = 60
    browser_task_worker_heartbeat_interval_seconds: float = 10.0
    browser_task_pack_path: Path | None = None
    browser_task_allowed_packs: tuple[str, ...] = ()
    browser_task_worker_isolation_mode: str = "local"
    journey_abandonment_sweep_enabled: bool = False
    journey_abandonment_sweep_interval_seconds: float = 300.0
    conversation_sweep_worker_enabled: bool = False
    conversation_sweep_interval_seconds: float = 120.0
    conversation_sweep_idle_timeout_seconds: float = 1800.0
    conversation_sweep_batch_size: int = 100
    sentiment_worker_enabled: bool = False
    # API-embedded sentiment analysis is legacy opt-in; scoring runs in the
    # worker process (sentiment.tick) by default.
    sentiment_embedded_worker_enabled: bool = False
    sentiment_worker_llm_base_url: str | None = None
    sentiment_worker_llm_api_key: str | None = None
    sentiment_worker_model: str = "gpt-4o-mini"
    sentiment_worker_interval_seconds: float = 60.0
    sentiment_worker_batch_size: int = 20
    sentiment_worker_max_attempts: int = 3
    sentiment_worker_backoff_base_seconds: float = 60.0
    sentiment_worker_timeout_seconds: float = 20.0
    ticketing_retry_worker_enabled: bool = False
    ticketing_retry_interval_seconds: float = 60.0
    ticketing_retry_batch_size: int = 25
    semantic_summary_webhook_worker_enabled: bool = False
    semantic_summary_webhook_interval_seconds: float = 30.0
    semantic_summary_webhook_batch_size: int = 100
    auth_open_signup_domains: list[str] = field(default_factory=list)
    stripe_secret_key: str | None = None
    stripe_publishable_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_billing_mode: str = "mock"
    redis_url: str | None = None
    # CIDRs (comma-separated in env) of reverse proxies whose
    # ``X-Forwarded-For`` header the rate limiter should honour.  When empty,
    # the public rate limiter ignores XFF entirely to prevent an attacker
    # from rotating spoofed source IPs per request.  Typical production
    # value: the CIDR of the load balancer subnet.
    rate_limit_trusted_proxy_cidrs: tuple[str, ...] = ()
    # Continuous (live) evaluation — see ``ruhu.live_eval``. When enabled,
    # a configurable percentage of completed turns are scored against the
    # 4-dimension quality taxonomy (correctness, helpfulness, safety,
    # goal_completion) and the results emitted as Prometheus metrics +
    # persisted to ``live_turn_scores``.
    #
    # Default OFF so existing deployments don't acquire a new background
    # worker thread without explicit opt-in.  Set RUHU_LIVE_EVAL_ENABLED=1
    # in environments where you want the live quality dashboard to populate.
    live_eval_enabled: bool = False
    # Fraction of turns to sample, in [0.0, 1.0].  1% strikes the Phase 1
    # balance: enough volume for trend detection in busy orgs without
    # paying scorer cost on every single turn.
    live_eval_sample_rate: float = 0.01
    # Per-plan-slug overrides for the sample rate.  Common shapes:
    #
    #   {"enterprise": 0.0}                 # opt enterprise tenants out entirely
    #   {"free": 0.05, "starter": 0.02}     # higher rates for lower-paying tiers
    #
    # Plan slug must match the values in ``billing/catalog.py`` (free,
    # starter, professional, enterprise).  Tiers not listed fall back to
    # ``live_eval_sample_rate``.  Provided via env as a comma-separated
    # list of ``slug=rate`` pairs (see ``from_env``).
    live_eval_sample_rate_by_tier: dict[str, float] = field(default_factory=dict)
    tool_credentials_encryption_key: str | None = None
    # OAuth provider client credentials — used by OAuthFlowManager and
    # OAuthTokenRefresher.  Leave unset to disable the corresponding provider.
    hubspot_client_id: str | None = None
    hubspot_client_secret: str | None = None
    # google_client_id / google_client_secret are already declared above
    # (shared with Google SSO login).
    salesforce_client_id: str | None = None
    salesforce_client_secret: str | None = None
    zendesk_client_id: str | None = None
    zendesk_client_secret: str | None = None
    microsoft_client_id: str | None = None
    microsoft_client_secret: str | None = None
    # Base URL of this Ruhu deployment — used to construct the OAuth redirect
    # URI sent to providers (e.g. https://api.example.com).
    tool_oauth_redirect_base_url: str | None = None
    # ── PII scanning (HIPAA/GDPR/SOC 2 compliance) ──────────────────────────
    pii_global_enabled: bool = False
    pii_presidio_enabled: bool = True
    pii_presidio_entities: tuple[str, ...] = (
        "PERSON",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "CREDIT_CARD",
        "US_SSN",
        "US_PASSPORT",
        "MEDICAL_LICENSE",
        "LOCATION",
        "DATE_TIME",
    )
    pii_presidio_language: str = "en"
    pii_presidio_spacy_model: str = "en_core_web_lg"
    pii_dlp_enabled: bool = False
    pii_dlp_project_id: str | None = None
    pii_dlp_info_types: tuple[str, ...] = ()
    pii_dlp_min_likelihood: str = "POSSIBLE"
    pii_dlp_always_run: bool = False
    pii_regex_fallback_enabled: bool = True
    pii_audit_findings: bool = True
    pii_scan_timeout_seconds: float = 2.0
    # Master kill switch for LLM move selection. Enable at the platform
    # level via ``RUHU_LLM_MOVE_SELECTION_ENABLED=true``; per-agent and
    # per-step opt-in is still required.
    llm_move_selection_enabled: bool = False

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        database_url = os.getenv("RUHU_DATABASE_URL") or None
        sync_db_pool_size = _parse_int(os.getenv("RUHU_SYNC_DB_POOL_SIZE"), default=20)
        sync_db_max_overflow = _parse_int(os.getenv("RUHU_SYNC_DB_MAX_OVERFLOW"), default=40)
        sync_db_pool_recycle = _parse_int(os.getenv("RUHU_SYNC_DB_POOL_RECYCLE"), default=1800)
        sync_db_pool_timeout = _parse_float(os.getenv("RUHU_SYNC_DB_POOL_TIMEOUT"), default=30.0)
        sync_db_statement_timeout_ms = _parse_int(os.getenv("RUHU_SYNC_DB_STATEMENT_TIMEOUT_MS"), default=30_000)
        async_db_pool_size = _parse_int(os.getenv("RUHU_ASYNC_DB_POOL_SIZE"), default=20)
        async_db_max_overflow = _parse_int(os.getenv("RUHU_ASYNC_DB_MAX_OVERFLOW"), default=40)
        async_db_pool_recycle = _parse_int(os.getenv("RUHU_ASYNC_DB_POOL_RECYCLE"), default=1800)
        async_db_pool_timeout = _parse_float(os.getenv("RUHU_ASYNC_DB_POOL_TIMEOUT"), default=30.0)
        async_db_statement_timeout_ms = _parse_int(os.getenv("RUHU_ASYNC_DB_STATEMENT_TIMEOUT_MS"), default=30_000)
        environment = _parse_environment(os.getenv("RUHU_ENVIRONMENT"), default="development")
        interpreter_name = os.getenv("RUHU_INTERPRETER") or None
        classifier_model_path_raw = os.getenv("RUHU_CLASSIFIER_MODEL_PATH")
        classifier_model_path: Path | None
        if classifier_model_path_raw is None:
            classifier_model_path = Path("/tmp/gemma-4-E4B-it")
        elif classifier_model_path_raw.strip() == "":
            classifier_model_path = None
        else:
            classifier_model_path = Path(classifier_model_path_raw)
        agent_interpreters = _parse_agent_interpreters(os.getenv("RUHU_AGENT_INTERPRETERS"))
        classifier_backend = (os.getenv("RUHU_CLASSIFIER_BACKEND") or "transformers").strip().lower()
        classifier_base_url = os.getenv("RUHU_CLASSIFIER_BASE_URL") or None
        classifier_model = os.getenv("RUHU_CLASSIFIER_MODEL") or None
        classifier_timeout_ms = _parse_int(os.getenv("RUHU_CLASSIFIER_TIMEOUT_MS"), default=500)
        classifier_lora_storage_uri = os.getenv("RUHU_CLASSIFIER_LORA_STORAGE_URI") or None
        classifier_mode_raw = (os.getenv("RUHU_CLASSIFIER_MODE") or "single").strip().lower()
        if classifier_mode_raw not in {"single", "off"}:
            raise ValueError(
                f"RUHU_CLASSIFIER_MODE must be one of single/off; got {classifier_mode_raw!r}"
            )
        classifier_mode = classifier_mode_raw
        intent_tags_classifier_base_url = os.getenv("RUHU_INTENT_TAGS_CLASSIFIER_BASE_URL") or None
        intent_tags_classifier_api_key = os.getenv("RUHU_INTENT_TAGS_CLASSIFIER_API_KEY") or None
        intent_tags_classifier_api_key_secret_version = _parse_optional_secret_version(
            os.getenv("RUHU_INTENT_TAGS_CLASSIFIER_API_KEY_SECRET_VERSION")
        )
        intent_tags_classifier_timeout_seconds = _parse_float(
            os.getenv("RUHU_INTENT_TAGS_CLASSIFIER_TIMEOUT_SECONDS"),
            default=5.0,
        )
        intent_tags_classifier_max_retries = _parse_int(
            os.getenv("RUHU_INTENT_TAGS_CLASSIFIER_MAX_RETRIES"),
            default=2,
        )
        intent_tags_classifier_retry_backoff_seconds = _parse_float(
            os.getenv("RUHU_INTENT_TAGS_CLASSIFIER_RETRY_BACKOFF_SECONDS"),
            default=0.25,
        )
        whatsapp_meta_channels = _parse_whatsapp_meta_channels_from_env()
        phone_number_routes = _parse_phone_number_routes_from_env()
        telnyx_api_key = os.getenv("RUHU_TELNYX_API_KEY") or None
        telnyx_api_base_url = os.getenv("RUHU_TELNYX_API_BASE_URL") or "https://api.telnyx.com/v2"
        telnyx_timeout_seconds = _parse_float(
            os.getenv("RUHU_TELNYX_TIMEOUT_SECONDS"),
            default=10.0,
        )
        africastalking_api_key = os.getenv("RUHU_AFRICASTALKING_API_KEY") or None
        africastalking_username = os.getenv("RUHU_AFRICASTALKING_USERNAME") or None
        africastalking_sandbox = os.getenv("RUHU_AFRICASTALKING_SANDBOX", "").lower() in ("1", "true", "yes")
        africastalking_timeout_seconds = _parse_float(
            os.getenv("RUHU_AFRICASTALKING_TIMEOUT_SECONDS"),
            default=10.0,
        )
        phone_number_reconciliation_interval_seconds = _parse_float(
            os.getenv("RUHU_PHONE_NUMBER_RECONCILIATION_INTERVAL_SECONDS"),
            default=300.0,
        )
        phone_number_reconciliation_batch_size = _parse_int(
            os.getenv("RUHU_PHONE_NUMBER_RECONCILIATION_BATCH_SIZE"),
            default=50,
        )
        livekit_server_url = os.getenv("RUHU_LIVEKIT_SERVER_URL")
        livekit_api_key = os.getenv("RUHU_LIVEKIT_API_KEY")
        livekit_api_secret = os.getenv("RUHU_LIVEKIT_API_SECRET")
        livekit_agent_name = os.getenv("RUHU_LIVEKIT_AGENT_NAME") or "ruhu-voice"
        livekit_room_prefix = os.getenv("RUHU_LIVEKIT_ROOM_PREFIX") or "ruhu"
        livekit_phone_provider = os.getenv("RUHU_LIVEKIT_PHONE_PROVIDER") or "livekit"
        livekit_agents_sdk_version_target = os.getenv("RUHU_LIVEKIT_AGENTS_SDK_VERSION") or "1.5.2"
        livekit_voice_mode = os.getenv("RUHU_LIVEKIT_VOICE_MODE") or "pipeline"
        livekit_dispatch_strategy = os.getenv("RUHU_LIVEKIT_DISPATCH_STRATEGY") or "hybrid"
        livekit_metadata = _parse_json_object(
            os.getenv("RUHU_LIVEKIT_METADATA"),
            setting_name="RUHU_LIVEKIT_METADATA",
        )
        livekit_control_plane_base_url = os.getenv("RUHU_LIVEKIT_CONTROL_PLANE_BASE_URL") or None
        livekit_room_metadata_secret = (
            os.getenv("LIVEKIT_ROOM_METADATA_SECRET")
            or os.getenv("RUHU_LIVEKIT_ROOM_METADATA_SECRET")
            or ""
        )
        provider_shared_secret = os.getenv("RUHU_PROVIDER_SHARED_SECRET") or None
        internal_api_secret = os.getenv("RUHU_INTERNAL_API_SECRET") or None
        auth_database_url = os.getenv("RUHU_AUTH_DATABASE_URL") or None
        auth_require_asymmetric_tokens = _parse_bool(
            os.getenv("RUHU_AUTH_REQUIRE_ASYMMETRIC_TOKENS"),
            default=environment in {"staging", "production"},
        )
        auth_jwt_secret = os.getenv("RUHU_AUTH_JWT_SECRET") or None
        auth_jwt_issuer = os.getenv("RUHU_AUTH_JWT_ISSUER") or "ruhu"
        auth_jwt_private_key_pem = os.getenv("RUHU_AUTH_JWT_PRIVATE_KEY_PEM") or None
        auth_jwt_private_key_path = _parse_optional_path(os.getenv("RUHU_AUTH_JWT_PRIVATE_KEY_PATH"))
        auth_jwt_private_key_secret_version = _parse_optional_secret_version(
            os.getenv("RUHU_AUTH_JWT_PRIVATE_KEY_SECRET_VERSION")
        )
        auth_jwt_active_kid = os.getenv("RUHU_AUTH_JWT_ACTIVE_KID") or None
        auth_jwt_verification_jwks = os.getenv("RUHU_AUTH_JWT_VERIFICATION_JWKS") or None
        auth_jwt_verification_jwks_path = _parse_optional_path(
            os.getenv("RUHU_AUTH_JWT_VERIFICATION_JWKS_PATH")
        )
        auth_jwt_verification_jwks_secret_version = _parse_optional_secret_version(
            os.getenv("RUHU_AUTH_JWT_VERIFICATION_JWKS_SECRET_VERSION")
        )
        frontend_url = _env_first("RUHU_FRONTEND_URL", "FRONTEND_URL")
        auth_allowed_redirect_origins = _parse_string_list(os.getenv("RUHU_AUTH_ALLOWED_REDIRECT_ORIGINS"))
        google_client_id = os.getenv("RUHU_GOOGLE_CLIENT_ID")
        google_client_secret = os.getenv("RUHU_GOOGLE_CLIENT_SECRET")
        smtp_host = _env_first("RUHU_SMTP_HOST", "SMTP_HOST")
        smtp_port = _parse_int(_env_first("RUHU_SMTP_PORT", "SMTP_PORT"), default=587)
        smtp_user = _env_first("RUHU_SMTP_USER", "SMTP_USER")
        smtp_password = _env_first("RUHU_SMTP_PASSWORD", "SMTP_PASSWORD")
        smtp_from_email = _env_first("RUHU_SMTP_FROM_EMAIL", "SMTP_FROM_EMAIL") or "noreply@ruhu.ai"
        smtp_from_name = _env_first("RUHU_SMTP_FROM_NAME", "SMTP_FROM_NAME") or "Ruhu AI"
        smtp_starttls = _parse_bool(_env_first("RUHU_SMTP_STARTTLS", "SMTP_STARTTLS"), default=True)
        email_provider = _env_first("RUHU_EMAIL_PROVIDER", "EMAIL_PROVIDER")
        resend_api_key = _env_first("RUHU_RESEND_API_KEY", "RESEND_API_KEY")
        resend_from_email = _env_first("RUHU_RESEND_FROM_EMAIL", "RESEND_FROM_EMAIL")
        resend_from_name = _env_first("RUHU_RESEND_FROM_NAME", "RESEND_FROM_NAME")
        resend_timeout_seconds = _parse_float(
            _env_first("RUHU_RESEND_TIMEOUT_SECONDS", "RESEND_TIMEOUT_SECONDS"),
            default=10.0,
        )
        blob_store_backend = (
            _env_first("RUHU_BLOB_STORE_BACKEND", "BLOB_STORE_BACKEND") or "in_memory"
        )
        blob_store_bucket = _env_first("RUHU_BLOB_STORE_BUCKET", "BLOB_STORE_BUCKET")
        # Default region: London. Operators on other regions override via
        # RUHU_BLOB_STORE_S3_REGION, BLOB_STORE_S3_REGION, or AWS_REGION.
        blob_store_s3_region = (
            _env_first("RUHU_BLOB_STORE_S3_REGION", "BLOB_STORE_S3_REGION", "AWS_REGION")
            or "eu-west-2"
        )
        blob_store_gcs_project = _env_first(
            "RUHU_BLOB_STORE_GCS_PROJECT",
            "BLOB_STORE_GCS_PROJECT",
            "GOOGLE_CLOUD_PROJECT",
        )
        blob_store_local_root = _env_first(
            "RUHU_BLOB_STORE_LOCAL_ROOT", "BLOB_STORE_LOCAL_ROOT"
        )
        knowledge_default_organization_id = os.getenv("RUHU_KNOWLEDGE_DEFAULT_ORGANIZATION_ID") or None
        knowledge_seed_path = _parse_optional_path(os.getenv("RUHU_KNOWLEDGE_SEED_PATH"))
        knowledge_auto_seed = _parse_bool(os.getenv("RUHU_KNOWLEDGE_AUTO_SEED"), default=False)
        knowledge_auto_reindex_on_startup = _parse_bool(
            os.getenv("RUHU_KNOWLEDGE_AUTO_REINDEX_ON_STARTUP"),
            default=True,
        )
        knowledge_reindex_workers = _parse_int(os.getenv("RUHU_KNOWLEDGE_REINDEX_WORKERS"), default=1)
        knowledge_embedding_base_url = os.getenv("RUHU_KNOWLEDGE_EMBEDDING_BASE_URL") or None
        knowledge_embedding_api_key = os.getenv("RUHU_KNOWLEDGE_EMBEDDING_API_KEY") or None
        knowledge_embedding_model = os.getenv("RUHU_KNOWLEDGE_EMBEDDING_MODEL") or None
        knowledge_embedding_dimensions = _parse_optional_int(
            os.getenv("RUHU_KNOWLEDGE_EMBEDDING_DIMENSIONS")
        )
        knowledge_embedding_timeout_seconds = _parse_float(
            os.getenv("RUHU_KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS"),
            default=20.0,
        )
        knowledge_max_file_bytes = _parse_int(
            os.getenv("RUHU_KNOWLEDGE_MAX_FILE_BYTES"),
            default=5 * 1024 * 1024,
        )
        knowledge_max_chunks_per_document = _parse_int(
            os.getenv("RUHU_KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT"),
            default=128,
        )
        knowledge_chunk_max_words = _parse_int(
            os.getenv("RUHU_KNOWLEDGE_CHUNK_MAX_WORDS"),
            default=120,
        )
        knowledge_chunk_overlap_words = _parse_int(
            os.getenv("RUHU_KNOWLEDGE_CHUNK_OVERLAP_WORDS"),
            default=24,
        )
        knowledge_weaviate_enabled = _parse_bool(
            os.getenv("RUHU_KNOWLEDGE_WEAVIATE_ENABLED"),
            default=False,
        )
        knowledge_weaviate_host = os.getenv("RUHU_KNOWLEDGE_WEAVIATE_HOST") or "localhost"
        knowledge_weaviate_port = _parse_int(os.getenv("RUHU_KNOWLEDGE_WEAVIATE_PORT"), default=8080)
        knowledge_weaviate_grpc_port = _parse_int(
            os.getenv("RUHU_KNOWLEDGE_WEAVIATE_GRPC_PORT"),
            default=50051,
        )
        knowledge_weaviate_collection = os.getenv("RUHU_KNOWLEDGE_WEAVIATE_COLLECTION") or "KnowledgeChunk"
        knowledge_weaviate_retry_attempts = _parse_int(
            os.getenv("RUHU_KNOWLEDGE_WEAVIATE_RETRY_ATTEMPTS"),
            default=2,
        )
        knowledge_weaviate_backoff_base_seconds = _parse_float(
            os.getenv("RUHU_KNOWLEDGE_WEAVIATE_BACKOFF_BASE_SECONDS"),
            default=0.1,
        )
        knowledge_weaviate_backoff_max_seconds = _parse_float(
            os.getenv("RUHU_KNOWLEDGE_WEAVIATE_BACKOFF_MAX_SECONDS"),
            default=1.0,
        )
        attachments_max_file_bytes = _parse_int(
            os.getenv("RUHU_ATTACHMENTS_MAX_FILE_BYTES"),
            default=10 * 1024 * 1024,
        )
        attachments_workers = _parse_int(
            os.getenv("RUHU_ATTACHMENTS_WORKERS"),
            default=2,
        )
        attachments_retention_sweep_enabled = _parse_bool(
            os.getenv("RUHU_ATTACHMENTS_RETENTION_SWEEP_ENABLED"),
            default=False,
        )
        attachments_retention_sweep_interval_seconds = _parse_float(
            os.getenv("RUHU_ATTACHMENTS_RETENTION_SWEEP_INTERVAL_SECONDS"),
            default=600.0,
        )
        attachments_retention_sweep_batch_size = _parse_int(
            os.getenv("RUHU_ATTACHMENTS_RETENTION_SWEEP_BATCH_SIZE"),
            default=100,
        )
        attachments_retention_hard_delete_grace_seconds = _parse_float(
            os.getenv("RUHU_ATTACHMENTS_RETENTION_HARD_DELETE_GRACE_SECONDS"),
            default=30 * 24 * 3600.0,
        )
        _default_retention_days_raw = os.getenv("RUHU_ATTACHMENTS_DEFAULT_RETENTION_DAYS")
        attachments_default_retention_days: int | None = (
            int(_default_retention_days_raw)
            if _default_retention_days_raw and _default_retention_days_raw.strip()
            else None
        )
        capture_audit_retention_days = _parse_int(
            os.getenv("RUHU_CAPTURE_AUDIT_RETENTION_DAYS"),
            default=90,
        )
        capture_audit_worker_enabled = _parse_bool(
            os.getenv("RUHU_CAPTURE_AUDIT_WORKER_ENABLED"),
            default=False,
        )
        capture_audit_worker_interval_seconds = _parse_float(
            os.getenv("RUHU_CAPTURE_AUDIT_WORKER_INTERVAL_SECONDS"),
            default=600.0,
        )
        capture_audit_retention_sweep_batch_size = _parse_int(
            os.getenv("RUHU_CAPTURE_AUDIT_RETENTION_SWEEP_BATCH_SIZE"),
            default=500,
        )
        capture_audit_outbox_batch_size = _parse_int(
            os.getenv("RUHU_CAPTURE_AUDIT_OUTBOX_BATCH_SIZE"),
            default=100,
        )
        attachments_view_ready_worker_enabled = _parse_bool(
            os.getenv("RUHU_ATTACHMENTS_VIEW_READY_WORKER_ENABLED"),
            default=False,
        )
        attachments_view_ready_worker_interval_seconds = _parse_float(
            os.getenv("RUHU_ATTACHMENTS_VIEW_READY_WORKER_INTERVAL_SECONDS"),
            default=5.0,
        )
        attachments_view_ready_worker_batch_size = _parse_int(
            os.getenv("RUHU_ATTACHMENTS_VIEW_READY_WORKER_BATCH_SIZE"),
            default=50,
        )
        attachments_view_ready_embedded_worker_enabled = _parse_bool(
            os.getenv("RUHU_ATTACHMENTS_VIEW_READY_EMBEDDED_WORKER_ENABLED"),
            default=False,
        )
        attachments_vision_enabled = _parse_bool(
            os.getenv("RUHU_ATTACHMENTS_VISION_ENABLED"),
            default=False,
        )
        simulation_eval_workers = _parse_int(
            os.getenv("RUHU_SIMULATION_EVAL_WORKERS"),
            default=2,
        )
        journey_runtime_workers = _parse_int(
            os.getenv("RUHU_JOURNEY_RUNTIME_WORKERS"),
            default=2,
        )
        journey_runtime_embedded_worker_enabled = _parse_bool(
            os.getenv("RUHU_JOURNEY_RUNTIME_EMBEDDED_WORKER_ENABLED"),
            default=False,
        )
        journey_runtime_worker_enabled = _parse_bool(
            os.getenv("RUHU_JOURNEY_RUNTIME_WORKER_ENABLED"),
            default=True,
        )
        journey_runtime_poll_interval_seconds = _parse_float(
            os.getenv("RUHU_JOURNEY_RUNTIME_POLL_INTERVAL_SECONDS"),
            default=1.0,
        )
        journey_runtime_job_lease_seconds = _parse_float(
            os.getenv("RUHU_JOURNEY_RUNTIME_JOB_LEASE_SECONDS"),
            default=300.0,
        )
        journey_runtime_job_heartbeat_interval_seconds = _parse_float(
            os.getenv("RUHU_JOURNEY_RUNTIME_JOB_HEARTBEAT_INTERVAL_SECONDS"),
            default=30.0,
        )
        journey_runtime_failure_alert_threshold = _parse_int(
            os.getenv("RUHU_JOURNEY_RUNTIME_FAILURE_ALERT_THRESHOLD"),
            default=3,
        )
        journey_runtime_failure_alert_window_seconds = _parse_float(
            os.getenv("RUHU_JOURNEY_RUNTIME_FAILURE_ALERT_WINDOW_SECONDS"),
            default=900.0,
        )
        tool_integration_embedded_worker_enabled = _parse_bool(
            os.getenv("RUHU_TOOL_INTEGRATION_EMBEDDED_WORKER_ENABLED"),
            default=False,
        )
        tool_integration_worker_enabled = _parse_bool(
            os.getenv("RUHU_TOOL_INTEGRATION_WORKER_ENABLED"),
            default=True,
        )
        tool_integration_worker_poll_interval_seconds = _parse_float(
            os.getenv("RUHU_TOOL_INTEGRATION_WORKER_POLL_INTERVAL_SECONDS"),
            default=2.0,
        )
        tool_integration_worker_batch_size = _parse_int(
            os.getenv("RUHU_TOOL_INTEGRATION_WORKER_BATCH_SIZE"),
            default=10,
        )
        browser_task_worker_enabled = _parse_bool(
            os.getenv("RUHU_BROWSER_TASK_WORKER_ENABLED"),
            default=False,
        )
        browser_task_worker_adapter = os.getenv("RUHU_BROWSER_TASK_WORKER_ADAPTER") or "disabled"
        browser_task_worker_identity = os.getenv("RUHU_BROWSER_TASK_WORKER_IDENTITY") or "ruhu-browser-worker"
        browser_task_worker_poll_interval_seconds = _parse_float(
            os.getenv("RUHU_BROWSER_TASK_WORKER_POLL_INTERVAL_SECONDS"),
            default=2.0,
        )
        browser_task_worker_batch_size = _parse_int(
            os.getenv("RUHU_BROWSER_TASK_WORKER_BATCH_SIZE"),
            default=1,
        )
        browser_task_worker_lease_seconds = _parse_int(
            os.getenv("RUHU_BROWSER_TASK_WORKER_LEASE_SECONDS"),
            default=60,
        )
        browser_task_worker_heartbeat_interval_seconds = _parse_float(
            os.getenv("RUHU_BROWSER_TASK_WORKER_HEARTBEAT_INTERVAL_SECONDS"),
            default=10.0,
        )
        browser_task_pack_path = _parse_optional_path(os.getenv("RUHU_BROWSER_TASK_PACK_PATH"))
        browser_task_allowed_packs = tuple(
            item.strip()
            for item in (os.getenv("RUHU_BROWSER_TASK_ALLOWED_PACKS") or "").split(",")
            if item.strip()
        )
        browser_task_worker_isolation_mode = (
            os.getenv("RUHU_BROWSER_TASK_WORKER_ISOLATION_MODE") or "local"
        ).strip().lower()
        journey_abandonment_sweep_enabled = _parse_bool(
            os.getenv("RUHU_JOURNEY_ABANDONMENT_SWEEP_ENABLED"),
            default=False,
        )
        journey_abandonment_sweep_interval_seconds = _parse_float(
            os.getenv("RUHU_JOURNEY_ABANDONMENT_SWEEP_INTERVAL_SECONDS"),
            default=300.0,
        )
        conversation_sweep_worker_enabled = _parse_bool(
            os.getenv("RUHU_CONVERSATION_SWEEP_WORKER_ENABLED"),
            default=False,
        )
        conversation_sweep_interval_seconds = _parse_float(
            os.getenv("RUHU_CONVERSATION_SWEEP_INTERVAL_SECONDS"),
            default=120.0,
        )
        conversation_sweep_idle_timeout_seconds = _parse_float(
            os.getenv("RUHU_CONVERSATION_SWEEP_IDLE_TIMEOUT_SECONDS"),
            default=1800.0,
        )
        conversation_sweep_batch_size = _parse_int(
            os.getenv("RUHU_CONVERSATION_SWEEP_BATCH_SIZE"),
            default=100,
        )
        sentiment_worker_enabled = _parse_bool(
            os.getenv("RUHU_SENTIMENT_WORKER_ENABLED"),
            default=False,
        )
        sentiment_embedded_worker_enabled = _parse_bool(
            os.getenv("RUHU_SENTIMENT_EMBEDDED_WORKER_ENABLED"),
            default=False,
        )
        sentiment_worker_llm_base_url = os.getenv("RUHU_SENTIMENT_WORKER_LLM_BASE_URL") or None
        sentiment_worker_llm_api_key = os.getenv("RUHU_SENTIMENT_WORKER_LLM_API_KEY") or None
        sentiment_worker_model = os.getenv("RUHU_SENTIMENT_WORKER_MODEL") or "gpt-4o-mini"
        sentiment_worker_interval_seconds = _parse_float(
            os.getenv("RUHU_SENTIMENT_WORKER_INTERVAL_SECONDS"),
            default=60.0,
        )
        sentiment_worker_batch_size = _parse_int(
            os.getenv("RUHU_SENTIMENT_WORKER_BATCH_SIZE"),
            default=20,
        )
        sentiment_worker_max_attempts = _parse_int(
            os.getenv("RUHU_SENTIMENT_WORKER_MAX_ATTEMPTS"),
            default=3,
        )
        sentiment_worker_backoff_base_seconds = _parse_float(
            os.getenv("RUHU_SENTIMENT_WORKER_BACKOFF_BASE_SECONDS"),
            default=60.0,
        )
        sentiment_worker_timeout_seconds = _parse_float(
            os.getenv("RUHU_SENTIMENT_WORKER_TIMEOUT_SECONDS"),
            default=20.0,
        )
        ticketing_retry_worker_enabled = _parse_bool(
            os.getenv("RUHU_TICKETING_RETRY_WORKER_ENABLED"),
            default=False,
        )
        ticketing_retry_interval_seconds = _parse_float(
            os.getenv("RUHU_TICKETING_RETRY_INTERVAL_SECONDS"),
            default=60.0,
        )
        ticketing_retry_batch_size = _parse_int(
            os.getenv("RUHU_TICKETING_RETRY_BATCH_SIZE"),
            default=25,
        )
        semantic_summary_webhook_worker_enabled = _parse_bool(
            os.getenv("RUHU_SEMANTIC_SUMMARY_WEBHOOK_WORKER_ENABLED"),
            default=False,
        )
        semantic_summary_webhook_interval_seconds = _parse_float(
            os.getenv("RUHU_SEMANTIC_SUMMARY_WEBHOOK_INTERVAL_SECONDS"),
            default=30.0,
        )
        semantic_summary_webhook_batch_size = _parse_int(
            os.getenv("RUHU_SEMANTIC_SUMMARY_WEBHOOK_BATCH_SIZE"),
            default=100,
        )
        auth_open_signup_domains = _parse_string_list(os.getenv("RUHU_AUTH_OPEN_SIGNUP_DOMAINS"))
        stripe_secret_key = os.getenv("RUHU_STRIPE_SECRET_KEY") or None
        stripe_publishable_key = os.getenv("RUHU_STRIPE_PUBLISHABLE_KEY") or None
        stripe_webhook_secret = os.getenv("RUHU_STRIPE_WEBHOOK_SECRET") or None
        stripe_billing_mode = os.getenv("RUHU_STRIPE_BILLING_MODE") or "mock"
        redis_url = os.getenv("RUHU_REDIS_URL") or os.getenv("REDIS_URL") or None
        raw_trusted_proxies = os.getenv("RUHU_RATE_LIMIT_TRUSTED_PROXY_CIDRS") or ""
        rate_limit_trusted_proxy_cidrs = tuple(
            cidr.strip() for cidr in raw_trusted_proxies.split(",") if cidr.strip()
        )
        # Live (continuous) evaluation — opt-in via env. Default OFF so
        # existing deployments don't acquire a new background thread on
        # upgrade without explicit operator consent.
        live_eval_enabled = (
            os.getenv("RUHU_LIVE_EVAL_ENABLED", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        live_eval_sample_rate_raw = os.getenv("RUHU_LIVE_EVAL_SAMPLE_RATE", "").strip()
        try:
            live_eval_sample_rate = (
                float(live_eval_sample_rate_raw) if live_eval_sample_rate_raw else 0.01
            )
        except ValueError:
            # Bad input → fall back to default rather than crash startup.
            # An operator typo shouldn't take a production deployment down.
            live_eval_sample_rate = 0.01
        # Clamp into [0, 1] defensively — values outside this range have
        # no useful interpretation.
        live_eval_sample_rate = max(0.0, min(1.0, live_eval_sample_rate))
        # Per-tier overrides — comma-separated slug=rate pairs.
        # Example: ``RUHU_LIVE_EVAL_SAMPLE_RATE_BY_TIER=enterprise=0,free=0.05``.
        # Malformed entries are silently dropped rather than crashing
        # startup — an operator typo on a sampling override shouldn't
        # take production down.
        live_eval_sample_rate_by_tier: dict[str, float] = {}
        raw_per_tier = os.getenv("RUHU_LIVE_EVAL_SAMPLE_RATE_BY_TIER", "").strip()
        if raw_per_tier:
            for entry in raw_per_tier.split(","):
                pair = entry.strip()
                if not pair or "=" not in pair:
                    continue
                slug, _, rate_str = pair.partition("=")
                slug = slug.strip()
                rate_str = rate_str.strip()
                if not slug:
                    continue
                try:
                    rate = float(rate_str)
                except ValueError:
                    continue
                live_eval_sample_rate_by_tier[slug] = max(0.0, min(1.0, rate))
        tool_credentials_encryption_key = os.getenv("RUHU_TOOL_CREDENTIALS_ENCRYPTION_KEY") or None
        hubspot_client_id = os.getenv("RUHU_HUBSPOT_CLIENT_ID") or None
        hubspot_client_secret = os.getenv("RUHU_HUBSPOT_CLIENT_SECRET") or None
        salesforce_client_id = os.getenv("RUHU_SALESFORCE_CLIENT_ID") or None
        salesforce_client_secret = os.getenv("RUHU_SALESFORCE_CLIENT_SECRET") or None
        zendesk_client_id = os.getenv("RUHU_ZENDESK_CLIENT_ID") or None
        zendesk_client_secret = os.getenv("RUHU_ZENDESK_CLIENT_SECRET") or None
        microsoft_client_id = os.getenv("RUHU_MICROSOFT_CLIENT_ID") or None
        microsoft_client_secret = os.getenv("RUHU_MICROSOFT_CLIENT_SECRET") or None
        tool_oauth_redirect_base_url = os.getenv("RUHU_TOOL_OAUTH_REDIRECT_BASE_URL") or None
        pii_global_enabled = _parse_bool(os.getenv("RUHU_PII_GLOBAL_ENABLED"), default=False)
        pii_presidio_enabled = _parse_bool(os.getenv("RUHU_PII_PRESIDIO_ENABLED"), default=True)
        pii_presidio_entities_raw = os.getenv("RUHU_PII_PRESIDIO_ENTITIES") or None
        pii_presidio_entities = tuple(
            e.strip().upper()
            for e in (pii_presidio_entities_raw.split(",") if pii_presidio_entities_raw else [
                "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD",
                "US_SSN", "US_PASSPORT", "MEDICAL_LICENSE", "LOCATION", "DATE_TIME",
            ])
            if e.strip()
        )
        pii_presidio_language = os.getenv("RUHU_PII_PRESIDIO_LANGUAGE") or "en"
        pii_presidio_spacy_model = os.getenv("RUHU_PII_PRESIDIO_SPACY_MODEL") or "en_core_web_lg"
        pii_dlp_enabled = _parse_bool(
            os.getenv("RUHU_PII_DLP_ENABLED") or os.getenv("RUHU_DLP_ENABLED"),
            default=False,
        )
        pii_dlp_project_id = (
            os.getenv("RUHU_PII_DLP_PROJECT_ID")
            or os.getenv("RUHU_DLP_PROJECT_ID")
            or os.getenv("GCP_PROJECT_ID")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or None
        )
        pii_dlp_info_types_raw = os.getenv("RUHU_PII_DLP_INFO_TYPES") or os.getenv("RUHU_DLP_INFO_TYPES") or None
        pii_dlp_info_types = tuple(
            t.strip().upper()
            for t in (pii_dlp_info_types_raw.split(",") if pii_dlp_info_types_raw else [])
            if t.strip()
        )
        pii_dlp_min_likelihood = os.getenv("RUHU_PII_DLP_MIN_LIKELIHOOD") or "POSSIBLE"
        pii_dlp_always_run = _parse_bool(os.getenv("RUHU_PII_DLP_ALWAYS_RUN"), default=False)
        pii_regex_fallback_enabled = _parse_bool(os.getenv("RUHU_PII_REGEX_FALLBACK_ENABLED"), default=True)
        pii_audit_findings = _parse_bool(os.getenv("RUHU_PII_AUDIT_FINDINGS"), default=True)
        pii_scan_timeout_seconds = _parse_float(os.getenv("RUHU_PII_SCAN_TIMEOUT_SECONDS"), default=2.0)
        # WI-3 of doc 36: master kill switch for LLM move selection.
        llm_move_selection_enabled = _parse_bool(
            os.getenv("RUHU_LLM_MOVE_SELECTION_ENABLED"), default=False
        )
        return cls(
            database_url=database_url,
            sync_db_pool_size=sync_db_pool_size,
            sync_db_max_overflow=sync_db_max_overflow,
            sync_db_pool_recycle=sync_db_pool_recycle,
            sync_db_pool_timeout=sync_db_pool_timeout,
            sync_db_statement_timeout_ms=sync_db_statement_timeout_ms,
            async_db_pool_size=async_db_pool_size,
            async_db_max_overflow=async_db_max_overflow,
            async_db_pool_recycle=async_db_pool_recycle,
            async_db_pool_timeout=async_db_pool_timeout,
            async_db_statement_timeout_ms=async_db_statement_timeout_ms,
            environment=environment,
            interpreter_name=interpreter_name,
            classifier_model_path=classifier_model_path,
            agent_interpreters=agent_interpreters,
            classifier_backend=classifier_backend,
            classifier_base_url=classifier_base_url,
            classifier_model=classifier_model,
            classifier_timeout_ms=classifier_timeout_ms,
            classifier_lora_storage_uri=classifier_lora_storage_uri,
            classifier_mode=classifier_mode,
            intent_tags_classifier_base_url=intent_tags_classifier_base_url,
            intent_tags_classifier_api_key=intent_tags_classifier_api_key,
            intent_tags_classifier_api_key_secret_version=intent_tags_classifier_api_key_secret_version,
            intent_tags_classifier_timeout_seconds=intent_tags_classifier_timeout_seconds,
            intent_tags_classifier_max_retries=intent_tags_classifier_max_retries,
            intent_tags_classifier_retry_backoff_seconds=intent_tags_classifier_retry_backoff_seconds,
            whatsapp_meta_channels=whatsapp_meta_channels,
            phone_number_routes=phone_number_routes,
            telnyx_api_key=telnyx_api_key,
            telnyx_api_base_url=telnyx_api_base_url,
            telnyx_timeout_seconds=telnyx_timeout_seconds,
            africastalking_api_key=africastalking_api_key,
            africastalking_username=africastalking_username,
            africastalking_sandbox=africastalking_sandbox,
            africastalking_timeout_seconds=africastalking_timeout_seconds,
            phone_number_reconciliation_interval_seconds=phone_number_reconciliation_interval_seconds,
            phone_number_reconciliation_batch_size=phone_number_reconciliation_batch_size,
            livekit_server_url=livekit_server_url,
            livekit_api_key=livekit_api_key,
            livekit_api_secret=livekit_api_secret,
            livekit_agent_name=livekit_agent_name,
            livekit_room_prefix=livekit_room_prefix,
            livekit_phone_provider=livekit_phone_provider,
            livekit_agents_sdk_version_target=livekit_agents_sdk_version_target,
            livekit_voice_mode=livekit_voice_mode,
            livekit_dispatch_strategy=livekit_dispatch_strategy,
            livekit_metadata=livekit_metadata,
            livekit_control_plane_base_url=livekit_control_plane_base_url,
            livekit_room_metadata_secret=livekit_room_metadata_secret,
            provider_shared_secret=provider_shared_secret,
            internal_api_secret=internal_api_secret,
            auth_database_url=auth_database_url,
            auth_require_asymmetric_tokens=auth_require_asymmetric_tokens,
            auth_jwt_secret=auth_jwt_secret,
            auth_jwt_issuer=auth_jwt_issuer,
            auth_jwt_private_key_pem=auth_jwt_private_key_pem,
            auth_jwt_private_key_path=auth_jwt_private_key_path,
            auth_jwt_private_key_secret_version=auth_jwt_private_key_secret_version,
            auth_jwt_active_kid=auth_jwt_active_kid,
            auth_jwt_verification_jwks=auth_jwt_verification_jwks,
            auth_jwt_verification_jwks_path=auth_jwt_verification_jwks_path,
            auth_jwt_verification_jwks_secret_version=auth_jwt_verification_jwks_secret_version,
            frontend_url=frontend_url,
            auth_allowed_redirect_origins=auth_allowed_redirect_origins,
            google_client_id=google_client_id,
            google_client_secret=google_client_secret,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            smtp_from_email=smtp_from_email,
            smtp_from_name=smtp_from_name,
            smtp_starttls=smtp_starttls,
            email_provider=email_provider,
            resend_api_key=resend_api_key,
            resend_from_email=resend_from_email,
            resend_from_name=resend_from_name,
            resend_timeout_seconds=resend_timeout_seconds,
            blob_store_backend=blob_store_backend,
            blob_store_bucket=blob_store_bucket,
            blob_store_s3_region=blob_store_s3_region,
            blob_store_gcs_project=blob_store_gcs_project,
            blob_store_local_root=blob_store_local_root,
            knowledge_default_organization_id=knowledge_default_organization_id,
            knowledge_seed_path=knowledge_seed_path,
            knowledge_auto_seed=knowledge_auto_seed,
            knowledge_auto_reindex_on_startup=knowledge_auto_reindex_on_startup,
            knowledge_reindex_workers=knowledge_reindex_workers,
            knowledge_embedding_base_url=knowledge_embedding_base_url,
            knowledge_embedding_api_key=knowledge_embedding_api_key,
            knowledge_embedding_model=knowledge_embedding_model,
            knowledge_embedding_dimensions=knowledge_embedding_dimensions,
            knowledge_embedding_timeout_seconds=knowledge_embedding_timeout_seconds,
            knowledge_max_file_bytes=knowledge_max_file_bytes,
            knowledge_max_chunks_per_document=knowledge_max_chunks_per_document,
            knowledge_chunk_max_words=knowledge_chunk_max_words,
            knowledge_chunk_overlap_words=knowledge_chunk_overlap_words,
            knowledge_weaviate_enabled=knowledge_weaviate_enabled,
            knowledge_weaviate_host=knowledge_weaviate_host,
            knowledge_weaviate_port=knowledge_weaviate_port,
            knowledge_weaviate_grpc_port=knowledge_weaviate_grpc_port,
            knowledge_weaviate_collection=knowledge_weaviate_collection,
            knowledge_weaviate_retry_attempts=knowledge_weaviate_retry_attempts,
            knowledge_weaviate_backoff_base_seconds=knowledge_weaviate_backoff_base_seconds,
            knowledge_weaviate_backoff_max_seconds=knowledge_weaviate_backoff_max_seconds,
            attachments_max_file_bytes=attachments_max_file_bytes,
            attachments_workers=attachments_workers,
            attachments_retention_sweep_enabled=attachments_retention_sweep_enabled,
            attachments_retention_sweep_interval_seconds=attachments_retention_sweep_interval_seconds,
            attachments_retention_sweep_batch_size=attachments_retention_sweep_batch_size,
            attachments_retention_hard_delete_grace_seconds=attachments_retention_hard_delete_grace_seconds,
            attachments_default_retention_days=attachments_default_retention_days,
            capture_audit_worker_enabled=capture_audit_worker_enabled,
            capture_audit_worker_interval_seconds=capture_audit_worker_interval_seconds,
            capture_audit_retention_days=capture_audit_retention_days,
            capture_audit_retention_sweep_batch_size=capture_audit_retention_sweep_batch_size,
            capture_audit_outbox_batch_size=capture_audit_outbox_batch_size,
            attachments_view_ready_worker_enabled=attachments_view_ready_worker_enabled,
            attachments_view_ready_worker_interval_seconds=attachments_view_ready_worker_interval_seconds,
            attachments_view_ready_worker_batch_size=attachments_view_ready_worker_batch_size,
            attachments_view_ready_embedded_worker_enabled=attachments_view_ready_embedded_worker_enabled,
            attachments_vision_enabled=attachments_vision_enabled,
            simulation_eval_workers=simulation_eval_workers,
            journey_runtime_workers=journey_runtime_workers,
            journey_runtime_embedded_worker_enabled=journey_runtime_embedded_worker_enabled,
            journey_runtime_worker_enabled=journey_runtime_worker_enabled,
            journey_runtime_poll_interval_seconds=journey_runtime_poll_interval_seconds,
            journey_runtime_job_lease_seconds=journey_runtime_job_lease_seconds,
            journey_runtime_job_heartbeat_interval_seconds=journey_runtime_job_heartbeat_interval_seconds,
            journey_runtime_failure_alert_threshold=journey_runtime_failure_alert_threshold,
            journey_runtime_failure_alert_window_seconds=journey_runtime_failure_alert_window_seconds,
            tool_integration_embedded_worker_enabled=tool_integration_embedded_worker_enabled,
            tool_integration_worker_enabled=tool_integration_worker_enabled,
            tool_integration_worker_poll_interval_seconds=tool_integration_worker_poll_interval_seconds,
            tool_integration_worker_batch_size=max(1, tool_integration_worker_batch_size),
            browser_task_worker_enabled=browser_task_worker_enabled,
            browser_task_worker_adapter=browser_task_worker_adapter,
            browser_task_worker_identity=browser_task_worker_identity,
            browser_task_worker_poll_interval_seconds=browser_task_worker_poll_interval_seconds,
            browser_task_worker_batch_size=max(1, browser_task_worker_batch_size),
            browser_task_worker_lease_seconds=max(5, browser_task_worker_lease_seconds),
            browser_task_worker_heartbeat_interval_seconds=max(1.0, browser_task_worker_heartbeat_interval_seconds),
            browser_task_pack_path=browser_task_pack_path,
            browser_task_allowed_packs=browser_task_allowed_packs,
            browser_task_worker_isolation_mode=browser_task_worker_isolation_mode,
            journey_abandonment_sweep_enabled=journey_abandonment_sweep_enabled,
            journey_abandonment_sweep_interval_seconds=journey_abandonment_sweep_interval_seconds,
            conversation_sweep_worker_enabled=conversation_sweep_worker_enabled,
            conversation_sweep_interval_seconds=conversation_sweep_interval_seconds,
            conversation_sweep_idle_timeout_seconds=conversation_sweep_idle_timeout_seconds,
            conversation_sweep_batch_size=conversation_sweep_batch_size,
            sentiment_worker_enabled=sentiment_worker_enabled,
            sentiment_embedded_worker_enabled=sentiment_embedded_worker_enabled,
            sentiment_worker_llm_base_url=sentiment_worker_llm_base_url,
            sentiment_worker_llm_api_key=sentiment_worker_llm_api_key,
            sentiment_worker_model=sentiment_worker_model,
            sentiment_worker_interval_seconds=sentiment_worker_interval_seconds,
            sentiment_worker_batch_size=sentiment_worker_batch_size,
            sentiment_worker_max_attempts=sentiment_worker_max_attempts,
            sentiment_worker_backoff_base_seconds=sentiment_worker_backoff_base_seconds,
            sentiment_worker_timeout_seconds=sentiment_worker_timeout_seconds,
            ticketing_retry_worker_enabled=ticketing_retry_worker_enabled,
            ticketing_retry_interval_seconds=ticketing_retry_interval_seconds,
            ticketing_retry_batch_size=ticketing_retry_batch_size,
            semantic_summary_webhook_worker_enabled=semantic_summary_webhook_worker_enabled,
            semantic_summary_webhook_interval_seconds=semantic_summary_webhook_interval_seconds,
            semantic_summary_webhook_batch_size=semantic_summary_webhook_batch_size,
            auth_open_signup_domains=auth_open_signup_domains,
            stripe_secret_key=stripe_secret_key,
            stripe_publishable_key=stripe_publishable_key,
            stripe_webhook_secret=stripe_webhook_secret,
            stripe_billing_mode=stripe_billing_mode,
            redis_url=redis_url,
            rate_limit_trusted_proxy_cidrs=rate_limit_trusted_proxy_cidrs,
            live_eval_enabled=live_eval_enabled,
            live_eval_sample_rate=live_eval_sample_rate,
            live_eval_sample_rate_by_tier=live_eval_sample_rate_by_tier,
            tool_credentials_encryption_key=tool_credentials_encryption_key,
            hubspot_client_id=hubspot_client_id,
            hubspot_client_secret=hubspot_client_secret,
            salesforce_client_id=salesforce_client_id,
            salesforce_client_secret=salesforce_client_secret,
            zendesk_client_id=zendesk_client_id,
            zendesk_client_secret=zendesk_client_secret,
            microsoft_client_id=microsoft_client_id,
            microsoft_client_secret=microsoft_client_secret,
            tool_oauth_redirect_base_url=tool_oauth_redirect_base_url,
            pii_global_enabled=pii_global_enabled,
            pii_presidio_enabled=pii_presidio_enabled,
            pii_presidio_entities=pii_presidio_entities,
            pii_presidio_language=pii_presidio_language,
            pii_presidio_spacy_model=pii_presidio_spacy_model,
            pii_dlp_enabled=pii_dlp_enabled,
            pii_dlp_project_id=pii_dlp_project_id,
            pii_dlp_info_types=pii_dlp_info_types,
            pii_dlp_min_likelihood=pii_dlp_min_likelihood,
            pii_dlp_always_run=pii_dlp_always_run,
            pii_regex_fallback_enabled=pii_regex_fallback_enabled,
            pii_audit_findings=pii_audit_findings,
            pii_scan_timeout_seconds=pii_scan_timeout_seconds,
            llm_move_selection_enabled=llm_move_selection_enabled,
        )


def _parse_json_object(value: str | None, *, setting_name: str) -> dict[str, object]:
    if not value:
        return {}
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError(f"{setting_name} must be a JSON object")
    return payload


def _parse_agent_interpreters(value: str | None) -> dict[str, str]:
    payload = _parse_json_object(value, setting_name="RUHU_AGENT_INTERPRETERS")
    agent_interpreters: dict[str, str] = {}
    for agent_id, interpreter_name in payload.items():
        if not isinstance(agent_id, str) or not isinstance(interpreter_name, str):
            raise ValueError("RUHU_AGENT_INTERPRETERS must map agent ids to interpreter names")
        if agent_id and interpreter_name:
            agent_interpreters[agent_id] = interpreter_name
    return agent_interpreters


def _parse_whatsapp_meta_channels_from_env() -> dict[str, dict[str, object]]:
    raw_channels = os.getenv("RUHU_WHATSAPP_META_CHANNELS")
    if raw_channels is not None:
        return _parse_json_object(raw_channels, setting_name="RUHU_WHATSAPP_META_CHANNELS")
    return {}


def _parse_phone_number_routes_from_env() -> dict[str, dict[str, object]]:
    raw_routes = os.getenv("RUHU_PHONE_NUMBER_ROUTES")
    if raw_routes is None:
        return {}
    return _parse_json_object(raw_routes, setting_name="RUHU_PHONE_NUMBER_ROUTES")


def _parse_string_list(value: str | None) -> list[str]:
    if not value:
        return []
    stripped = value.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        payload = json.loads(stripped)
        if not isinstance(payload, list):
            raise ValueError("list setting must be a JSON array or comma-separated string")
        items = payload
    else:
        items = [part.strip() for part in stripped.split(",")]
    result: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError("list setting items must be strings")
        normalized = item.strip()
        if normalized:
            result.append(normalized)
    return result


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
        normalized = value.strip()
        if normalized:
            return normalized
    return None


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("boolean setting must be true/false, 1/0, yes/no, or on/off")


def _parse_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    normalized = value.strip()
    if not normalized:
        return default
    return int(normalized)


def _parse_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return int(normalized)


def _parse_float(value: str | None, *, default: float) -> float:
    if value is None:
        return default
    normalized = value.strip()
    if not normalized:
        return default
    return float(normalized)


def _parse_optional_path(value: str | None) -> Path | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return Path(normalized)


def _parse_optional_secret_version(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalize_gcp_secret_version(normalized)


def _parse_environment(value: str | None, *, default: str) -> str:
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    if normalized not in {"development", "test", "staging", "production"}:
        raise ValueError("RUHU_ENVIRONMENT must be one of development, test, staging, or production")
    return normalized
