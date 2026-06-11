# Runbook: Turn Processing Latency

**Alert:** `RuhuTurnProcessingLatencyHigh`  
**Threshold:** p95 > 2 s sustained for 5 minutes on any channel

---

## Diagnosis

```bash
# Per-channel latency breakdown
histogram_quantile(0.95, sum(rate(ruhu_kernel_turn_duration_seconds_bucket[5m])) by (le, channel))

# Is LLM the bottleneck?
histogram_quantile(0.95, sum(rate(ruhu_llm_request_duration_seconds_bucket[5m])) by (le, stage))

# Are tools slow?
histogram_quantile(0.95, sum(rate(ruhu_tool_invocation_duration_seconds_bucket[5m])) by (le, executor_kind))
```

## Typical causes

| Cause | Signal | Fix |
|---|---|---|
| LLM slow | `ruhu_llm_request_duration_seconds p95 > 1.5s` | Reduce `max_output_tokens`; switch model |
| Tool HTTP timeout | `tool_invocations_total{status="error"}` rising | Check external tool endpoint health |
| DB query in hot path | `db_query_duration_seconds p95 > 100ms` | Add index; tune query |
| Conversation fan-out | Many concurrent turns on one conversation | Enable per-conversation turn rate limit |
