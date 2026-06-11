from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

from .models import (
    JourneyAnalyticsSnapshot,
    JourneyDefinitionVersion,
    JourneyInstance,
)
from .schemas import (
    JourneyChannelMixAnalysis,
    JourneyChannelMixEntry,
    JourneyDropOffAnalysis,
    JourneyDropOffRow,
    JourneyFunnelAnalysis,
    JourneyFunnelStage,
    JourneyPathAnalysis,
    JourneyPathRow,
    JourneyTrendAnalysis,
    JourneyTrendPoint,
)
from .store import JourneyInstanceStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class JourneyAnalyticsScope:
    organization_id: str
    definition_id: str | None = None
    definition_version_id: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    granularity: str = "day"
    channel: str | None = None
    agent_id: str | None = None


class JourneyAnalyticsService:
    def __init__(self, instance_store: JourneyInstanceStore) -> None:
        self._instance_store = instance_store

    def filter_instances(self, scope: JourneyAnalyticsScope) -> list[JourneyInstance]:
        items = self._instance_store.list_instances(
            organization_id=scope.organization_id,
            definition_id=scope.definition_id,
        )
        filtered: list[JourneyInstance] = []
        for item in items:
            if scope.definition_version_id is not None and item.definition_version_id != scope.definition_version_id:
                continue
            if scope.period_start is not None and item.started_at < scope.period_start:
                continue
            if scope.period_end is not None and item.started_at > scope.period_end:
                continue
            if scope.agent_id is not None and scope.agent_id not in {
                item.first_agent_id,
                item.latest_agent_id,
            }:
                touchpoints = self._instance_store.list_touchpoints(
                    item.journey_id,
                    organization_id=scope.organization_id,
                )
                if not any(touchpoint.agent_id == scope.agent_id for touchpoint in touchpoints):
                    continue
            if scope.channel is not None:
                touchpoints = self._instance_store.list_touchpoints(
                    item.journey_id,
                    organization_id=scope.organization_id,
                )
                if not any(touchpoint.channel == scope.channel for touchpoint in touchpoints):
                    continue
            filtered.append(item)
        return filtered

    def funnel(
        self,
        *,
        scope: JourneyAnalyticsScope,
        definition_version: JourneyDefinitionVersion,
        persist_snapshot: bool = True,
    ) -> JourneyFunnelAnalysis:
        instances = self.filter_instances(scope)
        stage_labels = {milestone.milestone_id: milestone.name for milestone in definition_version.rules.milestones}
        stages: list[JourneyFunnelStage] = []
        for milestone in definition_version.rules.milestones:
            entered = 0
            completed = 0
            active = 0
            for instance in instances:
                if milestone.milestone_id in instance.milestone_path:
                    entered += 1
                    completed += 1
                    continue
                if instance.current_milestone_id == milestone.milestone_id:
                    entered += 1
                    active += 1
            stages.append(
                JourneyFunnelStage(
                    milestone_id=milestone.milestone_id,
                    milestone_name=milestone.name,
                    order_index=milestone.order_index,
                    entered_count=entered,
                    completed_count=completed,
                    active_count=active,
                    completion_rate=0.0 if entered == 0 else completed / entered,
                )
            )
        analysis = JourneyFunnelAnalysis(
            definition_id=definition_version.definition_id,
            definition_version_id=definition_version.definition_version_id,
            period_start=scope.period_start,
            period_end=scope.period_end,
            total_journeys=len(instances),
            completed_journeys=sum(1 for item in instances if item.status == "completed"),
            stages=stages,
        )
        if persist_snapshot:
            self._save_snapshot(scope=scope, view_kind="funnel", metrics=analysis.model_dump(mode="json"))
        return analysis

    def drop_off(
        self,
        *,
        scope: JourneyAnalyticsScope,
        definition_version: JourneyDefinitionVersion,
        persist_snapshot: bool = True,
    ) -> JourneyDropOffAnalysis:
        instances = self.filter_instances(scope)
        stage_labels = {milestone.milestone_id: milestone.name for milestone in definition_version.rules.milestones}
        rows: list[JourneyDropOffRow] = []
        next_stage_by_id: dict[str, str | None] = {}
        milestones = sorted(definition_version.rules.milestones, key=lambda item: item.order_index)
        for index, milestone in enumerate(milestones):
            next_stage_by_id[milestone.milestone_id] = (
                milestones[index + 1].milestone_id if index + 1 < len(milestones) else None
            )
        for milestone in milestones:
            drop_off_count = 0
            open_count = 0
            outcome_counts: Counter[str] = Counter()
            for instance in instances:
                reached = milestone.milestone_id in instance.milestone_path or instance.current_milestone_id == milestone.milestone_id
                if not reached:
                    continue
                next_milestone_id = next_stage_by_id[milestone.milestone_id]
                progressed = next_milestone_id is None or next_milestone_id in instance.milestone_path or instance.current_milestone_id == next_milestone_id
                if progressed:
                    continue
                if instance.status == "open":
                    open_count += 1
                else:
                    drop_off_count += 1
                    outcome_counts[instance.outcome or instance.status] += 1
            rows.append(
                JourneyDropOffRow(
                    milestone_id=milestone.milestone_id,
                    milestone_name=stage_labels[milestone.milestone_id],
                    drop_off_count=drop_off_count,
                    active_count=open_count,
                    outcome_counts=dict(outcome_counts),
                )
            )
        analysis = JourneyDropOffAnalysis(
            definition_id=definition_version.definition_id,
            definition_version_id=definition_version.definition_version_id,
            period_start=scope.period_start,
            period_end=scope.period_end,
            rows=rows,
        )
        if persist_snapshot:
            self._save_snapshot(scope=scope, view_kind="drop_off", metrics=analysis.model_dump(mode="json"))
        return analysis

    def paths(
        self,
        *,
        scope: JourneyAnalyticsScope,
        definition_version: JourneyDefinitionVersion,
        persist_snapshot: bool = True,
    ) -> JourneyPathAnalysis:
        instances = self.filter_instances(scope)
        counter: Counter[tuple[str, ...]] = Counter()
        for instance in instances:
            path = list(instance.milestone_path)
            if instance.current_milestone_id and instance.current_milestone_id not in path:
                path.append(f"{instance.current_milestone_id}:active")
            counter[tuple(path)] += 1
        rows = [
            JourneyPathRow(path=list(path), count=count)
            for path, count in counter.most_common(20)
        ]
        analysis = JourneyPathAnalysis(
            definition_id=definition_version.definition_id,
            definition_version_id=definition_version.definition_version_id,
            period_start=scope.period_start,
            period_end=scope.period_end,
            rows=rows,
        )
        if persist_snapshot:
            self._save_snapshot(scope=scope, view_kind="paths", metrics=analysis.model_dump(mode="json"))
        return analysis

    def trends(
        self,
        *,
        scope: JourneyAnalyticsScope,
        persist_snapshot: bool = True,
    ) -> JourneyTrendAnalysis:
        instances = self.filter_instances(scope)
        opened_counts: dict[datetime, int] = defaultdict(int)
        outcome_counts: dict[datetime, Counter[str]] = defaultdict(Counter)
        for instance in instances:
            opened_counts[_bucket_start(instance.started_at, scope.granularity)] += 1
            if instance.ended_at is not None:
                outcome_counts[_bucket_start(instance.ended_at, scope.granularity)][instance.outcome or instance.status] += 1
        buckets = sorted(set(opened_counts) | set(outcome_counts))
        points = [
            JourneyTrendPoint(
                bucket_start=bucket,
                opened_count=opened_counts.get(bucket, 0),
                completed_count=outcome_counts.get(bucket, Counter()).get("completed", 0),
                abandoned_count=outcome_counts.get(bucket, Counter()).get("abandoned", 0),
                transferred_count=outcome_counts.get(bucket, Counter()).get("transferred", 0),
                failed_count=outcome_counts.get(bucket, Counter()).get("failed", 0),
            )
            for bucket in buckets
        ]
        analysis = JourneyTrendAnalysis(
            definition_id=scope.definition_id,
            definition_version_id=scope.definition_version_id,
            period_start=scope.period_start,
            period_end=scope.period_end,
            granularity=scope.granularity,
            points=points,
        )
        if persist_snapshot:
            self._save_snapshot(scope=scope, view_kind="trends", metrics=analysis.model_dump(mode="json"))
        return analysis

    def channel_mix(
        self,
        *,
        scope: JourneyAnalyticsScope,
        persist_snapshot: bool = True,
    ) -> JourneyChannelMixAnalysis:
        instances = self.filter_instances(scope)
        journeys_by_channel: dict[str, set[str]] = defaultdict(set)
        touchpoints_by_channel: Counter[str] = Counter()
        for instance in instances:
            touchpoints = self._instance_store.list_touchpoints(
                instance.journey_id,
                organization_id=scope.organization_id,
            )
            for touchpoint in touchpoints:
                channel = touchpoint.channel or "unknown"
                journeys_by_channel[channel].add(instance.journey_id)
                touchpoints_by_channel[channel] += 1
        rows = [
            JourneyChannelMixEntry(
                channel=channel,
                journey_count=len(journey_ids),
                touchpoint_count=touchpoints_by_channel[channel],
            )
            for channel, journey_ids in sorted(journeys_by_channel.items(), key=lambda item: (-len(item[1]), item[0]))
        ]
        analysis = JourneyChannelMixAnalysis(
            definition_id=scope.definition_id,
            definition_version_id=scope.definition_version_id,
            period_start=scope.period_start,
            period_end=scope.period_end,
            rows=rows,
        )
        if persist_snapshot:
            self._save_snapshot(scope=scope, view_kind="channel_mix", metrics=analysis.model_dump(mode="json"))
        return analysis

    def _save_snapshot(
        self,
        *,
        scope: JourneyAnalyticsScope,
        view_kind: str,
        metrics: dict[str, object],
    ) -> JourneyAnalyticsSnapshot:
        now = _utcnow()
        filter_payload = {
            "definition_id": scope.definition_id,
            "definition_version_id": scope.definition_version_id,
            "period_start": None if scope.period_start is None else scope.period_start.isoformat(),
            "period_end": None if scope.period_end is None else scope.period_end.isoformat(),
            "granularity": scope.granularity,
            "channel": scope.channel,
            "agent_id": scope.agent_id,
        }
        filter_key = hashlib.sha256(json.dumps(filter_payload, sort_keys=True).encode("utf-8")).hexdigest()[:24]
        existing = next(
            (
                snapshot
                for snapshot in self._instance_store.list_snapshots(
                    organization_id=scope.organization_id,
                    view_kind=view_kind,
                    definition_id=scope.definition_id,
                )
                if snapshot.definition_version_id == scope.definition_version_id
                and snapshot.period_start == (scope.period_start or _default_period_start(now))
                and snapshot.period_end == (scope.period_end or now)
                and snapshot.granularity == scope.granularity
                and snapshot.filter_key == filter_key
            ),
            None,
        )
        snapshot = JourneyAnalyticsSnapshot(
            snapshot_id=existing.snapshot_id if existing is not None else str(uuid4()),
            organization_id=scope.organization_id,
            view_kind=view_kind,  # type: ignore[arg-type]
            definition_id=scope.definition_id,
            definition_version_id=scope.definition_version_id,
            period_start=scope.period_start or _default_period_start(now),
            period_end=scope.period_end or now,
            granularity=scope.granularity,
            filter_key=filter_key,
            filters=filter_payload,
            metrics=metrics,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self._instance_store.save_snapshot(snapshot)
        return snapshot


def _bucket_start(value: datetime, granularity: str) -> datetime:
    normalized = value.astimezone(timezone.utc)
    if granularity == "hour":
        return normalized.replace(minute=0, second=0, microsecond=0)
    if granularity == "week":
        day_start = normalized.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start - timedelta(days=day_start.weekday())
    return normalized.replace(hour=0, minute=0, second=0, microsecond=0)


def _default_period_start(now: datetime) -> datetime:
    return now - timedelta(days=30)
