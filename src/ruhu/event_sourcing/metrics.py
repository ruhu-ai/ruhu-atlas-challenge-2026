"""Event sourcing metrics and observability instrumentation.

Tracks:
- Event processing latency per event type
- Projection update lag (staleness)
- Error rates and exception types
- Event throughput
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class EventMetric:
    """Metric for event processing."""
    event_type: str
    duration_ms: float
    success: bool
    error: Optional[str] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class EventMetricsCollector:
    """Collects metrics on event processing."""

    def __init__(self):
        self.metrics: list[EventMetric] = []
        self.counters = defaultdict(int)
        self.latencies = defaultdict(list)
        self.errors = defaultdict(int)

    def record_event(self, metric: EventMetric) -> None:
        """Record an event metric."""
        self.metrics.append(metric)

        # Update counters
        self.counters[f"{metric.event_type}.total"] += 1
        if metric.success:
            self.counters[f"{metric.event_type}.success"] += 1
        else:
            self.counters[f"{metric.event_type}.error"] += 1
            if metric.error:
                self.errors[f"{metric.event_type}:{metric.error}"] += 1

        # Track latencies
        self.latencies[metric.event_type].append(metric.duration_ms)

        # Log
        status = "✓" if metric.success else "✗"
        error_msg = f" ({metric.error})" if metric.error else ""
        logger.info(
            f"{status} Event processed: {metric.event_type} in {metric.duration_ms:.1f}ms{error_msg}"
        )

    def get_stats(self) -> dict:
        """Get summary statistics."""
        stats = {
            "total_events": len(self.metrics),
            "success_count": sum(1 for m in self.metrics if m.success),
            "error_count": sum(1 for m in self.metrics if not m.success),
            "event_types": {},
            "latencies_p50": {},
            "latencies_p95": {},
            "latencies_p99": {},
            "error_distribution": dict(self.errors),
        }

        # Per-type stats
        for event_type in set(m.event_type for m in self.metrics):
            latencies = self.latencies[event_type]
            if latencies:
                sorted_latencies = sorted(latencies)
                n = len(sorted_latencies)
                stats["latencies_p50"][event_type] = sorted_latencies[int(n * 0.50)]
                stats["latencies_p95"][event_type] = sorted_latencies[int(n * 0.95)]
                stats["latencies_p99"][event_type] = sorted_latencies[int(n * 0.99)]

                stats["event_types"][event_type] = {
                    "count": self.counters[f"{event_type}.total"],
                    "success": self.counters[f"{event_type}.success"],
                    "error": self.counters[f"{event_type}.error"],
                    "avg_latency_ms": sum(latencies) / len(latencies),
                    "min_latency_ms": min(latencies),
                    "max_latency_ms": max(latencies),
                }

        return stats

    def reset(self) -> None:
        """Reset all metrics."""
        self.metrics.clear()
        self.counters.clear()
        self.latencies.clear()
        self.errors.clear()


# Global collector instance
_collector: Optional[EventMetricsCollector] = None


def get_collector() -> EventMetricsCollector:
    """Get or create global metrics collector."""
    global _collector
    if _collector is None:
        _collector = EventMetricsCollector()
    return _collector
