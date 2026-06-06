from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..config import settings as _settings, UPLOAD_PATH
from ..db import get_db
from ..models import Job, JobAssignment, Assistant, Report, AssistantGroup, JobGroup, User
from ..schemas import JobIn, JobOut, JobAssignmentOut, ReportOut, JobUpdateIn, JobReassignIn
from ..security import get_current_user, get_current_user_qs
from ..services.bot_manager import manager
from ..services.ws_hub import hub
from ..services import audit, notion_sync

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

VALID_REPORT_TYPES = {"photo", "video", "document", "text", "any"}


def _next_code(db: Session) -> str:
    """YYMon#### — e.g. 26May0001."""
    try:
        tz = ZoneInfo(_settings.TIMEZONE)
    except Exception:
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    prefix = now.strftime("%y%b")
    n = db.query(Job).filter(Job.code.like(f"{prefix}%")).count() + 1
    return f"{prefix}{n:04d}"


def _action_for(job: Job, db: Session) -> str:
    """Human-readable schedule label.
    - Template rows show 'template:daily' etc.
    - Instance rows inherit the parent template's recurrence.
    - Stand-alone (no parent_template_id) → 'one-time'.
    """
    if job.is_template:
        return f"template:{job.recurrence or 'none'}"
    if job.parent_template_id:
        parent = db.query(Job).filter(Job.id == job.parent_template_id).first()
        if parent and parent.recurrence in ("daily", "weekly", "monthly", "frequent"):
            return parent.recurrence
    return "one-time"


def _serialize(job: Job, db: Session) -> JobOut:
    assignments = []
    for a in job.assignments:
        asst = db.query(Assistant).filter(Assistant.id == a.assistant_id).first()
        assignments.append(JobAssignmentOut(
            id=a.id, assistant_id=a.assistant_id,
            assistant_name=asst.name if asst else None,
            status=a.status, accepted_at=a.accepted_at, finished_at=a.finished_at,
            declined_reason=a.declined_reason,
        ))
    reports = [ReportOut(
        id=r.id, job_id=r.job_id, assistant_id=r.assistant_id, type=r.type,
        content_text=r.content_text, file_name=r.file_name, submitted_at=r.submitted_at,
    ) for r in job.reports]
    group_ids = [jg.group_id for jg in db.query(JobGroup).filter(JobGroup.job_id == job.id).all()]
    return JobOut(
        id=job.id, code=job.code, title=job.title, description=job.description,
        report_type=job.report_type, deadline_at=job.deadline_at, status=job.status,
        created_via=job.created_via or "web",
        created_at=job.created_at, completed_at=job.completed_at,
        recurrence=job.recurrence or "none",
        recurrence_every=job.recurrence_every or 0,
        completion_mode=job.completion_mode or "all",
        accept_mode=job.accept_mode or "any",
        is_template=bool(job.is_template),
        parent_template_id=job.parent_template_id,
        next_spawn_at=job.next_spawn_at,
        action=_action_for(job, db),
        group_ids=group_ids,
        assignments=assignments, reports=reports,
    )


def _expand_members(db: Session, assistant_ids: list[int], group_ids: list[int]) -> list[int]:
    aids = set(assistant_ids or [])
    if group_ids:
        for r in db.query(AssistantGroup).filter(AssistantGroup.group_id.in_(group_ids)).all():
            aids.add(r.assistant_id)
    return sorted(aids)


def _delta_for_recurrence(rec: str, every: int):
    if rec == "daily":   return timedelta(days=1)
    if rec == "weekly":  return timedelta(days=7)
    if rec == "monthly": return timedelta(days=30)
    if rec == "frequent" and every > 0:
        return timedelta(days=every)
    return None


@router.get("", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db), _user=Depends(get_current_user)):
    jobs = db.query(Job).filter(Job.is_template == False).order_by(Job.id.desc()).all()  # noqa: E712
    return [_serialize(j, db) for j in jobs]


