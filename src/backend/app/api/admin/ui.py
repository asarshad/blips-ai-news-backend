"""
Admin UI — server-rendered HTML with Tailwind CSS.

Pages
-----
GET  /admin/ui/              → redirect to /admin/ui/dashboard
GET  /admin/ui/dashboard     → curation pipeline dashboard (daily view)
GET  /admin/ui/content       → content list with filters
GET  /admin/ui/detail/{id}   → item detail + actions + audit trail
GET  /admin/ui/submit        → manual URL submission form

POST /admin/ui/submit
POST /admin/ui/action/{id}/boost
POST /admin/ui/action/{id}/suppress
POST /admin/ui/action/{id}/unsuppress
POST /admin/ui/action/{id}/promote
POST /admin/ui/action/{id}/demote
"""

from __future__ import annotations

import math
import secrets
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, Header, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_db
from app.domain.editorial.service import EditorialService
from app.repositories.editorial_repo import EditorialRepository

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _require_admin_key_or_query(
    key: Optional[str] = Query(None),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
) -> str:
    configured_key = settings.ADMIN_API_KEY
    if not configured_key:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Admin endpoints disabled")
    provided = x_admin_key or key
    if not provided:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Missing admin key")
    if not secrets.compare_digest(provided, configured_key):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Invalid admin key")
    return provided


router = APIRouter(prefix="/admin/ui", tags=["admin-ui"])
ACTOR = "admin"

# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


def _nav(key: str, active: str = "") -> str:
    def _link(href: str, label: str, name: str) -> str:
        base = "px-3 py-2 rounded-md text-sm font-medium transition-colors"
        if active == name:
            cls = f"{base} bg-gray-900 text-white"
        else:
            cls = f"{base} text-gray-300 hover:bg-gray-700 hover:text-white"
        return f'<a href="{href}?key={key}" class="{cls}">{label}</a>'

    return f"""
    <nav class="bg-gray-800 shadow mb-8">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div class="flex items-center justify-between h-14">
          <div class="flex items-center gap-1">
            <span class="text-white font-bold text-lg mr-4">⚡ Blips Admin</span>
            {_link('/api/v1/admin/ui/dashboard', 'Dashboard', 'dashboard')}
            {_link('/api/v1/admin/ui/content', 'Content', 'content')}
            {_link('/api/v1/admin/ui/submit', 'Submit URL', 'submit')}
          </div>
        </div>
      </div>
    </nav>"""


def _base(body: str, key: str = "", active: str = "") -> HTMLResponse:
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Blips Admin</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen">
  {_nav(key, active)}
  <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pb-16">
    {body}
  </main>
