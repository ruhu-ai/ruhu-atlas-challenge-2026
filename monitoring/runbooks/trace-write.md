# Runbook: TurnTrace Write Latency / Failures

**Alert:** `RuhuTraceWriteLatencyBreach` / `RuhuTraceWriteFailures`  
**Spec:** p99 write latency < 50 ms (§2.1); zero failures is the target

---

## Failures are critical

A `TraceWriteFailed` rolls back the entire turn — the customer sees an error
and the conversation state does not advance.  Sustained failures mean
conversations are frozen.

## Diagnosis

```bash
# Failure reasons
sum(rate(ruhu_trace_write_failure_total[5m])) by (reason)

# Payload truncations (soft cap hit)
rate(ruhu_trace_write_truncations_total[5m])

# DB query latency (INSERT path)
histogram_quantile(0.99, sum(rate(ruhu_db_query_duration_seconds_bucket{operation="insert"}[5m])) by (le))
```

Loki query for the trace write error:
```
{app="ruhu"} | json | event="trace write failed; turn rolled back"
```

---

## Mitigation

| Cause | Action |
|---|---|
| `db_error` | Check Postgres connectivity; failover if needed |
| `payload_too_large` | Trace payloads exceed 1 MB cap; investigate graph with huge tool outputs |
| `timeout` | DB under load; scale vertically or add read replicas |
