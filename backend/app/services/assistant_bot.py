"""Per-assistant bot handlers: /start binding, accept/decline/transfer, /finished + attachment."""
import re
import uuid
import logging
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

from sqlalchemy import func

from ..db import SessionLocal
from ..models import Bot as BotModel, Assistant, Job, JobAssignment, Report, Setting, AnnouncementRecipient, Announcement
from ..config import UPLOAD_PATH
from .ws_hub import hub
from . import audit, rate_limit, notion_sync

log = logging.getLogger("assistant_bot")
JOB_RE = re.compile(r"(?:\d{2}[A-Za-z]{3}\d{3,}|JOB-\d{3,})", re.IGNORECASE)

REPORT_TYPE_MM = {
    "photo": "ဓာတ်ပုံ", "video": "ဗီဒီယို",
    "document": "ဖိုင်", "file": "ဖိုင်", "text": "စာသား", "any": "မည်သည့်ပုံစံမဆို",
}


def build_assistant_handlers(app: Application, bot_id: int):
    app.bot_data["bot_id"] = bot_id
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler(["jobs", "myjobs"], my_jobs))
    app.add_handler(CommandHandler("pending", my_pending))
    app.add_handler(CommandHandler("finished", finished_cmd))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.TEXT, catch_submission))


def _frozen() -> bool:
    db = SessionLocal()
    try:
        s = db.query(Setting).filter(Setting.key == "bots_frozen").first()
        return bool(s and s.value == "1")
    finally:
        db.close()


def _rt_label(rt: str) -> str:
    return REPORT_TYPE_MM.get(rt, rt)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bot_id = ctx.bot_data["bot_id"]
    if not rate_limit.allow(update.effective_chat.id):
        return
    chat_id = str(update.effective_chat.id)
    db = SessionLocal()
    try:
        bot = db.query(BotModel).filter(BotModel.id == bot_id).first()
        if not bot:
            return
        asst = db.query(Assistant).filter(Assistant.bot_id == bot_id).first()
        if not asst:
            await update.message.reply_text("ဒီ bot ကို assistant နှင့် မချိတ်ရသေးပါ။")
            return
        if bot.status == "active":
            await update.message.reply_text(f"✅ {asst.name} အနေဖြင့် ချိတ်ဆက်ပြီးသား။")
            return
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ လက်ခံမယ်", callback_data=f"bind:accept:{bot_id}"),
            InlineKeyboardButton("❌ ငြင်းမယ်", callback_data=f"bind:decline:{bot_id}"),
        ]])
        await update.message.reply_text(
            f"မင်္ဂလာပါ {asst.name}! Assistant အဖြစ် ဖိတ်ခေါ်နေပါသည်။\n"
            f"ဒီ chat ကို ချိတ်ဆက်ရန် ⤵︎",
            reply_markup=kb,
        )
    finally:
        db.close()


async def help_cmd(update: Update, ctx):
    await update.message.reply_text(
        "📋 Commands —\n"
        "/jobs — pending + in_progress အလုပ်များ\n"
        "/pending — လုပ်နေဆဲ (in_progress) အလုပ်များ\n"
        "/finished [JOB ID] — ပြီးစီးကြောင်း တင်ပြ\n"
        "    (photo/video/document attach + caption: /finished 26May0001)\n\n"
        "💡 Job notify တိုင်း ✅ လက်ခံ / ❌ ငြင်း / ➡️ လွှဲ button နှိပ်လို့ရ။\n"
        "💡 /finished တစ်ခုထဲ ရိုက်ရင် — သင့်လက်ရှိ active jobs အကုန် ပြပေးမယ်။"
    )


