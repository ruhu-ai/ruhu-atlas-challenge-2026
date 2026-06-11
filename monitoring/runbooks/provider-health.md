# Runbook: LLM Provider Health

**Alert:** `RuhuLLMProviderErrorRateHigh`  
**Threshold:** > 5% error rate on any provider over 5 minutes

---

## Diagnosis

```bash
# Error kinds by provider
sum(rate(ruhu_provider_error_total[5m])) by (provider, kind)

# Success rate
sum(rate(ruhu_llm_request_duration_seconds_count{outcome="ok"}[5m])) by (provider)
  /
sum(rate(ruhu_llm_request_duration_seconds_count[5m])) by (provider)
```

Check provider status pages:
- Google AI / Vertex: https://status.cloud.google.com

---

## Mitigation

1. **`http_error` / `timeout` from Gemini** — check API key quota and region
   status.  Set `RUHU_RESPONSE_GENERATOR_MAX_OUTPUT_TOKENS` lower to reduce
   latency.
2. **`auth_error`** — API key expired or revoked; rotate immediately.
3. **Persistent outage (> 15 min)** — enable static-response fallback:
   set the graph's `response_policy.policy_type = "static"` for affected
   agents until the provider recovers.
