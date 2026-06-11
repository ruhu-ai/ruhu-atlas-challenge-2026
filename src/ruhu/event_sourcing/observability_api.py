"""Observability API for event sourcing metrics.

Endpoint: GET /internal/event-metrics
Returns: Event processing stats, latencies, error rates
"""

from fastapi import APIRouter
from ruhu.event_sourcing.metrics import get_collector

router = APIRouter(prefix="/internal", tags=["observability"])


@router.get("/event-metrics", tags=["observability"])
async def get_event_metrics() -> dict:
    """Get event processing metrics and statistics.

    Returns:
    - Total events processed
    - Success/error counts by event type
    - Latency percentiles (p50, p95, p99)
    - Error distribution
    """
    collector = get_collector()
    stats = collector.get_stats()

    if not stats["total_events"]:
        return {
            "status": "no_events",
            "message": "No events processed yet",
        }

    return {
        "status": "healthy" if stats["error_count"] == 0 else "degraded",
        "total_events_processed": stats["total_events"],
        "success_rate_pct": round(
            100 * stats["success_count"] / stats["total_events"], 2
        ),
        "error_count": stats["error_count"],
        "event_types": stats["event_types"],
        "latency_percentiles": {
            "p50_ms": stats["latencies_p50"],
            "p95_ms": stats["latencies_p95"],
            "p99_ms": stats["latencies_p99"],
        },
        "errors": stats["error_distribution"],
    }


@router.post("/event-metrics/reset", tags=["observability"])
async def reset_metrics() -> dict:
    """Reset event metrics (for testing/debugging only)."""
    collector = get_collector()
    collector.reset()
    return {"status": "reset", "message": "Metrics cleared"}


def install_observability_router(app) -> None:
    """Install observability endpoints into FastAPI app."""
    app.include_router(router)