async def my_jobs(update: Update, ctx):
    if _frozen() or not rate_limit.allow(update.effective_chat.id):
        return
    bot_id = ctx.bot_data["bot_id"]
    db = SessionLocal()
    try:
        asst = db.query(Assistant).filter(Assistant.bot_id == bot_id).first()
        if not asst:
            return
        rows = (
            db.query(JobAssignment, Job)
            .join(Job, Job.id == JobAssignment.job_id)
            .filter(JobAssignment.assistant_id == asst.id,
                    JobAssignment.status.in_(["accepted", "in_progress", "pending"]))
            .all()
        )
        if not rows:
            await update.message.reply_text("လက်ရှိ အလုပ် မရှိပါ။")
            return
        lines = [f"{j.code} — {j.title} [{a.status}]" for a, j in rows]
        await update.message.reply_text("📋 သင့် အလုပ်များ:\n" + "\n".join(lines))
    finally:
        db.close()


async def my_pending(update: Update, ctx):
    if _frozen() or not rate_limit.allow(update.effective_chat.id):
        return
    bot_id = ctx.bot_data["bot_id"]
    db = SessionLocal()
    try:
        asst = db.query(Assistant).filter(Assistant.bot_id == bot_id).first()
        if not asst:
            return
        rows = (
            db.query(JobAssignment, Job)
            .join(Job, Job.id == JobAssignment.job_id)
            .filter(JobAssignment.assistant_id == asst.id,
                    JobAssignment.status.in_(["accepted", "in_progress"]))
            .all()
        )
        if not rows:
            await update.message.reply_text("လုပ်နေဆဲ မရှိပါ။")
            return
        lines = [f"{j.code} — {j.title}" for _, j in rows]
        await update.message.reply_text("🔄 လုပ်နေဆဲ:\n" + "\n".join(lines))
    finally:
        db.close()


async def callback(update: Update, ctx):
    if _frozen() or not rate_limit.allow(update.effective_chat.id):
        return
    q = update.callback_query
    await q.answer()
    parts = (q.data or "").split(":")
    bot_id = ctx.bot_data["bot_id"]
    chat_id = str(q.from_user.id)

    if parts[0] == "bind":
        await _handle_bind(q, parts, bot_id, chat_id)
    elif parts[0] == "job":
        await _handle_job_cb(q, parts, bot_id)
    elif parts[0] == "ann":
        await _handle_ann_cb(q, parts)


async def _handle_ann_cb(q, parts):
    if len(parts) < 3 or parts[1] != "ack":
        return
    rid = int(parts[2])
    db = SessionLocal()
    try:
        rec = db.query(AnnouncementRecipient).filter(AnnouncementRecipient.id == rid).first()
        if not rec:
            return
        if rec.acked_at is None:
            rec.acked_at = datetime.utcnow()
            db.commit()
        ann = db.query(Announcement).filter(Announcement.id == rec.announcement_id).first()
        if ann:
            remaining = db.query(AnnouncementRecipient).filter(
                AnnouncementRecipient.announcement_id == ann.id,
                AnnouncementRecipient.acked_at.is_(None),
            ).count()
            if remaining == 0 and ann.status != "closed":
                ann.status = "closed"
                db.commit()
        await q.edit_message_text(f"✅ ({ann.code if ann else ''}) သိရှိကြောင်း မှတ်ပြီး။")
        await hub.broadcast("announcement.ack", {"recipient_id": rid, "announcement_id": rec.announcement_id})
    finally:
        db.close()


async def _handle_bind(q, parts, bot_id, chat_id):
    action = parts[1]
    db = SessionLocal()
    try:
        bot = db.query(BotModel).filter(BotModel.id == bot_id).first()
        asst = db.query(Assistant).filter(Assistant.bot_id == bot_id).first()
        if not bot or not asst:
            return
        if action == "accept":
            bot.chat_id = chat_id
            bot.status = "active"
            bot.last_seen_at = datetime.utcnow()
            asst.chat_id = chat_id
            asst.status = "active"
            db.commit()
            audit.write(db, actor_type="assistant_bot", actor_id=bot_id,
                        action="bot_paired", target_type="assistant", target_id=asst.id,
                        payload={"chat_id": chat_id})
            await q.edit_message_text(f"✅ ချိတ်ဆက်မှု ပြီးပါပြီ။ ကြိုဆိုပါတယ် {asst.name}!")
            await hub.broadcast("binding.updated", {"assistant_id": asst.id, "status": "active", "chat_id": chat_id})
        else:
            bot.status = "revoked"
            asst.status = "removed"
            db.commit()
            await q.edit_message_text("❌ ငြင်းပယ်လိုက်ပါပြီ။")
            await hub.broadcast("binding.updated", {"assistant_id": asst.id, "status": "revoked"})
    finally:
        db.close()


