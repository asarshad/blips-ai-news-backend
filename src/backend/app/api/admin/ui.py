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
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse

from fastapi import APIRouter, Cookie, Depends, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.article_hydration import display_article_title
from app.core.auth import (
    build_admin_ui_session_token,
    is_admin_key_configured,
    is_valid_admin_key,
    is_valid_admin_ui_session,
)
from app.core.config import settings
from app.core.dependencies import get_db, get_redis
from app.domain.editorial.service import EditorialService
from app.models.content import ContentItem, ContentStatus, ContentType
from app.models.push import PushSendLog
from app.repositories.editorial_repo import EditorialRepository
from app.schemas.push import PushMode, PushRuntimeConfigPatch
from app.services.content_readiness import describe_readiness_reason
from app.services.push_config_service import PushConfigService
from app.services.push_service import (
    PushNotificationError,
    PushNotificationService,
    create_push_messaging_client,
    evaluate_push_eligibility,
)
from app.services.tiered_feed_service import invalidate_tiered_feed_cache

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


_ADMIN_UI_COOKIE_NAME = "blips_admin_session"
_ADMIN_UI_COOKIE_TTL_SECONDS = 8 * 60 * 60
_ADMIN_UI_PREFIX = "/api/v1/admin/ui"
_ADMIN_UI_BULK_ACTION_LIMIT = 10


def _require_admin_ui_auth(
    admin_session: Optional[str] = Cookie(None, alias=_ADMIN_UI_COOKIE_NAME),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
) -> str:
    if not is_admin_key_configured():
        raise HTTPException(status_code=401, detail="Admin endpoints disabled")

    if is_valid_admin_key(x_admin_key) or is_valid_admin_ui_session(admin_session):
        return build_admin_ui_session_token()

    if x_admin_key or admin_session:
        raise HTTPException(status_code=403, detail="Invalid admin credentials")

    raise HTTPException(status_code=401, detail="Missing admin credentials")


def _has_admin_ui_auth(
    admin_session: Optional[str] = Cookie(None, alias=_ADMIN_UI_COOKIE_NAME),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
) -> bool:
    return is_valid_admin_key(x_admin_key) or is_valid_admin_ui_session(admin_session)


def _set_admin_ui_cookie(response: RedirectResponse) -> None:
    response.set_cookie(
        key=_ADMIN_UI_COOKIE_NAME,
        value=build_admin_ui_session_token(),
        max_age=_ADMIN_UI_COOKIE_TTL_SECONDS,
        httponly=True,
        secure=settings.ENV == "prod",
        samesite="strict",
        path=_ADMIN_UI_PREFIX,
    )


def admin_ui_auth_redirect_response(
    request: Request,
    *,
    status_code: int,
) -> RedirectResponse | None:
    """Redirect unauthenticated admin UI traffic to the login page."""
    path = request.url.path
    login_path = f"{_ADMIN_UI_PREFIX}/login"

    if status_code not in {401, 403}:
        return None
    if not path.startswith(_ADMIN_UI_PREFIX) or path == login_path:
        return None

    target = login_path
    if request.method in {"GET", "HEAD"}:
        next_url = path
        if request.url.query:
            next_url = f"{next_url}?{request.url.query}"
        target = f"{login_path}?{urlencode({'next': next_url})}"

    response = RedirectResponse(
        target,
        status_code=302 if request.method in {"GET", "HEAD"} else 303,
    )
    response.delete_cookie(_ADMIN_UI_COOKIE_NAME, path=_ADMIN_UI_PREFIX)
    return response


router = APIRouter(prefix="/admin/ui", tags=["admin-ui"])
ACTOR = "admin"

# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


def _nav(key: str, active: str = "") -> str:
    def _link(href: str, label: str, name: str) -> str:
        base = (
            "shrink-0 inline-flex items-center rounded-full px-3 py-2 text-sm font-medium "
            "transition-all duration-150"
        )
        if active == name:
            cls = f"{base} bg-white text-slate-950 shadow-sm"
        else:
            cls = f"{base} text-slate-300 hover:bg-slate-800/80 hover:text-white"
        return f'<a href="{href}" class="{cls}">{label}</a>'

    return f"""
    <nav class="sticky top-0 z-40 border-b border-slate-200/70 bg-white/85 backdrop-blur-xl">
      <div class="mx-auto max-w-7xl px-4 py-4 sm:px-6 lg:px-8">
        <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div class="min-w-0">
            <div class="flex items-center gap-3">
              <div class="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-slate-950 text-sm font-semibold tracking-[0.24em] text-white shadow-lg shadow-slate-900/20">BL</div>
              <div class="min-w-0">
                <p class="truncate text-base font-semibold tracking-tight text-slate-950">Blips Admin</p>
                <p class="truncate text-xs text-slate-500">Operations, curation, and feed monitoring</p>
              </div>
            </div>
          </div>
          <div class="rounded-[1.4rem] bg-slate-950 p-1.5 shadow-[0_18px_50px_-24px_rgba(15,23,42,0.95)] ring-1 ring-slate-800/80">
            <div class="no-scrollbar flex items-center gap-1 overflow-x-auto px-0.5">
              {_link("/api/v1/admin/ui/dashboard", "Dashboard", "dashboard")}
              {_link("/api/v1/admin/ui/video-lanes", "Video Lanes", "video-lanes")}
              {_link("/api/v1/admin/ui/video-sources", "Video Sources", "video-sources")}
              {_link("/api/v1/admin/ui/review", "Review Queue", "review")}
              {_link("/api/v1/admin/ui/content", "Content", "content")}
              {_link("/api/v1/admin/ui/submit", "Submit URL", "submit")}
              <form method="post" action="/api/v1/admin/ui/logout" class="shrink-0">
                <button type="submit" class="inline-flex items-center rounded-full px-3 py-2 text-sm font-medium text-slate-300 transition-all duration-150 hover:bg-slate-800/80 hover:text-white">
                  Logout
                </button>
              </form>
            </div>
          </div>
        </div>
      </div>
    </nav>"""


_ADMIN_STYLES = """
<style>
  :root {
    --admin-bg: #f3f6fb;
    --admin-ink: #0f172a;
    --admin-muted: #64748b;
    --admin-line: rgba(148, 163, 184, 0.22);
    --admin-panel: rgba(255, 255, 255, 0.84);
  }

  html {
    scroll-behavior: smooth;
  }

  body {
    font-family: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
    color: var(--admin-ink);
    background:
      radial-gradient(circle at top left, rgba(251, 191, 36, 0.16), transparent 26%),
      radial-gradient(circle at top right, rgba(59, 130, 246, 0.12), transparent 24%),
      linear-gradient(180deg, #f8fafc 0%, var(--admin-bg) 52%, #eef3f9 100%);
  }

  .no-scrollbar {
    -ms-overflow-style: none;
    scrollbar-width: none;
  }

  .no-scrollbar::-webkit-scrollbar {
    display: none;
  }

  .glass-panel {
    background: var(--admin-panel);
    border: 1px solid var(--admin-line);
    box-shadow: 0 24px 60px -36px rgba(15, 23, 42, 0.55);
    backdrop-filter: blur(18px);
  }

  .panel-kicker {
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--admin-muted);
  }

  .table-shell {
    overflow-x: auto;
    border-radius: 1.25rem;
  }

  .table-shell table {
    min-width: 100%;
  }

  @media (max-width: 640px) {
    main {
      padding-bottom: 5rem;
    }
  }
</style>
"""


def _strip_admin_auth_artifacts(html: str, key: str) -> str:
    if not key:
        return html

    html = html.replace(f"?key={key}&", "?")
    html = html.replace(f"&key={key}", "")
    html = html.replace(f"?key={key}", "")
    html = html.replace(f'<input type="hidden" name="key" value="{key}">', "")
    html = html.replace("?&", "?")
    html = html.replace("&&", "&")
    return html


def _base(body: str, key: str = "", active: str = "", *, show_nav: bool = True) -> HTMLResponse:
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Blips Admin</title>
  <script src="https://cdn.tailwindcss.com"></script>
  {_ADMIN_STYLES}
</head>
<body class="min-h-screen">
  {_nav(key, active) if show_nav else ""}
  <main class="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
    {body}
  </main>
