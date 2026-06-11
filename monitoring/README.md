# Ruhu — Staff Observability (Phase S4)

Git-tracked monitoring configuration for the Ruhu platform.  All files here
are intended for Ruhu staff (on-call, SRE, platform engineers) — not customer-facing.

---

## Directory structure

```
monitoring/
├── grafana/
│   ├── provisioning.yml          # Grafana auto-provisioning config
│   └── dashboards/
│       ├── ruhu-api.json         # HTTP request rate, error rate, latency
│       ├── ruhu-turn-runtime.json # Turn throughput, errors, trace write
│       ├── ruhu-providers.json   # LLM latency, token spend, cost
│       └── ruhu-database.json    # Query latency, pool saturation
├── alerts/
│   └── ruhu-slo-alerts.yml       # Prometheus alert rules (SLO-based)
└── runbooks/
    ├── api-availability.md
    ├── api-latency.md
    ├── provider-health.md
    ├── trace-write.md
    ├── db-pool.md
    └── turn-latency.md
```

---

## SLOs

| SLO | Target | Alert |
|---|---|---|
| API availability | 99.9% (≤ 0.1% 5xx) | `RuhuApiAvailabilityFastBurn` / `SlowBurn` |
| API p95 latency | < 1 s | `RuhuApiLatencyBreach` / `Critical` |
| Turn processing p95 | < 2 s | `RuhuTurnProcessingLatencyHigh` |
| TurnTrace write p99 | < 50 ms | `RuhuTraceWriteLatencyBreach` |

---

## Deploying dashboards

### Grafana with file provisioning

1. Copy `grafana/dashboards/` to the path your Grafana instance watches.
2. Copy `grafana/provisioning.yml` to `/etc/grafana/provisioning/dashboards/ruhu.yml`.
3. Grafana auto-imports within 30 seconds.

### Grafana Cloud / managed Grafana

Use the Grafana API to import each dashboard JSON:

```bash
for f in monitoring/grafana/dashboards/*.json; do
  curl -s -X POST \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $GRAFANA_API_KEY" \
    -d "{\"dashboard\": $(cat $f), \"overwrite\": true, \"folderId\": 0}" \
    "$GRAFANA_URL/api/dashboards/import"
done
```

---

## Deploying alert rules

### Prometheus + AlertManager

Load `alerts/ruhu-slo-alerts.yml` via the Prometheus `rule_files` config:

```yaml
rule_files:
  - /etc/prometheus/rules/ruhu-slo-alerts.yml
```

Reload Prometheus:

```bash
curl -X POST http://prometheus:9090/-/reload
```

### Alert routing (AlertManager)

Route `team=ruhu-platform` alerts to your on-call channel:

```yaml
# alertmanager.yml
route:
  routes:
    - match:
        team: ruhu-platform
      receiver: ruhu-oncall
receivers:
  - name: ruhu-oncall
    pagerduty_configs:
      - service_key: "<RUHU_PAGERDUTY_KEY>"
```

---

## Metrics reference

All metrics are defined in `src/ruhu/observability/metrics.py`.  Cardinality
rules: no `organization_id`, `conversation_id`, `turn_id`, or user-supplied
strings as label values — see `docs/observability-system/Log-Schema.md`.

Recent rollout-sensitive metrics:

- `ruhu_pg_notify_connected` — listener health for realtime LISTEN/NOTIFY
- `ruhu_pg_notify_reconnects_total` — reconnect attempts after listener failure
