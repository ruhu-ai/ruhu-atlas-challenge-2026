# Runbook: API Latency SLO

**Alert:** `RuhuApiLatencyBreach` / `RuhuApiLatencyCritical`  
**SLO:** p95 HTTP request latency < 1 s

---

## Diagnosis

```bash
# Latency by endpoint
histogram_quantile(0.95, sum(rate(ruhu_http_request_duration_seconds_bucket[5m])) by (le, endpoint))

# DB checkout wait (pool exhaustion symptom)
histogram_quantile(0.99, sum(rate(ruhu_db_session_checkout_seconds_bucket[5m])) by (le, pool))

# LLM latency contribution
histogram_quantile(0.95, sum(rate(ruhu_llm_request_duration_seconds_bucket[5m])) by (le, provider, model, stage))
```

Correlate with Loki logs:
```
{app="ruhu"} | json | level="warning" | line_format "{{.otel_trace_id}} {{.turn_id}} {{.event}}"
```

---

## Common causes and fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| `/conversations/*/turns` slow only | LLM latency | Switch to a faster model; enable streaming |
| All endpoints slow | DB pool exhaustion | Increase `RUHU_SYNC_DB_POOL_SIZE`; add read replica |
| Random spikes | GC pressure | Increase pod memory; reduce max_overflow |
| Steady climb post-deploy | Memory leak | Roll back; profile with `py-spy` |