</body>
</html>"""
    response = HTMLResponse(_strip_admin_auth_artifacts(html, key))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


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
    allowed_prefix = f"{_ADMIN_UI_PREFIX}/"
    for candidate in (next_url, referer):
        if not candidate:
            continue
        parsed = urlparse(candidate)
        path = parsed.path or ""
        if not path.startswith(allowed_prefix):
            continue
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params.pop("key", None)
        query = urlencode(params)
        return f"{path}?{query}" if query else path
    return fallback_path


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
    value = "" if s is None else str(s)
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _badge(text: str, color: str) -> str:
    palettes = {
        "green": "bg-emerald-100 text-emerald-800 ring-1 ring-emerald-200",
        "yellow": "bg-amber-100 text-amber-900 ring-1 ring-amber-200",
        "red": "bg-rose-100 text-rose-900 ring-1 ring-rose-200",
        "blue": "bg-sky-100 text-sky-900 ring-1 ring-sky-200",
        "gray": "bg-slate-100 text-slate-700 ring-1 ring-slate-200",
        "purple": "bg-violet-100 text-violet-900 ring-1 ring-violet-200",
    }
    cls = palettes.get(color, palettes["gray"])
    return f'<span class="inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-medium {cls}">{_esc(text)}</span>'


def _stat_card(label: str, value: str, sub: str = "", color: str = "blue") -> str:
    border = {
        "blue": "from-sky-500 to-cyan-400",
        "green": "from-emerald-500 to-teal-400",
        "yellow": "from-amber-500 to-orange-400",
        "red": "from-rose-500 to-pink-400",
        "purple": "from-violet-500 to-fuchsia-400",
        "gray": "from-slate-500 to-slate-400",
    }.get(color, "from-sky-500 to-cyan-400")
    return f"""
    <div class="glass-panel relative h-full overflow-hidden rounded-[1.6rem] p-5">
      <div class="absolute inset-x-0 top-0 h-1.5 bg-gradient-to-r {border}"></div>
      <div class="panel-kicker">{label}</div>
      <div class="mt-2 text-3xl font-semibold tracking-tight text-slate-950">{value}</div>
      {f'<div class="mt-2 text-sm leading-6 text-slate-600">{sub}</div>' if sub else ""}
    </div>"""


def _panel(title: str, body: str, subtitle: str = "", action: str = "", tone: str = "blue") -> str:
    accent = {
        "blue": "from-sky-500/40 via-sky-300/0 to-transparent",
        "green": "from-emerald-500/40 via-emerald-300/0 to-transparent",
        "yellow": "from-amber-500/45 via-amber-300/0 to-transparent",
        "red": "from-rose-500/40 via-rose-300/0 to-transparent",
        "purple": "from-violet-500/45 via-violet-300/0 to-transparent",
        "slate": "from-slate-500/35 via-slate-300/0 to-transparent",
    }.get(tone, "from-sky-500/40 via-sky-300/0 to-transparent")
    return f"""
    <section class="glass-panel relative overflow-hidden rounded-[1.8rem] p-5 sm:p-6">
      <div class="absolute inset-x-0 top-0 h-24 bg-gradient-to-r {accent}"></div>
      <div class="relative">
        <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p class="panel-kicker">{title}</p>
            {f'<p class="mt-2 max-w-3xl text-sm leading-6 text-slate-600">{subtitle}</p>' if subtitle else ""}
          </div>
          {action}
        </div>
        {body}
      </div>
    </section>"""


def _mini_metric(label: str, value: str, sub: str = "", tone: str = "blue") -> str:
    palette = {
        "blue": "border-sky-200 bg-sky-50/80",
        "green": "border-emerald-200 bg-emerald-50/80",
        "yellow": "border-amber-200 bg-amber-50/85",
        "red": "border-rose-200 bg-rose-50/80",
        "purple": "border-violet-200 bg-violet-50/80",
        "slate": "border-slate-200 bg-slate-50/80",
    }.get(tone, "border-sky-200 bg-sky-50/80")
    return f"""
    <div class="rounded-2xl border {palette} p-3">
      <div class="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">{label}</div>
      <div class="mt-1 text-2xl font-semibold tracking-tight text-slate-950">{value}</div>
      {f'<div class="mt-1 text-xs leading-5 text-slate-500">{sub}</div>' if sub else ""}
    </div>"""


def _data_rows(rows: list[tuple[str, str, str | None]]) -> str:
    return (
        '<div class="divide-y divide-slate-200/80">'
        + "".join(
            f"""
            <div class="flex flex-col gap-1 py-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
              <div class="min-w-0">
                <div class="text-sm font-medium text-slate-700">{_esc(label)}</div>
                {f'<div class="text-xs leading-5 text-slate-500">{_esc(sub)}</div>' if sub else ""}
              </div>
              <div class="text-sm font-semibold text-slate-950">{value}</div>
            </div>"""
            for label, value, sub in rows
        )
        + "</div>"
    )


def _lane_bucket(value: Optional[str]) -> str:
    lane = str(value or "").lower()
    if any(token in lane for token in ("search", "trending", "discovery")):
        return "discovery"
    return "curated"


def _source_status_tone(status: Optional[str]) -> str:
    normalized = str(status or "").lower()
    return {
        "core": "green",
        "rotation": "blue",
        "discovery": "yellow",
        "paused": "red",
        "disabled": "red",
    }.get(normalized, "gray")


def _lane_metric_sort_key(row: dict) -> tuple[int, float, int]:
    promotion_rate = row.get("promotion_rate")
    normalized_rate = float(promotion_rate) if isinstance(promotion_rate, (int, float)) else -1.0
    return (
        int(row.get("promoted") or 0),
        normalized_rate,
        int(row.get("candidates") or 0),
    )


def _feed_channel(item: dict) -> str:
    return str(item.get("channel_id") or item.get("source") or "unknown")


def _summarize_feed_window(
    page_one: list[dict],
    page_two: list[dict],
    *,
    remaining_count: int,
    duration_limit: Optional[int] = None,
) -> dict:
    combined = list(page_one) + list(page_two)
    channels = [_feed_channel(item) for item in combined]
    channel_counts = Counter(channels)
    lane_counts = Counter(
        _lane_bucket(item.get("acquisition_lane") or item.get("discovered_via"))
        for item in combined
    )
    source_status_counts = Counter(str(item.get("source_status") or "unknown") for item in combined)
    known_durations = [
        int(item["duration_seconds"])
        for item in combined
        if isinstance(item.get("duration_seconds"), (int, float))
    ]

    return {
        "item_count": len(combined),
        "page_one_count": len(page_one),
        "state": "warming_up"
        if not page_one
        else ("caught_up" if remaining_count == 0 else "healthy"),
        "remaining_count": remaining_count,
        "unique_channels": len(set(channels)),
        "max_channel_count": max(channel_counts.values()) if channel_counts else 0,
        "adjacent_duplicates": sum(
            1 for a, b in zip(channels, channels[1:], strict=False) if a == b
        ),
        "boundary_duplicate": bool(
            page_one and page_two and _feed_channel(page_one[-1]) == _feed_channel(page_two[0])
        ),
        "lane_counts": dict(lane_counts),
        "source_status_counts": dict(source_status_counts),
        "known_duration_count": len(known_durations),
        "max_known_duration": max(known_durations) if known_durations else None,
        "duration_violations": (
            sum(1 for value in known_durations if value > duration_limit)
            if duration_limit is not None
            else 0
        ),
        "sample_items": combined[:6],
    }


def _redirect_to_ui(
    *,
    admin_key: str,
    fallback_path: str,
    flash: str,
    next_url: Optional[str] = None,
    referer: Optional[str] = None,
) -> RedirectResponse:
    target = _resolve_ui_url(
        admin_key=admin_key,
        fallback_path=fallback_path,
        next_url=next_url,
        referer=referer,
    )
    return RedirectResponse(_add_flash(target, flash), status_code=303)


def _push_ui_reason(reason: str) -> str:
    if reason == "reels_excluded":
        return "Reels are excluded from push delivery."
    if reason == "unsupported_type":
        return "This item type does not support push delivery."
    if reason == "push_ready":
        return ""
    return describe_readiness_reason(reason)


def _readiness_badge(item: ContentItem) -> str:
    readiness_status = (getattr(item, "readiness_status", "") or "PENDING").strip().upper()
    tone = "green" if readiness_status == "READY" else "yellow"
    return _badge(f"ready {readiness_status.lower()}", tone)


def _push_log_summaries(db: Session, content_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not content_ids:
        return {}

    logs = (
        db.query(PushSendLog)
        .filter(PushSendLog.content_item_id.in_(content_ids))
        .order_by(PushSendLog.content_item_id.asc(), PushSendLog.created_at.desc())
        .all()
    )

    summaries: dict[int, dict[str, Any]] = {}
    for log in logs:
        summary = summaries.setdefault(
            log.content_item_id,
            {
                "total": 0,
                "manual": 0,
                "auto": 0,
                "last": None,
            },
        )
        summary["total"] += 1
        if log.mode == PushMode.manual.value:
            summary["manual"] += 1
        elif log.mode == PushMode.auto_all.value:
            summary["auto"] += 1
        if summary["last"] is None:
            summary["last"] = log

    return summaries


def _push_summary_markup(summary: dict[str, Any] | None, *, eligible: bool) -> str:
    if not summary:
        return _badge("push ready", "blue") if eligible else _badge("push n/a", "gray")

    last = summary.get("last")
    if not isinstance(last, PushSendLog):
        return _badge("push n/a", "gray")

    if last.failure_count and not last.success_count:
        last_tone = "red"
    elif last.mode == PushMode.auto_all.value:
        last_tone = "purple"
    else:
        last_tone = "blue"

    timestamp = last.created_at.strftime("%m-%d %H:%M") if last.created_at else "—"
    badges = " ".join(
        [
            _badge(f"{summary['total']} sent", "blue"),
            _badge(f"last {last.mode}", last_tone),
        ]
    )
    if last.invalid_token_count:
        badges += " " + _badge(f"{last.invalid_token_count} invalid", "red")
    return f'{badges}<div class="mt-1 text-[11px] text-slate-500">Last {timestamp}</div>'


def _push_send_button(
    *,
    content_id: int,
    admin_key: str,
    next_path: str,
    compact: bool = False,
) -> str:
    button_cls = (
        "w-full px-3 py-1.5 bg-sky-600 text-white text-sm rounded hover:bg-sky-700"
        if not compact
        else "px-2 py-1 text-xs bg-sky-600 text-white rounded hover:bg-sky-700"
    )
    return f"""
    <form method="post" action="/api/v1/admin/ui/push/send/{content_id}?key={admin_key}">
      <input type="hidden" name="next" value="{_esc(next_path)}">
      <button type="submit" class="{button_cls}">Send push now</button>
    </form>"""


# ---------------------------------------------------------------------------
# GET /admin/ui/ → redirect to login or dashboard
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def ui_root(is_authenticated: bool = Depends(_has_admin_ui_auth)):
    target = "/api/v1/admin/ui/dashboard" if is_authenticated else "/api/v1/admin/ui/login"
    return RedirectResponse(target, status_code=302)


@router.get("/login", response_class=HTMLResponse)
def ui_login_form(
    flash: Optional[str] = Query(None),
    next_url: Optional[str] = Query(None, alias="next"),
    is_authenticated: bool = Depends(_has_admin_ui_auth),
):
    if is_authenticated:
        target = _resolve_ui_url(
            admin_key="",
            fallback_path="/api/v1/admin/ui/dashboard",
            next_url=next_url,
        )
        return RedirectResponse(target, status_code=302)

    flash_html = ""
    if flash:
        cls = (
            "bg-rose-100 text-rose-900"
            if "invalid" in flash.lower()
            else "bg-slate-100 text-slate-700"
        )
        flash_html = f'<div class="mb-4 rounded-2xl px-4 py-3 text-sm {cls}">{_esc(flash)}</div>'

    next_value = _esc(next_url or "/api/v1/admin/ui/dashboard")
    body = f"""
    <div class="mx-auto max-w-md">
      <div class="glass-panel rounded-[2rem] p-8">
        <p class="panel-kicker mb-3">Admin Access</p>
        <h1 class="text-3xl font-semibold tracking-tight text-slate-950">Sign in</h1>
        <p class="mt-2 text-sm text-slate-600">
          Use the configured admin API key. The browser stores only an HTTP-only session cookie.
        </p>
        {flash_html}
        <form method="post" action="/api/v1/admin/ui/login" class="mt-6 space-y-4">
          <input type="hidden" name="next" value="{next_value}">
          <div>
            <label class="mb-1 block text-sm font-medium text-slate-700">Admin API key</label>
            <input
              type="password"
              name="admin_key"
              required
              autocomplete="current-password"
              class="w-full rounded-2xl border border-slate-300 px-4 py-3 text-sm text-slate-900 focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-200"
            >
          </div>
          <button
            type="submit"
            class="inline-flex w-full items-center justify-center rounded-2xl bg-slate-950 px-4 py-3 text-sm font-medium text-white hover:bg-slate-800"
          >
            Continue
          </button>
        </form>
      </div>
    </div>"""
    return _base(body, show_nav=False)


@router.post("/login")
def ui_login(
    admin_key: str = Form(...),
    next_path: str = Form("/api/v1/admin/ui/dashboard", alias="next"),
):
    if not is_admin_key_configured():
        raise HTTPException(status_code=401, detail="Admin endpoints disabled")

    if not is_valid_admin_key(admin_key):
        target = _add_flash("/api/v1/admin/ui/login", "Invalid admin key")
        return RedirectResponse(target, status_code=303)

    target = _resolve_ui_url(
        admin_key="",
        fallback_path="/api/v1/admin/ui/dashboard",
        next_url=next_path,
    )
    response = RedirectResponse(target, status_code=303)
    _set_admin_ui_cookie(response)
    return response


@router.post("/logout")
def ui_logout():
    response = RedirectResponse("/api/v1/admin/ui/login", status_code=303)
    response.delete_cookie(_ADMIN_UI_COOKIE_NAME, path=_ADMIN_UI_PREFIX)
    response.headers["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# GET /admin/ui/dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard", response_class=HTMLResponse)
def ui_dashboard(
    day: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_ui_auth),
):
    from sqlalchemy import and_, func, or_

    from app.core.feature_flags import FeatureFlags
    from app.models.signal import SignalURL
    from app.models.video_source import VideoSourceProfile
    from app.services.inventory_service import Surface, get_pipeline_counts
    from app.services.tiered_feed_service import get_cached_tiered_feed
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
    now = datetime.utcnow()
    window_start = now - timedelta(days=7)
    video_supply = compute_video_supply_metrics(db)
    video_lanes = compute_video_lane_metrics(db, hours=24)
    feature_flags = FeatureFlags()
    hybrid_video_rerank = feature_flags.is_enabled("video_hybrid_rerank")

    def _fmt_delta(value: Optional[float]) -> str:
        if value is None:
            return "—"
        prefix = "+" if value > 0 else ""
        return f"{prefix}{value}"

    def _fmt_hours(value: Optional[float]) -> str:
        if value is None:
            return "—"
        return f"{round(float(value), 1)}h"

    def _fmt_dt(value: Optional[datetime]) -> str:
        return value.strftime("%Y-%m-%d %H:%M UTC") if value else "—"

    def _health_color(state: str) -> str:
        return {
            "healthy": "green",
            "imbalanced": "red",
            "needs_refresh": "yellow",
            "warming_up": "yellow",
            "degraded": "red",
        }.get(state, "gray")

    def _video_kpi_card(surface: str, label: str) -> str:
        metrics = video_supply["surfaces"][surface]
        fresh_delta = metrics["deltas"]["fresh_inventory_24h"]
        dominance_delta = metrics["deltas"]["dominant_channel_pct_top20"]
        window_label = metrics["inventory_window_label"]
        state = metrics["inventory_state"].replace("_", " ")
        issues = metrics.get("issues") or []
        issue_hint = f" | issue: {issues[0]}" if issues else ""
        return _stat_card(
            label,
            str(metrics["fresh_inventory_window"]),
            (
                f"state {state} | "
                f"refresh {metrics['recent_refresh_count']}/{metrics['recent_refresh_threshold']} "
                f"in {metrics['refresh_window_hours']}h{issue_hint} | "
                f"rolling window {window_label} | "
                f"median age {metrics['median_age_top20_hours'] or '—'}h | "
                f"distinct channels {metrics['distinct_active_channels_window']} | "
                f"baseline {video_supply.get('baseline_tag') or '—'} {_fmt_delta(fresh_delta['baseline'])} | "
                f"24h {_fmt_delta(fresh_delta['vs_24h'])} | "
                f"7d {_fmt_delta(fresh_delta['vs_7d'])} | "
                f"dominance {metrics['dominant_channel_pct_top20']}% ({_fmt_delta(dominance_delta['vs_24h'])})"
            ),
            _health_color(metrics["inventory_state"]),
        )

    def _pipeline_card(label: str, candidate: int, promoted: int, tone: str) -> str:
        total = candidate + promoted
        pct = round(promoted / total * 100) if total else 0
        bar_color = {
            "blue": "bg-sky-500",
            "green": "bg-emerald-500",
            "purple": "bg-violet-500",
        }.get(tone, "bg-slate-500")
        return f"""
        <div class="glass-panel rounded-[1.6rem] p-5">
          <div class="flex items-start justify-between gap-4">
            <div>
              <div class="panel-kicker">{label}</div>
              <div class="mt-2 text-3xl font-semibold tracking-tight text-slate-950">{promoted}</div>
              <div class="mt-1 text-sm text-slate-500">promoted in the last 48 hours</div>
            </div>
            {_badge(f"{candidate} candidate", "yellow")}
          </div>
          <div class="mt-5">
            <div class="mb-2 flex items-center justify-between text-xs font-medium uppercase tracking-[0.16em] text-slate-500">
              <span>Promotion rate</span>
              <span>{pct}%</span>
            </div>
            <div class="h-2 overflow-hidden rounded-full bg-slate-200">
              <div class="{bar_color} h-2 rounded-full" style="width:{pct}%"></div>
            </div>
            <div class="mt-3 text-xs text-slate-500">{total} total items through this stage in the last 48 hours.</div>
          </div>
        </div>"""

    def _sample_item_card(item: dict) -> str:
        age_seconds = item.get("published_age_seconds")
        age_hours = (
            f"{round(float(age_seconds) / 3600, 1)}h old"
            if isinstance(age_seconds, (int, float))
            else None
        )
        lane = _lane_bucket(item.get("acquisition_lane") or item.get("discovered_via"))
        return f"""
        <div class="rounded-2xl border border-slate-200 bg-white/80 p-3">
          <div class="flex flex-wrap items-center gap-2">
            {_badge(f"Tier {item.get('freshness_tier') or '?'}", "blue")}
            {_badge(lane, "purple" if lane == "discovery" else "gray")}
          </div>
          <div class="mt-3 text-sm font-medium leading-6 text-slate-900">{_esc((item.get("title") or "Untitled")[:96])}</div>
          <div class="mt-2 text-xs text-slate-500">{_esc(item.get("source") or "Unknown")}{f" · {age_hours}" if age_hours else ""}</div>
        </div>"""

    def _live_surface_card(
        label: str, surface_key: str, snapshot: dict, duration_limit: Optional[int] = None
    ) -> str:
        metrics = video_supply["surfaces"][surface_key]
        total_items = snapshot["item_count"]
        discovery_items = snapshot["lane_counts"].get("discovery", 0)
        discovery_share = round(discovery_items / max(total_items, 1) * 100) if total_items else 0
        source_badges = "".join(
            _badge(f"{status} {count}", _source_status_tone(status))
            for status, count in sorted(
                snapshot["source_status_counts"].items(),
                key=lambda item: (-item[1], item[0]),
            )[:4]
        ) or _badge("No source tags", "gray")
        lane_badges = "".join(
            _badge(
                f"{bucket} {count}",
                "purple" if bucket == "discovery" else "gray",
            )
            for bucket, count in sorted(
                snapshot["lane_counts"].items(),
                key=lambda item: (-item[1], item[0]),
            )
        ) or _badge("No lane mix", "gray")
        supply_tone = "green" if metrics["is_healthy"] else "yellow"
        duplicate_summary = (
            f"{snapshot['adjacent_duplicates']} adjacent"
            f" · {'yes' if snapshot['boundary_duplicate'] else 'no'} boundary repeat"
        )
        rows = [
            (
                "Inventory state",
                _badge(
                    snapshot["state"].replace("_", " "), _health_color(metrics["inventory_state"])
                ),
                f"{metrics['fresh_inventory_window']} promoted items in the rolling window",
            ),
            (
                "Lane mix in sampled feed",
                lane_badges,
                f"{discovery_items} discovery items surfaced in the first {total_items}",
            ),
            (
                "Source profile states",
                source_badges,
                "Current governance status for channels represented in the sampled window",
            ),
            (
                "Duplicate guard",
                duplicate_summary,
                "Measures adjacent duplicates and page-boundary repeats across the first 40 items",
            ),
        ]
        if duration_limit is not None:
            rows.insert(
                2,
                (
                    "Format integrity",
                    _badge(
                        f"{snapshot['duration_violations']} violations",
                        "red" if snapshot["duration_violations"] else "green",
                    ),
                    f"{snapshot['known_duration_count']} known durations, max {snapshot['max_known_duration'] or '—'}s, limit {duration_limit}s",
                ),
            )
        issues_html = (
            "".join(
                f'<p class="text-sm leading-6 text-slate-600">{_esc(issue)}</p>'
                for issue in (metrics.get("issues") or [])[:3]
            )
            or '<p class="text-sm leading-6 text-slate-500">No active supply issues.</p>'
        )
        sample_html = "".join(_sample_item_card(item) for item in snapshot["sample_items"][:4]) or (
            '<div class="rounded-2xl border border-dashed border-slate-300 bg-white/70 p-4 text-sm text-slate-500">No surfaced items yet.</div>'
        )
        return f"""
        <div class="rounded-[1.6rem] border border-slate-200/80 bg-white/70 p-5 shadow-[0_20px_45px_-36px_rgba(15,23,42,0.6)]">
          <div class="mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div class="panel-kicker">{label}</div>
              <div class="mt-2 flex flex-wrap items-center gap-2">
                {_badge(metrics["inventory_state"].replace("_", " "), _health_color(metrics["inventory_state"]))}
                {_badge(f"{discovery_share}% discovery", "purple" if discovery_share else "gray")}
              </div>
            </div>
            <div class="text-sm text-slate-500">Sampled from page 1 and page 2 of the live feed.</div>
          </div>
          <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {_mini_metric("Page 1 remaining", str(snapshot["page_one_remaining"]), "7-day promoted items after the first page", "blue")}
            {_mini_metric("Page 2 remaining", str(snapshot["page_two_remaining"]), "remaining after the first 40 items", "slate")}
            {_mini_metric("Unique channels", str(snapshot["unique_channels"]), "represented in the first 40", "green")}
            {_mini_metric("Discovery share", f"{discovery_share}%", f"{discovery_items}/{max(total_items, 1)} items", "purple")}
            {_mini_metric("Refresh gate", f"{metrics['recent_refresh_count']}/{metrics['recent_refresh_threshold']}", f"in the last {metrics['refresh_window_hours']} hours", supply_tone)}
            {_mini_metric("Reservoir", f"{metrics['reservoir_count']}/{metrics['reservoir_threshold']}", "rolling pool behind the feed", supply_tone)}
          </div>
          <div class="mt-5">{_data_rows(rows)}</div>
          <div class="mt-5 rounded-[1.4rem] border border-slate-200 bg-slate-50/80 p-4">
            <div class="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Active issues</div>
            {issues_html}
          </div>
          <div class="mt-5 grid gap-3 sm:grid-cols-2">{sample_html}</div>
        </div>"""

    def _mix_surface_card(surface_key: str, label: str, tone: str) -> str:
        counts = mix_counts[surface_key]
        status_counts = promoted_source_status[surface_key]
        total = sum(counts.values())
        discovery = counts.get("discovery", 0)
        curated = counts.get("curated", 0)
        discovery_share = round(discovery / max(total, 1) * 100) if total else 0
        status_badges = "".join(
            _badge(f"{status} {count}", _source_status_tone(status))
            for status, count in sorted(
                status_counts.items(), key=lambda item: (-item[1], item[0])
            )[:4]
        ) or _badge("No source statuses", "gray")
        metrics = video_supply["surfaces"][surface_key]
        return f"""
        <div class="rounded-[1.5rem] border border-slate-200 bg-white/75 p-4">
          <div class="flex items-start justify-between gap-4">
            <div>
              <div class="panel-kicker">{label}</div>
              <div class="mt-2 text-2xl font-semibold tracking-tight text-slate-950">{total}</div>
              <div class="mt-1 text-sm text-slate-500">promoted items published in the last 7 days</div>
            </div>
            {_badge(f"{discovery_share}% discovery", tone if discovery else "gray")}
          </div>
          <div class="mt-4 grid gap-3 sm:grid-cols-3">
            {_mini_metric("Curated", str(curated), "promoted in the last 7 days", "slate")}
            {_mini_metric("Discovery", str(discovery), "search + trending + discovery", tone)}
            {_mini_metric("Refresh", f"{metrics['recent_refresh_count']}/{metrics['recent_refresh_threshold']}", f"{metrics['inventory_state'].replace('_', ' ')}", "green" if metrics["is_healthy"] else "yellow")}
          </div>
          <div class="mt-4">
            <div class="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Source statuses in the promoted pool</div>
            <div class="mt-2 flex flex-wrap gap-2">{status_badges}</div>
          </div>
        </div>"""

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
        .filter(SignalURL.first_seen_at >= now - timedelta(hours=24))
        .scalar()
        or 0
    )
    signal_by_status = (
        db.query(SignalURL.enqueue_status, func.count(SignalURL.id))
        .group_by(SignalURL.enqueue_status)
        .all()
    )
    sig_counts = {r[0].value: r[1] for r in signal_by_status}
    sig_ingested = sig_counts.get("ingested", sig_counts.get("INGESTED", 0))
    sig_duplicate = sig_counts.get("duplicate", sig_counts.get("DUPLICATE", 0))
    sig_rejected = sig_counts.get("rejected", sig_counts.get("REJECTED", 0))
    sig_pending = sig_counts.get("pending", sig_counts.get("PENDING", 0))

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
            ContentItem.published_at >= now - timedelta(hours=48),
        )
        .order_by(ContentItem.promotion_score.desc().nullslast())
        .limit(5)
        .all()
    )
    dashboard_next = f"/api/v1/admin/ui/dashboard?{urlencode({'day': day_str})}"
    pending_cards_html = ""
    pending_rows_html = ""
    for p in pending_promo:
        score = f"{p.promotion_score:.3f}" if p.promotion_score else "—"
        discovered_via = _esc(p.discovered_via or "—")
        item_type = p.type.value if p.type else ""
        pending_cards_html += f"""
        <article class="rounded-[1.5rem] border border-slate-200 bg-white/80 p-4">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="text-xs font-medium uppercase tracking-[0.16em] text-slate-500">#{p.id} · {item_type}</div>
              <a href="/api/v1/admin/ui/detail/{p.id}?key={admin_key}" class="mt-2 block text-sm font-semibold leading-6 text-slate-900 hover:text-sky-700">{_esc((p.title or "")[:120])}</a>
            </div>
            {_badge(f"score {score}", "blue")}
          </div>
          <div class="mt-3 flex flex-wrap gap-2">
            {_badge(discovered_via, "gray")}
          </div>
          <div class="mt-4 flex flex-wrap gap-2">
            <form method="post" action="/api/v1/admin/ui/action/{p.id}/approve-publish?key={admin_key}">
              <input type="hidden" name="next" value="{dashboard_next}">
              <input type="hidden" name="boost_level" value="3">
              <button class="inline-flex items-center rounded-full bg-emerald-600 px-3 py-2 text-xs font-medium text-white hover:bg-emerald-700">Publish top</button>
            </form>
            <a href="/api/v1/admin/ui/detail/{p.id}?key={admin_key}" class="inline-flex items-center rounded-full border border-slate-300 px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50">Review</a>
          </div>
        </article>"""
        pending_rows_html += f"""
        <tr class="border-b border-slate-200/80 last:border-0 hover:bg-slate-50/70">
          <td class="px-3 py-3 text-sm">
            <a href="/api/v1/admin/ui/detail/{p.id}?key={admin_key}" class="font-medium text-sky-700 hover:underline">{p.id}</a>
          </td>
          <td class="max-w-xs px-3 py-3 text-sm text-slate-800">{_esc((p.title or "")[:100])}</td>
          <td class="px-3 py-3 text-sm">{item_type}</td>
          <td class="px-3 py-3 text-sm font-mono">{score}</td>
          <td class="px-3 py-3 text-sm">{discovered_via}</td>
          <td class="px-3 py-3">
            <div class="flex gap-1">
              <form method="post" action="/api/v1/admin/ui/action/{p.id}/approve-publish?key={admin_key}">
                <input type="hidden" name="next" value="{dashboard_next}">
                <input type="hidden" name="boost_level" value="3">
                <button class="rounded-full bg-emerald-600 px-3 py-2 text-xs font-medium text-white hover:bg-emerald-700">Publish top</button>
              </form>
              <a href="/api/v1/admin/ui/detail/{p.id}?key={admin_key}"
                 class="rounded-full border border-slate-300 px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50">
                Review
              </a>
            </div>
          </td>
        </tr>"""

    if not pending_rows_html:
        pending_rows_html = '<tr><td colspan="6" class="px-3 py-6 text-center text-sm text-slate-400">No pending candidates</td></tr>'
    if not pending_cards_html:
        pending_cards_html = '<div class="rounded-[1.5rem] border border-dashed border-slate-300 bg-white/70 p-5 text-sm text-slate-500">No pending candidates in the last 48 hours.</div>'

    # Day summary cards
    day_rows_for_panel: list[tuple[str, str, str | None]] = []
    for t in ["ARTICLE", "VIDEO", "REEL"]:
        cand = day_summary.get(t, {}).get("CANDIDATE", 0)
        prom = day_summary.get(t, {}).get("PROMOTED", 0)
        total_t = cand + prom
        day_rows_for_panel.append(
            (
                t.title(),
                f"{prom} promoted · {cand} candidate",
                f"{total_t} total published on {day_str}",
            )
        )

    video_page_one, _, video_meta_one = get_cached_tiered_feed(
        db,
        Surface.VIDEOS,
        limit=20,
        offset=0,
        hybrid_video_rerank=hybrid_video_rerank,
    )
    video_page_two, _, video_meta_two = get_cached_tiered_feed(
        db,
        Surface.VIDEOS,
        limit=20,
        offset=20,
        hybrid_video_rerank=hybrid_video_rerank,
    )
    reel_page_one, _, reel_meta_one = get_cached_tiered_feed(
        db,
        Surface.REELS,
        limit=20,
        offset=0,
        hybrid_video_rerank=hybrid_video_rerank,
    )
    reel_page_two, _, reel_meta_two = get_cached_tiered_feed(
        db,
        Surface.REELS,
        limit=20,
        offset=20,
        hybrid_video_rerank=hybrid_video_rerank,
    )

    video_window = _summarize_feed_window(
        video_page_one,
        video_page_two,
        remaining_count=(
            video_meta_two.remaining_window_count
            if video_page_two
            else video_meta_one.remaining_window_count
        ),
    )
    video_window["page_one_remaining"] = int(video_meta_one.remaining_window_count or 0)
    video_window["page_two_remaining"] = int(
        video_meta_two.remaining_window_count
        if video_page_two
        else video_meta_one.remaining_window_count
    )

    reels_window = _summarize_feed_window(
        reel_page_one,
        reel_page_two,
        remaining_count=(
            reel_meta_two.remaining_window_count
            if reel_page_two
            else reel_meta_one.remaining_window_count
        ),
        duration_limit=settings.REEL_MAX_DURATION_SECONDS,
    )
    reels_window["page_one_remaining"] = int(reel_meta_one.remaining_window_count or 0)
    reels_window["page_two_remaining"] = int(
        reel_meta_two.remaining_window_count
        if reel_page_two
        else reel_meta_one.remaining_window_count
    )

    mix_rows = (
        db.query(
            ContentItem.type,
            ContentItem.acquisition_lane,
            ContentItem.discovered_via,
            ContentItem.source_status,
        )
        .filter(
            ContentItem.type.in_([ContentType.VIDEO, ContentType.REEL]),
            ContentItem.curation_status == ContentStatus.PROMOTED,
            ContentItem.is_suppressed.is_(False),
            ContentItem.published_at >= window_start,
        )
        .all()
    )
    mix_counts = {"videos": Counter(), "reels": Counter()}
    promoted_source_status = {"videos": Counter(), "reels": Counter()}
    for content_type, acquisition_lane, discovered_via, source_status in mix_rows:
        surface_key = "reels" if content_type == ContentType.REEL else "videos"
        mix_counts[surface_key][_lane_bucket(acquisition_lane or discovered_via)] += 1
        promoted_source_status[surface_key][str(source_status or "unknown")] += 1

    source_status_counts = Counter(
        {
            status: count
            for status, count in db.query(
                VideoSourceProfile.status,
                func.count(VideoSourceProfile.channel_id),
            )
            .group_by(VideoSourceProfile.status)
            .all()
        }
    )
    recent_source_actions = (
        db.query(VideoSourceProfile)
        .filter(
            VideoSourceProfile.status_changed_at.isnot(None),
            VideoSourceProfile.status_changed_at >= window_start,
        )
        .order_by(VideoSourceProfile.status_changed_at.desc())
        .limit(8)
        .all()
    )

    reel_base = [
        ContentItem.type == ContentType.REEL,
        ContentItem.curation_status == ContentStatus.PROMOTED,
        ContentItem.is_suppressed.is_(False),
        ContentItem.published_at >= window_start,
    ]
    reel_total_count = db.query(func.count(ContentItem.id)).filter(*reel_base).scalar() or 0
    reel_known_count = (
        db.query(func.count(ContentItem.id))
        .filter(*reel_base, ContentItem.duration_seconds.isnot(None))
        .scalar()
        or 0
    )
    reel_duration_violations = (
        db.query(func.count(ContentItem.id))
        .filter(
            *reel_base,
            ContentItem.duration_seconds.isnot(None),
            ContentItem.duration_seconds > settings.REEL_MAX_DURATION_SECONDS,
        )
        .scalar()
        or 0
    )
    reel_max_duration = (
        db.query(func.max(ContentItem.duration_seconds))
        .filter(*reel_base, ContentItem.duration_seconds.isnot(None))
        .scalar()
    )
    reel_unknown_count = max(0, reel_total_count - reel_known_count)

    lane_snapshot_rows: list[tuple[str, str, str | None]] = []
    for surface_key in ("videos", "reels"):
        ranked_lanes = sorted(
            video_lanes["surfaces"].get(surface_key, []),
            key=_lane_metric_sort_key,
            reverse=True,
        )
        for row in ranked_lanes[:3]:
            lane_snapshot_rows.append(
                (
                    f"{surface_key.title()} · {row['lane']}",
                    f"{row['promoted']}/{row['candidates']} ({row['promotion_rate']}%)",
                    f"median {_fmt_hours(row['median_promoted_age'])} · channels {row['distinct_promoted_channels']} · dup {row['duplicate_rejection_rate']}% · clickbait {row['clickbait_rejection_rate']}%",
                )
            )

    top_sources_body = (
        _data_rows(
            [
                (source or "Unknown", str(count), "promoted items on the selected day")
                for source, count in source_rows
            ]
        )
        if source_rows
        else '<p class="text-sm leading-6 text-slate-500">No promoted sources for this day.</p>'
    )

    signal_queue_body = _data_rows(
        [
            ("Pending", _badge(str(sig_pending), "yellow"), "waiting to be ingested"),
            ("Ingested", _badge(str(sig_ingested), "green"), "accepted into the pipeline"),
            ("Duplicates", _badge(str(sig_duplicate), "gray"), "skipped by dedupe"),
            ("Rejected", _badge(str(sig_rejected), "red"), "filtered out before ingest"),
        ]
    )

    day_bucket_body = _data_rows(day_rows_for_panel)

    source_status_badges = "".join(
        _badge(f"{status} {count}", _source_status_tone(status))
        for status, count in sorted(
            source_status_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ) or _badge("No source profiles", "gray")

    source_action_cards = "".join(
        f"""
        <div class="rounded-2xl border border-slate-200 bg-white/80 p-4">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="text-sm font-semibold leading-6 text-slate-900">{_esc(profile.channel_name)}</div>
              <div class="truncate text-xs font-mono text-slate-500">{_esc(profile.channel_id)}</div>
            </div>
            {_badge(profile.status, _source_status_tone(profile.status))}
          </div>
          <div class="mt-3 text-xs leading-5 text-slate-500">
            Changed {_fmt_dt(profile.status_changed_at)}
            {f" · probation until {_fmt_dt(profile.probation_until)}" if profile.probation_until else ""}
          </div>
        </div>"""
        for profile in recent_source_actions
    ) or (
        '<div class="rounded-2xl border border-dashed border-slate-300 bg-white/70 p-4 text-sm text-slate-500">No source status changes recorded in the last 7 days.</div>'
    )

    lane_snapshot_body = (
        _data_rows(lane_snapshot_rows)
        if lane_snapshot_rows
        else '<p class="text-sm leading-6 text-slate-500">No discovery lane runs captured in the selected window.</p>'
    )

    reels_supply = video_supply["surfaces"]["reels"]
    reels_refresh_gap = max(
        0,
        reels_supply["recent_refresh_threshold"] - reels_supply["recent_refresh_count"],
    )
    reels_reservoir_gap = max(
        0,
        reels_supply["reservoir_threshold"] - reels_supply["reservoir_count"],
    )
    reel_issues_html = (
        "".join(
            f'<p class="text-sm leading-6 text-slate-600">{_esc(issue)}</p>'
            for issue in (reels_supply.get("issues") or [])[:4]
        )
        or '<p class="text-sm leading-6 text-slate-500">No active reel health issues.</p>'
    )

    # Date nav
    prev_day = (selected_date - timedelta(days=1)).isoformat()
    next_day = (selected_date + timedelta(days=1)).isoformat()
    is_today = selected_date == date.today()

    body = f"""
    <section class="mb-8 flex flex-col gap-5 xl:flex-row xl:items-end xl:justify-between">
      <div class="max-w-3xl">
        <p class="panel-kicker">Operations Dashboard</p>
        <h1 class="mt-2 text-3xl font-semibold tracking-tight text-slate-950 sm:text-4xl">Continuous 7-day feed monitor</h1>
        <p class="mt-3 text-sm leading-7 text-slate-600">
          Live supply depth, discovery contribution, source health, and editorial load for the video and reel surfaces.
        </p>
      </div>
      <div class="glass-panel rounded-[1.7rem] p-4 sm:p-5">
        <div class="flex flex-wrap items-center gap-2">
          <a href="?key={admin_key}&day={
        prev_day
    }" class="inline-flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-700 hover:bg-slate-50">←</a>
          <form method="get" class="flex flex-wrap items-center gap-2">
            <input type="hidden" name="key" value="{admin_key}">
            <input
              type="date"
              name="day"
              value="{day_str}"
              class="rounded-full border border-slate-300 bg-white px-4 py-2 text-sm text-slate-700 shadow-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
              onchange="this.form.submit()"
            >
          </form>
          {
        f'<a href="?key={admin_key}&day={next_day}" class="inline-flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-700 hover:bg-slate-50">→</a>'
        if not is_today
        else ""
    }
          {_badge("Today", "blue") if is_today else _badge(day_str, "gray")}
        </div>
        <div class="mt-4 flex flex-wrap gap-2">
          <a href="/api/v1/admin/ui/video-lanes?key={
        admin_key
    }" class="inline-flex items-center rounded-full bg-slate-950 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800">Lane performance</a>
          <a href="/api/v1/admin/ui/video-sources?key={
        admin_key
    }" class="inline-flex items-center rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">Channel health</a>
          <a href="/api/v1/admin/ui/review?key={
        admin_key
    }" class="inline-flex items-center rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">Review queue</a>
        </div>
      </div>
    </section>

    <div class="mb-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {_stat_card("Signal URLs seen (24h)", str(signal_seen), "", "purple")}
      {_stat_card("Ingested → candidate", str(sig_ingested), "", "blue")}
      {_stat_card("Duplicates skipped", str(sig_duplicate), "", "gray")}
      {_stat_card("Avg promotion score", str(avg_score), f"promoted items on {day_str}", "green")}
    </div>

    <div class="mb-8 grid gap-4 lg:grid-cols-3">
      {_pipeline_card("Articles", art_cand, art_prom, "blue")}
      {_pipeline_card("Videos", vid_cand, vid_prom, "green")}
      {_pipeline_card("Reels", ree_cand, ree_prom, "purple")}
    </div>

    <div class="mb-8 grid gap-4 lg:grid-cols-2">
      {_video_kpi_card("videos", "Videos rolling inventory")}
      {_video_kpi_card("reels", "Reels rolling inventory")}
    </div>
    {
        _panel(
            "Live Feed Experience",
            f'''
        <div class="grid gap-6 xl:grid-cols-2">
          {_live_surface_card("Videos", "videos", video_window)}
          {_live_surface_card("Reels", "reels", reels_window, duration_limit=settings.REEL_MAX_DURATION_SECONDS)}
        </div>
        ''',
            subtitle="These cards mirror what the app is actually serving on page 1 and page 2, rather than only showing backend supply counters.",
        )
    }

    <div class="mb-8 grid gap-6 xl:grid-cols-2">
      {
        _panel(
            "Acquisition Mix",
            f'''
          <div class="grid gap-4 lg:grid-cols-2">
            {_mix_surface_card("videos", "Videos", "blue")}
            {_mix_surface_card("reels", "Reels", "purple")}
          </div>
          <div class="mt-5 rounded-[1.5rem] border border-slate-200 bg-white/75 p-4">
            <div class="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div class="panel-kicker">Lane snapshot (24h)</div>
              <a href="/api/v1/admin/ui/video-lanes?key={admin_key}" class="text-sm font-medium text-sky-700 hover:underline">Detailed lane view →</a>
            </div>
            {lane_snapshot_body}
          </div>
          ''',
            subtitle="Curated and discovery should both contribute. The lane snapshot shows which discovery lanes are actually converting.",
        )
    }
      {
        _panel(
            "Source Governance",
            f'''
          <div class="rounded-[1.5rem] border border-slate-200 bg-white/75 p-4">
            <div class="panel-kicker">Current status mix</div>
            <div class="mt-3 flex flex-wrap gap-2">{source_status_badges}</div>
          </div>
          <div class="mt-5 grid gap-3">{source_action_cards}</div>
          ''',
            subtitle="Recent source transitions and the current distribution of discovery, rotation, and core channels.",
            action=f'<a href="/api/v1/admin/ui/video-sources?key={admin_key}" class="text-sm font-medium text-sky-700 hover:underline">Channel health →</a>',
        )
    }
    </div>

    <div class="mb-8 grid gap-6 xl:grid-cols-[0.95fr,1.05fr]">
      {
        _panel(
            "Reels Integrity",
            f'''
          <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {
                _mini_metric(
                    "Known durations",
                    f"{reel_known_count}/{reel_total_count}",
                    f"{reel_unknown_count} unknown",
                    "blue",
                )
            }
            {
                _mini_metric(
                    "Max known duration",
                    f"{reel_max_duration or '—'}s",
                    f"limit {settings.REEL_MAX_DURATION_SECONDS}s",
                    "purple",
                )
            }
            {
                _mini_metric(
                    "Violations",
                    str(reel_duration_violations),
                    "promoted reels over the duration cap",
                    "red" if reel_duration_violations else "green",
                )
            }
            {
                _mini_metric(
                    "Refresh gap",
                    str(reels_refresh_gap),
                    f"reservoir gap {reels_reservoir_gap}",
                    "yellow" if reels_refresh_gap or reels_reservoir_gap else "green",
                )
            }
          </div>
          <div class="mt-5">{
                _data_rows(
                    [
                        (
                            "Inventory state",
                            _badge(
                                reels_supply["inventory_state"].replace("_", " "),
                                _health_color(reels_supply["inventory_state"]),
                            ),
                            f"refresh {reels_supply['recent_refresh_count']}/{reels_supply['recent_refresh_threshold']} · reservoir {reels_supply['reservoir_count']}/{reels_supply['reservoir_threshold']}",
                        ),
                        (
                            "Dominant channel share",
                            f"{reels_supply['dominant_channel_pct_top20']}%",
                            "share of the top 20 reel feed currently occupied by the largest creator",
                        ),
                        (
                            "Median age top 20",
                            _fmt_hours(reels_supply["median_age_top20_hours"]),
                            "fresh reels should stay young without collapsing discovery mix",
                        ),
                    ]
                )
            }</div>
          <div class="mt-5 rounded-[1.5rem] border border-slate-200 bg-white/75 p-4">
            <div class="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Open reel issues</div>
            {reel_issues_html}
          </div>
          ''',
            subtitle="Reels are the most fragile surface. This card tracks duration integrity and whether the 7-day reservoir is deep enough to avoid caught-up states.",
            tone="purple",
        )
    }
      {
        _panel(
            "Day Bucket",
            f'''
          <div class="grid gap-4 lg:grid-cols-3">
            <div class="rounded-[1.5rem] border border-slate-200 bg-white/75 p-4">
              <div class="mb-3 panel-kicker">Published on {day_str}</div>
              {day_bucket_body}
            </div>
            <div class="rounded-[1.5rem] border border-slate-200 bg-white/75 p-4">
              <div class="mb-3 panel-kicker">Signal queue</div>
              {signal_queue_body}
            </div>
            <div class="rounded-[1.5rem] border border-slate-200 bg-white/75 p-4">
              <div class="mb-3 panel-kicker">Top promoted sources</div>
              {top_sources_body}
            </div>
          </div>
          ''',
            subtitle="The selected day still matters for editorial operations even though video and reel serving now uses rolling freshness and reservoir windows.",
            tone="green",
        )
    }
    </div>

    {
        _panel(
            "Editorial Queue",
            f'''
        <div class="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div class="text-sm leading-6 text-slate-600">Highest-scoring candidates from the last 48 hours that still need editorial attention.</div>
          <a href="/api/v1/admin/ui/review?key={admin_key}" class="text-sm font-medium text-sky-700 hover:underline">Open review queue →</a>
        </div>
        <div class="grid gap-3 md:hidden">{pending_cards_html}</div>
        <div class="table-shell hidden md:block">
          <table class="min-w-full overflow-hidden rounded-[1.5rem] bg-white/80">
            <thead class="bg-slate-50/90">
              <tr class="text-xs uppercase tracking-[0.16em] text-slate-500">
                <th class="px-3 py-3 text-left">ID</th>
                <th class="px-3 py-3 text-left">Title</th>
                <th class="px-3 py-3 text-left">Type</th>
                <th class="px-3 py-3 text-left">Score</th>
                <th class="px-3 py-3 text-left">Discovered via</th>
                <th class="px-3 py-3 text-left">Action</th>
              </tr>
            </thead>
            <tbody>{pending_rows_html}</tbody>
          </table>
        </div>
        ''',
            subtitle="This queue stays separate from the supply metrics so operators can distinguish feed health from manual editorial load.",
            tone="yellow",
        )
    }"""
    return _base(body, key=admin_key, active="dashboard")


# ---------------------------------------------------------------------------
# GET /admin/ui/video-lanes
# ---------------------------------------------------------------------------


@router.get("/video-lanes", response_class=HTMLResponse)
def ui_video_lanes(
    hours: int = Query(24, ge=1, le=24 * 14),
    view: str = Query("lane"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_ui_auth),
):
    from app.services.video_metrics_service import compute_video_lane_metrics

    normalized_view = "query" if view == "query" else "lane"
    payload = compute_video_lane_metrics(
        db,
        hours=hours,
        breakdown="query" if normalized_view == "query" else None,
    )
    label_key = "query_label" if normalized_view == "query" else "lane"
    label_title = "Query" if normalized_view == "query" else "Lane"
    count_label = "queries" if normalized_view == "query" else "lanes"
    description = (
        "Compare weighted search queries over a rolling window and see which ones are actually producing promotable inventory."
        if normalized_view == "query"
        else "Compare search, trending, and curated lanes over a rolling window and see which ones are actually producing promotable inventory."
    )

    def _surface_rows(surface: str) -> list[dict]:
        return sorted(
            payload["surfaces"].get(surface, []),
            key=lambda row: (
                int(int(row.get("candidates") or 0) > 0),
                *_lane_metric_sort_key(row),
            ),
            reverse=True,
        )

    def _format_rate(value: object) -> str:
        return "—" if value is None else f"{value}%"

    def _surface_summary(surface: str) -> dict:
        rows = _surface_rows(surface)
        candidates = sum(int(row["candidates"]) for row in rows)
        promoted = sum(int(row["promoted"]) for row in rows)
        best = rows[0] if rows else None
        return {
            "rows": rows,
            "candidates": candidates,
            "promoted": promoted,
            "lane_count": len(rows),
            "promotion_rate": round(promoted / candidates * 100, 2) if candidates > 0 else None,
            "best": best,
        }

    def _lane_card(row: dict, tone: str) -> str:
        return f"""
        <div class="rounded-[1.5rem] border border-slate-200 bg-white/80 p-4">
          <div class="flex items-start justify-between gap-3">
            <div>
              <div class="panel-kicker">{_esc(row.get(label_key, "—"))}</div>
              <div class="mt-2 text-xl font-semibold tracking-tight text-slate-950">{row["promoted"]}/{row["candidates"]}</div>
              <div class="mt-1 text-sm text-slate-500">promotion rate {_format_rate(row["promotion_rate"])}</div>
            </div>
            {_badge(f"{row['distinct_promoted_channels']} channels", tone)}
          </div>
          <div class="mt-4 grid gap-3 sm:grid-cols-2">
            {_mini_metric("Median age", f"{row['median_promoted_age'] or '—'}h", "promoted items", "slate")}
            {_mini_metric("Dup / Clickbait", f"{row['duplicate_rejection_rate']}% / {row['clickbait_rejection_rate']}%", "rejection rates", tone)}
          </div>
        </div>"""

    def _table_rows(surface: str) -> str:
        rows = _surface_rows(surface)
        if not rows:
            return '<tr><td colspan="7" class="px-3 py-6 text-center text-sm text-slate-400">No lane data in this window.</td></tr>'
        return "".join(
            f"""
            <tr class="border-b border-slate-200/80 last:border-0 hover:bg-slate-50/70">
              <td class="px-3 py-3 text-sm font-medium text-slate-800">{_esc(row.get(label_key, "—"))}</td>
              <td class="px-3 py-3 text-right text-sm">{row["candidates"]}</td>
              <td class="px-3 py-3 text-right text-sm">{row["promoted"]}</td>
              <td class="px-3 py-3 text-right text-sm">{_format_rate(row["promotion_rate"])}</td>
              <td class="px-3 py-3 text-right text-sm">{row["median_promoted_age"] or "—"}h</td>
              <td class="px-3 py-3 text-right text-sm">{row["distinct_promoted_channels"]}</td>
              <td class="px-3 py-3 text-right text-sm">{row["duplicate_rejection_rate"]}% / {row["clickbait_rejection_rate"]}%</td>
            </tr>"""
            for row in rows
        )

    def _surface_panel(label: str, surface: str, tone: str) -> str:
        summary = _surface_summary(surface)
        best = summary["best"]
        best_text = (
            f"{best.get(label_key, '—')} · {_format_rate(best['promotion_rate'])}"
            if best
            else f"No {normalized_view} data"
        )
        cards = "".join(_lane_card(row, tone) for row in summary["rows"]) or (
            f'<div class="rounded-[1.5rem] border border-dashed border-slate-300 bg-white/70 p-5 text-sm text-slate-500">No {normalized_view} data in this window.</div>'
        )
        return _panel(
            label,
            f"""
            <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {_mini_metric("Candidates", str(summary["candidates"]), f"in the last {hours} hours", "slate")}
              {_mini_metric("Promoted", str(summary["promoted"]), "made it through scoring", tone)}
              {_mini_metric("Overall rate", _format_rate(summary["promotion_rate"]), "weighted by candidates", "green" if (summary["promotion_rate"] or 0) >= 25 else "yellow")}
              {_mini_metric(f"Active {count_label}", str(summary["lane_count"]), best_text, tone)}
            </div>
            <div class="mt-5 grid gap-3 md:hidden">{cards}</div>
            <div class="table-shell mt-5 hidden md:block">
              <table class="min-w-full overflow-hidden rounded-[1.5rem] bg-white/80">
                <thead class="bg-slate-50/90">
                  <tr class="text-xs uppercase tracking-[0.16em] text-slate-500">
                    <th class="px-3 py-3 text-left">{label_title}</th>
                    <th class="px-3 py-3 text-right">Candidates</th>
                    <th class="px-3 py-3 text-right">Promoted</th>
                    <th class="px-3 py-3 text-right">Rate</th>
                    <th class="px-3 py-3 text-right">Median age</th>
                    <th class="px-3 py-3 text-right">Channels</th>
                    <th class="px-3 py-3 text-right">Dup / Clickbait</th>
                  </tr>
                </thead>
                <tbody>{_table_rows(surface)}</tbody>
              </table>
            </div>
            """,
            subtitle=f"Per-{label_title.lower()} conversion and rejection quality for the last {hours} hours.",
            tone=tone,
        )

    video_summary = _surface_summary("videos")
    reels_summary = _surface_summary("reels")

    body = f"""
    <section class="mb-8 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
      <div class="max-w-3xl">
        <p class="panel-kicker">Video Discovery Lanes</p>
        <h1 class="mt-2 text-3xl font-semibold tracking-tight text-slate-950">Lane conversion and rejection quality</h1>
        <p class="mt-3 text-sm leading-7 text-slate-600">{description}</p>
      </div>
      <form method="get" class="glass-panel flex flex-wrap items-center gap-2 rounded-[1.6rem] p-4">
        <input type="hidden" name="key" value="{admin_key}">
        <label class="text-sm font-medium text-slate-600">Window (hours)</label>
        <input type="number" min="1" max="{24 * 14}" name="hours" value="{hours}" class="w-28 rounded-full border border-slate-300 px-4 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-sky-500">
        <input type="hidden" name="view" value="{normalized_view}">
        <button class="inline-flex items-center rounded-full bg-slate-950 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800">Apply</button>
      </form>
    </section>

    <div class="mb-6 inline-flex rounded-full border border-slate-200 bg-white/80 p-1">
      <a href="/api/v1/admin/ui/video-lanes?key={admin_key}&hours={hours}&view=lane" class="rounded-full px-4 py-2 text-sm font-medium {"bg-slate-950 text-white" if normalized_view == "lane" else "text-slate-600 hover:text-slate-900"}">Lane view</a>
      <a href="/api/v1/admin/ui/video-lanes?key={admin_key}&hours={hours}&view=query" class="rounded-full px-4 py-2 text-sm font-medium {"bg-slate-950 text-white" if normalized_view == "query" else "text-slate-600 hover:text-slate-900"}">Query view</a>
    </div>

    <div class="mb-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {_stat_card("Video candidates", str(video_summary["candidates"]), f"{video_summary['lane_count']} {count_label} in the last {hours}h", "blue")}
      {_stat_card("Video promoted", str(video_summary["promoted"]), f"weighted rate {_format_rate(video_summary['promotion_rate'])}", "green")}
      {_stat_card("Reel candidates", str(reels_summary["candidates"]), f"{reels_summary['lane_count']} {count_label} in the last {hours}h", "purple")}
      {_stat_card("Reel promoted", str(reels_summary["promoted"]), f"weighted rate {_format_rate(reels_summary['promotion_rate'])}", "green")}
    </div>

    <div class="grid gap-6 xl:grid-cols-2">
      {_surface_panel("Videos", "videos", "blue")}
      {_surface_panel("Reels", "reels", "purple")}
    </div>"""
    return _base(body, key=admin_key, active="video-lanes")


# ---------------------------------------------------------------------------
# GET /admin/ui/video-sources
# ---------------------------------------------------------------------------


@router.get("/video-sources", response_class=HTMLResponse)
def ui_video_sources(
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_ui_auth),
):
    from app.services.video_metrics_service import compute_video_source_metrics

    payload = compute_video_source_metrics(db)
    rows = sorted(
        payload["sources"],
        key=lambda row: (
            {"core": 0, "rotation": 1, "discovery": 2, "paused": 3}.get(row["status"], 4),
            -float(row["score_7d"]),
            -float(row["completion_rate"]),
            row["channel_name"],
        ),
    )
    status_counts = Counter(row["status"] for row in rows)
    role_counts = Counter(row["role"] for row in rows)
    flagged_rows = sorted(
        [
            row
            for row in rows
            if row["suppression_rate"] >= 20
            or row["early_skip_rate"] >= 25
            or row["completion_rate"] <= 20
        ],
        key=lambda row: (
            max(float(row["suppression_rate"]), float(row["early_skip_rate"])),
            -float(row["completion_rate"]),
        ),
        reverse=True,
    )[:6]
    leaders = sorted(
        rows,
        key=lambda row: (
            float(row["score_7d"]),
            float(row["completion_rate"]),
            float(row["save_share_rate"]),
        ),
        reverse=True,
    )[:6]

    def _fmt_iso(value: Optional[str]) -> str:
        return value.replace("T", " ")[:16] if value else "—"

    def _source_card(row: dict) -> str:
        return f"""
        <article class="rounded-[1.5rem] border border-slate-200 bg-white/80 p-4">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="text-sm font-semibold leading-6 text-slate-900">{_esc(row["channel_name"])}</div>
              <div class="truncate text-xs font-mono text-slate-500">{_esc(row["channel_id"])}</div>
            </div>
            {_badge(row["status"], _source_status_tone(row["status"]))}
          </div>
          <div class="mt-3 flex flex-wrap gap-2">
            {_badge(row["role"], "gray")}
            {_badge(row["content_format"], "gray")}
            {_badge(row["quality_tier"], "gray")}
          </div>
          <div class="mt-4 grid gap-3 sm:grid-cols-2">
            {_mini_metric("Score 7d", str(row["score_7d"]), "source quality composite", "blue")}
            {_mini_metric("Promoted share", f"{row['promoted_share']}%", "share of sampled items promoted", "green")}
            {_mini_metric("Suppression", f"{row['suppression_rate']}%", "higher means more filtered out", "yellow" if row["suppression_rate"] >= 20 else "slate")}
            {_mini_metric("Completion", f"{row['completion_rate']}%", f"early skip {row['early_skip_rate']}%", "purple")}
          </div>
          <div class="mt-4 text-xs leading-5 text-slate-500">Save/share {row["save_share_rate"]}% · last promoted {_fmt_iso(row["last_promoted_at"])}</div>
        </article>"""

    def _table_rows() -> str:
        if not rows:
            return '<tr><td colspan="10" class="px-3 py-6 text-center text-sm text-slate-400">No source profiles found.</td></tr>'
        return "".join(
            f"""
            <tr class="border-b border-slate-200/80 last:border-0 hover:bg-slate-50/70">
              <td class="px-3 py-3 text-sm">
                <div class="font-medium text-slate-900">{_esc(row["channel_name"])}</div>
                <div class="text-xs font-mono text-slate-500">{_esc(row["channel_id"])}</div>
              </td>
              <td class="px-3 py-3 text-sm">{_badge(row["status"], _source_status_tone(row["status"]))}</td>
              <td class="px-3 py-3 text-sm">
                <div>{_esc(row["role"])}</div>
                <div class="text-xs text-slate-500">{_esc(row["content_format"])} · {_esc(row["quality_tier"])}</div>
              </td>
              <td class="px-3 py-3 text-right text-sm">{row["score_7d"]}</td>
              <td class="px-3 py-3 text-right text-sm">{row["promoted_share"]}%</td>
              <td class="px-3 py-3 text-right text-sm">{row["suppression_rate"]}%</td>
              <td class="px-3 py-3 text-right text-sm">{row["early_skip_rate"]}%</td>
              <td class="px-3 py-3 text-right text-sm">{row["completion_rate"]}%</td>
              <td class="px-3 py-3 text-right text-sm">{row["save_share_rate"]}%</td>
              <td class="px-3 py-3 text-xs text-slate-500">{_fmt_iso(row["last_promoted_at"])}</td>
            </tr>"""
            for row in rows
        )

    flagged_body = (
        _data_rows(
            [
                (
                    row["channel_name"],
                    _badge(row["status"], _source_status_tone(row["status"])),
                    f"suppression {row['suppression_rate']}% · early skip {row['early_skip_rate']}% · completion {row['completion_rate']}%",
                )
                for row in flagged_rows
            ]
        )
        if flagged_rows
        else '<p class="text-sm leading-6 text-slate-500">No source profiles currently cross the attention thresholds.</p>'
    )
    leaders_body = (
        _data_rows(
            [
                (
                    row["channel_name"],
                    f"{row['score_7d']} score · {row['completion_rate']}% completion",
                    f"{row['status']} · promoted share {row['promoted_share']}% · save/share {row['save_share_rate']}%",
                )
                for row in leaders
            ]
        )
        if leaders
        else '<p class="text-sm leading-6 text-slate-500">No source profiles available yet.</p>'
    )
    role_badges = "".join(
        _badge(f"{role} {count}", "gray")
        for role, count in sorted(role_counts.items(), key=lambda item: (-item[1], item[0]))
    ) or _badge("No roles", "gray")
    mobile_cards = "".join(_source_card(row) for row in rows) or (
        '<div class="rounded-[1.5rem] border border-dashed border-slate-300 bg-white/70 p-5 text-sm text-slate-500">No source profiles found.</div>'
    )

    body = f"""
    <section class="mb-8 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
      <div class="max-w-3xl">
        <p class="panel-kicker">Video Source Health</p>
        <h1 class="mt-2 text-3xl font-semibold tracking-tight text-slate-950">Channel governance and quality</h1>
        <p class="mt-3 text-sm leading-7 text-slate-600">Use this view to decide which channels should stay in discovery, graduate into rotation or core, or get demoted when quality slips.</p>
      </div>
      <a href="/api/v1/admin/ui/dashboard?key={
        admin_key
    }" class="inline-flex items-center rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">Back to dashboard</a>
    </section>

    <div class="mb-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
      {
        _stat_card(
            "Total profiles",
            str(len(rows)),
            f"{len(flagged_rows)} currently need attention",
            "blue",
        )
    }
      {
        _stat_card(
            "Core",
            str(status_counts.get("core", 0)),
            "trusted channels kept in steady rotation",
            "green",
        )
    }
      {
        _stat_card(
            "Rotation", str(status_counts.get("rotation", 0)), "proven but not yet core", "blue"
        )
    }
      {
        _stat_card(
            "Discovery",
            str(status_counts.get("discovery", 0)),
            "channels still proving themselves",
            "yellow",
        )
    }
      {
        _stat_card(
            "Flagged",
            str(len(flagged_rows)),
            "suppression, skip, or completion risk",
            "red" if flagged_rows else "green",
        )
    }
    </div>

    <div class="mb-8 grid gap-6 xl:grid-cols-2">
      {
        _panel(
            "Operator Snapshot",
            f'''
          <div class="rounded-[1.5rem] border border-slate-200 bg-white/75 p-4">
            <div class="panel-kicker">Role mix</div>
            <div class="mt-3 flex flex-wrap gap-2">{role_badges}</div>
          </div>
          <div class="mt-5 rounded-[1.5rem] border border-slate-200 bg-white/75 p-4">
            <div class="mb-3 panel-kicker">Needs attention</div>
            {flagged_body}
          </div>
          ''',
            subtitle="Profiles are flagged here when suppression is high, skips are elevated, or completion drops too low.",
            tone="yellow",
        )
    }
      {
        _panel(
            "Performance Leaders",
            f'''
          <div class="rounded-[1.5rem] border border-slate-200 bg-white/75 p-4">
            <div class="mb-3 panel-kicker">Top current performers</div>
            {leaders_body}
          </div>
          ''',
            subtitle="These channels currently combine high score, strong completion, and healthy save/share behavior.",
            tone="green",
        )
    }
    </div>

    {
        _panel(
            "Source Directory",
            f'''
        <div class="mb-4 text-sm leading-6 text-slate-600">The mobile view uses stacked cards; the full comparison table is still available once there is room for it.</div>
        <div class="grid gap-3 md:hidden">{mobile_cards}</div>
        <div class="table-shell hidden md:block">
          <table class="min-w-full overflow-hidden rounded-[1.5rem] bg-white/80">
            <thead class="bg-slate-50/90">
              <tr class="text-xs uppercase tracking-[0.16em] text-slate-500">
                <th class="px-3 py-3 text-left">Channel</th>
                <th class="px-3 py-3 text-left">Status</th>
                <th class="px-3 py-3 text-left">Role</th>
                <th class="px-3 py-3 text-right">Score 7d</th>
                <th class="px-3 py-3 text-right">Promoted share</th>
                <th class="px-3 py-3 text-right">Suppression</th>
                <th class="px-3 py-3 text-right">Early skip</th>
                <th class="px-3 py-3 text-right">Completion</th>
                <th class="px-3 py-3 text-right">Save/share</th>
                <th class="px-3 py-3 text-left">Last promoted</th>
              </tr>
            </thead>
            <tbody>{_table_rows()}</tbody>
          </table>
        </div>
        ''',
            subtitle="All current source profiles with the metrics used to guide promotions, demotions, and discovery lane allowances.",
            tone="blue",
        )
    }"""
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
    admin_key: str = Depends(_require_admin_ui_auth),
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
    <div class="glass-panel rounded-[1.5rem] p-4 mb-4">
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
        <div class="glass-panel rounded-[1.25rem] p-3 border {mobile_selected_ring}">
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
        <div id="review-detail" class="glass-panel rounded-[1.5rem] sticky top-24">
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
        <div id="review-detail" class="glass-panel rounded-[1.5rem] p-6 text-sm text-gray-500">
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
    <div class="glass-panel rounded-[1.5rem] p-4 mb-4">
      <div class="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 class="text-sm font-semibold text-gray-800 uppercase tracking-wide">Bulk actions</h2>
          <p class="text-xs text-gray-500 mt-1">
            Select multiple rows and apply one action in a single submission.
            Requests are limited to {_ADMIN_UI_BULK_ACTION_LIMIT} items to avoid timeouts.
          </p>
          <div class="mt-2 flex flex-wrap items-center gap-2">
            <button type="button" id="bulk-select-page" class="px-2 py-1 text-xs rounded bg-gray-100 text-gray-700 hover:bg-gray-200">Select page</button>
            <button type="button" id="bulk-clear-page" class="px-2 py-1 text-xs rounded bg-gray-100 text-gray-700 hover:bg-gray-200">Clear</button>
            <span id="bulk-selected-count" class="text-xs text-gray-600">0 selected · max {_ADMIN_UI_BULK_ACTION_LIMIT}</span>
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

    <div class="mb-2 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">{pagination}</div>
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4">
      <section class="xl:col-span-7">
        <div class="md:hidden space-y-2">{mobile_cards_html}</div>
        <div class="table-shell hidden md:block glass-panel">
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
        const bulkLimit = {_ADMIN_UI_BULK_ACTION_LIMIT};

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
        const applySelectionLimit = () => {{
          const selected = visibleCheckboxes().filter((cb) => cb.checked);
          selected.slice(bulkLimit).forEach((cb) => {{
            cb.checked = false;
          }});
        }};
        const updateSelectionUi = () => {{
          applySelectionLimit();
          const activeCheckboxes = visibleCheckboxes();
          const selected = activeCheckboxes.filter((cb) => cb.checked).length;
          const selectableCount = Math.min(activeCheckboxes.length, bulkLimit);
          if (selectedCount) {{
            selectedCount.textContent = `${{selected}} selected · max ${{bulkLimit}}`;
          }}
          if (selectAll) {{
            selectAll.checked = selectableCount > 0 && selected === selectableCount;
            selectAll.indeterminate = selected > 0 && selected < selectableCount;
          }}
        }};

        if (selectAll) {{
          selectAll.addEventListener("change", () => {{
            visibleCheckboxes().forEach((cb, index) => {{
              cb.checked = selectAll.checked && index < bulkLimit;
            }});
            updateSelectionUi();
          }});
        }}

        if (selectPageButton) {{
          selectPageButton.addEventListener("click", () => {{
            visibleCheckboxes().forEach((cb, index) => {{
              cb.checked = index < bulkLimit;
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
            if (selectedIds.length > bulkLimit) {{
              event.preventDefault();
              window.alert(`Bulk actions are limited to ${{bulkLimit}} items per request.`);
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
    admin_key: str = Depends(_require_admin_ui_auth),
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
    if len(content_ids) > _ADMIN_UI_BULK_ACTION_LIMIT:
        target = _resolve_ui_url(
            admin_key=admin_key,
            fallback_path="/api/v1/admin/ui/review",
            next_url=next_path,
            referer=referer,
        )
        return RedirectResponse(
            _add_flash(
                target,
                f"Error: bulk actions are limited to {_ADMIN_UI_BULK_ACTION_LIMIT} items per request",
            ),
            status_code=303,
        )

    repo = EditorialRepository(db)
    service = EditorialService(db, repo=repo)
    clean_note = note.strip() or None
    bounded_boost = max(0, min(3, boost_level))

    applied = 0
    failed = 0
    for content_id in content_ids:
        item = None
        if action == "approve":
            item = service.approve_content(
                content_id,
                actor=ACTOR,
                note=clean_note,
                dispatch_events=False,
            )
        elif action == "approve_publish":
            item = service.approve_and_publish(
                content_id=content_id,
                actor=ACTOR,
                boost_level=bounded_boost,
                note=clean_note,
                dispatch_events=False,
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
        if action in {"approve", "approve_publish"}:
            service.dispatch_content_events_best_effort()

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
    q: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    suppressed: Optional[str] = Query(None),
    manual_added: Optional[str] = Query(None),
    has_image: Optional[str] = Query(None),
    curation_status: Optional[str] = Query(None),
    sort_by: str = Query("published_at"),
    page: int = Query(1, ge=1),
    flash: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_ui_auth),
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

    image_filter = None
    if has_image == "true":
        image_filter = True
    elif has_image == "false":
        image_filter = False

    items, total = repo.list_content(
        day=parsed_day,
        content_type=type,
        search_text=q,
        source=source,
        suppressed=supp,
        manual_added=manual,
        has_image=image_filter,
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
            "q": q or "",
            "source": source or "",
            "suppressed": suppressed or "",
            "manual_added": manual_added or "",
            "has_image": has_image or "",
            "curation_status": curation_status or "",
            "sort_by": sort_by,
        }
    )
    push_config_service = PushConfigService(redis_client=get_redis())
    push_config, push_source = push_config_service.get_raw_config()
    push_client = create_push_messaging_client()
    push_provider_ready = push_client.is_available
    push_provider_error = push_client.availability_error
    push_mode_tone = {
        PushMode.disabled: "gray",
        PushMode.manual: "blue",
        PushMode.auto_all: "purple",
    }.get(push_config.mode, "gray")
    push_mode_options = "".join(
        f'<option value="{mode.value}" {"selected" if push_config.mode == mode else ""}>'
        f"{mode.value}</option>"
        for mode in PushMode
    )
    push_enabled_checked = "checked" if push_config.enabled else ""
    push_summaries = _push_log_summaries(db, [item.id for item in items])
    push_runtime_panel = f"""
    <div class="glass-panel rounded-[1.5rem] p-4 mb-4">
      <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h2 class="text-sm font-semibold uppercase tracking-wide text-slate-600">Push runtime</h2>
          <p class="mt-1 text-sm text-slate-500">
            Control delivery mode for article and video notifications.
          </p>
          <div class="mt-3 flex flex-wrap gap-2">
            {_badge("enabled" if push_config.enabled else "disabled", "green" if push_config.enabled else "red")}
            {_badge(f"mode {push_config.mode.value}", push_mode_tone)}
            {_badge("provider ready" if push_provider_ready else "provider unavailable", "green" if push_provider_ready else "red")}
            {_badge(f"source {push_source}", "gray")}
            {_badge(f"ttl {push_config.config_ttl_seconds}s", "gray")}
          </div>
          {f'<p class="mt-3 text-xs text-rose-700">{_esc(push_provider_error)}</p>' if push_provider_error else ""}
        </div>
        <div class="w-full max-w-xl space-y-3">
          <form method="post" action="/api/v1/admin/ui/push/config?key={admin_key}" class="grid gap-3 rounded-2xl border border-slate-200 bg-white/70 p-3 sm:grid-cols-[auto,1fr,auto] sm:items-end">
            <input type="hidden" name="next" value="{content_next}">
            <label class="flex items-center gap-2 text-sm font-medium text-slate-700">
              <input type="checkbox" name="enabled" value="true" {push_enabled_checked} class="rounded border-slate-300 text-sky-600 focus:ring-sky-500">
              Enabled
            </label>
            <label class="block text-sm">
              <span class="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">Mode</span>
              <select name="mode" class="w-full rounded border border-slate-300 px-3 py-2 text-sm">
                {push_mode_options}
              </select>
            </label>
            <button type="submit" class="rounded bg-slate-950 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800">
              Save push config
            </button>
          </form>
          <form method="post" action="/api/v1/admin/ui/push/config/reset?key={admin_key}" class="flex justify-end">
            <input type="hidden" name="next" value="{content_next}">
            <button type="submit" class="rounded border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
              Reset to defaults
            </button>
          </form>
        </div>
      </div>
    </div>"""

    rows_html = ""
    for i in items:
        visible_title = display_article_title(
            getattr(i, "title", None),
            getattr(i, "canonical_url", None) or getattr(i, "source_url", None),
        )
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
        badges += _readiness_badge(i) + " "
        if not (getattr(i, "image_url", None) or "").strip():
            badges += _badge("no image", "gray") + " "

        pub = i.published_at.strftime("%m-%d %H:%M") if i.published_at else "—"
        score = f"{i.promotion_score:.3f}" if getattr(i, "promotion_score", None) else "—"
        disc = _esc(getattr(i, "discovered_via", None) or "—")
        hits = str(getattr(i, "signal_hits", 0) or 0)
        push_eligibility = evaluate_push_eligibility(i)
        push_eligible = push_eligibility.eligible
        push_summary_html = _push_summary_markup(
            push_summaries.get(i.id),
            eligible=push_eligible,
        )
        push_reason = _push_ui_reason(push_eligibility.reason) if not push_eligible else ""
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
            open_button = (
                f'<a href="/api/v1/admin/ui/detail/{i.id}?key={admin_key}" '
                'class="inline-flex items-center justify-center px-2 py-1 text-xs '
                'bg-white border border-gray-300 text-gray-700 rounded hover:bg-gray-50">'
                "Open"
                "</a>"
            )
            push_button = (
                f"<div>{_push_send_button(content_id=i.id, admin_key=admin_key, next_path=content_next, compact=True)}</div>"
                if push_eligible
                else ""
            )
            actions_html = f'<div class="flex flex-col gap-1">{open_button}{push_button}</div>'

        rows_html += f"""
        <tr class="{row_bg} hover:brightness-95 border-b border-gray-100">
          <td class="px-3 py-2 text-sm text-gray-500">{i.id}</td>
          <td class="px-3 py-2 text-sm max-w-xs">
            <a href="/api/v1/admin/ui/detail/{i.id}?key={admin_key}" class="text-blue-600 hover:underline font-medium">{_esc(visible_title[:65])}</a>
          </td>
          <td class="px-3 py-2 text-xs text-gray-600">{i.type.value if i.type else ""}</td>
          <td class="px-3 py-2 text-xs text-gray-600 truncate max-w-[90px]">{_esc(i.source or "")}</td>
          <td class="px-3 py-2 text-xs text-gray-500 whitespace-nowrap">{pub}</td>
          <td class="px-3 py-2">{badges}</td>
          <td class="px-3 py-2 text-xs text-gray-500">
            <div class="max-w-[180px]">
              {push_summary_html}
              {f'<div class="mt-1 text-[11px] text-slate-500">{_esc(push_reason)}</div>' if push_reason else ""}
            </div>
          </td>
          <td class="px-3 py-2 text-xs font-mono text-gray-600">{score}</td>
          <td class="px-3 py-2 text-xs text-gray-500">{disc}</td>
          <td class="px-3 py-2 text-xs text-gray-500">{hits}</td>
          <td class="px-3 py-2 text-xs text-gray-500">{actions_html}</td>
        </tr>"""

    if not rows_html:
        rows_html = (
            '<tr><td colspan="11" class="px-3 py-6 text-center text-sm text-gray-400">'
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
    <div class="glass-panel rounded-[1.5rem] p-4 mb-4">
      <form method="get" action="/api/v1/admin/ui/content" class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-9 gap-3 items-end">
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
          <label class="block text-xs text-gray-500 mb-1">Search</label>
          <input type="text" name="q" value="{_esc(q or "")}" placeholder="title, URL, summary..." class="w-full rounded border-gray-300 text-sm px-2 py-1">
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Source</label>
          <input type="text" name="source" value="{source or ""}" placeholder="filter..." class="w-full rounded border-gray-300 text-sm px-2 py-1">
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Image</label>
          {_sel("has_image", has_image or "", [("", "All"), ("true", "Present"), ("false", "Missing")])}
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
                "q": q or "",
                "source": source or "",
                "suppressed": suppressed or "",
                "manual_added": manual_added or "",
                "has_image": has_image or "",
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
        is_err = "error" in flash.lower() or "failed" in flash.lower()
        cls = "bg-red-100 text-red-800" if is_err else "bg-green-100 text-green-800"
        flash_html = f'<div class="mb-4 p-3 {cls} rounded text-sm">{_esc(flash)}</div>'

    body = f"""
    <h1 class="text-2xl font-bold text-gray-900 mb-4">Content</h1>
    {flash_html}
    {push_runtime_panel}
    {filter_form}
    <div class="mb-2 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">{pagination}</div>
    <div class="table-shell glass-panel">
      <table class="min-w-full">
        <thead class="bg-gray-50">
          <tr class="text-xs font-medium text-gray-500 uppercase tracking-wide">
            <th class="px-3 py-3 text-left">ID</th>
            <th class="px-3 py-3 text-left">Title</th>
            <th class="px-3 py-3 text-left">Type</th>
            <th class="px-3 py-3 text-left">Source</th>
            <th class="px-3 py-3 text-left">Published</th>
            <th class="px-3 py-3 text-left">Status</th>
            <th class="px-3 py-3 text-left">Push</th>
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
    admin_key: str = Depends(_require_admin_ui_auth),
):
    repo = EditorialRepository(db)
    item = repo.get_content_by_id(content_id)
    if not item:
        return _base("<h2 class='text-xl font-bold text-gray-800'>Not found</h2>", key=admin_key)
    visible_title = display_article_title(
        getattr(item, "title", None),
        getattr(item, "canonical_url", None) or getattr(item, "source_url", None),
    )

    actions = repo.get_actions_for_content(content_id, limit=30)
    push_config_service = PushConfigService(redis_client=get_redis())
    push_config, _push_source = push_config_service.get_raw_config()
    push_client = create_push_messaging_client()
    push_provider_ready = push_client.is_available
    push_provider_error = push_client.availability_error
    push_summary = _push_log_summaries(db, [content_id]).get(content_id)
    push_logs = (
        db.query(PushSendLog)
        .filter(PushSendLog.content_item_id == content_id)
        .order_by(PushSendLog.created_at.desc())
        .limit(12)
        .all()
    )

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
    push_eligibility = evaluate_push_eligibility(item)
    push_eligible = push_eligibility.eligible
    push_reason = _push_ui_reason(push_eligibility.reason) if not push_eligible else ""

    push_log_rows = ""
    for log in push_logs:
        ts = log.created_at.strftime("%Y-%m-%d %H:%M") if log.created_at else "—"
        delivered = f"{log.success_count}/{log.audience_count}"
        failures = str(log.failure_count or 0)
        invalid = str(log.invalid_token_count or 0)
        push_log_rows += f"""<tr class="border-b border-slate-100">
          <td class="px-3 py-2 text-xs whitespace-nowrap text-slate-500">{ts}</td>
          <td class="px-3 py-2">{_badge(log.mode, "purple" if log.mode == PushMode.auto_all.value else "blue")}</td>
          <td class="px-3 py-2 text-xs text-slate-600">{_esc(log.actor or "—")}</td>
          <td class="px-3 py-2 text-xs text-slate-600">{_esc(delivered)}</td>
          <td class="px-3 py-2 text-xs text-slate-600">{_esc(failures)}</td>
          <td class="px-3 py-2 text-xs text-slate-600">{_esc(invalid)}</td>
        </tr>"""

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
      <div class="glass-panel lg:col-span-2 rounded-[1.5rem]">
        <div class="p-5 border-b border-gray-100">
          <h1 class="text-xl font-bold text-gray-900 leading-snug">{_esc(visible_title[:120])}</h1>
          <div class="mt-2 flex flex-wrap gap-1">{badges}</div>
        </div>
        <table class="w-full">
          {_row("ID", str(item.id))}
          {_row("Type", item.type.value if item.type else "—")}
          {_row("Curation status", curation_badge)}
          {_row("Readiness", _readiness_badge(item))}
          {_row("Readiness reason", _esc(describe_readiness_reason(getattr(item, "readiness_reason", None))))}
          {_row("Ready at", item.ready_at.strftime("%Y-%m-%d %H:%M") if getattr(item, "ready_at", None) else "—")}
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
        <div class="glass-panel rounded-[1.5rem] p-5">
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
        <div class="glass-panel rounded-[1.5rem] p-5">
          <div class="flex items-center justify-between gap-3">
            <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide">Push delivery</h3>
            {_badge(push_config.mode.value, "purple" if push_config.mode == PushMode.auto_all else "blue" if push_config.mode == PushMode.manual else "gray")}
          </div>
          <div class="mt-3 flex flex-wrap gap-2">
            {_badge("enabled" if push_config.enabled else "disabled", "green" if push_config.enabled else "red")}
            {_badge("provider ready" if push_provider_ready else "provider unavailable", "green" if push_provider_ready else "red")}
          </div>
          <div class="mt-3 text-xs text-slate-500">
            {_push_summary_markup(push_summary, eligible=push_eligible)}
          </div>
          {f'<p class="mt-3 rounded-xl bg-rose-50 px-3 py-2 text-xs text-rose-700">{_esc(push_provider_error)}</p>' if push_provider_error else ""}
          {f'<p class="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">{_esc(push_reason)}</p>' if push_reason else ""}
          {f'<div class="mt-3">{_push_send_button(content_id=content_id, admin_key=admin_key, next_path=detail_next)}</div>' if push_eligible else ""}
          <div class="mt-4 overflow-x-auto rounded-2xl border border-slate-200">
            <table class="min-w-full">
              <thead class="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
                <tr>
                  <th class="px-3 py-2 text-left">Time</th>
                  <th class="px-3 py-2 text-left">Mode</th>
                  <th class="px-3 py-2 text-left">Actor</th>
                  <th class="px-3 py-2 text-left">Delivered</th>
                  <th class="px-3 py-2 text-left">Failed</th>
                  <th class="px-3 py-2 text-left">Invalid</th>
                </tr>
              </thead>
              <tbody>{push_log_rows or '<tr><td colspan="6" class="px-3 py-4 text-center text-sm text-slate-400">No push sends yet</td></tr>'}</tbody>
            </table>
          </div>
        </div>
        <div class="glass-panel rounded-[1.5rem] p-5">
          <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">Visibility</h3>
          {suppress_btn}
        </div>
        <div class="glass-panel rounded-[1.5rem] p-5">
          <h3 class="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">Editorial boost</h3>
          <form method="post" action="/api/v1/admin/ui/action/{content_id}/boost?key={admin_key}" class="flex gap-2">
            <input type="hidden" name="next" value="{detail_next}">
            <select name="level" class="flex-1 rounded border-gray-300 text-sm px-2 py-1">{boost_options}</select>
            <button type="submit" class="px-3 py-1.5 bg-purple-600 text-white text-sm rounded hover:bg-purple-700">Set</button>
          </form>
        </div>
      </div>
    </div>

    <div class="glass-panel mt-6 rounded-[1.5rem]">
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
    admin_key: str = Depends(_require_admin_ui_auth),
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
    admin_key: str = Depends(_require_admin_ui_auth),
):
    svc = EditorialService(db)
    result = svc.submit_url(url=url, importance_level=importance_level, actor=ACTOR)
    msg = f"{result.status}: {result.message}"
    if result.content_id:
        target = f"/api/v1/admin/ui/detail/{result.content_id}"
        return RedirectResponse(_add_flash(target, msg), status_code=303)
    return RedirectResponse(
        _add_flash("/api/v1/admin/ui/submit", msg),
        status_code=303,
    )


