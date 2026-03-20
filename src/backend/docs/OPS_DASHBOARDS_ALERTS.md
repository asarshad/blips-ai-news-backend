# Dashboards and Alert Definitions

## Purpose
This document defines the minimum production dashboards and alerts required to operate the curation pipeline safely.

Source APIs:
- `GET /health`
- `GET /metrics` (admin)
- `GET /api/v1/metrics/sources` (admin)
- `GET /api/v1/metrics/inventory/health` (admin)
- `GET /api/v1/metrics/ai-coverage` (admin)
- `GET /api/v1/inventory/health`
- `GET /ops/status` (admin)

---

## Dashboard 1: Platform Health

Panels:
- API health status (`/health.status`)
- DB/Redis health (`/health.database`, `/health.redis`)
- 5xx rate (`api_server_errors_total`)
- p95 request latency (`http_request_duration_seconds`)
- Process uptime/restarts

Primary alerts:
- `api_unhealthy_critical`
- `api_error_rate_high`
- `latency_p95_degraded`

---

## Dashboard 2: Ingestion Reliability

Panels:
- Per-source ingestion status (`/api/v1/metrics/sources.sources[].feeds[].status`)
- Retry backlog (`retry_count`, `retry_at`)
- Attempted vs inserted items
- Failed feed count
- Scheduler active workers (`/metrics.ingestion_health`)

Primary alerts:
- `ingestion_feed_failures`
- `retry_backlog_growth`
- `ingestion_success_rate_drop`

---

## Dashboard 3: Feed Quality and Inventory

Panels:
- Surface inventory health (`/api/v1/inventory/health`)
- Tier counts per surface (A/B/C)
- Dominant source percentage (`/api/v1/metrics/inventory/health.dominant_source_pct`)
- Infra coverage percentage (`/api/v1/metrics/inventory/health.infra_share_pct`)
- AI share/cap check (`/api/v1/metrics/ai-coverage.ai_share_pct`)

Primary alerts:
- `surface_inventory_low`
- `source_dominance_warning`
- `infra_coverage_low`
- `ai_share_cap_breach`

---

## Dashboard 4: Editorial and Session Delivery

Panels:
- Editorial queue depth (`CANDIDATE` vs `PROMOTED` counts)
- Editorial action throughput (boost/suppress/promote)
- Session playlist generation stats (`/api/v1/session/playlist-stats`)
- Cache hit/miss for playlist snapshots

Primary alerts:
- `candidate_backlog_stale`
- `playlist_generation_failures`

---

## Alert Severity and Routing

Severity classes:
- `SEV-1` customer-visible outage or no feed delivery
- `SEV-2` major degradation, recovery needed within 60 minutes
- `SEV-3` warning/drift, remediation within business day

Routing:
- `SEV-1` page on-call immediately
- `SEV-2` page on-call during active hours, escalate to incident channel
- `SEV-3` create ticket and track in backlog

---

## Implementation Notes

- Keep thresholds in a machine-readable file for parity across environments:
  `docs/ops/dashboard_alerts.yaml`
- Validate dashboards weekly against production traffic shape.
- Re-tune thresholds after each rollout phase.
