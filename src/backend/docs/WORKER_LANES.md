# Single-Service Worker Lanes

Blips runs one Render worker service. Inside that one Linux container, the
launcher starts small independent lane processes. Lanes coordinate through
durable Postgres/Redis state; they do not call each other inline.

```mermaid
flowchart TD
  R["Render worker service\none Linux container"] --> L["app.workers.launcher\nprocess lifecycle + leader lock only"]

  L --> I["ingestion_lane\nfetch sources + insert content"]
  L --> P["promotion_lane\ncontent.promotion_eval.requested"]
  L --> A["ai_summary_lane\ncontent.ai_summary.requested"]
  L --> IMG["article_images_lane\narticle.image_verification.requested"]
  L --> READY["ready_events_lane\ncontent.ready / content.unready"]
  L --> M["maintenance_lane\nclustering, scoring, health, cleanup"]

  I --> DB["Postgres"]
  P --> DB
  A --> DB
  IMG --> DB
  READY --> DB
  M --> DB

  DB --> OUTBOX["content_event_outbox\nFOR UPDATE SKIP LOCKED"]
  I -->|"queue promotion"| OUTBOX
  P -->|"queue AI / image / readiness"| OUTBOX
  A -->|"queue readiness"| OUTBOX
  IMG -->|"queue readiness"| OUTBOX
  READY -->|"refresh cache + push"| CACHE["feed cache / push side effects"]

  REDIS["Redis\nleader lock, source leases, cooldowns, lane heartbeats"] --- L
  REDIS --- I
  REDIS --- P
  REDIS --- A
  REDIS --- IMG
  REDIS --- READY
  REDIS --- M
```

## Rules

- Ingestion is only a producer: fetch sources, insert content, queue events.
- Promotion, AI summaries, image verification, and ready/unready handling run
  in their own lanes.
- No lane waits because another lane is active.
- Lanes may back off only for real resource pressure: memory, DB pressure,
  source cooldowns, or item/event-level locks.
- The launcher owns lifecycle only: start lanes, forward shutdown, and stop the
  container if a critical lane exits.
- If a lane process exits, the launcher exits the whole container so Render
  restarts it. If a lane stays alive but stops heartbeating, the launcher
  watchdog also exits the container after the configured stale window.
- The durable outbox is the source of truth. Redis pub/sub can be added later
  as a wake-up hint, but it must not replace durable work rows.

## Recovery

- Crash recovery is fail-fast: one lane crash causes a full worker restart.
- Hang recovery is heartbeat-based: each lane writes Redis heartbeats, and the
  launcher treats stale heartbeats as unhealthy.
- Defaults: `WORKER_LANE_STALE_SECONDS=900`,
  `WORKER_LANE_WATCHDOG_INTERVAL_SECONDS=30`,
  `WORKER_LANE_STARTUP_GRACE_SECONDS=120`.