async def _notify_admins(text: str):
    """Fire-and-forget DM to every active admin bot's owner chat."""
    from .bot_manager import manager
    db = SessionLocal()
    try:
        admins = db.query(BotModel).filter(
            BotModel.bot_type == "admin_bot",
            BotModel.status == "active",
            BotModel.chat_id.isnot(None),
        ).all()
        rows = [(a.id, a.chat_id) for a in admins]
    finally:
        db.close()
    for bid, chat in rows:
        try:
            await manager.send_message(bid, chat, text)
        except Exception:
            pass


def _accept_mode_promote(db, job: Job) -> bool:
    """For accept_mode='all': if every non-pending assignment is in a terminal/
    accepted state (accepted/in_progress/done/declined/transferred/on_leave/superseded)
    AND at least one is accepted, promote all 'accepted' rows to 'in_progress' and
    flip job.status to in_progress. Returns True if a promotion happened."""
    pending_ct = db.query(JobAssignment).filter(
        JobAssignment.job_id == job.id,
        JobAssignment.status == "pending",
    ).count()
    if pending_ct > 0:
        return False
    accepted_rows = db.query(JobAssignment).filter(
        JobAssignment.job_id == job.id,
        JobAssignment.status == "accepted",
    ).all()
    if not accepted_rows:
        return False
    for r in accepted_rows:
        r.status = "in_progress"
    if job.status != "in_progress":
        job.status = "in_progress"
    db.commit()
    return True


