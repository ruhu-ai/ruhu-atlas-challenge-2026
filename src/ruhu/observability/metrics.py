from __future__ import annotations

from collections.abc import Iterable

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, make_asgi_app

# Isolated registry — avoids polluting the default process-wide registry,
# which matters when multiple test workers share a process.
registry = CollectorRegistry(auto_describe=True)

# ── HTTP layer ─────────────────────────────────────────────────────────────────
# ``endpoint`` is always path-normalised (UUIDs collapsed to ``{id}``) before
# being used as a label so cardinality stays bounded.

http_requests_total = Counter(
    "ruhu_http_requests_total",
    "Total HTTP requests received",
    ["method", "endpoint", "status_code"],
    registry=registry,
)

http_request_duration_seconds = Histogram(
    "ruhu_http_request_duration_seconds",
    "End-to-end HTTP request latency",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registry=registry,
)

# ── Kernel / conversation ──────────────────────────────────────────────────────
# High-cardinality identifiers (org_id, agent_id, step_id) must NOT appear as
# labels.  They belong in structured log lines, not metric label sets.

kernel_turns_total = Counter(
    "ruhu_kernel_turns_total",
    "Conversation kernel turns processed",
    ["channel", "outcome"],
    registry=registry,
)

kernel_turn_duration_seconds = Histogram(
    "ruhu_kernel_turn_duration_seconds",
    "Time spent inside ConversationKernel.process_turn()",
    ["channel"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
    registry=registry,
)

turn_controller_of_record_total = Counter(
    "ruhu_turn_controller_of_record_total",
    "Which bounded controller actually drove a completed turn",
    ["controller"],
    registry=registry,
)

turn_classifier_fallback_total = Counter(
    "ruhu_turn_classifier_fallback_total",
    "Turns that used or degraded through the classifier fallback path",
    ["controller", "reason"],
    registry=registry,
)

conversation_version_conflicts_total = Counter(
    "ruhu_conversation_version_conflicts_total",
    "Optimistic lock conflicts detected on conversation state writes",
    [],
    registry=registry,
)

# ── P3 (doc 39 WI-8): per-move and proactive-trigger metrics ──────────────────

move_selection_committed_total = Counter(
    "ruhu_move_selection_committed_total",
    "Per-move count of committed LLM-selected moves",
    ["move_type", "step_profile"],
    registry=registry,
)

move_selection_sequence_length = Histogram(
    "ruhu_move_selection_sequence_length",
    "Histogram of accepted MoveSequence lengths (per turn)",
    ["step_profile"],
    buckets=[1, 2, 3],
    registry=registry,
)

move_selection_proactive_total = Counter(
    "ruhu_move_selection_proactive_total",
    "Outcomes of proactive move-selection turns by trigger and move type",
    ["trigger", "move_type"],
    registry=registry,
)

move_selection_pause_emitted_total = Counter(
    "ruhu_move_selection_pause_emitted_total",
    "Silent proactive moves (pause); rendered nothing but traced",
    ["trigger"],
    registry=registry,
)

move_selection_proposal_rejected_total = Counter(
    "ruhu_move_selection_proposal_rejected_total",
    "Rejected proposals broken down by validation rule and step profile",
    ["step_profile", "rule"],
    registry=registry,
)

move_selection_unavailable_total = Counter(
    "ruhu_move_selection_unavailable_total",
    "LLM provider failures / parse errors that bypassed move selection",
    ["step_profile", "reason"],
    registry=registry,
)

# ── P5 (doc 43 WI-6): per-tool counters for propose_tool_use ──────────────────

move_selection_tool_proposed_total = Counter(
    "ruhu_move_selection_tool_proposed_total",
    "propose_tool_use proposals broken down by tool and outcome",
    ["tool_ref", "outcome"],
    registry=registry,
)

move_selection_tool_confirmation_required_total = Counter(
    "ruhu_move_selection_tool_confirmation_required_total",
    "Tool proposals that resulted in pending confirmation",
    ["tool_ref"],
    registry=registry,
)

# ── Tool execution ─────────────────────────────────────────────────────────────

tool_invocations_total = Counter(
    "ruhu_tool_invocations_total",
    "Tool invocations by executor kind and outcome status",
    ["executor_kind", "status"],
    registry=registry,
)

tool_invocation_duration_seconds = Histogram(
    "ruhu_tool_invocation_duration_seconds",
    "Tool execution wall-clock time",
    ["executor_kind"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
    registry=registry,
)

tool_integration_jobs_total = Counter(
    "ruhu_tool_integration_jobs_total",
    "Durable external integration jobs by resolution mode and status",
    ["resolution_mode", "status"],
    registry=registry,
)

tool_integration_job_duration_seconds = Histogram(
    "ruhu_tool_integration_job_duration_seconds",
    "Wall-clock time from integration job submission to terminal resolution",
    ["resolution_mode", "outcome"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 300.0],
    registry=registry,
)

tool_integration_jobs_active = Gauge(
    "ruhu_tool_integration_jobs_active",
    "Current durable integration jobs grouped by provider and status",
    ["provider", "status"],
    registry=registry,
)

tool_integration_jobs_stuck = Gauge(
    "ruhu_tool_integration_jobs_stuck",
    "Current integration jobs considered stuck past their progress timeout",
    ["provider", "status"],
    registry=registry,
)

tool_integration_callbacks_total = Counter(
    "ruhu_tool_integration_callbacks_total",
    "Webhook callback processing outcomes for deferred integration jobs",
    ["provider", "outcome"],
    registry=registry,
)

tool_integration_retries_total = Counter(
    "ruhu_tool_integration_retries_total",
    "Retry scheduling and exhaustion events for deferred integration jobs",
    ["provider", "outcome"],
    registry=registry,
)

# ── Voice ──────────────────────────────────────────────────────────────────────

voice_sessions_started_total = Counter(
    "ruhu_voice_sessions_started_total",
    "Voice sessions initiated",
    ["outcome"],
    registry=registry,
)

voice_transcript_duplicates_suppressed_total = Counter(
    "ruhu_voice_transcript_duplicates_suppressed_total",
    "Transcript events suppressed as duplicates within the sliding window",
    [],
    registry=registry,
)

# ── Scalability Phase 1 — DB pool ─────────────────────────────────────────────

db_pool_checked_out = Gauge(
    "ruhu_db_pool_checked_out",
    "Number of currently checked-out connections in the pool",
    ["pool"],  # "sync" or "async"
    registry=registry,
)

db_pool_overflow = Gauge(
    "ruhu_db_pool_overflow",
    "Number of connections currently in overflow above pool_size",
    ["pool"],
    registry=registry,
)

# ── LLM / response generation ────────────────────────────────────────────────

llm_request_duration_seconds = Histogram(
    "ruhu_llm_request_duration_seconds",
    "Wall-clock time for instrumented LLM requests",
    ["provider", "model", "stage", "outcome"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0],
    registry=registry,
)

llm_tokens_total = Counter(
    "ruhu_llm_tokens_total",
    "LLM token usage by provider, canonical model, and direction",
    ["provider", "model", "direction"],
    registry=registry,
)

llm_cost_usd_total = Counter(
    "ruhu_llm_cost_usd_total",
    "Estimated LLM cost in USD",
    ["provider", "model"],
    registry=registry,
)

# ── Knowledge grounding gates (Google Vertex AI grounding pattern) ──────────
# Counter + histogram for the pre-call and post-call grounding gates
# implemented in ConversationKernel + GeminiDialogueGenerator. Fires
# whenever a knowledge-render path resolves a grounding policy.
#
# - phase: ``pre_call`` (kernel-side, before LLM call) | ``post_call``
#   (renderer-side, after LLM call against retrieved chunks).
# - decision: ``allowed`` (gate passed) | ``blocked`` (gate refused →
#   deterministic fallback fired) | ``no_op`` (mode == off, no gate to
#   evaluate).
# - mode: ``off`` | ``preferred`` | ``required`` — *effective* mode
#   (after auto-default resolution), so dashboards reflect runtime
#   behavior, not authoring intent.
# - reason: short tag explaining the decision (``below_threshold``,
#   ``grade_fail``, ``empty_evidence``, ``passed``, etc.).
knowledge_grounding_gate_total = Counter(
    "ruhu_knowledge_grounding_gate_total",
    "Knowledge-grounding gate decisions, labeled by phase / mode / reason",
    ["phase", "decision", "mode", "reason"],
    registry=registry,
)

knowledge_grounding_score = Histogram(
    "ruhu_knowledge_grounding_score",
    "Distribution of knowledge-grounding scores (0–1) at gate time",
    ["phase", "mode"],
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    registry=registry,
)


provider_error_total = Counter(
    "ruhu_provider_error_total",
    "Provider/API errors by provider and coarse error kind",
    ["provider", "kind"],
    registry=registry,
)


# ── Prefill-first classifier ────────────────────────────────────────────────
# See docs/pre-fill-intent-classifier-design/04-runtime-spec.md §Metrics.

classifier_request_duration_seconds = Histogram(
    "ruhu_classifier_request_duration_seconds",
    "Wall-clock time for one prefill-first classifier call",
    ["agent_id", "step_id", "backend", "lora", "cache_hit"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.2, 0.4, 0.8, 1.5, 3.0],
    registry=registry,
)

classifier_decisions_total = Counter(
    "ruhu_classifier_decisions_total",
    "Classifier label decisions",
    # ``chosen_label`` is the workflow-routing classifier's per-call
    # output (one of the step's outcome events; see
    # ``classifier.protocol.ClassificationResult.chosen_label``).
    # Operators with Grafana panels keyed on the legacy ``intent_name``
    # label need to update them in lockstep — this is a deliberate metric
    # rename, not a value-only change.
    ["agent_id", "step_id", "chosen_label", "backend", "lora"],
    registry=registry,
)

classifier_unknown_total = Counter(
    "ruhu_classifier_unknown_total",
    "Classifier returned 'unknown' (no outcome matched)",
    ["agent_id", "step_id", "backend"],
    registry=registry,
)

classifier_confidence = Histogram(
    "ruhu_classifier_confidence",
    "Per-call classifier confidence (joint logprob of chosen label)",
    ["agent_id", "step_id"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99],
    registry=registry,
)

classifier_prefill_tokens_total = Counter(
    "ruhu_classifier_prefill_tokens_total",
    "Tokens prefilled by the classifier (validates prefix-cache hit savings)",
    ["agent_id", "step_id", "cache_hit"],
    registry=registry,
)

classifier_errors_total = Counter(
    "ruhu_classifier_errors_total",
    "Classifier call errors by coarse kind (timeout/5xx/connection_error/unknown_label)",
    ["error_kind", "backend"],
    registry=registry,
)

turn_error_total = Counter(
    "ruhu_turn_error_total",
    "Turn traces written with a non-success error kind",
    ["error_kind"],
    registry=registry,
)

trace_write_success_total = Counter(
    "ruhu_trace_write_success_total",
    "Successful turn-trace writes",
    [],
    registry=registry,
)

trace_write_truncations_total = Counter(
    "ruhu_trace_write_truncations_total",
    "Turn traces that required payload truncation before write",
    [],
    registry=registry,
)

llm_response_wait_seconds = Histogram(
    "ruhu_llm_response_wait_seconds",
    "Wall-clock time waiting for LLM response generation",
    ["provider"],  # "gemini", "vertex"
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0],
    registry=registry,
)

atlas_generator_requests_total = Counter(
    "ruhu_atlas_generator_requests_total",
    "Atlas proposal generator attempts by provider path and outcome",
    ["provider", "model", "outcome"],
    registry=registry,
)

atlas_generator_request_duration_seconds = Histogram(
    "ruhu_atlas_generator_request_duration_seconds",
    "Wall-clock time spent inside the Atlas proposal generator backend call",
    ["provider", "model", "outcome"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0],
    registry=registry,
)

atlas_generator_fallback_total = Counter(
    "ruhu_atlas_generator_fallback_total",
    "Atlas proposal generator fallbacks by reason",
    ["reason"],
    registry=registry,
)

atlas_docs_parser_requests_total = Counter(
    "ruhu_atlas_docs_parser_requests_total",
    "Atlas LLM docs-page parser attempts by provider and outcome",
    ["provider", "model", "outcome"],
    registry=registry,
)

atlas_docs_parser_request_duration_seconds = Histogram(
    "ruhu_atlas_docs_parser_request_duration_seconds",
    "Wall-clock time spent inside the Atlas docs-page parser backend call",
    ["provider", "model", "outcome"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0],
    registry=registry,
)

atlas_docs_parser_fallback_total = Counter(
    "ruhu_atlas_docs_parser_fallback_total",
    "Atlas docs-page parser fallbacks to heuristic regex extraction",
    ["reason"],
    registry=registry,
)

atlas_generator_delta_candidates_total = Counter(
    "ruhu_atlas_generator_delta_candidates_total",
    "Atlas proposed delta candidates by generator mode and delta family",
    ["mode", "family"],
    registry=registry,
)

atlas_generator_delta_filtered_total = Counter(
    "ruhu_atlas_generator_delta_filtered_total",
    "Atlas proposed deltas filtered before review by family and reason",
    ["family", "reason"],
    registry=registry,
)

atlas_review_decisions_total = Counter(
    "ruhu_atlas_review_decisions_total",
    "Atlas review decisions by delta family and decision",
    ["family", "decision"],
    registry=registry,
)

atlas_apply_deltas_total = Counter(
    "ruhu_atlas_apply_deltas_total",
    "Atlas apply outcomes by delta family and outcome",
    ["family", "outcome"],
    registry=registry,
)

# ── Scalability Phase 1 — realtime / SSE ─────────────────────────────────────

sse_poll_queries_total = Counter(
    "ruhu_sse_poll_queries_total",
    "Number of DB replay queries issued by SSE polling loops",
    [],
    registry=registry,
)

sse_active_subscribers = Gauge(
    "ruhu_sse_active_subscribers",
    "Number of currently connected SSE subscribers",
    [],
    registry=registry,
)

# ── Scalability Phase 1 — voice ──────────────────────────────────────────────

voice_sessions_active = Gauge(
    "ruhu_voice_sessions_active",
    "Number of currently active voice sessions",
    [],
    registry=registry,
)

# ── Scalability Phase 1 — conversations ──────────────────────────────────────

active_conversations_total = Gauge(
    "ruhu_active_conversations_total",
    "Number of conversations with status=active",
    [],
    registry=registry,
)

# ── Scalability Phase 1 — list endpoints ─────────────────────────────────────

list_endpoint_row_count = Histogram(
    "ruhu_list_endpoint_row_count",
    "Number of rows returned by list endpoints",
    ["endpoint"],
    buckets=[1, 5, 10, 25, 50, 100, 200, 500, 1000],
    registry=registry,
)

# ── Audit ──────────────────────────────────────────────────────────────────────

audit_events_total = Counter(
    "ruhu_audit_events_total",
    "Audit events emitted",
    ["event_type", "outcome"],
    registry=registry,
)

audit_queue_drops_total = Counter(
    "ruhu_audit_queue_drops_total",
    "Audit events dropped because the in-process queue was full",
    [],
    registry=registry,
)

# ── PII Scanning ───────────────────────────────────────────────────────────────

pii_scans_total = Counter(
    "ruhu_pii_scans_total",
    "PII scan calls by field context and outcome",
    ["field_context", "has_findings"],
    registry=registry,
)

pii_scan_duration_seconds = Histogram(
    "ruhu_pii_scan_duration_seconds",
    "Wall-clock time for a full tiered PII scan",
    ["field_context"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
    registry=registry,
)

pii_tier_failures_total = Counter(
    "ruhu_pii_tier_failures_total",
    "PII scanner tier failures (Presidio/DLP/regex raised an exception)",
    ["tier"],
    registry=registry,
)

pii_all_tiers_failed_total = Counter(
    "ruhu_pii_all_tiers_failed_total",
    "PII scans where every tier failed — data passed through unredacted",
    [],
    registry=registry,
)

pii_findings_total = Counter(
    "ruhu_pii_findings_total",
    "PII entity findings aggregated by type and tier",
    ["entity_type", "tier"],
    registry=registry,
)

# ── DB query timing (async engine cursor events) ──────────────────────────────

db_query_duration_seconds = Histogram(
    "ruhu_db_query_duration_seconds",
    "Wall-clock time per database query (cursor execute events)",
    ["pool", "operation"],  # bounded operation label keeps cardinality low
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
    registry=registry,
)

# ── Retention sweep workers ───────────────────────────────────────────────────

retention_sweep_rows_total = Counter(
    "ruhu_retention_sweep_rows",
    "Rows deleted by retention sweep workers, by table.",
    ["table"],
    registry=registry,
)

retention_sweep_duration_seconds = Histogram(
    "ruhu_retention_sweep_duration_seconds",
    "Wall-clock time for one retention sweep batch, by table.",
    ["table"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registry=registry,
)

retention_archival_pressure = Gauge(
    "ruhu_retention_archival_pressure",
    "Rows that have exceeded the hot-window but have not yet been swept, by table.",
    ["table"],
    registry=registry,
)

# ── Capture pipeline ──────────────────────────────────────────────────────────

capture_pipeline_duration_seconds = Histogram(
    "ruhu_capture_pipeline_duration_seconds",
    "Wall-clock time for one capture pipeline extraction.",
    ["entrypoint"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
    registry=registry,
)

capture_candidates_total = Counter(
    "ruhu_capture_candidates_total",
    "Capture candidates processed by bounded source and outcome.",
    ["source", "outcome"],
    registry=registry,
)

capture_validator_rejections_total = Counter(
    "ruhu_capture_validator_rejections_total",
    "Capture validation rejections by bounded reason.",
    ["reason"],
    registry=registry,
)

capture_safety_hits_total = Counter(
    "ruhu_capture_safety_hits_total",
    "Capture safety guard hits by bounded reason.",
    ["reason"],
    registry=registry,
)

capture_audit_write_failures_total = Counter(
    "ruhu_capture_audit_write_failures_total",
    "Capture audit write failures by mode.",
    ["mode"],
    registry=registry,
)

capture_storage_writes_total = Counter(
    "ruhu_capture_storage_writes_total",
    "Capture writes routed to non-conversation storage by bounded scope.",
    ["scope"],
    registry=registry,
)

capture_llm_calls_total = Counter(
    "ruhu_capture_llm_calls_total",
    "User-message capture LLM calls by bounded outcome.",
    ["outcome"],
    registry=registry,
)


worker_unhandled_errors_total = Counter(
    "ruhu_worker_unhandled_errors_total",
    "Unhandled exceptions bubbling past a background worker's outer loop. "
    "Non-zero means the worker caught a raise it wasn't expecting; a sustained "
    "rate means the worker may have crashed and stopped processing.",
    ["worker"],
    registry=registry,
)

pg_notify_connected = Gauge(
    "ruhu_pg_notify_connected",
    "Whether the PgNotify listener currently has a healthy LISTEN connection",
    [],
    registry=registry,
)

pg_notify_reconnects_total = Counter(
    "ruhu_pg_notify_reconnects_total",
    "PgNotify listener reconnect attempts after a listener failure",
    [],
    registry=registry,
)

# ── Credential cipher ────────────────────────────────────────────────────────
# See ``src/ruhu/tools/cipher.py``.  Decrypt volume is the rate dashboard;
# decrypt failures are the alert.  ``purpose`` is a small closed vocabulary
# (http_tool_call, oauth_refresh, admin_inspect) so cardinality stays bounded.

credential_decrypts_total = Counter(
    "ruhu_credential_decrypts_total",
    "Successful credential decrypts (one per authorised read).  Compare against "
    "the audit trail to detect missing audit events.",
    ["purpose"],
    registry=registry,
)

credential_decrypt_failures_total = Counter(
    "ruhu_credential_decrypt_failures_total",
    "Credential decrypts that failed.  Any non-zero value should page — the "
    "most likely causes are key misconfiguration, premature key retirement, "
    "or ciphertext tamper.  ``error`` is an error-class name, not a message.",
    ["purpose", "error"],
    registry=registry,
)

# ── Rate limiting ──────────────────────────────────────────────────────────────
# Labels stay low-cardinality: ``tier`` is a small enum (free/starter/
# professional/enterprise/unknown), ``endpoint`` is a first-path-segment prefix
# (conversations/knowledge/billing/kpi/rules/...), ``decision`` is allowed|blocked.
# Raw org_id and path MUST NOT appear as labels — they go in structured logs.

rate_limit_decisions_total = Counter(
    "ruhu_rate_limit_decisions_total",
    "Rate-limit decisions by subscription tier, endpoint group, and outcome",
    ["tier", "endpoint", "decision"],
    registry=registry,
)

rate_limit_bypass_total = Counter(
    "ruhu_rate_limit_bypass_total",
    "Admin bypass uses (X-Ruhu-Internal-Secret matched). Non-zero is expected "
    "for health checks and internal ops; sudden spikes warrant investigation.",
    ["endpoint"],
    registry=registry,
)

rate_limit_tier_lookup_seconds = Histogram(
    "ruhu_rate_limit_tier_lookup_seconds",
    "Time spent resolving an org's tier (local cache → Redis → billing store). "
    "P99 >50ms suggests the cache is thrashing or the store is slow.",
    [],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.5],
    registry=registry,
)

# ── Live (continuous) evaluation ───────────────────────────────────────────────
# Sampled-turn scoring — see ``ruhu.live_eval``. Cardinality stays low: the
# ``dimension`` label is a 4-value enum, ``scorer`` is a small set of stable
# names, ``bucket`` is a 5-value coarse score histogram, ``error_class`` is
# the Python exception class name (rare, bounded). Org/agent IDs go in
# structured logs, never here.

live_eval_turns_processed_total = Counter(
    "ruhu_live_eval_turns_processed_total",
    "Sampled turns the live-eval worker successfully drained from its inbox",
    [],
    registry=registry,
)

live_eval_scores_total = Counter(
    "ruhu_live_eval_scores_total",
    "Scores emitted by live-eval scorers, bucketed for distribution tracking",
    ["dimension", "scorer", "bucket"],
    registry=registry,
)

live_eval_scorer_duration_seconds = Histogram(
    "ruhu_live_eval_scorer_duration_seconds",
    "Wall-clock time spent inside a single live-eval scorer call",
    ["scorer"],
    buckets=[0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
    registry=registry,
)

live_eval_scorer_errors_total = Counter(
    "ruhu_live_eval_scorer_errors_total",
    "Exceptions raised inside a live-eval scorer; non-zero is a quality alert",
    ["scorer", "error_class"],
    registry=registry,
)

# Cost accounting for LLM-judged scorers. Critical for budgeting: an
# operator who enables live eval with a real Gemini judge is paying
# per-token, and without these counters they'd find out the cost only
# at the next billing cycle. Direction (``input``/``output``) is the
# only label besides scorer name — the LLM provider's pricing is
# typically asymmetric (output ~3-5x input), so dashboards need to
# split them.
live_eval_judge_tokens_total = Counter(
    "ruhu_live_eval_judge_tokens_total",
    "LLM judge token usage broken down by scorer and direction (input/output)",
    ["scorer", "direction"],
    registry=registry,
)

live_eval_judge_cost_usd_total = Counter(
    "ruhu_live_eval_judge_cost_usd_total",
    "Cumulative LLM judge cost in USD per scorer (when reported by provider)",
    ["scorer"],
    registry=registry,
)

# Counts exceptions swallowed inside ``safe_observe`` / callers that used to do
# ``try: metric.xxx(...) except Exception: pass``. A bare ``except`` silently
# eats schema-drift bugs in label sets and dead-collector bugs; this counter
# makes that silence visible. Non-zero = something is wrong with the metric
# emission itself, not with the app path it's instrumenting.
metric_record_failures_total = Counter(
    "ruhu_metric_record_failures_total",
    "Exceptions caught while emitting a metric (label-set mismatch, dead collector, etc.)",
    ["metric"],
    registry=registry,
)


def safe_observe(metric_name: str, fn, *args, **kwargs) -> None:
    """Invoke a metric method defensively.

    Use this instead of ``try: metric.observe/inc/set(...) except Exception: pass``.
    A failure is logged at WARNING once per run, and ``metric_record_failures_total``
    is incremented so silent metric drift is visible in dashboards.

    ``metric_name`` is the metric identifier used as the label on the failure
    counter — keep its cardinality bounded (a small static string, not a
    dynamic value).

    Example::

        from .observability.metrics import safe_observe, tool_invocation_duration_seconds

        safe_observe(
            "tool_invocation_duration_seconds",
            tool_invocation_duration_seconds.labels(tool_ref=spec.ref).observe,
            latency_seconds,
        )
    """
    try:
        fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 — observability must never kill a request
        import logging
        logging.getLogger(__name__).warning(
            "metric.record_failed",
            extra={"metric": metric_name},
            exc_info=True,
        )
        try:
            metric_record_failures_total.labels(metric=metric_name).inc()
        except Exception:  # noqa: BLE001 — final fallback; do not recurse
            pass


def make_metrics_app():
    """Return an ASGI app that serves Prometheus metrics at the mount point."""
    return make_asgi_app(registry=registry)


def counter_snapshot_rows(counter: Counter, *, sample_name_suffix: str = "_total") -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for metric in counter.collect():
        for sample in metric.samples:
            if not sample.name.endswith(sample_name_suffix):
                continue
            rows.append(
                {
                    "labels": {str(key): str(value) for key, value in sample.labels.items()},
                    "value": float(sample.value),
                }
            )
    rows.sort(key=lambda item: (sorted(item["labels"].items()), item["value"]))  # type: ignore[arg-type]
    return rows
