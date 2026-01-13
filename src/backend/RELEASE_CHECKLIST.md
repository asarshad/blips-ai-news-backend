# Release Checklist

Use this checklist before each release to production.

## Pre-Release

### Code Quality
- [ ] All tests pass locally
- [ ] No linting errors (`ruff check .`)
- [ ] Code reviewed and approved
- [ ] Feature branch merged to `develop`

### Testing in Dev
- [ ] Deployed to dev environment
- [ ] Manual smoke test passed
- [ ] API endpoints respond correctly
- [ ] Background jobs running
- [ ] No errors in logs

### Database
- [ ] Migrations are additive only (no drops)
- [ ] Migrations tested in dev
- [ ] Backward compatible with current code
- [ ] No breaking schema changes

### Documentation
- [ ] README updated if needed
- [ ] API changes documented
- [ ] CHANGELOG updated

## Release

### Deploy to Production
- [ ] Create PR from `develop` to `main`
- [ ] PR reviewed and approved
- [ ] Merge PR (triggers auto-deploy)
- [ ] Monitor Render deploy logs

### Verify
- [ ] Health endpoint returns 200
- [ ] API endpoints respond correctly
- [ ] Background jobs running
- [ ] Mobile app connects successfully
- [ ] No errors in logs

## Post-Release

### Monitor (first 30 min)
- [ ] Error rate normal
- [ ] Response times normal
- [ ] Memory/CPU usage normal
- [ ] No user-reported issues

### Document
- [ ] Tag release in git (if major)
- [ ] Update CHANGELOG
- [ ] Notify team of successful release

## Rollback Triggers

Initiate rollback if:
- Error rate > 5%
- Response time > 2s (p95)
- Any data corruption
- Critical functionality broken

## Rollback Steps

1. Go to Render Dashboard → Service → Events
2. Find last working deploy
3. Click "Rollback to this deploy"
4. If DB migration: `alembic downgrade -1`
5. Verify rollback successful
6. Investigate and fix issue
7. Re-release when ready

## Hotfix Process

For critical production issues:

1. Create branch from `main`: `hotfix/issue-name`
2. Fix the issue
3. Test locally
4. PR directly to `main` (bypass develop)
5. After merge, cherry-pick to `develop`