async def _handle_job_cb(q, parts, bot_id):
    from .bot_manager import manager
    action = parts[1]
    aid = int(parts[2])
    db = SessionLocal()
    try:
        a = db.query(JobAssignment).filter(JobAssignment.id == aid).first()
        if not a:
            await q.edit_message_text("တာဝန်ပေးမှု ရှာမတွေ့ပါ။")
            return
        job = db.query(Job).filter(Job.id == a.job_id).first()
        asst = db.query(Assistant).filter(Assistant.id == a.assistant_id).first()
        accept_mode = (job.accept_mode or "any")

        if action == "accept":
            a.accepted_at = datetime.utcnow()
            promoted = False
            if accept_mode == "all":
                # Hold this acceptance in 'accepted'; flip to in_progress only when
                # every other non-pending assignment is also resolved.
                a.status = "accepted"
                db.commit()
                promoted = _accept_mode_promote(db, job)
            else:  # "any" — first accepter moves the job to in_progress
                a.status = "in_progress"
                if job.status != "in_progress":
                    job.status = "in_progress"
                db.commit()
                promoted = True

            audit.write(db, actor_type="assistant_bot", actor_id=bot_id,
                        action="job_accepted", target_type="job", target_id=job.id,
                        payload={"assistant_id": asst.id, "accept_mode": accept_mode,
                                 "promoted": promoted})

            deadline = job.deadline_at.strftime("%Y-%m-%d %H:%M") if job.deadline_at else "—"
            type_mm = _rt_label(job.report_type)
            desc = f"\n📝 {job.description}" if job.description else ""

            if accept_mode == "all" and not promoted:
                # Show waiting status to the staff
                pending_names = [
                    nm for nm, in db.query(Assistant.name).join(
                        JobAssignment, JobAssignment.assistant_id == Assistant.id
                    ).filter(
                        JobAssignment.job_id == job.id,
                        JobAssignment.status == "pending",
                    ).all()
                ]
                wait_line = (f"\n⏳ ကျန်နေသူ: {', '.join(pending_names)}"
                             if pending_names else "")
                await q.edit_message_text(
                    f"✅ {job.code} — {asst.name} လက်ခံပြီး။\n"
                    f"📌 {job.title}{desc}\n"
                    f"⏰ {deadline}\n"
                    f"🤝 All-accept mode — အဖွဲ့လူတိုင်း လက်ခံမှ in_progress ဖြစ်မယ်။"
                    f"{wait_line}"
                )
            else:
                # Two-message UX: details first, then a single line with the
                # /finished command alone so the staff can long-press → Copy
                # → paste back as their completion report.
                await q.edit_message_text(
                    f"✅ {job.code} — {asst.name} လက်ခံပြီး။\n"
                    f"📌 {job.title}{desc}\n"
                    f"⏰ {deadline}\n"
                    f"📎 တင်ရမယ့်ပုံစံ: {type_mm}\n"
                    f"\nTask ပြီးရင် အောက်ကစာကို copy ပြီး ပူးတွဲဖိုင်/စာသား နဲ့အတူ ပို့ပေးပါ —"
                )
                try:
                    await q.message.reply_text(f"/finished {job.code}")
                except Exception:
                    pass

            await hub.broadcast("job.status_changed", {"job_id": job.id, "status": job.status})
            notion_sync.enqueue(job.id)
            if promoted and accept_mode == "all":
                await _notify_admins(f"🎉 {job.code} — အဖွဲ့လူတိုင်း လက်ခံပြီး in_progress သို့ ပြောင်းသွားပြီ။")
            await _notify_admins(f"✅ {asst.name} — {job.code} လက်ခံပြီး။")
        elif action == "leave":
            a.status = "on_leave"
            db.commit()
            audit.write(db, actor_type="assistant_bot", actor_id=bot_id,
                        action="job_leave", target_type="job", target_id=job.id,
                        payload={"assistant_id": asst.id})
            promoted = False
            if accept_mode == "all":
                promoted = _accept_mode_promote(db, job)
            await q.edit_message_text(f"🏖️ {job.code} — {asst.name} ခွင့်ဖြစ်ကြောင်း မှတ်ပြီး။")
            await hub.broadcast("job.status_changed", {"job_id": job.id, "status": job.status})
            notion_sync.enqueue(job.id)
            await _notify_admins(f"🏖️ {asst.name} — {job.code} ခွင့်ယူ (on leave)。")
            if promoted:
                await _notify_admins(f"🎉 {job.code} — ကျန်လူ အကုန် လက်ခံပြီးလို့ in_progress သို့ ပြောင်းပြီ။")
        elif action == "decline":
            a.status = "declined"
            db.commit()
            audit.write(db, actor_type="assistant_bot", actor_id=bot_id,
                        action="job_declined", target_type="job", target_id=job.id,
                        payload={"assistant_id": asst.id})
            promoted = False
            if accept_mode == "all":
                promoted = _accept_mode_promote(db, job)
            remaining = db.query(JobAssignment).filter(
                JobAssignment.job_id == job.id,
                JobAssignment.status.in_(["pending", "accepted", "in_progress"])
            ).count()
            if remaining == 0:
                job.status = "cancelled"
                db.commit()
            await q.edit_message_text(f"❌ {job.code} — ငြင်းပယ်ပြီး။")
            await hub.broadcast("job.status_changed", {"job_id": job.id, "status": job.status})
            notion_sync.enqueue(job.id)
            await _notify_admins(f"❌ {asst.name} — {job.code} ငြင်းပယ်ပြီး။")
            if promoted:
                await _notify_admins(f"🎉 {job.code} — ကျန်လူ အကုန် လက်ခံပြီးလို့ in_progress သို့ ပြောင်းပြီ။")
        elif action == "transfer":
            others = db.query(Assistant).filter(Assistant.id != asst.id, Assistant.status == "active").all()
            if not others:
                await q.edit_message_text("လွှဲပေးနိုင်မည့် တခြား assistant မရှိပါ။")
                return
            buttons = [[InlineKeyboardButton(o.name, callback_data=f"job:transfer_to:{aid}:{o.id}")] for o in others]
            await q.edit_message_text(f"{job.code} ကို မည်သူ့ကို လွှဲမည်နည်း?", reply_markup=InlineKeyboardMarkup(buttons))
        elif action == "transfer_to":
            target_id = int(parts[3])
            target = db.query(Assistant).filter(Assistant.id == target_id).first()
            a.status = "transferred"
            a.transferred_to_id = target_id
            new_a = JobAssignment(job_id=job.id, assistant_id=target_id, status="pending")
            db.add(new_a)
            db.commit()
            audit.write(db, actor_type="assistant_bot", actor_id=bot_id,
                        action="job_transferred", target_type="job", target_id=job.id,
                        payload={"from": asst.id, "to": target_id})
            await q.edit_message_text(f"➡️ {job.code} ကို {target.name} ထံ လွှဲပြီး။")
            if target and target.bot_id and target.chat_id:
                kb = _job_buttons(new_a.id)
                deadline = job.deadline_at.strftime("%Y-%m-%d %H:%M") if job.deadline_at else "—"
                desc = f"\n📝 {job.description}" if job.description else ""
                await manager.send_message(
                    target.bot_id, target.chat_id,
                    f"📋 အလုပ်အသစ် (လွှဲပြောင်း) {job.code}\n📌 {job.title}{desc}\n⏰ {deadline}\n📎 {_rt_label(job.report_type)}",
                    reply_markup=kb,
                )
            await hub.broadcast("job.status_changed", {"job_id": job.id, "status": job.status})
            notion_sync.enqueue(job.id)
            await _notify_admins(f"➡️ {asst.name} — {job.code} ကို {target.name if target else '?'} ထံ လွှဲပြီး။")
    finally:
        db.close()


