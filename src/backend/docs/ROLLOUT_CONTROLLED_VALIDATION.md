# Controlled Rollout and Post-Launch Validation

## Rollout Strategy

Phase 0 (`0%`):
- Deploy with monitoring only.
- Validate `/health`, `/api/v1/inventory/health`, and admin metrics manually.

Phase 1 (`10%` traffic canary):
- Enable canary users.
- Watch error rate, latency, retry backlog, and inventory guardrails for 2 hours.

Phase 2 (`25%`):
- Expand only if no `SEV-1/SEV-2` alerts in Phase 1.
- Validate editorial actions and playlist continuity.

Phase 3 (`50%`):
- Hold for one business day.
- Compare feed diversity metrics against baseline.

Phase 4 (`100%`):
- Full rollout.
- Keep elevated alert sensitivity for first 24 hours.

Rollback trigger (any phase):
- sustained 5xx > 3%
- feed inventory unhealthy for 10+ minutes
- ingestion failed feed count >= 3 for 10+ minutes

---

## Post-Launch Validation Command

Run:

```bash
PYTHONPATH=. python scripts/post_launch_validation.py \
  --base-url http://localhost:8000 \
  --admin-key <ADMIN_API_KEY>
```

Checks performed:
- API health
- inventory health
- session playlist availability
- source metrics
- inventory metrics guardrails

Guardrails:
- `dominant_source_pct <= 30`
- `infra_share_pct >= 10`
- inventory not requesting top-up continuously

---

## Launch-Day Checklist

1. Confirm dashboards are loaded and alert routes are active.
2. Execute rollout validation script before increasing each phase.
3. Verify at least one manual editorial action is reflected in feed ranking.
4. Record phase start/end timestamps in incident channel.
5. Keep rollback command and previous deploy artifact ready.

---

## 24h Post-Launch Checklist

1. Run rollout validation script every 4 hours.
2. Review top 20 feed items for diversity and freshness drift.
3. Confirm retry backlog is flat/downward.
4. Confirm no unresolved `SEV-2` alerts.
5. File follow-up tuning tasks for thresholds or source mix changes.
