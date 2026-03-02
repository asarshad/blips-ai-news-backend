"""
Minimal admin UI — server-rendered HTML.

Serves a lightweight editorial dashboard with:
- Content list with filters
- URL submit form
- Boost dropdown + suppress toggle
- Detail page with audit trail

Uses inline styles (Pico CSS CDN) to avoid a build step.
"""

import secrets
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Form, Header, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_db
from app.domain.editorial.service import EditorialService
from app.repositories.editorial_repo import EditorialRepository


def _require_admin_key_or_query(
    key: Optional[str] = Query(None),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
) -> str:
    """Accept admin key from query param (?key=) or X-Admin-Key header.

    This is used only for the browser-rendered UI pages where the
    browser cannot send custom headers on regular navigations.
    """
    configured_key = settings.ADMIN_API_KEY
    if not configured_key:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Admin endpoints disabled")

    provided = x_admin_key or key
    if not provided:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Missing admin key (pass ?key= or X-Admin-Key header)")

    if not secrets.compare_digest(provided, configured_key):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Invalid admin key")

    return provided


router = APIRouter(
    prefix="/admin/ui",
    tags=["admin-ui"],
)

ACTOR = "admin"

# ---------------------------------------------------------------------------
# Helper: tiny Jinja-like template (no dependency needed)
# ---------------------------------------------------------------------------

