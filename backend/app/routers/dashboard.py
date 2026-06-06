from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import Job, JobAssignment, Assistant
from ..security import get_current_user

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _bucket_key(dt: datetime, rng: str) -> str:
    if rng == "daily":
        ts = dt.replace(minute=0, second=0, microsecond=0)
    else:
        ts = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return ts.isoformat()


@router.get("/stats")
def stats(range_: str = Query("daily", alias="range"),
          db: Session = Depends(get_db), _u=Depends(get_current_user)):
    now = datetime.utcnow()
    rng = range_ if range_ in {"daily", "weekly", "monthly"} else "daily"
    if rng == "weekly":
        start = now - timedelta(days=7)
    elif rng == "monthly":
        start = now - timedelta(days=30)
    else:
        start = now - timedelta(days=1)

    jobs = db.query(Job).filter(Job.created_at >= start, Job.is_template == False).all()  # noqa: E712
    total = len(jobs)
    done = sum(1 for j in jobs if j.status == "done")
    in_progress = sum(1 for j in jobs if j.status == "in_progress")
    pending = sum(1 for j in jobs if j.status == "pending")
    overdue = sum(1 for j in jobs if j.status == "overdue")

    on_time = 0; eligible = 0
    for j in jobs:
        if j.status == "done" and j.deadline_at:
            eligible += 1
            last_finish = max([a.finished_at for a in j.assignments if a.finished_at], default=None)
            if last_finish and last_finish <= j.deadline_at:
                on_time += 1
    on_time_ratio = (on_time / eligible) if eligible else 0.0

    buckets = {}
    if rng == "daily":
        for h in range(24):
            ts = (now - timedelta(hours=23 - h)).replace(minute=0, second=0, microsecond=0)
            buckets[ts.isoformat()] = {"ts": ts.isoformat(), "created": 0, "done": 0}
    else:
        days = 7 if rng == "weekly" else 30
        for d in range(days):
            ts = (now - timedelta(days=days - 1 - d)).replace(hour=0, minute=0, second=0, microsecond=0)
            buckets[ts.isoformat()] = {"ts": ts.isoformat(), "created": 0, "done": 0}

    keys = sorted(buckets.keys())
    for j in jobs:
        key = _bucket_key(j.created_at, rng)
        if key in buckets:
            buckets[key]["created"] += 1
        if j.status == "done":
            last_finish = max([a.finished_at for a in j.assignments if a.finished_at], default=None)
            if last_finish:
                k2 = _bucket_key(last_finish, rng)
                if k2 in buckets:
                    buckets[k2]["done"] += 1

    assts = db.query(Assistant).all()
    per_assistant = []
    for a in assts:
        rows = db.query(JobAssignment).filter(JobAssignment.assistant_id == a.id).all()
        per_assistant.append({
            "id": a.id, "name": a.name, "status": a.status,
            "total": len(rows),
            "done": sum(1 for r in rows if r.status == "done"),
            "in_progress": sum(1 for r in rows if r.status in ("in_progress", "accepted")),
            "declined": sum(1 for r in rows if r.status == "declined"),
        })

    return {
        "range": rng,
        "total": total, "done": done, "pending": pending,
        "in_progress": in_progress, "overdue": overdue,
        "on_time_ratio": round(on_time_ratio, 3),
        "timeseries": [buckets[k] for k in keys],
        "status_breakdown": {"done": done, "pending": pending,
                             "in_progress": in_progress, "overdue": overdue},
        "per_assistant": per_assistant,
    }
