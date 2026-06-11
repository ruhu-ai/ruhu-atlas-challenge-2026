# Runbook: DB Pool Saturation

**Alert:** `RuhuDBPoolOverflowHigh`  
**Threshold:** `ruhu_db_pool_overflow > 10` sustained for 5 minutes

---

## Diagnosis

```bash
# Current pool state
ruhu_db_pool_checked_out
ruhu_db_pool_overflow

# Checkout wait time
histogram_quantile(0.99, sum(rate(ruhu_db_session_checkout_seconds_bucket[5m])) by (le, pool))

# Slow queries
histogram_quantile(0.99, sum(rate(ruhu_db_query_duration_seconds_bucket[5m])) by (le, operation))
```

---

## Mitigation

1. **Short term** — increase `RUHU_SYNC_DB_POOL_SIZE` (default 20) and
   `RUHU_SYNC_DB_MAX_OVERFLOW` (default 40).  Restart the pod to apply.
2. **Medium term** — check for missing indexes on high-volume queries;
   check for long-running transactions holding connections.
3. **Long term** — consider read-replica routing for `SELECT` heavy paths
   (conversation history, trace listing).

Postgres diagnostic queries:
```sql
SELECT pid, state, wait_event_type, wait_event, query_start, query
FROM pg_stat_activity
WHERE state != 'idle'
ORDER BY query_start;
```
