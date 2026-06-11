# Runbook: API Availability SLO

**Alert:** `RuhuApiAvailabilityFastBurn` / `RuhuApiAvailabilitySlowBurn`  
**SLO:** 99.9% availability (≤ 0.1% HTTP 5xx error rate over 30 days)

---

## Symptoms

HTTP 5xx error rate is elevated.  Fast burn fires when the 1-hour rate exceeds
14× the budget (> 1.44%); slow burn when the 6-hour rate exceeds 6×.

---

## Diagnosis

```bash
# What endpoints are returning 5xx?
sum(rate(ruhu_http_requests_total{status_code=~"5.."}[10m])) by (endpoint, status_code)

# Overall error rate
ruhu:http_error_rate:1h
```

1. **Check recent deployments** — roll back if a new image was pushed in the
   last 30 minutes.
2. **Check probes directly** — `/live` should stay green for a healthy process;
   `/ready` should reflect DB dependency failure.
3. **Check DB connectivity** — `ruhu_db_pool_overflow > 0` is a saturation
   signal; correlate with connection failures and readiness degradation.
4. **Check realtime health** if customer-visible streaming is degraded:
   `ruhu_pg_notify_connected == 0` or a spike in `ruhu_pg_notify_reconnects_total`
   indicates the LISTEN/NOTIFY listener is unhealthy.
5. **Check LLM provider** — `ruhu_provider_error_total` rising suggests the
   provider is down or rate-limiting.
6. **Check application logs** in Loki:
   ```
   {app="ruhu"} | json | level="error" | __error__=""
   ```
7. **Check OTel traces** in Tempo for the failing request spans — pivot from
   `otel_trace_id` in log lines.

---

## Mitigation

| Cause | Action |
|---|---|
| Bad deployment | `kubectl rollout undo deployment/ruhu-api` |
| DB unreachable | Fail traffic via readiness, restore primary DB connectivity, then recover pool pressure |
| Readiness failing only | Keep liveness untouched; fix dependency failure before restoring readiness |
| PgNotify unhealthy | Restart the affected pod, verify `RUHU_PG_DIRECT_URL`, and fall back to polling clients if needed |
| LLM rate-limit | Enable static-response fallback in `RuntimeSettings` |
| Memory pressure | Increase pod memory limits; restart pods |

---

## Escalation

- **P1 (fast burn firing):** page on-call engineer immediately.
- **P2 (slow burn only):** create ticket, investigate within 4 hours.
