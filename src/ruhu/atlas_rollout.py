"""Atlas generator rollout-readiness summary (AR-5.1d).

Extracted from ``atlas_coordinator`` — this is pure metrics aggregation over the
Prometheus counters with no coordinator state beyond the set of enabled
heuristic families, so it lives on its own rather than inflating the coordinator.
"""
from __future__ import annotations

from .atlas_protocol import (
    AtlasRolloutCounterRow,
    AtlasRolloutFamilySummary,
    AtlasRolloutPolicy,
    AtlasRolloutSummaryResponse,
)
from .observability.metrics import (
    atlas_apply_deltas_total,
    atlas_generator_delta_candidates_total,
    atlas_generator_delta_filtered_total,
    atlas_generator_fallback_total,
    atlas_generator_requests_total,
    atlas_review_decisions_total,
    counter_snapshot_rows,
)

# Trial-retirement gates: a heuristic family is eligible to be retired in favour
# of the Anthropic generator once it has enough Anthropic-generated candidates,
# reviewed deltas and apply attempts, all meeting quality thresholds.
_ATLAS_ROLLOUT_MIN_ANTHROPIC_GENERATED_CANDIDATES = 200
_ATLAS_ROLLOUT_MIN_REVIEWED_DELTAS = 50
_ATLAS_ROLLOUT_MIN_APPLY_ATTEMPTS = 20
_ATLAS_ROLLOUT_MIN_ANTHROPIC_SUCCESS_RATE = 0.95
_ATLAS_ROLLOUT_MIN_REVIEW_APPROVAL_RATE = 0.70
_ATLAS_ROLLOUT_MIN_APPLY_SUCCESS_RATE = 0.95
_ATLAS_ROLLOUT_MAX_FALLBACK_RATE = 0.10
_ATLAS_ROLLOUT_MIN_SEMANTIC_VALIDATION_PASS_RATE = 0.90
# Fallback reasons that reflect generation quality. Configuration states
# (missing API key) and empty turns are not quality signals.
_ATLAS_QUALITY_FALLBACK_REASONS = {"provider_failure"}


