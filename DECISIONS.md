# Architecture Decisions

This document locks in the technology decisions for the Blips backend. Do not deviate without team discussion.

## Locked Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Cloud Provider | Render | Simple deployment, good free tier, managed services |
| Backend Framework | FastAPI | Async Python, automatic OpenAPI docs, type safety |
| Database | Managed Postgres (Render) | Reliable, familiar, managed backups |
| Cache | Managed Redis (Render) | Session storage, job queues, caching |
| LLM Provider | Mistral Small | Cost-effective, good quality for summarization |
| Environments | dev, prod | Simple two-environment model |
| Background Jobs | Separate worker service | Isolate scheduler from web traffic |
| Migration Strategy | Additive only | Never drop columns/tables in production |

## Rules

1. **No new providers** without documented justification
2. **No breaking migrations** - always additive
3. **Secrets in env vars only** - never in code
4. **Feature flags** for risky changes
