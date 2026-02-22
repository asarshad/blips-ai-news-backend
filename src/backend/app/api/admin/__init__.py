"""
Admin API module.

Isolated admin routes for editorial control, content management,
and audit logging. All routes require admin authentication.
"""

from fastapi import APIRouter, Depends

from app.core.auth import require_admin_key
from app.api.admin.routes import router as editorial_router
from app.api.admin.ui import router as ui_router

admin_router = APIRouter(
    prefix="/admin",
    tags=["admin-editorial"],
    dependencies=[Depends(require_admin_key)],
)

admin_router.include_router(editorial_router)

# UI is mounted separately with its own auth dependency (already on the sub-router).
# We include it on a sibling router so it doesn't double-apply the dependency.
admin_ui_router = ui_router