def build_atlas_rollout_summary(
    *, enabled_heuristic_families: set[str]
) -> AtlasRolloutSummaryResponse:
    """Aggregate the generator/review/apply counters into a rollout summary."""

    def _rows(counter) -> list[AtlasRolloutCounterRow]:
        return [AtlasRolloutCounterRow.model_validate(item) for item in counter_snapshot_rows(counter)]

    generator_request_rows = _rows(atlas_generator_requests_total)
    anthropic_request_total = sum(
        item.value for item in generator_request_rows if item.labels.get("provider") == "anthropic"
    )
    anthropic_request_success_total = sum(
        item.value
        for item in generator_request_rows
        if item.labels.get("provider") == "anthropic" and item.labels.get("outcome") == "success"
    )
    anthropic_success_rate = (
        anthropic_request_success_total / anthropic_request_total if anthropic_request_total > 0 else None
    )
    fallback_rows = _rows(atlas_generator_fallback_total)
    quality_fallback_total = sum(
        item.value for item in fallback_rows if item.labels.get("reason") in _ATLAS_QUALITY_FALLBACK_REASONS
    )
    fallback_rate = quality_fallback_total / anthropic_request_total if anthropic_request_total > 0 else None
    generated_rows = _rows(atlas_generator_delta_candidates_total)
    filtered_rows = _rows(atlas_generator_delta_filtered_total)
    review_rows = _rows(atlas_review_decisions_total)
    apply_rows = _rows(atlas_apply_deltas_total)

    families = sorted(
        {
            *enabled_heuristic_families,
            *(item.labels.get("family", "") for item in generated_rows if item.labels.get("family")),
            *(item.labels.get("family", "") for item in filtered_rows if item.labels.get("family")),
            *(item.labels.get("family", "") for item in review_rows if item.labels.get("family")),
            *(item.labels.get("family", "") for item in apply_rows if item.labels.get("family")),
        }
    )

    family_summaries: list[AtlasRolloutFamilySummary] = []
    for family in families:
        generated_total = sum(item.value for item in generated_rows if item.labels.get("family") == family)
        anthropic_generated_total = sum(
            item.value
            for item in generated_rows
            if item.labels.get("family") == family and item.labels.get("mode") == "anthropic"
        )
        fallback_generated_total = sum(
            item.value
            for item in generated_rows
            if item.labels.get("family") == family and item.labels.get("mode") == "fallback"
        )
        filtered_total = sum(item.value for item in filtered_rows if item.labels.get("family") == family)
        approved_total = sum(
            item.value
            for item in review_rows
            if item.labels.get("family") == family and item.labels.get("decision") == "approved"
        )
        rejected_total = sum(
            item.value
            for item in review_rows
            if item.labels.get("family") == family and item.labels.get("decision") == "rejected"
        )
        applied_total = sum(
            item.value
            for item in apply_rows
            if item.labels.get("family") == family and item.labels.get("outcome") == "applied"
        )
        failed_total = sum(
            item.value
            for item in apply_rows
            if item.labels.get("family") == family and item.labels.get("outcome") == "failed"
        )
        rejected_apply_total = sum(
            item.value
            for item in apply_rows
            if item.labels.get("family") == family and item.labels.get("outcome") == "rejected"
        )
        review_total = approved_total + rejected_total
        apply_total = applied_total + failed_total + rejected_apply_total
        rollout_reasons: list[str] = []
        if anthropic_generated_total < _ATLAS_ROLLOUT_MIN_ANTHROPIC_GENERATED_CANDIDATES:
            rollout_reasons.append(
                "needs at least "
                f"{_ATLAS_ROLLOUT_MIN_ANTHROPIC_GENERATED_CANDIDATES} "
                f"Anthropic-generated candidates; currently {int(anthropic_generated_total)}"
            )
        if review_total < _ATLAS_ROLLOUT_MIN_REVIEWED_DELTAS:
            rollout_reasons.append(
                f"needs at least {_ATLAS_ROLLOUT_MIN_REVIEWED_DELTAS} reviewed deltas; currently {int(review_total)}"
            )
        if apply_total < _ATLAS_ROLLOUT_MIN_APPLY_ATTEMPTS:
            rollout_reasons.append(
                f"needs at least {_ATLAS_ROLLOUT_MIN_APPLY_ATTEMPTS} apply attempts; currently {int(apply_total)}"
            )
        if anthropic_success_rate is None:
            rollout_reasons.append("no Anthropic request data recorded yet")
        elif anthropic_success_rate < _ATLAS_ROLLOUT_MIN_ANTHROPIC_SUCCESS_RATE:
            rollout_reasons.append(
                "global Anthropic success rate is below "
                f"{_ATLAS_ROLLOUT_MIN_ANTHROPIC_SUCCESS_RATE:.0%}; "
                f"currently {anthropic_success_rate:.1%}"
            )
        approval_rate = (approved_total / review_total) if review_total > 0 else None
        apply_success_rate = (applied_total / apply_total) if apply_total > 0 else None
        semantic_filtered_total = sum(
            item.value
            for item in filtered_rows
            if item.labels.get("family") == family
            and item.labels.get("reason") in {"semantic_validation", "invalid_dependency"}
        )
        semantic_validation_pass_rate = (
            max(0.0, 1.0 - (semantic_filtered_total / generated_total)) if generated_total > 0 else None
        )
        if approval_rate is not None and approval_rate < _ATLAS_ROLLOUT_MIN_REVIEW_APPROVAL_RATE:
            rollout_reasons.append(
                f"approval rate is below {_ATLAS_ROLLOUT_MIN_REVIEW_APPROVAL_RATE:.0%}; currently {approval_rate:.1%}"
            )
        if apply_success_rate is not None and apply_success_rate < _ATLAS_ROLLOUT_MIN_APPLY_SUCCESS_RATE:
            rollout_reasons.append(
                f"apply success rate is below {_ATLAS_ROLLOUT_MIN_APPLY_SUCCESS_RATE:.0%}; currently {apply_success_rate:.1%}"
            )
        if fallback_rate is not None and fallback_rate > _ATLAS_ROLLOUT_MAX_FALLBACK_RATE:
            rollout_reasons.append(
                f"generator fallback rate is above {_ATLAS_ROLLOUT_MAX_FALLBACK_RATE:.0%}; currently {fallback_rate:.1%}"
            )
        if (
            semantic_validation_pass_rate is not None
            and semantic_validation_pass_rate < _ATLAS_ROLLOUT_MIN_SEMANTIC_VALIDATION_PASS_RATE
        ):
            rollout_reasons.append(
                "semantic validation pass rate is below "
                f"{_ATLAS_ROLLOUT_MIN_SEMANTIC_VALIDATION_PASS_RATE:.0%}; "
                f"currently {semantic_validation_pass_rate:.1%}"
            )
        if (
            anthropic_generated_total >= _ATLAS_ROLLOUT_MIN_ANTHROPIC_GENERATED_CANDIDATES
            and review_total >= _ATLAS_ROLLOUT_MIN_REVIEWED_DELTAS
            and apply_total >= _ATLAS_ROLLOUT_MIN_APPLY_ATTEMPTS
        ):
            rollout_status = "eligible_for_trial_retirement" if not rollout_reasons else "hold"
        else:
            rollout_status = "not_enough_data"
        family_summaries.append(
            AtlasRolloutFamilySummary(
                family=family,
                heuristic_enabled=family in enabled_heuristic_families,
                generated_candidates=generated_total,
                anthropic_generated_candidates=anthropic_generated_total,
                fallback_generated_candidates=fallback_generated_total,
                filtered_candidates=filtered_total,
                approved_reviews=approved_total,
                rejected_reviews=rejected_total,
                applied_deltas=applied_total,
                failed_applies=failed_total,
                rejected_applies=rejected_apply_total,
                approval_rate=approval_rate,
                apply_success_rate=apply_success_rate,
                semantic_validation_pass_rate=semantic_validation_pass_rate,
                rollout_status=rollout_status,
                rollout_reasons=rollout_reasons,
            )
        )

    return AtlasRolloutSummaryResponse(
        policy=AtlasRolloutPolicy(
            min_anthropic_generated_candidates=_ATLAS_ROLLOUT_MIN_ANTHROPIC_GENERATED_CANDIDATES,
            min_reviewed_deltas=_ATLAS_ROLLOUT_MIN_REVIEWED_DELTAS,
            min_apply_attempts=_ATLAS_ROLLOUT_MIN_APPLY_ATTEMPTS,
            min_anthropic_success_rate=_ATLAS_ROLLOUT_MIN_ANTHROPIC_SUCCESS_RATE,
            min_review_approval_rate=_ATLAS_ROLLOUT_MIN_REVIEW_APPROVAL_RATE,
            min_apply_success_rate=_ATLAS_ROLLOUT_MIN_APPLY_SUCCESS_RATE,
            max_fallback_rate=_ATLAS_ROLLOUT_MAX_FALLBACK_RATE,
            min_semantic_validation_pass_rate=_ATLAS_ROLLOUT_MIN_SEMANTIC_VALIDATION_PASS_RATE,
        ),
        heuristic_enabled_families=sorted(enabled_heuristic_families),
        family_summaries=family_summaries,
        generator_requests=generator_request_rows,
        generator_fallbacks=fallback_rows,
        generated_delta_candidates=generated_rows,
        filtered_deltas=filtered_rows,
        review_decisions=review_rows,
        apply_outcomes=apply_rows,
    )