</body>
</html>"""
    return HTMLResponse(html)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _badge(text: str, color: str) -> str:
    palettes = {
        "green":  "bg-green-100 text-green-800",
        "yellow": "bg-yellow-100 text-yellow-800",
        "red":    "bg-red-100 text-red-800",
        "blue":   "bg-blue-100 text-blue-800",
        "gray":   "bg-gray-100 text-gray-700",
        "purple": "bg-purple-100 text-purple-800",
    }
    cls = palettes.get(color, palettes["gray"])
    return f'<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium {cls}">{_esc(text)}</span>'


def _stat_card(label: str, value: str, sub: str = "", color: str = "blue") -> str:
    border = {
        "blue": "border-blue-500", "green": "border-green-500",
        "yellow": "border-yellow-500", "red": "border-red-500",
        "purple": "border-purple-500", "gray": "border-gray-400",
    }.get(color, "border-blue-500")
    return f"""
    <div class="bg-white rounded-lg shadow p-5 border-l-4 {border}">
      <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide">{label}</div>
      <div class="mt-1 text-3xl font-bold text-gray-900">{value}</div>
      {f'<div class="mt-1 text-xs text-gray-500">{sub}</div>' if sub else ''}
    </div>"""

# ---------------------------------------------------------------------------
# GET /admin/ui/ → redirect to dashboard
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def ui_root(admin_key: str = Depends(_require_admin_key_or_query)):
    return RedirectResponse(f"/api/v1/admin/ui/dashboard?key={admin_key}", status_code=302)


# ---------------------------------------------------------------------------
# GET /admin/ui/dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard", response_class=HTMLResponse)
def ui_dashboard(
    day: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    from sqlalchemy import func

    from app.models.content import ContentItem, ContentStatus
    from app.models.signal import SignalURL
    from app.services.inventory_service import get_pipeline_counts

    # ── Date selection ────────────────────────────────────────────────────
    try:
        selected_date = date.fromisoformat(day) if day else date.today()
    except ValueError:
        selected_date = date.today()

    day_str = selected_date.isoformat()
    day_start = datetime.combine(selected_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    # ── Pipeline counts (48h window) ──────────────────────────────────────
    pipeline = get_pipeline_counts(db)
    per_type = pipeline.get("per_type", {})

    def _pipe_count(type_key: str, status: str) -> int:
        return per_type.get(type_key, {}).get(status, {}).get("count", 0)

    art_cand = _pipe_count("articles", "candidate")
    art_prom = _pipe_count("articles", "promoted")
    vid_cand = _pipe_count("videos", "candidate")
    vid_prom = _pipe_count("videos", "promoted")
    ree_cand = _pipe_count("reels", "candidate")
    ree_prom = _pipe_count("reels", "promoted")

    def _pipeline_card(label: str, candidate: int, promoted: int) -> str:
        total = candidate + promoted
        pct = round(promoted / total * 100) if total else 0
        bar_color = "bg-green-500" if pct >= 50 else "bg-yellow-400"
        return f"""
        <div class="bg-white rounded-lg shadow p-5">
          <div class="flex justify-between items-center mb-3">
            <span class="font-semibold text-gray-700">{label}</span>
            <span class="text-xs text-gray-400">{total} total (48h)</span>
          </div>
          <div class="flex gap-4 mb-3">
            <div class="text-center">
              <div class="text-2xl font-bold text-yellow-600">{candidate}</div>
              <div class="text-xs text-gray-500">Candidate</div>
            </div>
            <div class="text-center">
              <div class="text-2xl font-bold text-green-600">{promoted}</div>
              <div class="text-xs text-gray-500">Promoted</div>
            </div>
            <div class="text-center ml-auto">
              <div class="text-2xl font-bold text-gray-700">{pct}%</div>
              <div class="text-xs text-gray-500">promotion rate</div>
            </div>
          </div>
          <div class="w-full bg-gray-200 rounded-full h-2">
            <div class="{bar_color} h-2 rounded-full" style="width:{pct}%"></div>
          </div>
        </div>"""

    # ── Items published on selected day ───────────────────────────────────
    day_rows = (
        db.query(
            ContentItem.type,
            ContentItem.curation_status,
            func.count(ContentItem.id).label("cnt"),
        )
        .filter(
            ContentItem.published_at >= day_start,
            ContentItem.published_at < day_end,
            ContentItem.is_suppressed.is_(False),
        )
        .group_by(ContentItem.type, ContentItem.curation_status)
        .all()
    )

    day_summary: dict = {}
    for row in day_rows:
        t = row.type.value if row.type else "UNKNOWN"
        s = row.curation_status.value if row.curation_status else "PROMOTED"
        day_summary.setdefault(t, {})
        day_summary[t][s] = row.cnt

    # Avg promotion score for promoted items today
    avg_score_row = (
        db.query(func.avg(ContentItem.promotion_score))
        .filter(
            ContentItem.published_at >= day_start,
            ContentItem.published_at < day_end,
            ContentItem.curation_status == ContentStatus.PROMOTED,
            ContentItem.promotion_score.isnot(None),
        )
        .scalar()
    )
    avg_score = round(float(avg_score_row), 3) if avg_score_row else "—"

    # Signal stats (24h)
    signal_seen = (
        db.query(func.count(SignalURL.id))
        .filter(SignalURL.first_seen_at >= datetime.utcnow() - timedelta(hours=24))
        .scalar() or 0
    )
    signal_by_status = (
        db.query(SignalURL.enqueue_status, func.count(SignalURL.id))
        .group_by(SignalURL.enqueue_status)
        .all()
    )
    sig_counts = {r[0].value: r[1] for r in signal_by_status}
    sig_ingested  = sig_counts.get("INGESTED", 0)
    sig_duplicate = sig_counts.get("DUPLICATE", 0)
    sig_rejected  = sig_counts.get("REJECTED", 0)
    sig_pending   = sig_counts.get("PENDING", 0)

    # Top sources for selected day (promoted)
    source_rows = (
        db.query(ContentItem.source, func.count(ContentItem.id).label("cnt"))
        .filter(
            ContentItem.published_at >= day_start,
            ContentItem.published_at < day_end,
            ContentItem.curation_status == ContentStatus.PROMOTED,
            ContentItem.is_suppressed.is_(False),
        )
        .group_by(ContentItem.source)
        .order_by(func.count(ContentItem.id).desc())
        .limit(8)
        .all()
    )
    top_sources_html = ""
    for r in source_rows:
        top_sources_html += f"""
        <div class="flex justify-between items-center py-1.5 border-b border-gray-100 last:border-0">
          <span class="text-sm text-gray-700 truncate">{_esc(r[0] or 'Unknown')}</span>
          <span class="ml-2 text-sm font-semibold text-gray-900">{r[1]}</span>
        </div>"""

    # Pending candidates with highest scores (need manual attention)
    pending_promo = (
        db.query(ContentItem)
        .filter(
            ContentItem.curation_status == ContentStatus.CANDIDATE,
            ContentItem.is_suppressed.is_(False),
            ContentItem.published_at >= datetime.utcnow() - timedelta(hours=48),
        )
        .order_by(ContentItem.promotion_score.desc().nullslast())
        .limit(5)
        .all()
    )
    pending_rows_html = ""
    for p in pending_promo:
        score = f"{p.promotion_score:.3f}" if p.promotion_score else "—"
        pending_rows_html += f"""
        <tr class="hover:bg-gray-50">
          <td class="px-3 py-2 text-sm">
            <a href="/api/v1/admin/ui/detail/{p.id}?key={admin_key}" class="text-blue-600 hover:underline">{p.id}</a>
          </td>
          <td class="px-3 py-2 text-sm text-gray-800 max-w-xs truncate">{_esc((p.title or '')[:70])}</td>
          <td class="px-3 py-2 text-sm">{p.type.value if p.type else ''}</td>
          <td class="px-3 py-2 text-sm font-mono">{score}</td>
          <td class="px-3 py-2 text-sm">{_esc(p.discovered_via or '—')}</td>
          <td class="px-3 py-2">
            <form method="post" action="/api/v1/admin/ui/action/{p.id}/promote?key={admin_key}">
              <button class="px-2 py-1 text-xs bg-green-600 text-white rounded hover:bg-green-700">Promote</button>
            </form>
          </td>
        </tr>"""

    if not pending_rows_html:
        pending_rows_html = '<tr><td colspan="6" class="px-3 py-4 text-sm text-gray-400 text-center">No pending candidates</td></tr>'

    # Day summary table
    day_table_rows = ""
    for t in ["ARTICLE", "VIDEO", "REEL"]:
        cand = day_summary.get(t, {}).get("CANDIDATE", 0)
        prom = day_summary.get(t, {}).get("PROMOTED", 0)
        total_t = cand + prom
        day_table_rows += f"""
        <tr class="border-b border-gray-100">
          <td class="px-3 py-2 text-sm font-medium text-gray-700">{t}</td>
          <td class="px-3 py-2 text-center">{_badge(str(cand), 'yellow') if cand else _badge('0', 'gray')}</td>
          <td class="px-3 py-2 text-center">{_badge(str(prom), 'green') if prom else _badge('0', 'gray')}</td>
          <td class="px-3 py-2 text-center text-sm text-gray-500">{total_t}</td>
        </tr>"""

    # Date nav
    prev_day = (selected_date - timedelta(days=1)).isoformat()
    next_day = (selected_date + timedelta(days=1)).isoformat()
    is_today = selected_date == date.today()

    body = f"""
    <div class="flex items-center gap-3 mb-6">
      <h1 class="text-2xl font-bold text-gray-900">Dashboard</h1>
      <div class="flex items-center gap-2 ml-4">
        <a href="?key={admin_key}&day={prev_day}" class="px-2 py-1 rounded bg-white shadow text-sm hover:bg-gray-50">←</a>
        <form method="get" class="flex items-center gap-2">
          <input type="hidden" name="key" value="{admin_key}">
          <input type="date" name="day" value="{day_str}"
                 class="rounded border border-gray-300 text-sm px-2 py-1 focus:outline-none focus:ring-2 focus:ring-blue-500"
                 onchange="this.form.submit()">
        </form>
        {f'<a href="?key={admin_key}&day={next_day}" class="px-2 py-1 rounded bg-white shadow text-sm hover:bg-gray-50">→</a>' if not is_today else ''}
        {_badge('Today', 'blue') if is_today else ''}
      </div>
    </div>

    <h2 class="text-lg font-semibold text-gray-700 mb-3">Pipeline (last 48 h)</h2>
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
      {_pipeline_card('Articles', art_cand, art_prom)}
      {_pipeline_card('Videos', vid_cand, vid_prom)}
      {_pipeline_card('Reels', ree_cand, ree_prom)}
    </div>

    <div class="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
      {_stat_card('Signal URLs seen (24h)', str(signal_seen), '', 'purple')}
      {_stat_card('Ingested → candidate', str(sig_ingested), '', 'blue')}
      {_stat_card('Duplicates skipped', str(sig_duplicate), '', 'gray')}
      {_stat_card('Avg promotion score', str(avg_score), f'promoted items on {day_str}', 'green')}
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
      <div class="bg-white rounded-lg shadow p-5">
        <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">
          Published on {day_str}
        </h3>
        <table class="w-full">
          <thead>
            <tr class="text-xs text-gray-500 uppercase">
              <th class="px-3 py-2 text-left">Type</th>
              <th class="px-3 py-2 text-center">Candidate</th>
              <th class="px-3 py-2 text-center">Promoted</th>
              <th class="px-3 py-2 text-center">Total</th>
            </tr>
          </thead>
          <tbody>{day_table_rows}</tbody>
        </table>
      </div>

      <div class="bg-white rounded-lg shadow p-5">
        <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">Signal queue (all time)</h3>
        <div class="space-y-2">
          <div class="flex justify-between"><span class="text-sm text-gray-600">Pending</span>{_badge(str(sig_pending), 'yellow')}</div>
          <div class="flex justify-between"><span class="text-sm text-gray-600">Ingested</span>{_badge(str(sig_ingested), 'green')}</div>
          <div class="flex justify-between"><span class="text-sm text-gray-600">Duplicate</span>{_badge(str(sig_duplicate), 'gray')}</div>
          <div class="flex justify-between"><span class="text-sm text-gray-600">Rejected</span>{_badge(str(sig_rejected), 'red')}</div>
        </div>
      </div>

      <div class="bg-white rounded-lg shadow p-5">
        <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">
          Top sources (promoted, {day_str})
        </h3>
        {top_sources_html or '<p class="text-sm text-gray-400">No data for this day</p>'}
      </div>
    </div>

    <div class="bg-white rounded-lg shadow p-5 mb-8">
      <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">
        Top candidates awaiting promotion (last 48 h)
      </h3>
      <div class="overflow-x-auto">
        <table class="min-w-full">
          <thead class="bg-gray-50">
            <tr class="text-xs text-gray-500 uppercase">
              <th class="px-3 py-2 text-left">ID</th>
              <th class="px-3 py-2 text-left">Title</th>
              <th class="px-3 py-2 text-left">Type</th>
              <th class="px-3 py-2 text-left">Score</th>
              <th class="px-3 py-2 text-left">Discovered via</th>
              <th class="px-3 py-2 text-left">Action</th>
            </tr>
          </thead>
          <tbody>{pending_rows_html}</tbody>
        </table>
      </div>
    </div>"""
    return _base(body, key=admin_key, active="dashboard")


# ---------------------------------------------------------------------------
# GET /admin/ui/content — Content list
# ---------------------------------------------------------------------------


@router.get("/content", response_class=HTMLResponse)
def ui_content_list(
    day: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    suppressed: Optional[str] = Query(None),
    manual_added: Optional[str] = Query(None),
    curation_status: Optional[str] = Query(None),
    sort_by: str = Query("published_at"),
    page: int = Query(1, ge=1),
    flash: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    from app.models.content import ContentStatus

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
        curation_status=curation_status or None,
        sort_by=sort_by, page=page, page_size=50,
    )

    pages = max(1, math.ceil(total / 50))

    rows_html = ""
    for i in items:
        cs = getattr(i, "curation_status", None)
        if i.is_suppressed:
            row_bg = "bg-red-50"
        elif cs == ContentStatus.CANDIDATE:
            row_bg = "bg-yellow-50"
        else:
            row_bg = "bg-white"

        if cs == ContentStatus.CANDIDATE:
            status_badge = _badge("CANDIDATE", "yellow")
        else:
            status_badge = _badge("PROMOTED", "green")

        badges = status_badge + " "
        if i.is_suppressed:
            badges += _badge("suppressed", "red") + " "
        if i.manual_added:
            badges += _badge("manual", "blue") + " "
        if (i.editorial_boost or 0) > 0:
            badges += _badge(f"boost {i.editorial_boost}", "purple") + " "

        pub = i.published_at.strftime("%m-%d %H:%M") if i.published_at else "—"
        score = f"{i.promotion_score:.3f}" if getattr(i, "promotion_score", None) else "—"
        disc = _esc(getattr(i, "discovered_via", None) or "—")
        hits = str(getattr(i, "signal_hits", 0) or 0)

        rows_html += f"""
        <tr class="{row_bg} hover:brightness-95 border-b border-gray-100">
          <td class="px-3 py-2 text-sm text-gray-500">{i.id}</td>
          <td class="px-3 py-2 text-sm max-w-xs">
            <a href="/api/v1/admin/ui/detail/{i.id}?key={admin_key}" class="text-blue-600 hover:underline font-medium">{_esc((i.title or '')[:65])}</a>
          </td>
          <td class="px-3 py-2 text-xs text-gray-600">{i.type.value if i.type else ''}</td>
          <td class="px-3 py-2 text-xs text-gray-600 truncate max-w-[90px]">{_esc(i.source or '')}</td>
          <td class="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">{pub}</td>
          <td class="px-3 py-2">{badges}</td>
          <td class="px-3 py-2 text-xs font-mono text-gray-600">{score}</td>
          <td class="px-3 py-2 text-xs text-gray-500">{disc}</td>
          <td class="px-3 py-2 text-xs text-gray-500">{hits}</td>
        </tr>"""

    def _sel(name: str, cur: str, opts: list) -> str:
        o = "".join(
            f'<option value="{v}" {"selected" if v == cur else ""}>{lbl}</option>'
            for v, lbl in opts
        )
        return f'<select name="{name}" class="w-full rounded border-gray-300 text-sm px-2 py-1">{o}</select>'

    filter_form = f"""
    <div class="bg-white rounded-lg shadow p-4 mb-4">
      <form method="get" action="/api/v1/admin/ui/content" class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3 items-end">
        <input type="hidden" name="key" value="{admin_key}">
        <div>
          <label class="block text-xs text-gray-500 mb-1">Day</label>
          <input type="date" name="day" value="{day or ''}" class="w-full rounded border-gray-300 text-sm px-2 py-1">
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Type</label>
          {_sel('type', type or '', [('', 'All'), ('ARTICLE', 'Article'), ('VIDEO', 'Video'), ('REEL', 'Reel')])}
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Curation</label>
          {_sel('curation_status', curation_status or '', [('', 'All'), ('PROMOTED', 'Promoted'), ('CANDIDATE', 'Candidate')])}
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Source</label>
          <input type="text" name="source" value="{source or ''}" placeholder="filter..." class="w-full rounded border-gray-300 text-sm px-2 py-1">
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Suppressed</label>
          {_sel('suppressed', suppressed or '', [('', 'All'), ('false', 'No'), ('true', 'Yes')])}
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Sort by</label>
          {_sel('sort_by', sort_by, [('published_at', 'Published'), ('created_at', 'Created'), ('editorial_boost', 'Boost')])}
        </div>
        <div>
          <button type="submit" class="w-full px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700">Filter</button>
        </div>
      </form>
    </div>"""

    def _page_link(p: int, label: str) -> str:
        return (
            f'<a href="?key={admin_key}&page={p}&day={day or ""}&type={type or ""}'
            f'&source={source or ""}&suppressed={suppressed or ""}'
            f'&curation_status={curation_status or ""}&sort_by={sort_by}"'
            f' class="px-3 py-1 rounded bg-white shadow text-sm hover:bg-gray-50">{label}</a>'
        )

    pagination = f'<span class="text-sm text-gray-600">Page {page}/{pages} — {total} items</span> '
    if page > 1:
        pagination += _page_link(page - 1, "← Prev") + " "
    if page < pages:
        pagination += _page_link(page + 1, "Next →")

    flash_html = ""
    if flash:
        flash_html = f'<div class="mb-4 p-3 bg-green-100 text-green-800 rounded text-sm">{_esc(flash)}</div>'

    body = f"""
    <h1 class="text-2xl font-bold text-gray-900 mb-4">Content</h1>
    {flash_html}
    {filter_form}
    <div class="flex justify-between items-center mb-2">{pagination}</div>
    <div class="bg-white shadow rounded-lg overflow-x-auto">
      <table class="min-w-full">
        <thead class="bg-gray-50">
          <tr class="text-xs font-medium text-gray-500 uppercase tracking-wide">
            <th class="px-3 py-3 text-left">ID</th>
            <th class="px-3 py-3 text-left">Title</th>
            <th class="px-3 py-3 text-left">Type</th>
            <th class="px-3 py-3 text-left">Source</th>
            <th class="px-3 py-3 text-left">Published</th>
            <th class="px-3 py-3 text-left">Status</th>
            <th class="px-3 py-3 text-left">Promo score</th>
            <th class="px-3 py-3 text-left">Discovered via</th>
            <th class="px-3 py-3 text-left">Hits</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
    <div class="mt-3">{pagination}</div>"""
    return _base(body, key=admin_key, active="content")


# ---------------------------------------------------------------------------
# GET /admin/ui/detail/{id}
# ---------------------------------------------------------------------------


@router.get("/detail/{content_id}", response_class=HTMLResponse)
def ui_content_detail(
    content_id: int,
    flash: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    from app.models.content import ContentStatus

    repo = EditorialRepository(db)
    item = repo.get_content_by_id(content_id)
    if not item:
        return _base("<h2 class='text-xl font-bold text-gray-800'>Not found</h2>", key=admin_key)

    actions = repo.get_actions_for_content(content_id, limit=30)

    flash_html = ""
    if flash:
        is_err = "error" in flash.lower()
        cls = "bg-red-100 text-red-800" if is_err else "bg-green-100 text-green-800"
        flash_html = f'<div class="mb-4 p-3 {cls} rounded text-sm">{_esc(flash)}</div>'

    cs = getattr(item, "curation_status", None)
    curation_badge = _badge("CANDIDATE", "yellow") if cs == ContentStatus.CANDIDATE else _badge("PROMOTED", "green")

    badges = curation_badge + " "
    if item.is_suppressed:
        badges += _badge("suppressed", "red") + " "
    if item.manual_added:
        badges += _badge("manual", "blue") + " "
    if (item.editorial_boost or 0) > 0:
        badges += _badge(f"boost {item.editorial_boost}", "purple") + " "

    pub = item.published_at.strftime("%Y-%m-%d %H:%M") if item.published_at else "—"
    created = item.created_at.strftime("%Y-%m-%d %H:%M") if item.created_at else "—"

    def _row(label: str, val: str) -> str:
        return f"""<tr class="border-b border-gray-100">
          <td class="px-4 py-2 text-sm font-medium text-gray-500 w-40">{label}</td>
          <td class="px-4 py-2 text-sm text-gray-900">{val}</td>
        </tr>"""

    if cs == ContentStatus.CANDIDATE:
        curation_action = f"""
        <form method="post" action="/api/v1/admin/ui/action/{content_id}/promote?key={admin_key}">
          <button class="w-full px-4 py-2 bg-green-600 text-white text-sm rounded hover:bg-green-700 font-medium">
            ↑ Promote to feed
          </button>
        </form>"""
    else:
        curation_action = f"""
        <form method="post" action="/api/v1/admin/ui/action/{content_id}/demote?key={admin_key}">
          <button class="w-full px-4 py-2 bg-yellow-500 text-white text-sm rounded hover:bg-yellow-600 font-medium">
            ↓ Move to Candidate
          </button>
        </form>"""

    if item.is_suppressed:
        suppress_btn = f"""
        <form method="post" action="/api/v1/admin/ui/action/{content_id}/unsuppress?key={admin_key}">
          <button class="w-full px-4 py-2 bg-gray-200 text-gray-800 text-sm rounded hover:bg-gray-300 font-medium">Unsuppress</button>
        </form>"""
    else:
        suppress_btn = f"""
        <form method="post" action="/api/v1/admin/ui/action/{content_id}/suppress?key={admin_key}">
          <button class="w-full px-4 py-2 bg-red-600 text-white text-sm rounded hover:bg-red-700 font-medium">Suppress</button>
        </form>"""

    boost_options = "".join(
        f'<option value="{lvl}" {"selected" if (item.editorial_boost or 0) == lvl else ""}>{lvl}</option>'
        for lvl in range(4)
    )

    actions_rows = ""
    for a in actions:
        ts = a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else "—"
        actions_rows += f"""<tr class="border-b border-gray-100 hover:bg-gray-50">
          <td class="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">{ts}</td>
          <td class="px-3 py-2">{_badge(a.action_type, 'blue')}</td>
          <td class="px-3 py-2 text-xs text-gray-600">{_esc(a.actor or '')}</td>
          <td class="px-3 py-2 text-xs text-gray-500 font-mono">{_esc(str(a.old_value or ''))}</td>
          <td class="px-3 py-2 text-xs text-gray-500 font-mono">{_esc(str(a.new_value or ''))}</td>
        </tr>"""

    body = f"""
    <div class="mb-4">
      <a href="/api/v1/admin/ui/content?key={admin_key}" class="text-sm text-blue-600 hover:underline">← Back to content</a>
    </div>
    {flash_html}
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div class="lg:col-span-2 bg-white rounded-lg shadow">
        <div class="p-5 border-b border-gray-100">
          <h1 class="text-xl font-bold text-gray-900 leading-snug">{_esc((item.title or '')[:120])}</h1>
          <div class="mt-2 flex flex-wrap gap-1">{badges}</div>
        </div>
        <table class="w-full">
          {_row('ID', str(item.id))}
          {_row('Type', item.type.value if item.type else '—')}
          {_row('Curation status', curation_badge)}
          {_row('Promotion score', f"{item.promotion_score:.4f}" if getattr(item, 'promotion_score', None) else '—')}
          {_row('Discovered via', _esc(getattr(item, 'discovered_via', None) or '—'))}
          {_row('Signal hits', str(getattr(item, 'signal_hits', 0) or 0))}
          {_row('Source', _esc(item.source or '—'))}
          {_row('URL', f'<a href="{item.source_url}" target="_blank" class="text-blue-600 hover:underline text-xs break-all">{_esc((item.source_url or "")[:90])}</a>')}
          {_row('Published', pub)}
          {_row('Created', created)}
          {_row('Global score', f"{item.global_score:.4f}" if item.global_score else '—')}
          {_row('Quality score', f"{item.quality_score:.4f}" if item.quality_score else '—')}
          {_row('Editorial boost', str(item.editorial_boost or 0))}
          {_row('AI processed', '✅ Yes' if item.ai_processed else '⏳ No')}
          {_row('Cluster', str(item.cluster_id) if item.cluster_id else '—')}
          {_row('Manual added', '✅ Yes' if item.manual_added else 'No')}
        </table>
      </div>

      <div class="space-y-4">
        <div class="bg-white rounded-lg shadow p-5">
          <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">Curation</h3>
          {curation_action}
        </div>
        <div class="bg-white rounded-lg shadow p-5">
          <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">Visibility</h3>
          {suppress_btn}
        </div>
        <div class="bg-white rounded-lg shadow p-5">
          <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">Editorial boost</h3>
          <form method="post" action="/api/v1/admin/ui/action/{content_id}/boost?key={admin_key}" class="flex gap-2">
            <select name="level" class="flex-1 rounded border-gray-300 text-sm px-2 py-1">{boost_options}</select>
            <button type="submit" class="px-3 py-1.5 bg-purple-600 text-white text-sm rounded hover:bg-purple-700">Set</button>
          </form>
        </div>
      </div>
    </div>

    <div class="mt-6 bg-white rounded-lg shadow">
      <div class="p-4 border-b border-gray-100">
        <h3 class="font-semibold text-gray-700">Audit trail</h3>
      </div>
      <div class="overflow-x-auto">
        <table class="min-w-full">
          <thead class="bg-gray-50 text-xs font-medium text-gray-500 uppercase">
            <tr>
              <th class="px-3 py-2 text-left">Time</th>
              <th class="px-3 py-2 text-left">Action</th>
              <th class="px-3 py-2 text-left">Actor</th>
              <th class="px-3 py-2 text-left">Old</th>
              <th class="px-3 py-2 text-left">New</th>
            </tr>
          </thead>
          <tbody>{actions_rows or '<tr><td colspan="5" class="px-3 py-4 text-sm text-gray-400 text-center">No actions yet</td></tr>'}</tbody>
        </table>
      </div>
    </div>"""
    return _base(body, key=admin_key, active="content")


# ---------------------------------------------------------------------------
# GET /admin/ui/submit
# ---------------------------------------------------------------------------


@router.get("/submit", response_class=HTMLResponse)
def ui_submit_form(
    flash: Optional[str] = Query(None),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    flash_html = ""
    if flash:
        is_err = "error" in flash.lower() or "failed" in flash.lower()
        cls = "bg-red-100 text-red-800" if is_err else "bg-green-100 text-green-800"
        flash_html = f'<div class="mb-4 p-3 {cls} rounded text-sm">{_esc(flash)}</div>'

    body = f"""
    <div class="max-w-lg">
      <h1 class="text-2xl font-bold text-gray-900 mb-6">Submit URL</h1>
      {flash_html}
      <div class="bg-white rounded-lg shadow p-6">
        <form method="post" action="/api/v1/admin/ui/submit?key={admin_key}" class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">URL</label>
            <input type="url" name="url" required placeholder="https://..."
                   class="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">Importance level</label>
            <select name="importance_level" class="w-full rounded border border-gray-300 px-3 py-2 text-sm">
              <option value="0">0 — Normal</option>
              <option value="1">1 — Slightly boosted</option>
              <option value="2">2 — Important</option>
              <option value="3">3 — Must-read</option>
            </select>
          </div>
          <button type="submit"
                  class="w-full px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded hover:bg-blue-700">
            Submit
          </button>
        </form>
      </div>
    </div>"""
    return _base(body, key=admin_key, active="submit")


# ---------------------------------------------------------------------------
# POST actions
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


@router.post("/action/{content_id}/promote")
def ui_promote(
    content_id: int,
    key: str = Form(""),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)
    repo.promote(content_id, actor=ACTOR)
    return RedirectResponse(
        f"/api/v1/admin/ui/detail/{content_id}?key={admin_key}&flash=Promoted+to+feed",
        status_code=303,
    )


@router.post("/action/{content_id}/demote")
def ui_demote(
    content_id: int,
    key: str = Form(""),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)
    repo.demote(content_id, actor=ACTOR)
    return RedirectResponse(
        f"/api/v1/admin/ui/detail/{content_id}?key={admin_key}&flash=Moved+to+candidate",
        status_code=303,
    )
