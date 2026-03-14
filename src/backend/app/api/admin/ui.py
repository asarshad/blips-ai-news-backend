"""
Admin UI — server-rendered HTML with Tailwind CSS.

Pages
-----
GET  /admin/ui/              → redirect to /admin/ui/dashboard
GET  /admin/ui/dashboard     → curation pipeline dashboard (daily view)
GET  /admin/ui/review        → reviewer queue (candidate triage + decisions)
GET  /admin/ui/content       → content list with filters
GET  /admin/ui/detail/{id}   → item detail + actions + audit trail
GET  /admin/ui/submit        → manual URL submission form

POST /admin/ui/submit
POST /admin/ui/review/bulk-action
POST /admin/ui/action/{id}/boost
POST /admin/ui/action/{id}/suppress
POST /admin/ui/action/{id}/unsuppress
POST /admin/ui/action/{id}/promote
POST /admin/ui/action/{id}/demote
POST /admin/ui/action/{id}/approve
POST /admin/ui/action/{id}/reject
POST /admin/ui/action/{id}/hold
POST /admin/ui/action/{id}/request-changes
POST /admin/ui/action/{id}/approve-publish
POST /admin/ui/action/{id}/note
"""

from __future__ import annotations

import math
import secrets
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse

from fastapi import APIRouter, Depends, Form, Header, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_db
from app.domain.editorial.service import EditorialService
from app.repositories.editorial_repo import EditorialRepository
from app.services.tiered_feed_service import invalidate_tiered_feed_cache

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
            {_link("/api/v1/admin/ui/dashboard", "Dashboard", "dashboard")}
            {_link("/api/v1/admin/ui/video-lanes", "Video Lanes", "video-lanes")}
            {_link("/api/v1/admin/ui/video-sources", "Video Sources", "video-sources")}
            {_link("/api/v1/admin/ui/review", "Review Queue", "review")}
            {_link("/api/v1/admin/ui/content", "Content", "content")}
            {_link("/api/v1/admin/ui/submit", "Submit URL", "submit")}
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


def _add_flash(url: str, flash: str) -> str:
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params["flash"] = flash
    query = urlencode(params)
    return f"{parsed.path}?{query}"


def _resolve_ui_url(
    *,
    admin_key: str,
    fallback_path: str,
    next_url: Optional[str] = None,
    referer: Optional[str] = None,
) -> str:
    allowed_prefix = "/api/v1/admin/ui/"
    for candidate in (next_url, referer):
        if not candidate:
            continue
        parsed = urlparse(candidate)
        path = parsed.path or ""
        if not path.startswith(allowed_prefix):
            continue
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params["key"] = admin_key
        query = urlencode(params)
        return f"{path}?{query}"
    return f"{fallback_path}?key={admin_key}"


def _resolve_next_ui_url(
    *,
    content_id: int,
    admin_key: str,
    next_url: Optional[str] = None,
    referer: Optional[str] = None,
) -> str:
    return _resolve_ui_url(
        admin_key=admin_key,
        fallback_path=f"/api/v1/admin/ui/detail/{content_id}",
        next_url=next_url,
        referer=referer,
    )


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _badge(text: str, color: str) -> str:
    palettes = {
        "green": "bg-green-100 text-green-800",
        "yellow": "bg-yellow-100 text-yellow-800",
        "red": "bg-red-100 text-red-800",
        "blue": "bg-blue-100 text-blue-800",
        "gray": "bg-gray-100 text-gray-700",
        "purple": "bg-purple-100 text-purple-800",
    }
    cls = palettes.get(color, palettes["gray"])
    return f'<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium {cls}">{_esc(text)}</span>'


