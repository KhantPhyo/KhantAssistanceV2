"""APScheduler tick: pre-deadline reminders, overdue flagging, recurrence spawning,
announcement re-pings, ad-hoc one-shot reminders triggered by /reminder, plus
two daily cron jobs: 17:00 EOD reminder and 09:30 morning carry-over reminder."""
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger

from ..db import SessionLocal
from ..models import (
    Job, JobAssignment, Assistant, Setting, Bot as BotModel,
    Announcement, AnnouncementRecipient, JobGroup, AssistantGroup,
)
from ..config import settings as _settings
from .bot_manager import manager
from .ws_hub import hub
from . import notion_sync

log = logging.getLogger("reminders")
scheduler = AsyncIOScheduler()


def _local_tz() -> ZoneInfo:
    try:
        return ZoneInfo(_settings.TIMEZONE)
    except Exception:
        return ZoneInfo("UTC")


def _frozen() -> bool:
    db = SessionLocal()
    try:
        s = db.query(Setting).filter(Setting.key == "bots_frozen").first()
        return bool(s and s.value == "1")
    finally:
        db.close()


async def _ping_admins(text: str):
    db = SessionLocal()
    try:
        rows = db.query(BotModel).filter(
            BotModel.bot_type == "admin_bot",
            BotModel.status == "active",
            BotModel.chat_id.isnot(None),
        ).all()
        targets = [(b.id, b.chat_id) for b in rows]
    finally:
        db.close()
    for bid, chat in targets:
        try:
            await manager.send_message(bid, chat, text)
        except Exception:
            pass


def _delta_for(rec: str, every: int):
    if rec == "daily":   return timedelta(days=1)
    if rec == "weekly":  return timedelta(days=7)
    if rec == "monthly": return timedelta(days=30)
    if rec == "frequent" and every > 0:
        return timedelta(days=every)
    return None


def _cadence_to_delta(c: str):
    if c == "2h":    return timedelta(hours=2)
    if c == "daily": return timedelta(days=1)
    return None


async def _spawn_recurring_instances():
    from ..routers.jobs import _next_code, _notify_assignment
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        templates = db.query(Job).filter(
            Job.is_template == True,  # noqa: E712
            Job.next_spawn_at.isnot(None),
            Job.next_spawn_at <= now,
        ).all()
        for t in templates:
            delta = _delta_for(t.recurrence, t.recurrence_every or 0)
            if not delta:
                continue
            aids = {a.assistant_id for a in t.assignments}
            group_ids = [jg.group_id for jg in db.query(JobGroup).filter(JobGroup.job_id == t.id).all()]
            if group_ids:
                for r in db.query(AssistantGroup).filter(AssistantGroup.group_id.in_(group_ids)).all():
                    aids.add(r.assistant_id)
            if not aids:
                t.next_spawn_at = now + delta
                continue

            code = _next_code(db)
            deadline = t.deadline_at
            if deadline and t.created_at:
                window = deadline - t.created_at
                deadline = now + window if window.total_seconds() > 0 else now + delta
            else:
                deadline = now + delta

            child = Job(
                code=code, title=t.title, description=t.description,
                report_type=t.report_type, deadline_at=deadline, status="pending",
                is_template=False, parent_template_id=t.id, recurrence="none",
                completion_mode=t.completion_mode or "all",
                created_via=t.created_via or "web",
            )
            db.add(child); db.flush()
            for gid in group_ids:
                db.add(JobGroup(job_id=child.id, group_id=gid))
            pairs = []
            for aid in aids:
                row = JobAssignment(job_id=child.id, assistant_id=aid, status="pending")
                db.add(row)
                pairs.append((aid, row))
            t.next_spawn_at = now + delta
            db.commit()
            db.refresh(child)
            for aid, row in pairs:
                asst = db.query(Assistant).filter(Assistant.id == aid).first()
                if asst:
                    try:
                        await _notify_assignment(asst, row, child, header="🔁 အလုပ် (ပုံမှန်)")
                    except Exception as e:
                        log.warning("notify failed: %s", e)
            try:
                await hub.broadcast("job.created", {"job_id": child.id, "code": child.code})
            except Exception:
                pass
    finally:
        db.close()