@router.post("/push/config")
def ui_update_push_config(
    enabled: str = Form(""),
    mode: str = Form(PushMode.manual.value),
    next_path: str = Form("/api/v1/admin/ui/content", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    admin_key: str = Depends(_require_admin_ui_auth),
    redis_client=Depends(get_redis),
):
    try:
        push_mode = PushMode(mode)
    except ValueError:
        return _redirect_to_ui(
            admin_key=admin_key,
            fallback_path="/api/v1/admin/ui/content",
            flash="Error: invalid push mode",
            next_url=next_path,
            referer=referer,
        )

    push_config_service = PushConfigService(redis_client=redis_client)
    try:
        push_config_service.update_config(
            PushRuntimeConfigPatch(
                enabled=enabled == "true",
                mode=push_mode,
            )
        )
    except RuntimeError as exc:
        return _redirect_to_ui(
            admin_key=admin_key,
            fallback_path="/api/v1/admin/ui/content",
            flash=f"Error: {exc}",
            next_url=next_path,
            referer=referer,
        )

    return _redirect_to_ui(
        admin_key=admin_key,
        fallback_path="/api/v1/admin/ui/content",
        flash=f"Push config updated to {push_mode.value}",
        next_url=next_path,
        referer=referer,
    )


@router.post("/push/config/reset")
def ui_reset_push_config(
    next_path: str = Form("/api/v1/admin/ui/content", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    admin_key: str = Depends(_require_admin_ui_auth),
    redis_client=Depends(get_redis),
):
    push_config_service = PushConfigService(redis_client=redis_client)
    try:
        config = push_config_service.reset_config()
    except RuntimeError as exc:
        return _redirect_to_ui(
            admin_key=admin_key,
            fallback_path="/api/v1/admin/ui/content",
            flash=f"Error: {exc}",
            next_url=next_path,
            referer=referer,
        )

    return _redirect_to_ui(
        admin_key=admin_key,
        fallback_path="/api/v1/admin/ui/content",
        flash=f"Push config reset to {config.mode.value}",
        next_url=next_path,
        referer=referer,
    )


@router.post("/push/send/{content_id}")
def ui_send_push_now(
    content_id: int,
    next_path: str = Form("", alias="next"),
    key: str = Form(""),
    referer: Optional[str] = Header(None, alias="Referer"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(_require_admin_ui_auth),
):
    service = PushNotificationService(db=db)
    try:
        result = service.send_manual(content_id=content_id, actor=ACTOR)
    except PushNotificationError as exc:
        return _redirect_after_action(
            content_id=content_id,
            admin_key=admin_key,
            flash=f"Error: {exc}",
            next_url=next_path,
            referer=referer,
        )

    return _redirect_after_action(
        content_id=content_id,
        admin_key=admin_key,
        flash=result.message,
        next_url=next_path,
        referer=referer,
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
    admin_key: str = Depends(_require_admin_ui_auth),
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
    admin_key: str = Depends(_require_admin_ui_auth),
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
    admin_key: str = Depends(_require_admin_ui_auth),
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
    admin_key: str = Depends(_require_admin_ui_auth),
):
    service = EditorialService(db)
    item = service.promote_content(content_id, actor=ACTOR)
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
    admin_key: str = Depends(_require_admin_ui_auth),
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
    admin_key: str = Depends(_require_admin_ui_auth),
):
    service = EditorialService(db)
    clean_note = note.strip() or None
    item = service.approve_content(content_id, actor=ACTOR, note=clean_note)
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
    admin_key: str = Depends(_require_admin_ui_auth),
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
    admin_key: str = Depends(_require_admin_ui_auth),
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
    admin_key: str = Depends(_require_admin_ui_auth),
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
    admin_key: str = Depends(_require_admin_ui_auth),
):
    bounded_boost = max(0, min(3, boost_level))
    clean_note = note.strip() or None
    service = EditorialService(db)
    item = service.approve_and_publish(
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
    admin_key: str = Depends(_require_admin_ui_auth),
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