@router.get("/templates", response_model=list[JobOut])
def list_templates(db: Session = Depends(get_db), _user=Depends(get_current_user)):
    """Active recurring-job templates. Excludes one-off jobs (is_template=False)."""
    rows = (
        db.query(Job)
        .filter(Job.is_template == True)  # noqa: E712
        .order_by(Job.recurrence, Job.id.desc())
        .all()
    )
    return [_serialize(j, db) for j in rows]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db), _u=Depends(get_current_user)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404)
    return _serialize(job, db)


@router.post("", response_model=JobOut)
async def create_job(body: JobIn, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    if body.report_type not in VALID_REPORT_TYPES:
        raise HTTPException(400, "Invalid report_type")
    if body.recurrence not in {"none", "daily", "weekly", "monthly", "frequent"}:
        raise HTTPException(400, "Invalid recurrence")
    if body.completion_mode not in {"any", "all"}:
        raise HTTPException(400, "Invalid completion_mode")
    if body.accept_mode not in {"any", "all"}:
        raise HTTPException(400, "Invalid accept_mode")
    ids = _expand_members(db, body.assistant_ids, body.group_ids)
    if not ids:
        raise HTTPException(400, "At least one assignee required")

    is_recurring = body.recurrence != "none"
    delta = _delta_for_recurrence(body.recurrence, body.recurrence_every)
    if is_recurring and delta is None:
        raise HTTPException(400, "recurrence_every required for 'frequent'")

    template_id = None
    if is_recurring:
        tcode = _next_code(db)
        tmpl = Job(code=tcode, title=body.title, description=body.description,
                   report_type=body.report_type, deadline_at=body.deadline_at,
                   status="pending", is_template=True,
                   recurrence=body.recurrence, recurrence_every=body.recurrence_every,
                   completion_mode=body.completion_mode,
                   accept_mode=body.accept_mode,
                   created_via="web", created_by_user_id=me.id,
                   next_spawn_at=datetime.utcnow() + delta)
        db.add(tmpl); db.flush()
        for aid in ids:
            db.add(JobAssignment(job_id=tmpl.id, assistant_id=aid, status="pending"))
        for gid in body.group_ids:
            db.add(JobGroup(job_id=tmpl.id, group_id=gid))
        template_id = tmpl.id
        db.commit()

    code = _next_code(db)
    job = Job(code=code, title=body.title, description=body.description,
              report_type=body.report_type, deadline_at=body.deadline_at,
              status="pending", is_template=False, parent_template_id=template_id,
              recurrence="none", completion_mode=body.completion_mode,
              accept_mode=body.accept_mode,
              created_via="web", created_by_user_id=me.id)
    db.add(job); db.flush()
    for gid in body.group_ids:
        db.add(JobGroup(job_id=job.id, group_id=gid))
    asst_rows = db.query(Assistant).filter(Assistant.id.in_(ids)).all()
    pairs = []
    for a in asst_rows:
        row = JobAssignment(job_id=job.id, assistant_id=a.id, status="pending")
        db.add(row)
        pairs.append((a, row))
    db.commit(); db.refresh(job)

    audit.write(db, actor_type="web_admin", actor_id=me.id, action="job_created",
                target_type="job", target_id=job.id,
                payload={"title": body.title, "assignees": ids})

    for a, row in pairs:
        await _notify_assignment(a, row, job, header="📋 အလုပ်အသစ်")

    # notify admin bots
    try:
        from ..models import Bot as BotM
        admin_bots = db.query(BotM).filter(BotM.bot_type == "admin_bot",
                                           BotM.status == "active",
                                           BotM.chat_id.isnot(None)).all()
        for ab in admin_bots:
            try:
                await manager.send_message(ab.id, ab.chat_id, f"✅ Job {job.code} created: {job.title}")
            except Exception:
                pass
    except Exception:
        pass

    await hub.broadcast("job.created", {"job_id": job.id, "code": job.code})
    notion_sync.enqueue(job.id)
    return _serialize(job, db)


def _job_keyboard(assignment_id: int) -> InlineKeyboardMarkup:
    """Standard 4-button keyboard attached to job notifications + reminders."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ လက်ခံ", callback_data=f"job:accept:{assignment_id}"),
            InlineKeyboardButton("❌ ငြင်း", callback_data=f"job:decline:{assignment_id}"),
        ],
        [
            InlineKeyboardButton("➡️ လွှဲ", callback_data=f"job:transfer:{assignment_id}"),
            InlineKeyboardButton("🏖️ Leave", callback_data=f"job:leave:{assignment_id}"),
        ],
    ])


async def _notify_assignment(asst: Assistant, row: JobAssignment, job: Job, header: str = "📋 အလုပ်အသစ်"):
    if not (asst.bot_id and asst.chat_id and asst.status == "active"):
        return
    kb = _job_keyboard(row.id)
    deadline = job.deadline_at.strftime("%Y-%m-%d %H:%M") if job.deadline_at else "—"
    type_mm = {"photo": "ဓာတ်ပုံ", "video": "ဗီဒီယို", "document": "ဖိုင်",
               "text": "စာသား", "any": "မည်သည့်ပုံစံမဆို"}.get(job.report_type, job.report_type)
    desc = f"\n📝 {job.description}" if job.description else ""
    mode_hint = ""
    if (job.accept_mode or "any") == "all":
        mode_hint = "\n🤝 Mode: All-accept (လူတိုင်း လက်ခံမှ in_progress)"
    text = (f"{header} {job.code}\n"
            f"📌 {job.title}{desc}\n"
            f"⏰ {deadline}\n"
            f"📎 ပုံစံ: {type_mm}{mode_hint}")
    try:
        await manager.send_message(asst.bot_id, asst.chat_id, text, reply_markup=kb)
    except Exception:
        pass


@router.patch("/{job_id}", response_model=JobOut)
async def update_job(job_id: int, body: JobUpdateIn, db: Session = Depends(get_db),
                     me: User = Depends(get_current_user)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404)
    if body.report_type is not None and body.report_type not in VALID_REPORT_TYPES:
        raise HTTPException(400, "Invalid report_type")
    changed: list[str] = []
    if body.title is not None and body.title != job.title:
        job.title = body.title; changed.append("title")
    if body.description is not None and body.description != job.description:
        job.description = body.description; changed.append("description")
    if body.report_type is not None and body.report_type != job.report_type:
        job.report_type = body.report_type; changed.append("report_type")
    if body.deadline_at is not None and body.deadline_at != job.deadline_at:
        job.deadline_at = body.deadline_at; changed.append("deadline")
    if body.completion_mode is not None and body.completion_mode in ("any", "all") \
            and body.completion_mode != job.completion_mode:
        job.completion_mode = body.completion_mode; changed.append("completion_mode")

    # ---------------- Re-assignment (overwrite assignee set) ----------------
    added_names: list[str] = []
    removed_names: list[str] = []
    new_assignment_rows: list[tuple[Assistant, JobAssignment]] = []
    if body.assistant_ids is not None or body.group_ids is not None:
        new_ids = set(_expand_members(db, body.assistant_ids or [], body.group_ids or []))
        # Keep history: all currently-active assignments
        active = [a for a in job.assignments
                  if a.status in ("pending", "accepted", "in_progress")]
        current_ids = {a.assistant_id for a in active}
        to_add = new_ids - current_ids
        to_remove = current_ids - new_ids

        if to_add or to_remove:
            changed.append("assignees")
            # Soft-remove: mark as superseded so history is preserved
            for a in active:
                if a.assistant_id in to_remove:
                    a.status = "superseded"
            # Add new assignments
            for aid in to_add:
                asst = db.query(Assistant).filter(Assistant.id == aid).first()
                if asst:
                    row = JobAssignment(job_id=job.id, assistant_id=aid, status="pending")
                    db.add(row); db.flush()
                    new_assignment_rows.append((asst, row))
                    added_names.append(asst.name)
            for aid in to_remove:
                asst = db.query(Assistant).filter(Assistant.id == aid).first()
                if asst:
                    removed_names.append(asst.name)

    # ---------------- Recurrence cascade ----------------
    if body.recurrence is not None:
        new_recur = body.recurrence
        if new_recur not in {"none", "daily", "weekly", "monthly", "frequent"}:
            raise HTTPException(400, "Invalid recurrence")
        rec_every = body.recurrence_every or 0
        delta = _delta_for_recurrence(new_recur, rec_every)
        if new_recur == "frequent" and delta is None:
            raise HTTPException(400, "recurrence_every required for 'frequent'")

        if job.is_template:
            # Direct edit of the template
            if job.recurrence != new_recur or (job.recurrence_every or 0) != rec_every:
                job.recurrence = new_recur
                job.recurrence_every = rec_every
                job.next_spawn_at = (datetime.utcnow() + delta) if delta else None
                changed.append("recurrence")
        elif job.parent_template_id:
            # Cascade to parent template — affects all future spawns
            parent = db.query(Job).filter(Job.id == job.parent_template_id).first()
            if parent and (parent.recurrence != new_recur or (parent.recurrence_every or 0) != rec_every):
                parent.recurrence = new_recur
                parent.recurrence_every = rec_every
                parent.next_spawn_at = (datetime.utcnow() + delta) if delta else None
                changed.append("recurrence")
        else:
            # One-off being upgraded to recurring → create a fresh template
            if new_recur != "none" and delta:
                tcode = _next_code(db)
                tmpl = Job(
                    code=tcode, title=job.title, description=job.description,
                    report_type=job.report_type, deadline_at=job.deadline_at,
                    status="pending", is_template=True,
                    recurrence=new_recur, recurrence_every=rec_every,
                    completion_mode=job.completion_mode,
                    accept_mode=job.accept_mode,
                    next_spawn_at=datetime.utcnow() + delta,
                    created_via=job.created_via,
                    created_by_user_id=me.id,
                )
                db.add(tmpl); db.flush()
                # Mirror current active assignments onto the template so future spawns inherit them
                for a in job.assignments:
                    if a.status in ("pending", "accepted", "in_progress", "done"):
                        db.add(JobAssignment(job_id=tmpl.id, assistant_id=a.assistant_id, status="pending"))
                job.parent_template_id = tmpl.id
                changed.append("recurrence")

    db.commit(); db.refresh(job)
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="job_updated",
                target_type="job", target_id=job.id,
                payload={"changed": changed, "added": added_names, "removed": removed_names})
    await hub.broadcast("job.updated", {"job_id": job.id, "changed": changed})
    notion_sync.enqueue(job.id)

    # Notify newly-assigned staff with the standard job card
    for asst, row in new_assignment_rows:
        try:
            await _notify_assignment(asst, row, job, header="📋 Admin က ပေးထားသော အလုပ်")
        except Exception:
            pass

    # Notify removed staff (one-shot)
    if removed_names:
        removed_assts = db.query(Assistant).filter(Assistant.name.in_(removed_names)).all()
        for asst in removed_assts:
            if not (asst.bot_id and asst.chat_id and asst.status == "active"):
                continue
            try:
                await manager.send_message(
                    asst.bot_id, asst.chat_id,
                    f"ℹ️ {job.code} — {job.title}\n"
                    f"Admin က ဒီ task ကို သင့်ထံမှ ဖယ်ပြီး တခြားသူထံ လွှဲထားပါပြီ။"
                )
            except Exception:
                pass

    # Admin-bot summary (for both reassignment and recurrence change)
    if "assignees" in changed or "recurrence" in changed:
        from ..models import Bot as BotM
        admin_bots = db.query(BotM).filter(
            BotM.bot_type == "admin_bot", BotM.status == "active",
            BotM.chat_id.isnot(None),
        ).all()
        lines = [f"🔧 {job.code} — {job.title}\n  Edited by: {me.email}"]
        if added_names:
            lines.append(f"  ➕ Added: {', '.join(added_names)}")
        if removed_names:
            lines.append(f"  ➖ Removed: {', '.join(removed_names)}")
        if "recurrence" in changed:
            lines.append(f"  🔁 Action → {body.recurrence}")
        msg = "\n".join(lines)
        for ab in admin_bots:
            try:
                await manager.send_message(ab.id, ab.chat_id, msg)
            except Exception:
                pass

    return _serialize(job, db)


@router.post("/{job_id}/reassign", response_model=JobOut)
async def reassign_job(job_id: int, body: JobReassignIn, db: Session = Depends(get_db),
                       me: User = Depends(get_current_user)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404)
    expanded = _expand_members(db, body.assistant_ids, body.group_ids)
    if not expanded:
        raise HTTPException(400, "At least one assignee required")

    already = {a.assistant_id for a in job.assignments
               if a.status in ("pending", "accepted", "in_progress", "done")}
    new_ids = [aid for aid in expanded if aid not in already]
    if not new_ids:
        raise HTTPException(400, "Selected assistants are already assigned")
    asst_rows = db.query(Assistant).filter(Assistant.id.in_(new_ids)).all()
    new_rows = []
    for a in asst_rows:
        row = JobAssignment(job_id=job.id, assistant_id=a.id, status="pending")
        db.add(row); new_rows.append((a, row))
    if job.status in ("cancelled", "overdue"):
        job.status = "pending"; job.completed_at = None
    db.commit(); db.refresh(job)
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="job_reassigned",
                target_type="job", target_id=job.id, payload={"to": new_ids})
    for a, row in new_rows:
        await _notify_assignment(a, row, job, header="📋 အလုပ် ပြန်တာဝန်ပေး")
    await hub.broadcast("job.status_changed", {"job_id": job.id, "status": job.status})
    notion_sync.enqueue(job.id)
    return _serialize(job, db)


@router.delete("/{job_id}")
async def delete_job(job_id: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(404)
    page_id = job.notion_page_id
    db.delete(job); db.commit()
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="job_deleted",
                target_type="job", target_id=job_id)
    if page_id:
        await notion_sync.archive(job_id)
    return {"ok": True}


@router.get("/{job_id}/reports/{report_id}/download")
def download_report(job_id: int, report_id: int, db: Session = Depends(get_db),
                    _u=Depends(get_current_user_qs)):
    r = db.query(Report).filter(Report.id == report_id, Report.job_id == job_id).first()
    if not r or not r.file_path:
        raise HTTPException(404)
    p = Path(r.file_path)
    if not p.is_absolute():
        p = (UPLOAD_PATH.parent / r.file_path).resolve()
    if not p.exists():
        raise HTTPException(404, "File missing")
    return FileResponse(str(p), filename=r.file_name or p.name,
                        media_type=r.mime_type or "application/octet-stream")


@router.get("/{job_id}/reports/{report_id}/preview")
def preview_report(job_id: int, report_id: int, db: Session = Depends(get_db),
                   _u=Depends(get_current_user_qs)):
    r = db.query(Report).filter(Report.id == report_id, Report.job_id == job_id).first()
    if not r:
        raise HTTPException(404)
    if r.type == "text":
        return {"type": "text", "content": r.content_text}
    if not r.file_path:
        raise HTTPException(404)
    p = Path(r.file_path)
    if not p.is_absolute():
        p = (UPLOAD_PATH.parent / r.file_path).resolve()
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), media_type=r.mime_type or "application/octet-stream")