def _stat_card(label: str, value: str, sub: str = "", color: str = "blue") -> str:
    border = {
        "blue": "border-blue-500",
        "green": "border-green-500",
        "yellow": "border-yellow-500",
        "red": "border-red-500",
        "purple": "border-purple-500",
        "gray": "border-gray-400",
    }.get(color, "border-blue-500")
    return f"""
    <div class="bg-white rounded-lg shadow p-5 border-l-4 {border}">
      <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide">{label}</div>
      <div class="mt-1 text-3xl font-bold text-gray-900">{value}</div>
      {f'<div class="mt-1 text-xs text-gray-500">{sub}</div>' if sub else ""}
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
    from sqlalchemy import and_, func, or_

    from app.models.content import ContentItem, ContentStatus
    from app.models.signal import SignalURL
    from app.services.inventory_service import get_pipeline_counts
    from app.services.video_metrics_service import (
        compute_video_lane_metrics,
        compute_video_supply_metrics,
    )

    # ── Date selection ────────────────────────────────────────────────────
    try:
        selected_date = date.fromisoformat(day) if day else date.today()
    except ValueError:
        selected_date = date.today()

    day_str = selected_date.isoformat()
    day_start = datetime.combine(selected_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    video_supply = compute_video_supply_metrics(db)
    video_lanes = compute_video_lane_metrics(db, hours=24)

    def _fmt_delta(value: Optional[float]) -> str:
        if value is None:
            return "—"
        prefix = "+" if value > 0 else ""
        return f"{prefix}{value}"

    def _video_kpi_card(surface: str, label: str) -> str:
        metrics = video_supply["surfaces"][surface]
        fresh_delta = metrics["deltas"]["fresh_inventory_24h"]
        dominance_delta = metrics["deltas"]["dominant_channel_pct_top20"]
        window_label = metrics["inventory_window_label"]
        return _stat_card(
            label,
            str(metrics["fresh_inventory_window"]),
            (
                f"rolling window {window_label} | "
                f"median age {metrics['median_age_top20_hours'] or '—'}h | "
                f"distinct channels {metrics['distinct_active_channels_window']} | "
                f"baseline {video_supply.get('baseline_tag') or '—'} {_fmt_delta(fresh_delta['baseline'])} | "
                f"24h {_fmt_delta(fresh_delta['vs_24h'])} | "
                f"7d {_fmt_delta(fresh_delta['vs_7d'])} | "
                f"dominance {metrics['dominant_channel_pct_top20']}% ({_fmt_delta(dominance_delta['vs_24h'])})"
            ),
            "purple" if surface == "reels" else "blue",
        )

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

    # ── Day bucket (ingestion-aware, with legacy fallback) ───────────────
    day_bucket_filter = or_(
        ContentItem.ingestion_day == selected_date,
        and_(
            ContentItem.ingestion_day.is_(None),
            ContentItem.published_at >= day_start,
            ContentItem.published_at < day_end,
        ),
    )

    # ── Items for selected day ────────────────────────────────────────────
    day_rows = (
        db.query(
            ContentItem.type,
            ContentItem.curation_status,
            func.count(ContentItem.id).label("cnt"),
        )
        .filter(
            day_bucket_filter,
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
            day_bucket_filter,
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
        .scalar()
        or 0
    )
    signal_by_status = (
        db.query(SignalURL.enqueue_status, func.count(SignalURL.id))
        .group_by(SignalURL.enqueue_status)
        .all()
    )
    sig_counts = {r[0].value: r[1] for r in signal_by_status}
    sig_ingested = sig_counts.get("INGESTED", 0)
    sig_duplicate = sig_counts.get("DUPLICATE", 0)
    sig_rejected = sig_counts.get("REJECTED", 0)
    sig_pending = sig_counts.get("PENDING", 0)

    # Top sources for selected day (promoted)
    source_rows = (
        db.query(ContentItem.source, func.count(ContentItem.id).label("cnt"))
        .filter(
            day_bucket_filter,
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
          <span class="text-sm text-gray-700 truncate">{_esc(r[0] or "Unknown")}</span>
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
    dashboard_next = f"/api/v1/admin/ui/dashboard?{urlencode({'day': day_str})}"
    pending_rows_html = ""
    for p in pending_promo:
        score = f"{p.promotion_score:.3f}" if p.promotion_score else "—"
        pending_rows_html += f"""
        <tr class="hover:bg-gray-50">
          <td class="px-3 py-2 text-sm">
            <a href="/api/v1/admin/ui/detail/{p.id}?key={admin_key}" class="text-blue-600 hover:underline">{p.id}</a>
          </td>
          <td class="px-3 py-2 text-sm text-gray-800 max-w-xs truncate">{_esc((p.title or "")[:70])}</td>
          <td class="px-3 py-2 text-sm">{p.type.value if p.type else ""}</td>
          <td class="px-3 py-2 text-sm font-mono">{score}</td>
          <td class="px-3 py-2 text-sm">{_esc(p.discovered_via or "—")}</td>
          <td class="px-3 py-2">
            <div class="flex gap-1">
              <form method="post" action="/api/v1/admin/ui/action/{p.id}/approve-publish?key={admin_key}">
                <input type="hidden" name="next" value="{dashboard_next}">
                <input type="hidden" name="boost_level" value="3">
                <button class="px-2 py-1 text-xs bg-green-600 text-white rounded hover:bg-green-700">Publish top</button>
              </form>
              <a href="/api/v1/admin/ui/detail/{p.id}?key={admin_key}"
                 class="px-2 py-1 text-xs bg-white border border-gray-300 text-gray-700 rounded hover:bg-gray-50">
                Review
              </a>
            </div>
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
          <td class="px-3 py-2 text-center">{_badge(str(cand), "yellow") if cand else _badge("0", "gray")}</td>
          <td class="px-3 py-2 text-center">{_badge(str(prom), "green") if prom else _badge("0", "gray")}</td>
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
        <a href="?key={admin_key}&day={
        prev_day
    }" class="px-2 py-1 rounded bg-white shadow text-sm hover:bg-gray-50">←</a>
        <form method="get" class="flex items-center gap-2">
          <input type="hidden" name="key" value="{admin_key}">
          <input type="date" name="day" value="{day_str}"
                 class="rounded border border-gray-300 text-sm px-2 py-1 focus:outline-none focus:ring-2 focus:ring-blue-500"
                 onchange="this.form.submit()">
        </form>
        {
        f'<a href="?key={admin_key}&day={next_day}" class="px-2 py-1 rounded bg-white shadow text-sm hover:bg-gray-50">→</a>'
        if not is_today
        else ""
    }
        {_badge("Today", "blue") if is_today else ""}
      </div>
    </div>

    <h2 class="text-lg font-semibold text-gray-700 mb-3">Pipeline (last 48 h)</h2>
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-8">
      {_pipeline_card("Articles", art_cand, art_prom)}
      {_pipeline_card("Videos", vid_cand, vid_prom)}
      {_pipeline_card("Reels", ree_cand, ree_prom)}
    </div>

    <div class="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
      {_stat_card("Signal URLs seen (24h)", str(signal_seen), "", "purple")}
      {_stat_card("Ingested → candidate", str(sig_ingested), "", "blue")}
      {_stat_card("Duplicates skipped", str(sig_duplicate), "", "gray")}
      {_stat_card("Avg promotion score", str(avg_score), f"promoted items on {day_str}", "green")}
    </div>

    <h2 class="text-lg font-semibold text-gray-700 mb-3">Video And Reels Health</h2>
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
      {_video_kpi_card("videos", "Videos rolling inventory")}
      {_video_kpi_card("reels", "Reels rolling inventory")}
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
      <div class="bg-white rounded-lg shadow p-5">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide">Supply gates</h3>
          <a href="/api/v1/admin/ui/video-lanes?key={
        admin_key
    }" class="text-sm text-blue-600 hover:underline">Lane performance →</a>
        </div>
        <div class="space-y-2 text-sm text-gray-700">
          <div class="flex justify-between"><span>Videos rolling inventory</span><span class="font-semibold">{
        video_supply["surfaces"]["videos"]["fresh_inventory_window"]
    } / {video_supply["surfaces"]["videos"]["floor_target"]} ({
        video_supply["surfaces"]["videos"]["inventory_window_label"]
    })</span></div>
          <div class="flex justify-between"><span>Reels rolling inventory</span><span class="font-semibold">{
        video_supply["surfaces"]["reels"]["fresh_inventory_window"]
    } / {video_supply["surfaces"]["reels"]["floor_target"]} ({
        video_supply["surfaces"]["reels"]["inventory_window_label"]
    })</span></div>
          <div class="flex justify-between"><span>Videos median age</span><span class="font-semibold">{
        video_supply["surfaces"]["videos"]["median_age_top20_hours"] or "—"
    }h</span></div>
          <div class="flex justify-between"><span>Reels median age</span><span class="font-semibold">{
        video_supply["surfaces"]["reels"]["median_age_top20_hours"] or "—"
    }h</span></div>
          <div class="flex justify-between"><span>Videos dominant channel</span><span class="font-semibold">{
        video_supply["surfaces"]["videos"]["dominant_channel_pct_top20"]
    }%</span></div>
          <div class="flex justify-between"><span>Reels dominant channel</span><span class="font-semibold">{
        video_supply["surfaces"]["reels"]["dominant_channel_pct_top20"]
    }%</span></div>
        </div>
      </div>
      <div class="bg-white rounded-lg shadow p-5">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide">Lane snapshot (24h)</h3>
          <a href="/api/v1/admin/ui/video-sources?key={
        admin_key
    }" class="text-sm text-blue-600 hover:underline">Channel health →</a>
        </div>
        <div class="space-y-2 text-sm text-gray-700">
          {
        "".join(
            f'<div class="flex justify-between"><span>{surface.title()} {lane["lane"]}</span><span class="font-semibold">{lane["promoted"]}/{lane["candidates"]} ({lane["promotion_rate"]}%)</span></div>'
            for surface, lanes in video_lanes["surfaces"].items()
            for lane in lanes[:2]
        )
        or '<p class="text-sm text-gray-400">No discovery runs captured yet.</p>'
    }
        </div>
      </div>
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
          <div class="flex justify-between"><span class="text-sm text-gray-600">Pending</span>{
        _badge(str(sig_pending), "yellow")
    }</div>
          <div class="flex justify-between"><span class="text-sm text-gray-600">Ingested</span>{
        _badge(str(sig_ingested), "green")
    }</div>
          <div class="flex justify-between"><span class="text-sm text-gray-600">Duplicate</span>{
        _badge(str(sig_duplicate), "gray")
    }</div>
          <div class="flex justify-between"><span class="text-sm text-gray-600">Rejected</span>{
        _badge(str(sig_rejected), "red")
    }</div>
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
        Top candidates awaiting editorial decision (last 48 h)
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
# GET /admin/ui/video-lanes
# ---------------------------------------------------------------------------