def _base_head(key: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Blips Admin</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
<style>
  body{{padding:1rem 2rem}}table{{font-size:.85rem}}
  .badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.75rem}}
  .badge-suppressed{{background:#dc3545;color:#fff}}
  .badge-manual{{background:#0d6efd;color:#fff}}
  .badge-boost{{background:#ffc107;color:#000}}
  .flash{{padding:10px;margin-bottom:12px;border-radius:4px;background:#d4edda;color:#155724}}
  .flash-error{{background:#f8d7da;color:#721c24}}
  form.inline{{display:inline}}
</style>
</head><body>
<nav><ul><li><strong>Blips Admin</strong></li></ul>
<ul><li><a href="/api/v1/admin/ui/?key={key}">Content</a></li>
    <li><a href="/api/v1/admin/ui/submit?key={key}">Submit URL</a></li></ul></nav>
"""

_BASE_FOOT = "</body></html>"


def _page(body: str, key: str = "") -> HTMLResponse:
    return HTMLResponse(_base_head(key) + body + _BASE_FOOT)


# ---------------------------------------------------------------------------
# GET /admin/ui/  — Content list
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def ui_content_list(
    day: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    suppressed: Optional[str] = Query(None),
    manual_added: Optional[str] = Query(None),
    sort_by: str = Query("published_at"),
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)

    parsed_day = None
    if day:
        try:
            parsed_day = date.fromisoformat(day)
        except ValueError:
            pass

    supp = None
    if suppressed == "true":
        supp = True
    elif suppressed == "false":
        supp = False

    manual = None
    if manual_added == "true":
        manual = True
    elif manual_added == "false":
        manual = False

    items, total = repo.list_content(
        day=parsed_day, content_type=type, source=source,
        suppressed=supp, manual_added=manual,
        sort_by=sort_by, page=page, page_size=50,
    )

    import math
    pages = max(1, math.ceil(total / 50))

    rows = ""
    for i in items:
        badges = ""
        if i.is_suppressed:
            badges += '<span class="badge badge-suppressed">suppressed</span> '
        if i.manual_added:
            badges += '<span class="badge badge-manual">manual</span> '
        if (i.editorial_boost or 0) > 0:
            badges += f'<span class="badge badge-boost">boost {i.editorial_boost}</span> '

        pub = i.published_at.strftime("%Y-%m-%d %H:%M") if i.published_at else "—"
        rows += f"""<tr>
            <td><a href="/api/v1/admin/ui/detail/{i.id}?key={admin_key}">{i.id}</a></td>
            <td>{_esc(i.title[:80])}</td>
            <td>{i.type.value if i.type else ''}</td>
            <td>{_esc(i.source or '')}</td>
            <td>{pub}</td>
            <td>{badges}</td>
            <td>{round(i.global_score or 0, 3)}</td>
        </tr>"""

    # Build filter form
    filter_form = f"""
    <details open><summary>Filters</summary>
    <form method="get" action="/api/v1/admin/ui/">
      <input type="hidden" name="key" value="{admin_key}">
      <div class="grid">
        <label>Day <input type="date" name="day" value="{day or ''}"></label>
        <label>Type <select name="type"><option value="">All</option>
          <option {"selected" if type=="ARTICLE" else ""} value="ARTICLE">Article</option>
          <option {"selected" if type=="VIDEO" else ""} value="VIDEO">Video</option>
          <option {"selected" if type=="REEL" else ""} value="REEL">Reel</option>
        </select></label>
        <label>Source <input type="text" name="source" value="{source or ''}"></label>
      </div>
      <div class="grid">
        <label>Suppressed <select name="suppressed"><option value="">All</option>
          <option {"selected" if suppressed=="true" else ""} value="true">Yes</option>
          <option {"selected" if suppressed=="false" else ""} value="false">No</option>
        </select></label>
        <label>Manual <select name="manual_added"><option value="">All</option>
          <option {"selected" if manual_added=="true" else ""} value="true">Yes</option>
          <option {"selected" if manual_added=="false" else ""} value="false">No</option>
        </select></label>
        <label>Sort <select name="sort_by">
          <option {"selected" if sort_by=="published_at" else ""} value="published_at">Published</option>
          <option {"selected" if sort_by=="created_at" else ""} value="created_at">Created</option>
          <option {"selected" if sort_by=="editorial_boost" else ""} value="editorial_boost">Boost</option>
        </select></label>
      </div>
      <button type="submit">Apply</button>
    </form></details>"""

    pagination = f"<p>Page {page}/{pages} — {total} items</p>"
    if page > 1:
        pagination += f'<a href="?key={admin_key}&page={page-1}">← Prev</a> '
    if page < pages:
        pagination += f'<a href="?key={admin_key}&page={page+1}">Next →</a>'

    body = f"""<h2>Content</h2>
    {filter_form}
    {pagination}
    <table role="grid"><thead><tr>
        <th>ID</th><th>Title</th><th>Type</th><th>Source</th>
        <th>Published</th><th>Status</th><th>Score</th>
    </tr></thead><tbody>{rows}</tbody></table>
    {pagination}
    """
    return _page(body, key=admin_key)


# ---------------------------------------------------------------------------
# GET /admin/ui/detail/{id} — Detail + actions
# ---------------------------------------------------------------------------

@router.get("/detail/{content_id}", response_class=HTMLResponse)
def ui_content_detail(
    content_id: int,
    flash: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)
    item = repo.get_content_by_id(content_id)
    if not item:
        return _page("<h2>Not found</h2>")

    actions = repo.get_actions_for_content(content_id, limit=30)

    flash_html = ""
    if flash:
        flash_html = f'<div class="flash">{_esc(flash)}</div>'

    badges = ""
    if item.is_suppressed:
        badges += '<span class="badge badge-suppressed">suppressed</span> '
    if item.manual_added:
        badges += '<span class="badge badge-manual">manual</span> '

    pub = item.published_at.strftime("%Y-%m-%d %H:%M") if item.published_at else "—"

    suppress_btn = ""
    if item.is_suppressed:
        suppress_btn = f"""
        <form class="inline" method="post" action="/api/v1/admin/ui/action/{content_id}/unsuppress?key={admin_key}">
            <input type="hidden" name="key" value="{admin_key}">
            <button type="submit" class="secondary">Unsuppress</button>
        </form>"""
    else:
        suppress_btn = f"""
        <form class="inline" method="post" action="/api/v1/admin/ui/action/{content_id}/suppress?key={admin_key}">
            <input type="hidden" name="key" value="{admin_key}">
            <button type="submit" class="contrast">Suppress</button>
        </form>"""

    boost_options = ""
    for lvl in range(4):
        sel = "selected" if (item.editorial_boost or 0) == lvl else ""
        boost_options += f'<option {sel} value="{lvl}">{lvl}</option>'

    actions_rows = ""
    for a in actions:
        ts = a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else "—"
        actions_rows += f"""<tr>
            <td>{ts}</td><td>{a.action_type}</td><td>{a.actor}</td>
            <td><small>{a.old_value}</small></td><td><small>{a.new_value}</small></td>
        </tr>"""

    body = f"""
    {flash_html}
    <h2>{_esc(item.title[:100])}</h2>
    <p>{badges}</p>
    <table role="grid">
        <tr><td><strong>ID</strong></td><td>{item.id}</td></tr>
        <tr><td><strong>Type</strong></td><td>{item.type.value if item.type else ''}</td></tr>
        <tr><td><strong>Source</strong></td><td>{_esc(item.source or '')}</td></tr>
        <tr><td><strong>URL</strong></td><td><a href="{item.source_url}" target="_blank">{_esc(item.source_url[:80])}</a></td></tr>
        <tr><td><strong>Published</strong></td><td>{pub}</td></tr>
        <tr><td><strong>Quality</strong></td><td>{round(item.quality_score or 0, 3)}</td></tr>
        <tr><td><strong>Global Score</strong></td><td>{round(item.global_score or 0, 3)}</td></tr>
        <tr><td><strong>Boost</strong></td><td>{item.editorial_boost or 0}</td></tr>
        <tr><td><strong>AI Processed</strong></td><td>{'Yes' if item.ai_processed else 'No'}</td></tr>
        <tr><td><strong>Cluster</strong></td><td>{item.cluster_id or '—'}</td></tr>
    </table>

    <div class="grid">
        <div>
            <h4>Set Boost</h4>
            <form method="post" action="/api/v1/admin/ui/action/{content_id}/boost?key={admin_key}">
                <input type="hidden" name="key" value="{admin_key}">
                <select name="level">{boost_options}</select>
                <button type="submit">Update Boost</button>
            </form>
        </div>
        <div>
            <h4>Suppress / Unsuppress</h4>
            {suppress_btn}
        </div>
    </div>

    <h3>Audit Trail</h3>
    <table role="grid"><thead><tr>
        <th>Time</th><th>Action</th><th>Actor</th><th>Old</th><th>New</th>
    </tr></thead><tbody>{actions_rows}</tbody></table>

    <p><a href="/api/v1/admin/ui/?key={admin_key}">← Back to list</a></p>
    """
    return _page(body, key=admin_key)


# ---------------------------------------------------------------------------
# GET /admin/ui/submit — Submit URL form
# ---------------------------------------------------------------------------

@router.get("/submit", response_class=HTMLResponse)
def ui_submit_form(
    flash: Optional[str] = Query(None),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    flash_html = ""
    if flash:
        flash_html = f'<div class="flash">{_esc(flash)}</div>'

    body = f"""
    <h2>Submit URL</h2>
    {flash_html}
    <form method="post" action="/api/v1/admin/ui/submit?key={admin_key}">
        <input type="hidden" name="key" value="{admin_key}">
        <label>URL <input type="url" name="url" required placeholder="https://..."></label>
        <label>Importance Level
            <select name="importance_level">
                <option value="0">0 – Normal</option>
                <option value="1">1 – Slightly boosted</option>
                <option value="2">2 – Important</option>
                <option value="3">3 – Must-read</option>
            </select>
        </label>
        <button type="submit">Submit</button>
    </form>
    """
    return _page(body, key=admin_key)


# ---------------------------------------------------------------------------
# POST actions (form-based)
# ---------------------------------------------------------------------------

@router.post("/submit")
def ui_submit_url(
    url: str = Form(...),
    importance_level: int = Form(0),
    key: str = Form(""),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    svc = EditorialService(db)
    result = svc.submit_url(url=url, importance_level=importance_level, actor=ACTOR)
    msg = f"{result.status}: {result.message}"
    if result.content_id:
        return RedirectResponse(
            f"/api/v1/admin/ui/detail/{result.content_id}?key={admin_key}&flash={msg}",
            status_code=303,
        )
    return RedirectResponse(f"/api/v1/admin/ui/submit?key={admin_key}&flash={msg}", status_code=303)


@router.post("/action/{content_id}/boost")
def ui_boost(
    content_id: int,
    level: int = Form(0),
    key: str = Form(""),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)
    repo.set_boost(content_id, level, actor=ACTOR)
    return RedirectResponse(
        f"/api/v1/admin/ui/detail/{content_id}?key={admin_key}&flash=Boost+set+to+{level}",
        status_code=303,
    )


@router.post("/action/{content_id}/suppress")
def ui_suppress(
    content_id: int,
    key: str = Form(""),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)
    repo.suppress(content_id, actor=ACTOR)
    return RedirectResponse(
        f"/api/v1/admin/ui/detail/{content_id}?key={admin_key}&flash=Content+suppressed",
        status_code=303,
    )


@router.post("/action/{content_id}/unsuppress")
def ui_unsuppress(
    content_id: int,
    key: str = Form(""),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)
    repo.unsuppress(content_id, actor=ACTOR)
    return RedirectResponse(
        f"/api/v1/admin/ui/detail/{content_id}?key={admin_key}&flash=Content+unsuppressed",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    """Basic HTML escaping."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
