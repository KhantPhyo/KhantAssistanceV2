"""Push Khant Assistance jobs into a Notion database in real time.

Each Job lifecycle event (created, accepted, finished, reassigned, cancelled, status_changed)
calls `enqueue(job_id)`. The function fires a background task that POSTs/PATCHes to the Notion
REST API. The Notion page ID is stored back on the Job row for subsequent updates.

If `NOTION_TOKEN` or `NOTION_JOBS_DATABASE_ID` is empty, all calls are no-ops.

Notion API reference: https://developers.notion.com/reference/post-page
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from ..config import settings
from ..db import SessionLocal
from ..models import Job, JobAssignment, Assistant

log = logging.getLogger("notion_sync")

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------------- helpers ----------------

def is_enabled() -> bool:
    return bool(settings.NOTION_TOKEN and settings.NOTION_JOBS_DATABASE_ID)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _week_of_month(dt: datetime) -> str:
    return f"Week {((dt.day - 1) // 7) + 1}"


def _month_label(dt: datetime) -> str:
    return MONTH_NAMES[dt.month]


def _iso_year_week(dt: datetime) -> str:
    # ISO 8601 — e.g. 2026-W19
    return dt.strftime("%G-W%V")


def _truncate(s: str | None, n: int = 1900) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _date_prop(dt: datetime | None) -> dict[str, Any]:
    if not dt:
        return {"date": None}
    return {"date": {"start": dt.replace(microsecond=0).isoformat()}}


def _build_properties(job: Job, db) -> dict[str, Any]:
    # Resolve assignees + per-assignee status
    assignments = (
        db.query(JobAssignment, Assistant)
        .join(Assistant, Assistant.id == JobAssignment.assistant_id)
        .filter(JobAssignment.job_id == job.id)
        .all()
    )
    names = [a.name for _, a in assignments]
    pairs = [f"{a.name}:{ja.status}" for ja, a in assignments]
    reports_count = len(job.reports) if job.reports is not None else 0

    created = job.created_at or datetime.utcnow()

    props: dict[str, Any] = {
        "Job Code": {"title": [{"type": "text", "text": {"content": job.code}}]},
        "Status": {"select": {"name": job.status}},
        "Title": {"rich_text": [{"type": "text", "text": {"content": _truncate(job.title)}}]},
        "Description": {"rich_text": [{"type": "text", "text": {"content": _truncate(job.description)}}]},
        "Assignees": {"rich_text": [{"type": "text", "text": {"content": _truncate(", ".join(names))}}]},
        "Assignee Statuses": {"rich_text": [{"type": "text", "text": {"content": _truncate(", ".join(pairs))}}]},
        "Source": {"select": {"name": job.created_via or "web"}},
        "Report Type": {"select": {"name": job.report_type}},
        "Month": {"select": {"name": _month_label(created)}},
        "Week": {"select": {"name": _week_of_month(created)}},
        "Year-Week": {"rich_text": [{"type": "text", "text": {"content": _iso_year_week(created)}}]},
        "Created": _date_prop(created),
        "Deadline": _date_prop(job.deadline_at),
        "Completed": _date_prop(job.completed_at),
        "Reports Count": {"number": reports_count},
    }
    return props


# ---------------- public API ----------------

def enqueue(job_id: int) -> None:
    """Fire-and-forget: schedules `_sync_job` on the running event loop. Safe to call from
    sync code paths — silently no-ops if there's no loop or sync is disabled."""
    if not is_enabled():
        return
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_sync_job(job_id))
        else:
            # No loop running — best effort run-to-completion
            loop.run_until_complete(_sync_job(job_id))
    except RuntimeError:
        # Called outside an event loop context — try the new-loop fallback
        try:
            asyncio.run(_sync_job(job_id))
        except Exception:
            log.exception("notion_sync.enqueue fallback failed for job %s", job_id)


async def archive(job_id: int) -> None:
    """Archive (soft-delete) the Notion page corresponding to a deleted job."""
    if not is_enabled():
        return
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        page_id = job.notion_page_id if job else None
    finally:
        db.close()
    if not page_id:
        return
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.patch(
                f"{NOTION_API}/pages/{page_id}",
                headers=_headers(),
                json={"archived": True},
            )
            if r.status_code >= 400:
                log.warning("notion archive failed [%s]: %s", r.status_code, r.text[:200])
    except Exception:
        log.exception("notion archive error for job %s", job_id)


async def sync_all() -> dict:
    """Push every non-template Job to Notion. Returns a small report."""
    if not is_enabled():
        return {"ok": False, "reason": "Notion not configured"}
    db = SessionLocal()
    try:
        ids = [j.id for j in db.query(Job).filter(Job.is_template == False).all()]  # noqa: E712
    finally:
        db.close()
    ok = 0
    fail = 0
    for jid in ids:
        try:
            await _sync_job(jid)
            ok += 1
        except Exception:
            fail += 1
            log.exception("sync_all failed for job %s", jid)
    return {"ok": True, "synced": ok, "failed": fail, "total": len(ids)}


# ---------------- internals ----------------

async def _sync_job(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return
        if job.is_template:
            return  # never push templates
        properties = _build_properties(job, db)
        existing_page_id = job.notion_page_id
    finally:
        db.close()

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            if existing_page_id:
                r = await client.patch(
                    f"{NOTION_API}/pages/{existing_page_id}",
                    headers=_headers(),
                    json={"properties": properties, "archived": False},
                )
                if r.status_code >= 400:
                    log.warning("notion update failed [%s] for job %s: %s",
                                r.status_code, job_id, r.text[:300])
                    return
            else:
                r = await client.post(
                    f"{NOTION_API}/pages",
                    headers=_headers(),
                    json={
                        "parent": {"database_id": settings.NOTION_JOBS_DATABASE_ID},
                        "properties": properties,
                    },
                )
                if r.status_code >= 400:
                    log.warning("notion create failed [%s] for job %s: %s",
                                r.status_code, job_id, r.text[:300])
                    return
                page = r.json()
                existing_page_id = page.get("id")
    except Exception:
        log.exception("notion API error for job %s", job_id)
        return

    # Persist page id + synced timestamp
    db = SessionLocal()
    try:
        j = db.query(Job).filter(Job.id == job_id).first()
        if j:
            if existing_page_id and not j.notion_page_id:
                j.notion_page_id = existing_page_id
            j.notion_synced_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()