@router.get("/video-lanes", response_class=HTMLResponse)
def ui_video_lanes(
    hours: int = Query(24, ge=1, le=24 * 14),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    from app.services.video_metrics_service import compute_video_lane_metrics

    payload = compute_video_lane_metrics(db, hours=hours)

    def _rows(surface: str) -> str:
        rows = payload["surfaces"].get(surface, [])
        if not rows:
            return '<tr><td colspan="7" class="px-3 py-4 text-sm text-gray-400 text-center">No data</td></tr>'
        return "".join(
            f"""
            <tr class="border-b border-gray-100">
              <td class="px-3 py-2 text-sm font-medium text-gray-800">{_esc(row["lane"])}</td>
              <td class="px-3 py-2 text-sm text-right">{row["candidates"]}</td>
              <td class="px-3 py-2 text-sm text-right">{row["promoted"]}</td>
              <td class="px-3 py-2 text-sm text-right">{row["promotion_rate"]}%</td>
              <td class="px-3 py-2 text-sm text-right">{row["median_promoted_age"] or "—"}h</td>
              <td class="px-3 py-2 text-sm text-right">{row["distinct_promoted_channels"]}</td>
              <td class="px-3 py-2 text-sm text-right">{row["duplicate_rejection_rate"]}% / {row["clickbait_rejection_rate"]}%</td>
            </tr>"""
            for row in rows
        )

    body = f"""
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-gray-900">Video Lanes</h1>
      <form method="get" class="flex items-center gap-2">
        <input type="hidden" name="key" value="{admin_key}">
        <label class="text-sm text-gray-600">Window (hours)</label>
        <input type="number" min="1" max="{24 * 14}" name="hours" value="{hours}" class="w-24 rounded border border-gray-300 px-2 py-1 text-sm">
        <button class="px-3 py-1.5 rounded bg-gray-900 text-white text-sm">Apply</button>
      </form>
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div class="bg-white rounded-lg shadow p-5">
        <h2 class="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">Videos</h2>
        <table class="min-w-full">
          <thead class="bg-gray-50">
            <tr class="text-xs text-gray-500 uppercase">
              <th class="px-3 py-2 text-left">Lane</th>
              <th class="px-3 py-2 text-right">Candidates</th>
              <th class="px-3 py-2 text-right">Promoted</th>
              <th class="px-3 py-2 text-right">Rate</th>
              <th class="px-3 py-2 text-right">Median age</th>
              <th class="px-3 py-2 text-right">Channels</th>
              <th class="px-3 py-2 text-right">Dup / Clickbait</th>
            </tr>
          </thead>
          <tbody>{_rows("videos")}</tbody>
        </table>
      </div>
      <div class="bg-white rounded-lg shadow p-5">
        <h2 class="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">Reels</h2>
        <table class="min-w-full">
          <thead class="bg-gray-50">
            <tr class="text-xs text-gray-500 uppercase">
              <th class="px-3 py-2 text-left">Lane</th>
              <th class="px-3 py-2 text-right">Candidates</th>
              <th class="px-3 py-2 text-right">Promoted</th>
              <th class="px-3 py-2 text-right">Rate</th>
              <th class="px-3 py-2 text-right">Median age</th>
              <th class="px-3 py-2 text-right">Channels</th>
              <th class="px-3 py-2 text-right">Dup / Clickbait</th>
            </tr>
          </thead>
          <tbody>{_rows("reels")}</tbody>
        </table>
      </div>
    </div>"""
    return _base(body, key=admin_key, active="video-lanes")


# ---------------------------------------------------------------------------
# GET /admin/ui/video-sources
# ---------------------------------------------------------------------------


@router.get("/video-sources", response_class=HTMLResponse)
def ui_video_sources(
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    from app.services.video_metrics_service import compute_video_source_metrics

    payload = compute_video_source_metrics(db)
    rows = payload["sources"]
    body_rows = (
        "".join(
            f"""
        <tr class="border-b border-gray-100">
          <td class="px-3 py-2 text-sm">
            <div class="font-medium text-gray-900">{_esc(row["channel_name"])}</div>
            <div class="text-xs text-gray-500 font-mono">{_esc(row["channel_id"])}</div>
          </td>
          <td class="px-3 py-2 text-sm">{_badge(row["status"], "green" if row["status"] == "core" else "blue" if row["status"] == "rotation" else "yellow" if row["status"] == "discovery" else "red")}</td>
          <td class="px-3 py-2 text-sm">{_esc(row["role"])}</td>
          <td class="px-3 py-2 text-sm text-right">{row["score_7d"]}</td>
          <td class="px-3 py-2 text-sm text-right">{row["promoted_share"]}%</td>
          <td class="px-3 py-2 text-sm text-right">{row["suppression_rate"]}%</td>
          <td class="px-3 py-2 text-sm text-right">{row["early_skip_rate"]}%</td>
          <td class="px-3 py-2 text-sm text-right">{row["completion_rate"]}%</td>
          <td class="px-3 py-2 text-sm text-right">{row["save_share_rate"]}%</td>
          <td class="px-3 py-2 text-xs text-gray-500">{_esc(row["last_promoted_at"] or "—")}</td>
        </tr>"""
            for row in rows
        )
        or '<tr><td colspan="10" class="px-3 py-4 text-sm text-gray-400 text-center">No source profiles found</td></tr>'
    )

    body = f"""
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-gray-900">Video Sources</h1>
      <a href="/api/v1/admin/ui/dashboard?key={admin_key}" class="text-sm text-blue-600 hover:underline">Back to dashboard</a>
    </div>
    <div class="bg-white rounded-lg shadow p-5">
      <table class="min-w-full">
        <thead class="bg-gray-50">
          <tr class="text-xs text-gray-500 uppercase">
            <th class="px-3 py-2 text-left">Channel</th>
            <th class="px-3 py-2 text-left">Status</th>
            <th class="px-3 py-2 text-left">Role</th>
            <th class="px-3 py-2 text-right">Score 7d</th>
            <th class="px-3 py-2 text-right">Promoted share</th>
            <th class="px-3 py-2 text-right">Suppression</th>
            <th class="px-3 py-2 text-right">Early skip</th>
            <th class="px-3 py-2 text-right">Completion</th>
            <th class="px-3 py-2 text-right">Save/share</th>
            <th class="px-3 py-2 text-left">Last promoted</th>
          </tr>
        </thead>
        <tbody>{body_rows}</tbody>
      </table>
    </div>"""
    return _base(body, key=admin_key, active="video-sources")


# ---------------------------------------------------------------------------
# GET /admin/ui/review — Reviewer queue
# ---------------------------------------------------------------------------


@router.get("/review", response_class=HTMLResponse)
def ui_review_queue(
    review_status: str = Query("CANDIDATE"),
    include_suppressed: bool = Query(False),
    type: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    discovered_via: Optional[str] = Query(None),
    min_signal_hits: int = Query(0, ge=0),
    start_day: Optional[str] = Query(None),
    end_day: Optional[str] = Query(None),
    sort_by: str = Query("priority"),
    selected_id: Optional[int] = Query(None, ge=1),
    page: int = Query(1, ge=1),
    flash: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    from app.models.content import ContentStatus

    repo = EditorialRepository(db)
    page_size = 25
    parsed_start_day = None
    if start_day:
        try:
            parsed_start_day = date.fromisoformat(start_day)
        except ValueError:
            parsed_start_day = None

    parsed_end_day = None
    if end_day:
        try:
            parsed_end_day = date.fromisoformat(end_day)
        except ValueError:
            parsed_end_day = None

    if parsed_start_day and parsed_end_day and parsed_start_day > parsed_end_day:
        parsed_start_day, parsed_end_day = parsed_end_day, parsed_start_day

    status_value = (review_status or "CANDIDATE").upper()
    if status_value not in {"CANDIDATE", "PROMOTED", "ALL"}:
        status_value = "CANDIDATE"
    status_filter = None if status_value == "ALL" else status_value

    start_day_value = parsed_start_day.isoformat() if parsed_start_day else ""
    end_day_value = parsed_end_day.isoformat() if parsed_end_day else ""

    items, total = repo.list_candidate_queue(
        content_type=type or None,
        source=source or None,
        discovered_via=discovered_via or None,
        min_signal_hits=min_signal_hits,
        start_day=parsed_start_day,
        end_day=parsed_end_day,
        curation_status=status_filter,
        include_suppressed=include_suppressed,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )
    counts = repo.candidate_queue_counts(
        include_suppressed=include_suppressed,
        curation_status=status_filter,
    )
    pages = max(1, math.ceil(total / page_size))

    if page > pages and total > 0:
        page = pages
        items, total = repo.list_candidate_queue(
            content_type=type or None,
            source=source or None,
            discovered_via=discovered_via or None,
            min_signal_hits=min_signal_hits,
            start_day=parsed_start_day,
            end_day=parsed_end_day,
            curation_status=status_filter,
            include_suppressed=include_suppressed,
            sort_by=sort_by,
            page=page,
            page_size=page_size,
        )

    def _sel(name: str, cur: str, opts: list[tuple[str, str]]) -> str:
        options_html = "".join(
            f'<option value="{v}" {"selected" if v == cur else ""}>{lbl}</option>'
            for v, lbl in opts
        )
        return (
            f'<select name="{name}" class="w-full rounded border border-gray-300 text-sm px-2 py-1">'
            f"{options_html}</select>"
        )

    flash_html = ""
    if flash:
        is_err = "error" in flash.lower() or "failed" in flash.lower()
        cls = "bg-red-100 text-red-800" if is_err else "bg-green-100 text-green-800"
        flash_html = f'<div class="mb-4 p-3 {cls} rounded text-sm">{_esc(flash)}</div>'

    if selected_id is not None:
        selected_item = next(
            (candidate for candidate in items if candidate.id == selected_id), None
        )
    else:
        selected_item = None

    if selected_item is None and selected_id is not None:
        candidate_item = repo.get_content_by_id(selected_id)
        if candidate_item and (
            status_filter is None or candidate_item.curation_status == ContentStatus[status_filter]
        ):
            selected_item = candidate_item

    if selected_item is None and items:
        selected_item = items[0]
        selected_id = selected_item.id

    def _review_query(
        *,
        target_page: Optional[int] = None,
        target_selected_id: Optional[int] = None,
    ) -> str:
        effective_selected_id = (
            target_selected_id if target_selected_id is not None else (selected_id or "")
        )
        return urlencode(
            {
                "key": admin_key,
                "page": target_page if target_page is not None else page,
                "review_status": status_value,
                "include_suppressed": "true" if include_suppressed else "false",
                "type": type or "",
                "source": source or "",
                "discovered_via": discovered_via or "",
                "min_signal_hits": min_signal_hits,
                "start_day": start_day_value,
                "end_day": end_day_value,
                "sort_by": sort_by,
                "selected_id": effective_selected_id,
            }
        )

    filter_form = f"""
    <div class="bg-white rounded-lg shadow p-4 mb-4">
      <form method="get" action="/api/v1/admin/ui/review" class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-10 gap-3 items-end">
        <input type="hidden" name="key" value="{admin_key}">
        <div>
          <label class="block text-xs text-gray-500 mb-1">Status</label>
          {_sel("review_status", status_value, [("CANDIDATE", "Candidates"), ("PROMOTED", "Promoted"), ("ALL", "All statuses")])}
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Suppressed</label>
          {_sel("include_suppressed", "true" if include_suppressed else "false", [("false", "Hide suppressed"), ("true", "Show suppressed")])}
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Type</label>
          {_sel("type", type or "", [("", "All"), ("ARTICLE", "Article"), ("VIDEO", "Video"), ("REEL", "Reel")])}
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Source contains</label>
          <input type="text" name="source" value="{source or ""}" placeholder="e.g. techcrunch"
                 class="w-full rounded border border-gray-300 text-sm px-2 py-1">
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Discovered via</label>
          <input type="text" name="discovered_via" value="{discovered_via or ""}" placeholder="signal / manual / ... "
                 class="w-full rounded border border-gray-300 text-sm px-2 py-1">
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Min signal hits</label>
          <input type="number" min="0" name="min_signal_hits" value="{min_signal_hits}"
                 class="w-full rounded border border-gray-300 text-sm px-2 py-1">
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">From date</label>
          <input type="date" name="start_day" value="{start_day_value}"
                 class="w-full rounded border border-gray-300 text-sm px-2 py-1">
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">To date</label>
          <input type="date" name="end_day" value="{end_day_value}"
                 class="w-full rounded border border-gray-300 text-sm px-2 py-1">
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Sort</label>
          {_sel("sort_by", sort_by, [("priority", "Priority"), ("first_seen", "First seen"), ("published_at", "Published")])}
        </div>
        <div>
          <button type="submit" class="w-full px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700">
            Apply
          </button>
        </div>
      </form>
    </div>"""

    review_next = "/api/v1/admin/ui/review?" + _review_query()
    publish_boost_options = "".join(
        f'<option value="{lvl}" {"selected" if lvl == 3 else ""}>{lvl}</option>' for lvl in range(4)
    )

    rows_html = ""
    mobile_cards_html = ""
    for item in items:
        score = f"{item.promotion_score:.3f}" if item.promotion_score else "—"
        published = item.published_at.strftime("%m-%d %H:%M") if item.published_at else "—"
        first_seen = getattr(item, "candidate_first_seen_at", None)
        first_seen_str = first_seen.strftime("%m-%d %H:%M") if first_seen else "—"
        discovered_label = _esc(getattr(item, "discovered_via", None) or "—")
        signal_hits = str(getattr(item, "signal_hits", 0) or 0)
        source_label = _esc(item.source or "—")
        title = _esc((item.title or "Untitled")[:85])
        item_link = (
            "/api/v1/admin/ui/review?"
            + _review_query(target_selected_id=item.id)
            + "#review-detail"
        )
        item_selected = selected_id == item.id
        row_class = "bg-blue-50" if item_selected else "hover:bg-gray-50"
        mobile_selected_ring = (
            "ring-2 ring-blue-400 border-blue-300" if item_selected else "border-gray-200"
        )

        rows_html += f"""
        <tr class="border-b border-gray-100 {row_class} cursor-pointer" data-detail-href="{item_link}">
          <td class="px-3 py-2">
            <input type="checkbox" value="{item.id}" class="bulk-item rounded border-gray-300" aria-label="Select {item.id}">
          </td>
          <td class="px-3 py-2 text-xs text-gray-500">{item.id}</td>
          <td class="px-3 py-2 text-sm">
            <a href="{item_link}" class="text-blue-600 hover:underline font-medium">{title}</a>
            <div class="text-xs text-gray-500 mt-0.5">{source_label}</div>
          </td>
          <td class="px-3 py-2 text-xs text-gray-600">{item.type.value if item.type else "—"}</td>
          <td class="px-3 py-2 text-xs font-mono text-gray-700">{score}</td>
          <td class="px-3 py-2 text-xs text-gray-500">{signal_hits}</td>
          <td class="px-3 py-2 text-xs text-gray-500">{discovered_label}</td>
          <td class="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">{first_seen_str}</td>
          <td class="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">{published}</td>
        </tr>"""

        mobile_cards_html += f"""
        <div class="bg-white rounded-lg shadow p-3 border {mobile_selected_ring}">
          <div class="flex items-start gap-2">
            <input type="checkbox" value="{item.id}" class="bulk-item mt-1 rounded border-gray-300" aria-label="Select {item.id}">
            <div class="min-w-0 flex-1">
              <a href="{item_link}" class="text-sm font-medium text-blue-700 hover:underline">{title}</a>
              <div class="text-xs text-gray-500 mt-1">{source_label}</div>
              <div class="mt-2 flex flex-wrap gap-1">
                {_badge(item.type.value if item.type else "—", "gray")}
                {_badge(f"score {score}", "blue")}
                {_badge(f"hits {signal_hits}", "yellow")}
              </div>
              <div class="mt-2 text-xs text-gray-500">
                First seen {first_seen_str} · Published {published}
              </div>
            </div>
          </div>
        </div>"""

    if not rows_html:
        rows_html = (
            '<tr><td colspan="9" class="px-3 py-6 text-center text-sm text-gray-400">'
            "No items match current filters."
            "</td></tr>"
        )
    if not mobile_cards_html:
        mobile_cards_html = (
            '<div class="bg-white rounded-lg shadow p-6 text-center text-sm text-gray-400">'
            "No items match current filters."
            "</div>"
        )

    def _page_link(target_page: int, label: str) -> str:
        query = _review_query(target_page=target_page)
        return f'<a href="?{query}" class="px-3 py-1 rounded bg-white shadow text-sm hover:bg-gray-50">{label}</a>'

    status_label = (
        "candidate items"
        if status_value == "CANDIDATE"
        else ("promoted items" if status_value == "PROMOTED" else "items")
    )
    pagination = (
        f'<span class="text-sm text-gray-600">Page {page}/{pages} — {total} {status_label}</span> '
    )
    if page > 1:
        pagination += _page_link(page - 1, "← Prev") + " "
    if page < pages:
        pagination += _page_link(page + 1, "Next →")

    queue_stats = (
        _badge(f"Articles {counts.get('ARTICLE', 0)}", "yellow")
        + " "
        + _badge(f"Videos {counts.get('VIDEO', 0)}", "yellow")
        + " "
        + _badge(f"Reels {counts.get('REEL', 0)}", "yellow")
    )

    if selected_item is not None:
        selected_actions = repo.get_actions_for_content(selected_item.id, limit=10)
        detail_next = (
            "/api/v1/admin/ui/review?"
            + _review_query(target_selected_id=selected_item.id)
            + "#review-detail"
        )
        selected_state = (
            selected_item.curation_status.value if selected_item.curation_status else "UNKNOWN"
        )
        status_color = (
            "yellow"
            if selected_state == "CANDIDATE"
            else ("green" if selected_state == "PROMOTED" else "gray")
        )
        selected_status = _badge(selected_state, status_color)
        if selected_item.is_suppressed:
            selected_status += " " + _badge("suppressed", "red")
        if (selected_item.editorial_boost or 0) > 0:
            selected_status += " " + _badge(f"boost {selected_item.editorial_boost}", "purple")

        selected_url = _esc(selected_item.source_url or "")
        selected_description = (
            _esc((selected_item.description or "").strip()[:280]) or "No description"
        )
        selected_score = (
            f"{selected_item.promotion_score:.4f}" if selected_item.promotion_score else "—"
        )
        selected_pub = (
            selected_item.published_at.strftime("%Y-%m-%d %H:%M")
            if selected_item.published_at
            else "—"
        )
        selected_first_seen = getattr(selected_item, "candidate_first_seen_at", None)
        selected_first_seen_str = (
            selected_first_seen.strftime("%Y-%m-%d %H:%M") if selected_first_seen else "—"
        )
        selected_actions_rows = ""
        for action in selected_actions:
            ts = action.created_at.strftime("%m-%d %H:%M") if action.created_at else "—"
            selected_actions_rows += f"""
            <tr class="border-b border-gray-100">
              <td class="px-2 py-1.5 text-xs text-gray-500 whitespace-nowrap">{ts}</td>
              <td class="px-2 py-1.5">{_badge(action.action_type, "blue")}</td>
              <td class="px-2 py-1.5 text-xs text-gray-600">{_esc(action.actor or "—")}</td>
            </tr>"""

        selected_panel_html = f"""
        <div id="review-detail" class="bg-white rounded-lg shadow sticky top-4">
          <div class="p-4 border-b border-gray-100">
            <h2 class="text-lg font-semibold text-gray-900 leading-snug">{_esc((selected_item.title or "Untitled")[:120])}</h2>
            <div class="mt-2 flex flex-wrap gap-1">{selected_status}</div>
          </div>
          <div class="p-4 space-y-4">
            <div class="space-y-1 text-sm">
              <div class="flex justify-between gap-3"><span class="text-gray-500">ID</span><span class="font-medium text-gray-800">{selected_item.id}</span></div>
              <div class="flex justify-between gap-3"><span class="text-gray-500">Type</span><span class="font-medium text-gray-800">{selected_item.type.value if selected_item.type else "—"}</span></div>
              <div class="flex justify-between gap-3"><span class="text-gray-500">Score</span><span class="font-mono text-gray-800">{selected_score}</span></div>
              <div class="flex justify-between gap-3"><span class="text-gray-500">Hits</span><span class="text-gray-800">{selected_item.signal_hits or 0}</span></div>
              <div class="flex justify-between gap-3"><span class="text-gray-500">First seen</span><span class="text-gray-800">{selected_first_seen_str}</span></div>
              <div class="flex justify-between gap-3"><span class="text-gray-500">Published</span><span class="text-gray-800">{selected_pub}</span></div>
            </div>

            <div>
              <div class="text-xs uppercase tracking-wide text-gray-500 mb-1">Source</div>
              <a href="{selected_url}" target="_blank" class="text-sm text-blue-700 hover:underline break-all">{selected_url}</a>
            </div>

            <div>
              <div class="text-xs uppercase tracking-wide text-gray-500 mb-1">Description</div>
              <p class="text-sm text-gray-700">{selected_description}</p>
            </div>

            <div class="grid grid-cols-2 gap-2">
              <form method="post" action="/api/v1/admin/ui/action/{selected_item.id}/approve?key={admin_key}">
                <input type="hidden" name="next" value="{detail_next}">
                <button class="w-full px-2 py-1.5 text-xs bg-emerald-600 text-white rounded hover:bg-emerald-700">Approve</button>
              </form>
              <form method="post" action="/api/v1/admin/ui/action/{selected_item.id}/hold?key={admin_key}">
                <input type="hidden" name="next" value="{detail_next}">
                <button class="w-full px-2 py-1.5 text-xs bg-amber-500 text-white rounded hover:bg-amber-600">Hold</button>
              </form>
              <form method="post" action="/api/v1/admin/ui/action/{selected_item.id}/reject?key={admin_key}">
                <input type="hidden" name="next" value="{detail_next}">
                <button class="w-full px-2 py-1.5 text-xs bg-red-600 text-white rounded hover:bg-red-700">Reject</button>
              </form>
              <form method="post" action="/api/v1/admin/ui/action/{selected_item.id}/approve-publish?key={admin_key}">
                <input type="hidden" name="next" value="{detail_next}">
                <input type="hidden" name="boost_level" value="3">
                <button class="w-full px-2 py-1.5 text-xs bg-green-600 text-white rounded hover:bg-green-700">Publish top</button>
              </form>
            </div>

            <form method="post" action="/api/v1/admin/ui/action/{selected_item.id}/approve-publish?key={admin_key}" class="p-3 rounded border border-green-200 bg-green-50">
              <input type="hidden" name="next" value="{detail_next}">
              <label class="block text-xs text-gray-600 mb-1">Boost and note (optional)</label>
              <div class="flex gap-2">
                <select name="boost_level" class="rounded border border-gray-300 text-sm px-2 py-1">{publish_boost_options}</select>
                <input type="text" name="note" placeholder="Why now"
                       class="flex-1 rounded border border-gray-300 text-sm px-2 py-1">
              </div>
              <button class="mt-2 w-full px-3 py-1.5 text-xs bg-green-700 text-white rounded hover:bg-green-800">Approve + publish</button>
            </form>

            <form method="post" action="/api/v1/admin/ui/action/{selected_item.id}/note?key={admin_key}">
              <input type="hidden" name="next" value="{detail_next}">
              <label class="block text-xs text-gray-600 mb-1">Reviewer note</label>
              <textarea name="note" rows="2" required class="w-full rounded border border-gray-300 text-sm px-2 py-1"></textarea>
              <button class="mt-2 w-full px-3 py-1.5 text-xs bg-gray-700 text-white rounded hover:bg-gray-800">Save note</button>
            </form>

            <a href="/api/v1/admin/ui/detail/{selected_item.id}?key={admin_key}" class="inline-block text-xs text-blue-700 hover:underline">
              Open full detail page
            </a>

            <div class="border-t border-gray-100 pt-3">
              <div class="text-xs uppercase tracking-wide text-gray-500 mb-2">Recent audit trail</div>
              <div class="overflow-x-auto">
                <table class="min-w-full">
                  <tbody>{selected_actions_rows or '<tr><td colspan="3" class="px-2 py-2 text-xs text-gray-400">No actions yet</td></tr>'}</tbody>
                </table>
              </div>
            </div>
          </div>
        </div>"""
    else:
        selected_panel_html = """
        <div id="review-detail" class="bg-white rounded-lg shadow p-6 text-sm text-gray-500">
          Select an item from the queue to open details and apply single-item actions here.
        </div>"""

    scope_description = (
        "candidate content"
        if status_value == "CANDIDATE"
        else ("promoted content" if status_value == "PROMOTED" else "all queued content")
    )

    body = f"""
    <div class="mb-4">
      <h1 class="text-2xl font-bold text-gray-900">Review Queue</h1>
      <p class="text-sm text-gray-600 mt-1">
        Triage {scope_description} in one place. Clicking an item keeps you on this page and opens details on the right.
      </p>
      <div class="mt-2">{queue_stats}</div>
    </div>
    {flash_html}
    {filter_form}
    <div class="bg-white rounded-lg shadow p-4 mb-4">
      <div class="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-3">
        <div>
          <h2 class="text-sm font-semibold text-gray-800 uppercase tracking-wide">Bulk actions</h2>
          <p class="text-xs text-gray-500 mt-1">Select multiple rows and apply one action in a single submission.</p>
          <div class="mt-2 flex flex-wrap items-center gap-2">
            <button type="button" id="bulk-select-page" class="px-2 py-1 text-xs rounded bg-gray-100 text-gray-700 hover:bg-gray-200">Select page</button>
            <button type="button" id="bulk-clear-page" class="px-2 py-1 text-xs rounded bg-gray-100 text-gray-700 hover:bg-gray-200">Clear</button>
            <span id="bulk-selected-count" class="text-xs text-gray-600">0 selected</span>
          </div>
        </div>
        <form id="bulk-action-form" method="post" action="/api/v1/admin/ui/review/bulk-action?key={admin_key}" class="grid grid-cols-1 sm:grid-cols-4 gap-2 w-full lg:w-auto">
          <input type="hidden" name="next" value="{review_next}">
          <input type="hidden" id="bulk-content-ids" name="content_ids_csv" value="">
          <select name="action" class="rounded border border-gray-300 text-sm px-2 py-1.5">
            <option value="approve">Approve</option>
            <option value="approve_publish">Approve + publish top</option>
            <option value="hold">Hold</option>
            <option value="reject">Reject and suppress</option>
          </select>
          <select name="boost_level" class="rounded border border-gray-300 text-sm px-2 py-1.5">
            {publish_boost_options}
          </select>
          <input type="text" name="note" placeholder="optional note"
                 class="rounded border border-gray-300 text-sm px-2 py-1.5">
          <button type="submit" class="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700">
            Apply
          </button>
        </form>
      </div>
    </div>

    <div class="flex justify-between items-center mb-2">{pagination}</div>
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4">
      <section class="xl:col-span-7">
        <div class="md:hidden space-y-2">{mobile_cards_html}</div>
        <div class="hidden md:block bg-white shadow rounded-lg overflow-x-auto">
          <table class="min-w-full">
            <thead class="bg-gray-50 text-xs font-medium text-gray-500 uppercase tracking-wide">
              <tr>
                <th class="px-3 py-3 text-left">
                  <input type="checkbox" id="bulk-select-all" class="rounded border-gray-300" aria-label="Select all on page">
                </th>
                <th class="px-3 py-3 text-left">ID</th>
                <th class="px-3 py-3 text-left">Item</th>
                <th class="px-3 py-3 text-left">Type</th>
                <th class="px-3 py-3 text-left">Score</th>
                <th class="px-3 py-3 text-left">Hits</th>
                <th class="px-3 py-3 text-left">Discovered</th>
                <th class="px-3 py-3 text-left">First seen</th>
                <th class="px-3 py-3 text-left">Published</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>
        <div class="mt-3">{pagination}</div>
      </section>
      <aside class="xl:col-span-5">{selected_panel_html}</aside>
    </div>

    <script>
      (() => {{
        const checkboxes = Array.from(document.querySelectorAll(".bulk-item"));
        const selectAll = document.getElementById("bulk-select-all");
        const selectPageButton = document.getElementById("bulk-select-page");
        const clearPageButton = document.getElementById("bulk-clear-page");
        const selectedCount = document.getElementById("bulk-selected-count");
        const bulkForm = document.getElementById("bulk-action-form");
        const bulkIdsInput = document.getElementById("bulk-content-ids");

        const interactiveSelector = "a,button,input,select,textarea,label,form";
        document.querySelectorAll("[data-detail-href]").forEach((row) => {{
          row.addEventListener("click", (event) => {{
            if (event.target.closest(interactiveSelector)) {{
              return;
            }}
            const href = row.getAttribute("data-detail-href");
            if (href) {{
              window.location.href = href;
            }}
          }});
        }});

        const visibleCheckboxes = () => checkboxes.filter((cb) => cb.offsetParent !== null);
        const updateSelectionUi = () => {{
          const activeCheckboxes = visibleCheckboxes();
          const selected = activeCheckboxes.filter((cb) => cb.checked).length;
          if (selectedCount) {{
            selectedCount.textContent = `${{selected}} selected`;
          }}
          if (selectAll) {{
            selectAll.checked = selected > 0 && selected === activeCheckboxes.length;
          }}
        }};

        if (selectAll) {{
          selectAll.addEventListener("change", () => {{
            visibleCheckboxes().forEach((cb) => {{
              cb.checked = selectAll.checked;
            }});
            updateSelectionUi();
          }});
        }}

        if (selectPageButton) {{
          selectPageButton.addEventListener("click", () => {{
            visibleCheckboxes().forEach((cb) => {{
              cb.checked = true;
            }});
            updateSelectionUi();
          }});
        }}

        if (clearPageButton) {{
          clearPageButton.addEventListener("click", () => {{
            visibleCheckboxes().forEach((cb) => {{
              cb.checked = false;
            }});
            updateSelectionUi();
          }});
        }}

        checkboxes.forEach((cb) => {{
          cb.addEventListener("change", updateSelectionUi);
        }});
        updateSelectionUi();

        if (bulkForm) {{
          bulkForm.addEventListener("submit", (event) => {{
            const selectedIds = visibleCheckboxes()
              .filter((cb) => cb.checked)
              .map((cb) => cb.value);
            if (!selectedIds.length) {{
              event.preventDefault();
              window.alert("Select at least one item for bulk actions.");
              return;
            }}
            if (bulkIdsInput) {{
              bulkIdsInput.value = selectedIds.join(",");
            }}
          }});
        }}
      }})();
    </script>"""
    return _base(body, key=admin_key, active="review")


@router.post("/review/bulk-action")
def ui_review_bulk_action(
    action: str = Form(...),
    content_ids_csv: str = Form(""),
    boost_level: int = Form(3),
    note: str = Form(""),
    next_path: str = Form("", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    raw_ids = [chunk.strip() for chunk in content_ids_csv.split(",") if chunk.strip()]
    parsed_ids: list[int] = []
    for raw_id in raw_ids:
        try:
            parsed_ids.append(int(raw_id))
        except ValueError:
            continue

    content_ids = list(dict.fromkeys(parsed_ids))
    if not content_ids:
        target = _resolve_ui_url(
            admin_key=admin_key,
            fallback_path="/api/v1/admin/ui/review",
            next_url=next_path,
            referer=referer,
        )
        return RedirectResponse(
            _add_flash(target, "Error: select at least one content item"),
            status_code=303,
        )

    repo = EditorialRepository(db)
    clean_note = note.strip() or None
    bounded_boost = max(0, min(3, boost_level))

    applied = 0
    failed = 0
    for content_id in content_ids:
        item = None
        if action == "approve":
            item = repo.approve(content_id, actor=ACTOR, note=clean_note)
        elif action == "approve_publish":
            item = repo.approve_and_publish(
                content_id=content_id,
                actor=ACTOR,
                boost_level=bounded_boost,
                note=clean_note,
            )
        elif action == "hold":
            item = repo.hold(content_id, actor=ACTOR, note=clean_note)
        elif action == "reject":
            item = repo.reject(content_id, actor=ACTOR, note=clean_note)

        if item is None:
            failed += 1
        else:
            applied += 1

    if applied > 0:
        invalidate_tiered_feed_cache()

    action_label = {
        "approve": "approved",
        "approve_publish": f"published to top (boost {bounded_boost})",
        "hold": "placed on hold",
        "reject": "rejected and suppressed",
    }.get(action, "")

    if not action_label:
        flash = "Error: unsupported bulk action"
    elif failed == 0:
        flash = f"Bulk action complete: {applied} items {action_label}"
    else:
        flash = f"Bulk action partial: {applied} succeeded, {failed} failed"

    target = _resolve_ui_url(
        admin_key=admin_key,
        fallback_path="/api/v1/admin/ui/review",
        next_url=next_path,
        referer=referer,
    )
    return RedirectResponse(_add_flash(target, flash), status_code=303)


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
        day=parsed_day,
        content_type=type,
        source=source,
        suppressed=supp,
        manual_added=manual,
        curation_status=curation_status or None,
        sort_by=sort_by,
        page=page,
        page_size=50,
    )

    pages = max(1, math.ceil(total / 50))
    content_next = "/api/v1/admin/ui/content?" + urlencode(
        {
            "page": page,
            "day": day or "",
            "type": type or "",
            "source": source or "",
            "suppressed": suppressed or "",
            "curation_status": curation_status or "",
            "sort_by": sort_by,
        }
    )

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
        if cs == ContentStatus.CANDIDATE:
            actions_html = f"""
            <div class="flex gap-1">
              <form method="post" action="/api/v1/admin/ui/action/{i.id}/approve-publish?key={admin_key}">
                <input type="hidden" name="next" value="{content_next}">
                <input type="hidden" name="boost_level" value="3">
                <button class="px-2 py-1 text-xs bg-green-600 text-white rounded hover:bg-green-700">Publish top</button>
              </form>
              <a href="/api/v1/admin/ui/detail/{i.id}?key={admin_key}"
                 class="px-2 py-1 text-xs bg-white border border-gray-300 text-gray-700 rounded hover:bg-gray-50">
                Review
              </a>
            </div>"""
        else:
            actions_html = (
                f'<a href="/api/v1/admin/ui/detail/{i.id}?key={admin_key}" '
                'class="px-2 py-1 text-xs bg-white border border-gray-300 text-gray-700 rounded hover:bg-gray-50">'
                "Open"
                "</a>"
            )

        rows_html += f"""
        <tr class="{row_bg} hover:brightness-95 border-b border-gray-100">
          <td class="px-3 py-2 text-sm text-gray-500">{i.id}</td>
          <td class="px-3 py-2 text-sm max-w-xs">
            <a href="/api/v1/admin/ui/detail/{i.id}?key={admin_key}" class="text-blue-600 hover:underline font-medium">{_esc((i.title or "")[:65])}</a>
          </td>
          <td class="px-3 py-2 text-xs text-gray-600">{i.type.value if i.type else ""}</td>
          <td class="px-3 py-2 text-xs text-gray-600 truncate max-w-[90px]">{_esc(i.source or "")}</td>
          <td class="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">{pub}</td>
          <td class="px-3 py-2">{badges}</td>
          <td class="px-3 py-2 text-xs font-mono text-gray-600">{score}</td>
          <td class="px-3 py-2 text-xs text-gray-500">{disc}</td>
          <td class="px-3 py-2 text-xs text-gray-500">{hits}</td>
          <td class="px-3 py-2 text-xs text-gray-500">{actions_html}</td>
        </tr>"""

    if not rows_html:
        rows_html = (
            '<tr><td colspan="10" class="px-3 py-6 text-center text-sm text-gray-400">'
            "No content found for current filters."
            "</td></tr>"
        )

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
          <input type="date" name="day" value="{day or ""}" class="w-full rounded border-gray-300 text-sm px-2 py-1">
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Type</label>
          {_sel("type", type or "", [("", "All"), ("ARTICLE", "Article"), ("VIDEO", "Video"), ("REEL", "Reel")])}
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Curation</label>
          {_sel("curation_status", curation_status or "", [("", "All"), ("PROMOTED", "Promoted"), ("CANDIDATE", "Candidate")])}
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Source</label>
          <input type="text" name="source" value="{source or ""}" placeholder="filter..." class="w-full rounded border-gray-300 text-sm px-2 py-1">
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Suppressed</label>
          {_sel("suppressed", suppressed or "", [("", "All"), ("false", "No"), ("true", "Yes")])}
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Sort by</label>
          {_sel("sort_by", sort_by, [("published_at", "Published"), ("created_at", "Created"), ("editorial_boost", "Boost")])}
        </div>
        <div>
          <button type="submit" class="w-full px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700">Filter</button>
        </div>
      </form>
    </div>"""

    def _page_link(p: int, label: str) -> str:
        query = urlencode(
            {
                "key": admin_key,
                "page": p,
                "day": day or "",
                "type": type or "",
                "source": source or "",
                "suppressed": suppressed or "",
                "curation_status": curation_status or "",
                "sort_by": sort_by,
            }
        )
        return f'<a href="?{query}" class="px-3 py-1 rounded bg-white shadow text-sm hover:bg-gray-50">{label}</a>'

    pagination = f'<span class="text-sm text-gray-600">Page {page}/{pages} — {total} items</span> '
    if page > 1:
        pagination += _page_link(page - 1, "← Prev") + " "
    if page < pages:
        pagination += _page_link(page + 1, "Next →")

    flash_html = ""
    if flash:
        flash_html = (
            f'<div class="mb-4 p-3 bg-green-100 text-green-800 rounded text-sm">{_esc(flash)}</div>'
        )

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
            <th class="px-3 py-3 text-left">Actions</th>
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
    curation_badge = (
        _badge("CANDIDATE", "yellow")
        if cs == ContentStatus.CANDIDATE
        else _badge("PROMOTED", "green")
    )

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

    publish_boost_options = "".join(
        f'<option value="{lvl}" {"selected" if lvl == 3 else ""}>{lvl}</option>' for lvl in range(4)
    )
    detail_next = f"/api/v1/admin/ui/detail/{content_id}"

    if cs == ContentStatus.CANDIDATE:
        state_hint = _badge("Awaiting review", "yellow")
        primary_button = f"""
        <form method="post" action="/api/v1/admin/ui/action/{content_id}/approve?key={admin_key}">
          <input type="hidden" name="next" value="{detail_next}">
          <button class="w-full px-4 py-2 bg-emerald-600 text-white text-sm rounded hover:bg-emerald-700 font-medium">
            Approve for feed
          </button>
        </form>"""
        secondary_button = f"""
        <form method="post" action="/api/v1/admin/ui/action/{content_id}/hold?key={admin_key}">
          <input type="hidden" name="next" value="{detail_next}">
          <button class="w-full px-4 py-2 bg-amber-500 text-white text-sm rounded hover:bg-amber-600 font-medium">
            Hold for later
          </button>
        </form>"""
        tertiary_button = f"""
        <form method="post" action="/api/v1/admin/ui/action/{content_id}/reject?key={admin_key}">
          <input type="hidden" name="next" value="{detail_next}">
          <button class="w-full px-4 py-2 bg-red-600 text-white text-sm rounded hover:bg-red-700 font-medium">
            Reject and suppress
          </button>
        </form>"""
    else:
        state_hint = _badge("Already promoted", "green")
        primary_button = f"""
        <form method="post" action="/api/v1/admin/ui/action/{content_id}/approve-publish?key={admin_key}">
          <input type="hidden" name="next" value="{detail_next}">
          <input type="hidden" name="boost_level" value="3">
          <button class="w-full px-4 py-2 bg-emerald-600 text-white text-sm rounded hover:bg-emerald-700 font-medium">
            Republish to top
          </button>
        </form>"""
        secondary_button = f"""
        <form method="post" action="/api/v1/admin/ui/action/{content_id}/demote?key={admin_key}">
          <input type="hidden" name="next" value="{detail_next}">
          <button class="w-full px-4 py-2 bg-amber-500 text-white text-sm rounded hover:bg-amber-600 font-medium">
            Move back to candidate
          </button>
        </form>"""
        tertiary_button = f"""
        <form method="post" action="/api/v1/admin/ui/action/{content_id}/reject?key={admin_key}">
          <input type="hidden" name="next" value="{detail_next}">
          <button class="w-full px-4 py-2 bg-red-600 text-white text-sm rounded hover:bg-red-700 font-medium">
            Reject and suppress
          </button>
        </form>"""

    if item.is_suppressed:
        suppress_btn = f"""
        <form method="post" action="/api/v1/admin/ui/action/{content_id}/unsuppress?key={admin_key}">
          <input type="hidden" name="next" value="{detail_next}">
          <button class="w-full px-4 py-2 bg-gray-200 text-gray-800 text-sm rounded hover:bg-gray-300 font-medium">Unsuppress</button>
        </form>"""
    else:
        suppress_btn = f"""
        <form method="post" action="/api/v1/admin/ui/action/{content_id}/suppress?key={admin_key}">
          <input type="hidden" name="next" value="{detail_next}">
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
          <td class="px-3 py-2">{_badge(a.action_type, "blue")}</td>
          <td class="px-3 py-2 text-xs text-gray-600">{_esc(a.actor or "")}</td>
          <td class="px-3 py-2 text-xs text-gray-500 font-mono">{_esc(str(a.old_value or ""))}</td>
          <td class="px-3 py-2 text-xs text-gray-500 font-mono">{_esc(str(a.new_value or ""))}</td>
        </tr>"""

    body = f"""
    <div class="mb-4">
      <a href="/api/v1/admin/ui/content?key={admin_key}" class="text-sm text-blue-600 hover:underline">← Back to content</a>
    </div>
    {flash_html}
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div class="lg:col-span-2 bg-white rounded-lg shadow">
        <div class="p-5 border-b border-gray-100">
          <h1 class="text-xl font-bold text-gray-900 leading-snug">{_esc((item.title or "")[:120])}</h1>
          <div class="mt-2 flex flex-wrap gap-1">{badges}</div>
        </div>
        <table class="w-full">
          {_row("ID", str(item.id))}
          {_row("Type", item.type.value if item.type else "—")}
          {_row("Curation status", curation_badge)}
          {_row("Promotion score", f"{item.promotion_score:.4f}" if getattr(item, "promotion_score", None) else "—")}
          {_row("Discovered via", _esc(getattr(item, "discovered_via", None) or "—"))}
          {_row("Signal hits", str(getattr(item, "signal_hits", 0) or 0))}
          {_row("Source", _esc(item.source or "—"))}
          {_row("URL", f'<a href="{item.source_url}" target="_blank" class="text-blue-600 hover:underline text-xs break-all">{_esc((item.source_url or "")[:90])}</a>')}
          {_row("Published", pub)}
          {_row("Created", created)}
          {_row("Global score", f"{item.global_score:.4f}" if item.global_score else "—")}
          {_row("Quality score", f"{item.quality_score:.4f}" if item.quality_score else "—")}
          {_row("Editorial boost", str(item.editorial_boost or 0))}
          {_row("AI processed", "✅ Yes" if item.ai_processed else "⏳ No")}
          {_row("Cluster", str(item.cluster_id) if item.cluster_id else "—")}
          {_row("Manual added", "✅ Yes" if item.manual_added else "No")}
        </table>
      </div>

      <div class="space-y-4">
        <div class="bg-white rounded-lg shadow p-5">
          <div class="flex items-center justify-between mb-3">
            <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide">Editorial decision</h3>
            {state_hint}
          </div>
          <p class="text-xs text-gray-500 mb-3">
            Pick the decision you want applied to this content item.
          </p>
          <div class="space-y-2">
            {primary_button}
            {secondary_button}
            {tertiary_button}
          </div>
          <form method="post" action="/api/v1/admin/ui/action/{content_id}/approve-publish?key={admin_key}" class="mt-3 p-3 bg-green-50 rounded border border-green-100">
            <input type="hidden" name="next" value="{detail_next}">
            <label class="block text-xs text-gray-600 mb-1">Publish to top note (optional)</label>
            <input type="text" name="note" placeholder="Why this should be highlighted now"
                   class="w-full rounded border border-gray-300 text-sm px-2 py-1 mb-2">
            <div class="flex items-center gap-2">
              <select name="boost_level" class="rounded border border-gray-300 text-sm px-2 py-1">
                {publish_boost_options}
              </select>
              <button type="submit" class="flex-1 px-3 py-1.5 bg-green-600 text-white text-sm rounded hover:bg-green-700">
                Approve + publish top
              </button>
            </div>
          </form>
          <form method="post" action="/api/v1/admin/ui/action/{content_id}/request-changes?key={admin_key}" class="mt-3">
            <input type="hidden" name="next" value="{detail_next}">
            <label class="block text-xs text-gray-600 mb-1">Request changes note (required)</label>
            <textarea name="note" required rows="2"
                      placeholder="Tell the team what should be changed"
                      class="w-full rounded border border-gray-300 text-sm px-2 py-1 mb-2"></textarea>
            <button type="submit" class="w-full px-3 py-1.5 bg-indigo-600 text-white text-sm rounded hover:bg-indigo-700">
              Request changes
            </button>
          </form>
          <form method="post" action="/api/v1/admin/ui/action/{content_id}/note?key={admin_key}" class="mt-3">
            <input type="hidden" name="next" value="{detail_next}">
            <label class="block text-xs text-gray-600 mb-1">Reviewer note</label>
            <textarea name="note" required rows="2"
                      placeholder="Internal note for audit trail"
                      class="w-full rounded border border-gray-300 text-sm px-2 py-1 mb-2"></textarea>
            <button type="submit" class="w-full px-3 py-1.5 bg-gray-700 text-white text-sm rounded hover:bg-gray-800">
              Save note
            </button>
          </form>
        </div>
        <div class="bg-white rounded-lg shadow p-5">
          <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">Visibility</h3>
          {suppress_btn}
        </div>
        <div class="bg-white rounded-lg shadow p-5">
          <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">Editorial boost</h3>
          <form method="post" action="/api/v1/admin/ui/action/{content_id}/boost?key={admin_key}" class="flex gap-2">
            <input type="hidden" name="next" value="{detail_next}">
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
        target = f"/api/v1/admin/ui/detail/{result.content_id}?key={admin_key}"
        return RedirectResponse(_add_flash(target, msg), status_code=303)
    return RedirectResponse(
        _add_flash(f"/api/v1/admin/ui/submit?key={admin_key}", msg),
        status_code=303,
    )


def _redirect_after_action(
    *,
    content_id: int,
    admin_key: str,
    flash: str,
    next_url: Optional[str] = None,
    referer: Optional[str] = None,
) -> RedirectResponse:
    target = _resolve_next_ui_url(
        content_id=content_id,
        admin_key=admin_key,
        next_url=next_url,
        referer=referer,
    )
    return RedirectResponse(_add_flash(target, flash), status_code=303)


@router.post("/action/{content_id}/boost")
def ui_boost(
    content_id: int,
    level: int = Form(0),
    next_path: str = Form("", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)
    bounded_level = max(0, min(3, level))
    item = repo.set_boost(content_id, bounded_level, actor=ACTOR)
    if not item:
        return _redirect_after_action(
            content_id=content_id,
            admin_key=admin_key,
            flash="Error: content not found",
            next_url=next_path,
            referer=referer,
        )
    invalidate_tiered_feed_cache()
    return _redirect_after_action(
        content_id=content_id,
        admin_key=admin_key,
        flash=f"Boost set to {bounded_level}",
        next_url=next_path,
        referer=referer,
    )


@router.post("/action/{content_id}/suppress")
def ui_suppress(
    content_id: int,
    next_path: str = Form("", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)
    item = repo.suppress(content_id, actor=ACTOR)
    if not item:
        return _redirect_after_action(
            content_id=content_id,
            admin_key=admin_key,
            flash="Error: content not found",
            next_url=next_path,
            referer=referer,
        )
    invalidate_tiered_feed_cache()
    return _redirect_after_action(
        content_id=content_id,
        admin_key=admin_key,
        flash="Content suppressed",
        next_url=next_path,
        referer=referer,
    )


@router.post("/action/{content_id}/unsuppress")
def ui_unsuppress(
    content_id: int,
    next_path: str = Form("", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)
    item = repo.unsuppress(content_id, actor=ACTOR)
    if not item:
        return _redirect_after_action(
            content_id=content_id,
            admin_key=admin_key,
            flash="Error: content not found",
            next_url=next_path,
            referer=referer,
        )
    invalidate_tiered_feed_cache()
    return _redirect_after_action(
        content_id=content_id,
        admin_key=admin_key,
        flash="Content unsuppressed",
        next_url=next_path,
        referer=referer,
    )


@router.post("/action/{content_id}/promote")
def ui_promote(
    content_id: int,
    next_path: str = Form("", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)
    item = repo.promote(content_id, actor=ACTOR)
    if not item:
        return _redirect_after_action(
            content_id=content_id,
            admin_key=admin_key,
            flash="Error: content not found",
            next_url=next_path,
            referer=referer,
        )
    invalidate_tiered_feed_cache()
    return _redirect_after_action(
        content_id=content_id,
        admin_key=admin_key,
        flash="Promoted to feed",
        next_url=next_path,
        referer=referer,
    )


@router.post("/action/{content_id}/demote")
def ui_demote(
    content_id: int,
    next_path: str = Form("", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)
    item = repo.demote(content_id, actor=ACTOR)
    if not item:
        return _redirect_after_action(
            content_id=content_id,
            admin_key=admin_key,
            flash="Error: content not found",
            next_url=next_path,
            referer=referer,
        )
    invalidate_tiered_feed_cache()
    return _redirect_after_action(
        content_id=content_id,
        admin_key=admin_key,
        flash="Moved to candidate",
        next_url=next_path,
        referer=referer,
    )


@router.post("/action/{content_id}/approve")
def ui_approve(
    content_id: int,
    note: str = Form(""),
    next_path: str = Form("", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)
    clean_note = note.strip() or None
    item = repo.approve(content_id, actor=ACTOR, note=clean_note)
    if not item:
        return _redirect_after_action(
            content_id=content_id,
            admin_key=admin_key,
            flash="Error: content not found",
            next_url=next_path,
            referer=referer,
        )
    invalidate_tiered_feed_cache()
    return _redirect_after_action(
        content_id=content_id,
        admin_key=admin_key,
        flash="Content approved",
        next_url=next_path,
        referer=referer,
    )


@router.post("/action/{content_id}/reject")
def ui_reject(
    content_id: int,
    note: str = Form(""),
    next_path: str = Form("", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)
    clean_note = note.strip() or None
    item = repo.reject(content_id, actor=ACTOR, note=clean_note)
    if not item:
        return _redirect_after_action(
            content_id=content_id,
            admin_key=admin_key,
            flash="Error: content not found",
            next_url=next_path,
            referer=referer,
        )
    invalidate_tiered_feed_cache()
    return _redirect_after_action(
        content_id=content_id,
        admin_key=admin_key,
        flash="Content rejected and suppressed",
        next_url=next_path,
        referer=referer,
    )


@router.post("/action/{content_id}/hold")
def ui_hold(
    content_id: int,
    note: str = Form(""),
    next_path: str = Form("", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    repo = EditorialRepository(db)
    clean_note = note.strip() or None
    item = repo.hold(content_id, actor=ACTOR, note=clean_note)
    if not item:
        return _redirect_after_action(
            content_id=content_id,
            admin_key=admin_key,
            flash="Error: content not found",
            next_url=next_path,
            referer=referer,
        )
    invalidate_tiered_feed_cache()
    return _redirect_after_action(
        content_id=content_id,
        admin_key=admin_key,
        flash="Content placed on hold",
        next_url=next_path,
        referer=referer,
    )


@router.post("/action/{content_id}/request-changes")
def ui_request_changes(
    content_id: int,
    note: str = Form(""),
    next_path: str = Form("", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    clean_note = note.strip()
    if not clean_note:
        return _redirect_after_action(
            content_id=content_id,
            admin_key=admin_key,
            flash="Error: request changes note is required",
            next_url=next_path,
            referer=referer,
        )

    repo = EditorialRepository(db)
    item = repo.request_changes(content_id, actor=ACTOR, note=clean_note)
    if not item:
        return _redirect_after_action(
            content_id=content_id,
            admin_key=admin_key,
            flash="Error: content not found",
            next_url=next_path,
            referer=referer,
        )
    invalidate_tiered_feed_cache()
    return _redirect_after_action(
        content_id=content_id,
        admin_key=admin_key,
        flash="Changes requested",
        next_url=next_path,
        referer=referer,
    )


@router.post("/action/{content_id}/approve-publish")
def ui_approve_publish(
    content_id: int,
    boost_level: int = Form(3),
    note: str = Form(""),
    next_path: str = Form("", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    bounded_boost = max(0, min(3, boost_level))
    clean_note = note.strip() or None
    repo = EditorialRepository(db)
    item = repo.approve_and_publish(
        content_id=content_id,
        actor=ACTOR,
        boost_level=bounded_boost,
        note=clean_note,
    )
    if not item:
        return _redirect_after_action(
            content_id=content_id,
            admin_key=admin_key,
            flash="Error: content not found",
            next_url=next_path,
            referer=referer,
        )
    invalidate_tiered_feed_cache()
    return _redirect_after_action(
        content_id=content_id,
        admin_key=admin_key,
        flash=f"Published to top with boost {bounded_boost}",
        next_url=next_path,
        referer=referer,
    )


@router.post("/action/{content_id}/note")
def ui_add_note(
    content_id: int,
    note: str = Form(""),
    next_path: str = Form("", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_key_or_query),
):
    clean_note = note.strip()
    if not clean_note:
        return _redirect_after_action(
            content_id=content_id,
            admin_key=admin_key,
            flash="Error: note is required",
            next_url=next_path,
            referer=referer,
        )

    repo = EditorialRepository(db)
    action = repo.add_reviewer_note(content_id=content_id, actor=ACTOR, note=clean_note)
    if action is None:
        return _redirect_after_action(
            content_id=content_id,
            admin_key=admin_key,
            flash="Error: content not found",
            next_url=next_path,
            referer=referer,
        )
    return _redirect_after_action(
        content_id=content_id,
        admin_key=admin_key,
        flash="Reviewer note saved",
        next_url=next_path,
        referer=referer,
    )