def _job_buttons(assignment_id: int) -> InlineKeyboardMarkup:
    """4-button keyboard. Mirrors routers.jobs._job_keyboard but lives here too
    to avoid an import cycle on transfer-to notifications."""
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


async def _list_my_active(bot_id: int) -> str:
    """Return a Telegram-ready string listing this bot's assistant's pending +
    in-progress jobs, with codes that the user can copy directly."""
    db = SessionLocal()
    try:
        asst = db.query(Assistant).filter(Assistant.bot_id == bot_id).first()
        if not asst:
            return ""
        rows = (
            db.query(JobAssignment, Job)
            .join(Job, Job.id == JobAssignment.job_id)
            .filter(
                JobAssignment.assistant_id == asst.id,
                JobAssignment.status.in_(["pending", "accepted", "in_progress"]),
            )
            .order_by(Job.deadline_at.asc().nullslast())
            .all()
        )
        if not rows:
            return ""
        lines = []
        for ja, j in rows:
            badge = "⏳" if ja.status == "pending" else "🔄"
            dl = j.deadline_at.strftime("%m-%d %H:%M") if j.deadline_at else "—"
            lines.append(f"{badge} {j.code} — {j.title} (⏰ {dl})")
        return "\n".join(lines)
    finally:
        db.close()


def _finished_help(active: str = "") -> str:
    base = (
        "အသုံးပြုပုံ: /finished [JOB ID]  ([ပူးတွဲ ဖိုင်/စာသား] နှင့်)\n"
        "Example: /finished 26May0001\n"
        "         (photo/video/document attach လုပ်ပြီး caption ထဲ ရေးပေးပါ)"
    )
    if active:
        base += "\n\n📋 သင့် လက်ရှိ pending / in-progress အလုပ်များ:\n" + active
    else:
        base += "\n\nℹ️ သင့်မှာ လက်ရှိ active job မရှိပါ။"
    return base


async def finished_cmd(update: Update, ctx):
    if _frozen() or not rate_limit.allow(update.effective_chat.id):
        return
    bot_id = ctx.bot_data["bot_id"]
    text = update.message.text or ""
    m = JOB_RE.search(text)
    if not m:
        active = await _list_my_active(bot_id)
        await update.message.reply_text(_finished_help(active))
        return
    await _process_finish(update, ctx, m.group(0), text_after_cmd=text[m.end():].strip())