async def _reping_announcements():
    from ..routers.announcements import send_announcement_to
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        anns = db.query(Announcement).filter(
            Announcement.status == "active",
            Announcement.cadence.in_(["2h", "daily"]),
        ).all()
        for ann in anns:
            delta = _cadence_to_delta(ann.cadence)
            if not delta:
                continue
            recs = db.query(AnnouncementRecipient).filter(
                AnnouncementRecipient.announcement_id == ann.id,
                AnnouncementRecipient.acked_at.is_(None),
            ).all()
            for rec in recs:
                last = rec.last_sent_at or ann.created_at
                if last and (now - last) < delta:
                    continue
                asst = db.query(Assistant).filter(Assistant.id == rec.assistant_id).first()
                if not asst:
                    continue
                rec.last_sent_at = now
                db.commit()
                try:
                    await send_announcement_to(asst, ann, rec, header="🔁 သတိပေး")
                except Exception:
                    pass
    finally:
        db.close()


async def _remind_pending_all_mode():
    """For accept_mode=='all' jobs that are still 'pending' >= 1h after creation,
    ping every still-pending assignee once with a Leave button so on-leave staff
    can opt out and free the quorum."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        cutoff = now - timedelta(hours=1)
        jobs = db.query(Job).filter(
            Job.accept_mode == "all",
            Job.status == "pending",
            Job.is_template == False,  # noqa: E712
            Job.created_at <= cutoff,
        ).all()
        for job in jobs:
            pending_pairs = (
                db.query(JobAssignment, Assistant)
                .join(Assistant, Assistant.id == JobAssignment.assistant_id)
                .filter(
                    JobAssignment.job_id == job.id,
                    JobAssignment.status == "pending",
                    JobAssignment.reminded_at.is_(None),
                )
                .all()
            )
            if not pending_pairs:
                continue
            pending_names = [a.name for _, a in pending_pairs]
            for ja, asst in pending_pairs:
                if not (asst.bot_id and asst.chat_id and asst.status == "active"):
                    ja.reminded_at = now
                    continue
                others = [n for n in pending_names if n != asst.name]
                others_line = f"⏳ ကျန်နေသူ: {', '.join(others)}\n" if others else ""
                kb = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ လက်ခံ", callback_data=f"job:accept:{ja.id}"),
                        InlineKeyboardButton("❌ ငြင်း", callback_data=f"job:decline:{ja.id}"),
                    ],
                    [
                        InlineKeyboardButton("➡️ လွှဲ", callback_data=f"job:transfer:{ja.id}"),
                        InlineKeyboardButton("🏖️ Leave", callback_data=f"job:leave:{ja.id}"),
                    ],
                ])
                msg = (
                    f"⏰ Reminder — {job.code}\n"
                    f"📌 {job.title}\n"
                    f"{others_line}"
                    f"\nMode: all-accept (လူတိုင်း လက်ခံမှ in_progress)\n"
                    f"ခွင့်ဖြစ်ရင် 🏖️ Leave နှိပ်ပါ။"
                )
                try:
                    await manager.send_message(asst.bot_id, asst.chat_id, msg, reply_markup=kb)
                except Exception as e:
                    log.warning("remind send failed: %s", e)
                ja.reminded_at = now
        db.commit()
    finally:
        db.close()


async def tick():
    db = SessionLocal()
    try:
        reminder_min_row = db.query(Setting).filter(Setting.key == "reminder_minutes").first()
        reminder_min = int(reminder_min_row.value) if reminder_min_row and reminder_min_row.value.isdigit() else 15
        now = datetime.utcnow()

        due_soon = (
            db.query(JobAssignment, Job, Assistant)
            .join(Job, Job.id == JobAssignment.job_id)
            .join(Assistant, Assistant.id == JobAssignment.assistant_id)
            .filter(
                JobAssignment.status.in_(["pending", "accepted", "in_progress"]),
                JobAssignment.reminded_at.is_(None),
                Job.deadline_at.isnot(None),
                Job.deadline_at <= now + timedelta(minutes=reminder_min),
                Job.deadline_at > now,
                Job.is_template == False,  # noqa: E712
            ).all()
        )
        for a, j, asst in due_soon:
            if asst.bot_id and asst.chat_id and asst.status == "active":
                try:
                    await manager.send_message(asst.bot_id, asst.chat_id,
                        f"⏰ သတိပေး: {j.code} — {j.title}\nDeadline: {j.deadline_at:%Y-%m-%d %H:%M}")
                except Exception:
                    pass
            a.reminded_at = now
        db.commit()

        over = db.query(Job).filter(
            Job.status.in_(["pending", "in_progress"]),
            Job.deadline_at.isnot(None),
            Job.deadline_at < now,
            Job.is_template == False,  # noqa: E712
        ).all()
        for j in over:
            j.status = "overdue"
        if over:
            db.commit()
            # Notify each overdue job's still-non-terminal assignees + admin bot.
            # Status filter (status was "pending"|"in_progress") guarantees we
            # only fire ONCE per job — the next tick won't re-pick it up since
            # status is now "overdue".
            for j in over:
                await hub.broadcast("job.status_changed", {"job_id": j.id, "status": "overdue"})
                notion_sync.enqueue(j.id)

                pairs = (
                    db.query(JobAssignment, Assistant)
                    .join(Assistant, Assistant.id == JobAssignment.assistant_id)
                    .filter(
                        JobAssignment.job_id == j.id,
                        JobAssignment.status.in_(["pending", "accepted", "in_progress"]),
                    )
                    .all()
                )
                if not pairs:
                    continue

                deadline_str = j.deadline_at.strftime("%Y-%m-%d %H:%M") if j.deadline_at else "—"
                notified: list[str] = []
                skipped: list[str] = []
                if not _frozen():
                    for ja, asst in pairs:
                        if not (asst.bot_id and asst.chat_id and asst.status == "active"):
                            skipped.append(asst.name)
                            continue
                        try:
                            await manager.send_message(
                                asst.bot_id, asst.chat_id,
                                f"⚠️ OVERDUE — {j.code}\n"
                                f"📌 {j.title}\n"
                                f"⏰ Deadline: {deadline_str} (လွန်သွားပြီ)\n\n"
                                f"အမြန် ပြီးအောင်လုပ်ပြီး /finished {j.code} ဖြင့် report တင်ပါ။"
                            )
                            notified.append(asst.name)
                        except Exception as e:
                            log.warning("overdue notify failed: %s", e)
                            skipped.append(asst.name)

                # Admin-bot summary, even if no staff received DMs (so admin knows)
                lines = [
                    f"⚠️ OVERDUE — {j.code}",
                    f"📌 {j.title}",
                    f"⏰ Deadline: {deadline_str} (passed)",
                ]
                if notified:
                    lines.append(f"  ✓ Notified: {', '.join(notified)}")
                if skipped:
                    lines.append(f"  ⚠️ Could not notify (no bot bound): {', '.join(skipped)}")
                await _ping_admins("\n".join(lines))
    finally:
        db.close()

    try:
        await _spawn_recurring_instances()
    except Exception:
        log.exception("recurrence spawn failed")
    try:
        await _reping_announcements()
    except Exception:
        log.exception("announcement reping failed")
    try:
        await _remind_pending_all_mode()
    except Exception:
        log.exception("all-mode pending reminder failed")


# --------- ad-hoc /reminder one-shots from admin bot ---------

async def _one_shot_fire(job_id: int):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return
        rows = db.query(JobAssignment, Assistant).join(
            Assistant, Assistant.id == JobAssignment.assistant_id
        ).filter(
            JobAssignment.job_id == job_id,
            JobAssignment.status.in_(["pending", "accepted", "in_progress"]),
        ).all()
        for a, asst in rows:
            if asst.bot_id and asst.chat_id and asst.status == "active":
                try:
                    await manager.send_message(asst.bot_id, asst.chat_id,
                        f"🔔 သတိပေးချက်: {job.code} — {job.title}")
                except Exception:
                    pass
    finally:
        db.close()


def schedule_one_shot(job_id: int, when: datetime):
    scheduler.add_job(_one_shot_fire, DateTrigger(run_date=when), args=[job_id],
                      id=f"oneshot:{job_id}:{when.isoformat()}", replace_existing=False)


# ---------------- EOD (17:00 local) + Morning (09:30 local) reminders ----------------

async def _gather_open_assignments(only_carryover: bool):
    """Return list of (assistant, list_of(job, assignment)) for non-terminal
    work. If only_carryover=True, restricts to jobs created BEFORE today's local
    midnight — i.e. work that has rolled over to a new day."""
    tz = _local_tz()
    local_now = datetime.now(tz)
    local_today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_today_start = local_today_start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    db = SessionLocal()
    try:
        q = (
            db.query(JobAssignment, Job, Assistant)
            .join(Job, Job.id == JobAssignment.job_id)
            .join(Assistant, Assistant.id == JobAssignment.assistant_id)
            .filter(
                Job.is_template == False,  # noqa: E712
                Job.status.in_(["pending", "in_progress", "overdue"]),
                JobAssignment.status.in_(["pending", "accepted", "in_progress"]),
            )
        )
        if only_carryover:
            q = q.filter(Job.created_at < utc_today_start)
        rows = q.all()
    finally:
        db.close()

    by_asst: dict[int, list] = defaultdict(list)
    asst_by_id: dict[int, Assistant] = {}
    for ja, job, asst in rows:
        by_asst[asst.id].append((job, ja))
        asst_by_id[asst.id] = asst
    return [(asst_by_id[aid], items) for aid, items in by_asst.items()]


async def _send_summary_reminder(label: str, only_carryover: bool):
    """Shared body for 17:00 and 09:30 cron reminders."""
    if _frozen():
        log.info("[%s] bots frozen, skipping", label)
        return
    grouped = await _gather_open_assignments(only_carryover=only_carryover)
    if not grouped:
        log.info("[%s] nothing to remind", label)
        return

    total_jobs = 0
    admin_lines: list[str] = []

    for asst, items in grouped:
        total_jobs += len(items)
        admin_lines.append(f"  • {asst.name} → {len(items)} job(s)")
        if not (asst.bot_id and asst.chat_id and asst.status == "active"):
            continue
        lines = [f"⏰ {label}\nအောက်ပါ Job တွေ မပြီးသေးပါ —"]
        for job, ja in items:
            badge = "⏳" if ja.status == "pending" else "🔄"
            dl = job.deadline_at.strftime("%m-%d %H:%M") if job.deadline_at else "—"
            lines.append(f"{badge} {job.code} — {job.title} (⏰ {dl})")
        try:
            await manager.send_message(asst.bot_id, asst.chat_id, "\n".join(lines))
        except Exception as e:
            log.warning("send remind failed: %s", e)

    # Admin summary
    admin_msg = (
        f"📊 {label}\n"
        f"မပြီးသေးတဲ့ Job: {total_jobs}\n"
        f"သက်ဆိုင်တဲ့ ဝန်ထမ်း: {len(grouped)}\n\n"
        + "\n".join(admin_lines)
    )
    await _ping_admins(admin_msg)


async def _eod_reminder():
    """17:00 local — every staff with open work gets a list; admins get a summary."""
    await _send_summary_reminder("ညနေ ၅:၀၀ — EOD Reminder", only_carryover=False)


async def _morning_reminder():
    """09:30 local — focus on jobs that rolled over from previous day(s)."""
    await _send_summary_reminder(
        "မနက် ၉:၃၀ — အရင်ရက် ကျန် Task များ", only_carryover=True
    )


def start_scheduler():
    tz = _local_tz()
    scheduler.add_job(tick, "interval", seconds=60, id="reminder_tick", replace_existing=True)
    scheduler.add_job(
        _eod_reminder,
        CronTrigger(hour=17, minute=0, timezone=tz),
        id="eod_remind",
        replace_existing=True,
    )
    scheduler.add_job(
        _morning_reminder,
        CronTrigger(hour=9, minute=30, timezone=tz),
        id="morning_remind",
        replace_existing=True,
    )
    scheduler.start()
    log.info("scheduler started: tick(60s) + eod(17:00 %s) + morning(09:30 %s)", tz, tz)
