# Runbook: Prefill-First Classifier

Alerts in `monitoring/alerts/ruhu-classifier-alerts.yml`. Dashboard at
`monitoring/grafana/dashboards/ruhu-classifier.json` (uid `ruhu-classifier-v1`).

Architecture: see [`docs/pre-fill-intent-classifier-design/`](../../docs/pre-fill-intent-classifier-design/).

---

## Latency

**Alerts:** `RuhuClassifierLatencyHigh` (warn, p95 > 500ms / 10m), `RuhuClassifierLatencyCritical` (crit, p95 > 1s / 2m)

```promql
# Per-backend p95
histogram_quantile(0.95, sum by (le, backend) (rate(ruhu_classifier_request_duration_seconds_bucket[5m])))

# Cold cache vs warm cache split — if false-bucket dominates, prefix cache is cold
histogram_quantile(0.95, sum by (le, cache_hit) (rate(ruhu_classifier_request_duration_seconds_bucket[5m])))

# Per-step — narrow down which step's catalog is slow
topk(10, histogram_quantile(0.95, sum by (le, step_id) (rate(ruhu_classifier_request_duration_seconds_bucket[5m]))))
```

| Cause | Signal | Fix |
|---|---|---|
| Cold prefix cache after deploy / restart | `cache_hit="false"` dominates `prefill_tokens` rate | Wait 5–10 min for cache to warm; alert window absorbs this |
| GPU contention (vLLM/Stage 3) | `vllm:gpu_cache_usage_perc > 0.9` | Scale replicas; reduce `max_num_seqs` |
| Oversized label catalog | `decode_tokens` p95 > 5 on the affected step | Tighten intent set; fewer/shorter labels per step |
| LoRA adapter swap thrash (vLLM) | Many distinct `lora` labels firing per minute | Pin agents to a smaller adapter pool |
| In-process backend overloaded | `backend="transformers"` only, single replica | Failover: `RUHU_CLASSIFIER_FAILOVER_TO_MAIN_LLM=true`, or move to vLLM (Stage 3) |

If both alerts are critical and persist > 10 min, set `RUHU_CLASSIFIER_FAILOVER_TO_MAIN_LLM=true` to route classification to the main LLM and restore turn throughput while you investigate.

---

## Unknown rate

**Alert:** `RuhuClassifierUnknownRateHigh` (warn, ratio > 20% / 15m)

```promql
# Per-step unknown rate — find the step whose catalog is too narrow
sum by (step_id) (rate(ruhu_classifier_unknown_total[10m]))
/
clamp_min(
  sum by (step_id) (rate(ruhu_classifier_unknown_total[10m]))
  + sum by (step_id) (rate(ruhu_classifier_decisions_total[10m])),
  0.0001
)
```

| Cause | Signal | Fix |
|---|---|---|
| Label catalog doesn't cover real traffic | One step has unknown ratio ≫ others | Inspect recent turns at that step; add missing intents |
| Out-of-policy traffic spike (e.g. broadcast campaign) | Spike correlated with traffic spike | Usually self-resolves; consider a fallback step |
| Prompt drift after agent edit | Spike correlated with new agent_version_id | Roll back the agent version; review the catalog change |
| Base model regression after weights swap | Spike across all agents on one backend | Roll back the model; bisect via `lora` label |

---

## Confidence drift

**Alert:** `RuhuClassifierConfidenceLow` (warn, p50 < 0.6 / 1h)

```promql
# Confidence percentiles over a longer window
histogram_quantile(0.50, sum by (le) (rate(ruhu_classifier_confidence_bucket[1h])))
histogram_quantile(0.10, sum by (le) (rate(ruhu_classifier_confidence_bucket[1h])))
```

A healthy classifier has a bimodal confidence distribution: a tall peak near 1.0 (confident wins) and a smaller mass below 0.5 (genuinely ambiguous turns). Sustained low p50 means the bimodality has collapsed.

| Cause | Signal | Fix |
|---|---|---|
| LoRA degraded after fine-tune | New `lora` label correlates with the drop | Roll back to previous LoRA version |
| Base model update flattened distribution | Drop is global across all `lora` values | Pin to previous base model version |
| New step with overlapping intents | Drop localised to one step | Merge or sharpen the overlapping labels |

---

## Prefix cache

**Alert:** `RuhuClassifierPrefixCacheBroken` (warn, hit ratio < 50% / 30m with traffic > 1 rps)

```promql
# Hit ratio
sum(rate(ruhu_classifier_prefill_tokens_total{cache_hit="true"}[30m]))
/
clamp_min(sum(rate(ruhu_classifier_prefill_tokens_total[30m])), 0.0001)

# Token-per-call breakdown — cache misses prefill more tokens
sum by (cache_hit) (rate(ruhu_classifier_prefill_tokens_total[5m]))
```

This is the load-bearing claim of the prefill-first design. A 50%+ miss rate in steady state means the prefix changes per turn, defeating the optimisation.

| Cause | Signal | Fix |
|---|---|---|
| Prompt template embeds user message in prefix | Hit ratio always ~0% | Move user text after the prefix boundary; see `docs/pre-fill-intent-classifier-design/04-runtime-spec.md` §Prompt structure |
| `agent_version_id` cycling per request | Many distinct `lora` values per minute | Pin agents to a stable version unless deploying |
| vLLM cache eviction pressure (Stage 3+) | `vllm:gpu_cache_usage_perc > 0.95` | Scale replicas or reduce `max_num_seqs` |
| Cache cleared on classifier restart | Drop coincides with deploy | Self-heals in 5–10 min; alert window absorbs |

---

## Errors

**Alert:** `RuhuClassifierErrorRateHigh` (warn, > 0.1 errors/s by kind / 5m)

```promql
sum by (error_kind, backend) (rate(ruhu_classifier_errors_total[5m]))
```

`error_kind` is the colon-prefix of `ClassificationResult.error`. Common kinds:

| `error_kind` | Meaning | Fix |
|---|---|---|
| `torch_unavailable` | Transformers backend can't import torch | Install `torch` in the deployment; usually a dependency drift |
| `generate_failed` | `model.generate` raised | Check GPU health and OOM logs; reduce `max_new_tokens` or label size |
| `empty_request` | No `user_text` or no valid intents | Upstream bug — kernel called classifier with an empty step; file a bug |
| `unknown_label` | Decoder emitted a non-catalog label | Constrained-decoding processor regressed — check `ConstrainedLabelProcessor` |
| `timeout` (Stage 3+) | vLLM request exceeded deadline | Reduce label catalog; raise client timeout |
| `5xx` (Stage 3+) | vLLM returned 5xx | Check vLLM health endpoint and logs |
| `connection_error` (Stage 3+) | vLLM unreachable | Check pod readiness and network |

Failover: enable `RUHU_CLASSIFIER_FAILOVER_TO_MAIN_LLM=true` to route the classifier turn to the main LLM when the prefill-first backend fails. Costs more per call but keeps turns flowing.