async def catch_submission(update: Update, ctx):
    msg = update.message
    if not msg:
        return
    text = (msg.text or msg.caption or "") or ""
    lowered = text.lstrip().lower()
    if lowered.startswith("/finished"):
        m = JOB_RE.search(text)
        if not m:
            bot_id = ctx.bot_data["bot_id"]
            active = await _list_my_active(bot_id)
            await msg.reply_text(_finished_help(active))
            return
        stripped = text.lstrip()
        after = stripped.split(None, 1)[1] if " " in stripped else ""
        await _process_finish(update, ctx, m.group(0), text_after_cmd=after)
        return
    if text.startswith("/"):
        return
    if _frozen() or not rate_limit.allow(update.effective_chat.id):
        return

    bot_id = ctx.bot_data["bot_id"]
    db = SessionLocal()
    try:
        asst = db.query(Assistant).filter(Assistant.bot_id == bot_id).first()
        if not asst:
            return
        m = JOB_RE.search(text)
        if m:
            await _process_finish(update, ctx, m.group(0), text_after_cmd=text)
            return
        active = (
            db.query(JobAssignment, Job)
            .join(Job, Job.id == JobAssignment.job_id)
            .filter(JobAssignment.assistant_id == asst.id, JobAssignment.status.in_(["in_progress", "accepted"]))
            .all()
        )
        if not active:
            return
        ids = "\n".join([f"• {j.code} — {j.title}" for _, j in active])
        await msg.reply_text(
            f"ဘယ် JOB ID အတွက်လဲ?\n{ids}\n\nCaption: /finished <JOB-ID>"
        )
    finally:
        db.close()


def _matches_type(rt: str, msg) -> bool:
    if rt == "any":
        return bool(msg.photo or msg.video or msg.document or msg.text or msg.caption)
    if rt == "photo":
        return bool(msg.photo)
    if rt == "video":
        return bool(msg.video)
    if rt in ("document", "file"):
        return bool(msg.document)
    if rt == "text":
        return True  # validated separately
    return False


async def _process_finish(update: Update, ctx, code: str, text_after_cmd: str = ""):
    bot_id = ctx.bot_data["bot_id"]
    msg = update.message
    db = SessionLocal()
    try:
        asst = db.query(Assistant).filter(Assistant.bot_id == bot_id).first()
        if not asst:
            return
        job = db.query(Job).filter(func.lower(Job.code) == code.lower()).first()
        if not job:
            await msg.reply_text(f"{code} ကို ရှာမတွေ့ပါ။")
            return
        a = (
            db.query(JobAssignment)
            .filter(JobAssignment.job_id == job.id, JobAssignment.assistant_id == asst.id,
                    JobAssignment.status.in_(["accepted", "in_progress", "pending"]))
            .first()
        )
        if not a:
            await msg.reply_text(f"{code} မှာ သင် တာဝန်ယူထားသူ မဟုတ်ပါ။")
            return

        rt = job.report_type
        file_path = None
        file_name = None
        mime_type = None
        content_text = None
        tg_file = None

        try:
            if rt == "photo" and msg.photo:
                tg_file = await msg.photo[-1].get_file()
                ext = ".jpg"; mime_type = "image/jpeg"
            elif rt == "video" and msg.video:
                tg_file = await msg.video.get_file()
                mime_type = msg.video.mime_type or "video/mp4"
                ext = "." + (mime_type.split("/")[-1] if mime_type else "mp4")
            elif rt in ("document", "file") and msg.document:
                tg_file = await msg.document.get_file()
                file_name = msg.document.file_name
                mime_type = msg.document.mime_type
                ext = Path(file_name or "file").suffix or ".bin"
            elif rt == "text":
                content_text = text_after_cmd.strip() or (msg.text or msg.caption or "")
                if not content_text:
                    await msg.reply_text(f"{code} သည် စာသား လိုအပ်ပါသည်။")
                    return
            elif rt == "any":
                if msg.photo:
                    tg_file = await msg.photo[-1].get_file(); ext = ".jpg"; mime_type = "image/jpeg"
                elif msg.video:
                    tg_file = await msg.video.get_file()
                    mime_type = msg.video.mime_type or "video/mp4"
                    ext = "." + (mime_type.split("/")[-1] if mime_type else "mp4")
                elif msg.document:
                    tg_file = await msg.document.get_file()
                    file_name = msg.document.file_name
                    mime_type = msg.document.mime_type
                    ext = Path(file_name or "file").suffix or ".bin"
                else:
                    content_text = text_after_cmd.strip() or (msg.text or msg.caption or "")
                    if not content_text:
                        await msg.reply_text(f"{code} သည် ဖိုင်/စာသား တစ်ခု လိုအပ်ပါသည်။")
                        return
            else:
                await msg.reply_text(f"{code} သည် {_rt_label(rt)} လိုအပ်ပါသည်။ မှန်သော အမျိုးအစား ပူးတွဲ ပို့ပါ။")
                return
        except Exception as e:
            log.exception("download error: %s", e)
            await msg.reply_text("ပူးတွဲဖိုင် ဆွဲယူ၍ မရပါ။ ပြန်ကြိုးစားပါ။")
            return

        if tg_file is not None:
            folder = UPLOAD_PATH / "reports" / str(job.id)
            folder.mkdir(parents=True, exist_ok=True)
            fname = f"{uuid.uuid4().hex}{ext}"
            dest = folder / fname
            await tg_file.download_to_drive(custom_path=str(dest))
            file_path = str(dest.relative_to(UPLOAD_PATH.parent)) if dest.is_absolute() else str(dest)
            if not file_name:
                file_name = fname

        rep = Report(job_id=job.id, assistant_id=asst.id, type=rt,
                     content_text=content_text, file_path=file_path,
                     file_name=file_name, mime_type=mime_type)
        db.add(rep)
        a.status = "done"
        a.finished_at = datetime.utcnow()
        db.flush()

        if (job.completion_mode or "all") == "any":
            others = db.query(JobAssignment).filter(
                JobAssignment.job_id == job.id,
                JobAssignment.id != a.id,
                JobAssignment.status.in_(["pending", "accepted", "in_progress"]),
            ).all()
            for o in others:
                o.status = "superseded"
            job.status = "done"
            job.completed_at = datetime.utcnow()
        else:
            remaining = db.query(JobAssignment).filter(
                JobAssignment.job_id == job.id,
                JobAssignment.status.notin_(["declined", "transferred", "done", "superseded"]),
            ).count()
            if remaining == 0:
                job.status = "done"
                job.completed_at = datetime.utcnow()
        db.commit()
        audit.write(db, actor_type="assistant_bot", actor_id=bot_id,
                    action="job_finished", target_type="job", target_id=job.id,
                    payload={"assistant_id": asst.id, "report_id": rep.id})

        await msg.reply_text(f"✅ {code} ပြီးစီးကြောင်း မှတ်ပြီး။ အစီရင်ခံစာ သိမ်းပြီး။")
        await hub.broadcast("report.uploaded", {"job_id": job.id, "report_id": rep.id})
        await hub.broadcast("job.status_changed", {"job_id": job.id, "status": job.status})
        notion_sync.enqueue(job.id)

        # notify owner admin bot if any
        try:
            from ..models import Bot as BotM
            admin_bots = db.query(BotM).filter(BotM.bot_type == "admin_bot",
                                               BotM.status == "active",
                                               BotM.chat_id.isnot(None)).all()
            from .bot_manager import manager
            for ab in admin_bots:
                try:
                    await manager.send_message(ab.id, ab.chat_id,
                        f"📥 {asst.name} သည် {job.code} ပြီးစီးကြောင်း တင်ပြီး။")
                except Exception:
                    pass
        except Exception:
            pass
    finally:
        db.close()
