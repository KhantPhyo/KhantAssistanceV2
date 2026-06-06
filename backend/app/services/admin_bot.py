"""Admin remote-control bot.

v2 command set: /newjob (inline), /createjob (multi-step), /jobs [filter],
/assistants, /broadcast, /stats, /report, /reassign, /cancel, /reminder,
/pause, /resume. Hardened with anti-hijack pairing, allowlist, and rate limiting.
"""
import re
import logging
from datetime import datetime, time, timedelta
from functools import wraps
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from ..db import SessionLocal
from ..models import (
    Bot as BotModel, Assistant, Job, JobAssignment, Report, Setting, User, Group,
)
from ..config import settings, UPLOAD_PATH
from . import audit, rate_limit, notion_sync, targets as targets_svc
from .ws_hub import hub

log = logging.getLogger("admin_bot")

# Hard-blocked verbs — refused even if /newjob_<x> aliasing is attempted
BLOCKED_COMMANDS = {
    "delete_admin", "remove_admin", "drop_admin", "drop_db",
    "rotate_secret", "wipe_uploads", "drop_database",
}


# --------------- helpers ---------------

def _frozen() -> bool:
    db = SessionLocal()
    try:
        s = db.query(Setting).filter(Setting.key == "bots_frozen").first()
        return bool(s and s.value == "1")
    finally:
        db.close()


def _local_tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.TIMEZONE)
    except Exception:
        return ZoneInfo("UTC")


def _today_utc_midnight() -> datetime:
    tz = _local_tz()
    local_now = datetime.now(tz)
    local_midnight = datetime.combine(local_now.date(), time.min, tzinfo=tz)
    return local_midnight.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _authorize(update: Update, bot_id: int) -> tuple[bool, Optional[BotModel], Optional[User]]:
    """Returns (ok, bot, owner). On first /start by the right Telegram username, binds chat_id."""
    db = SessionLocal()
    try:
        bot = db.query(BotModel).filter(BotModel.id == bot_id, BotModel.bot_type == "admin_bot").first()
        if not bot:
            return False, None, None
        owner = db.query(User).filter(User.id == bot.owner_user_id).first() if bot.owner_user_id else None
        chat_id = str(update.effective_chat.id)
        sender_username = (update.effective_user.username or "").lstrip("@").lower() if update.effective_user else ""
        expected = (owner.telegram_username or "").lstrip("@").lower() if owner else ""

        if bot.chat_id:
            return (bot.chat_id == chat_id, bot, owner)

        # Pairing: only the owner's verified username may bind
        if not expected:
            return False, bot, owner  # owner must set telegram_username first
        if sender_username and sender_username == expected:
            bot.chat_id = chat_id
            bot.status = "active"
            bot.last_seen_at = datetime.utcnow()
            db.commit()
            audit.write(db, actor_type="admin_bot", actor_id=bot.id,
                        action="bot_paired", target_type="user",
                        target_id=owner.id if owner else None,
                        payload={"chat_id": chat_id, "username": sender_username})
            return True, bot, owner
        return False, bot, owner
    finally:
        db.close()


def guard(allow_frozen: bool = False):
    """Decorator: runs auth + rate-limit + freeze checks before the handler."""
    def deco(fn):
        @wraps(fn)
        async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            bot_id = ctx.bot_data["bot_id"]
            chat_id = update.effective_chat.id if update.effective_chat else None
            if chat_id is not None and not rate_limit.allow(chat_id):
                if update.message:
                    await update.message.reply_text("⏳ Rate limit (60s window).")
                return
            ok, bot, owner = _authorize(update, bot_id)
            if not ok:
                if update.message:
                    await update.message.reply_text(
                        "❌ ခွင့်မပြုပါ — bot owner ၏ Telegram username သတ်မှတ်ပြီးမှ /start ပို့ပါ။"
                    )
                return
            if (not allow_frozen) and _frozen():
                if update.message:
                    await update.message.reply_text("⏸ Bots are paused. Use /resume to continue.")
                return
            return await fn(update, ctx, bot, owner)
        return wrapper
    return deco


def _denied_audit(bot_id: int, command: str, raw: str):
    db = SessionLocal()
    try:
        audit.write(db, actor_type="admin_bot", actor_id=bot_id,
                    action="blocked_command", target_type=None, target_id=None,
                    payload={"command": command, "raw": raw[:500]})
    finally:
        db.close()


# --------------- handler registration ---------------

def build_admin_handlers(app: Application, bot_id: int):
    app.bot_data["bot_id"] = bot_id

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("newjob", newjob))
    app.add_handler(CommandHandler("jobs", list_jobs))
    app.add_handler(CommandHandler("assistants", list_assistants))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("reassign", reassign))
    app.add_handler(CommandHandler("cancel", cancel_job))
    app.add_handler(CommandHandler("reminder", reminder))
    app.add_handler(CommandHandler("pause", pause_bots))
    app.add_handler(CommandHandler("resume", resume_bots))

    # Multi-step /createjob (now supports multi-target selection)
    app.add_handler(CommandHandler("createjob", createjob))
    app.add_handler(CommandHandler("cjcancel", cj_cancel))
    app.add_handler(CommandHandler("done", cj_done))
    app.add_handler(CommandHandler("proceed", cj_proceed))
    app.add_handler(CommandHandler("mode_all", cj_mode_all))
    app.add_handler(CommandHandler("mode_any", cj_mode_any))
    app.add_handler(CommandHandler(["photo", "video", "file", "document", "text"], cj_type))
    app.add_handler(MessageHandler(filters.Regex(r"^/asst_\d+"), cj_asst))
    app.add_handler(MessageHandler(filters.Regex(r"^/g_\d+"), cj_group))
    app.add_handler(MessageHandler(filters.Regex(r"^/all\b"), cj_all))
    app.add_handler(MessageHandler(filters.Regex(r"^/who\b"), cj_who))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cj_capture_text))

    # Allowlist refusal — register an explicit handler per BLOCKED command
    for cmd in BLOCKED_COMMANDS:
        app.add_handler(CommandHandler(cmd, denied_handler))


# --------------- handlers ---------------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bot_id = ctx.bot_data["bot_id"]
    if not rate_limit.allow(update.effective_chat.id):
        return
    ok, bot, owner = _authorize(update, bot_id)
    if not ok:
        await update.message.reply_text(
            "❌ ဒီ bot ကို သင် ထိန်းချုပ်ခွင့် မရှိပါ။\n"
            "Web admin panel မှ Telegram username သတ်မှတ်ပြီးမှ ပြန်ကြိုးစားပါ။"
        )
        return
    await hub.broadcast("binding.updated", {"admin_bot": True, "status": "active"})
    await update.message.reply_text(
        "👑 Admin bot ချိတ်ဆက်ပြီးပါပြီ။\n\n"
        "📋 Job creation —\n"
        "/newjob TITLE, DESC, DEADLINE, TARGETS [, MODE]\n"
        "  DEADLINE: today / tomorrow / 17:00 / 5pm / +2h / 2026-05-12 18:00 / (empty=+2h)\n"
        "  TARGETS:  @all  @shop1  @alice  @alice @bob @shop2 …\n"
        "  MODE:     any (default) | all (everyone must accept)\n"
        "/createjob — အဆင့်လိုက် multi-select\n\n"
        "📊 Listing —\n"
        "/jobs [done|pending|overdue]\n"
        "/assistants   /stats [today|week|month]\n\n"
        "✉️  Communication —\n"
        "/broadcast <message>\n"
        "/report JOB-xxxx — report file ပြန်ယူ\n\n"
        "🔄 Job control —\n"
        "/reassign JOB-xxxx @target [@target2 …]\n"
        "/cancel JOB-xxxx     /reminder JOB-xxxx <minutes>\n\n"
        "⏸  Fleet —\n"
        "/pause   /resume"
    )


async def help_cmd(update, ctx):
    await update.message.reply_text(NEWJOB_HELP + "\n\n"
        "Other —\n"
        "/createjob (wizard)  /jobs [filter]  /assistants  /stats [range]\n"
        "/broadcast <msg>     /report <code>\n"
        "/reassign <code> TARGETS  /cancel <code>\n"
        "/reminder <code> <min>    /pause  /resume"
    )


async def denied_handler(update: Update, ctx):
    bot_id = ctx.bot_data["bot_id"]
    cmd = (update.message.text or "").split()[0].lstrip("/").lower()
    _denied_audit(bot_id, cmd, update.message.text or "")
    await update.message.reply_text(
        "❌ ခွင့်မပြုပါ — destructive command များကို Telegram မှတဆင့် မလုပ်နိုင်ပါ။"
    )


@guard()
async def list_jobs(update, ctx, bot, owner):
    parts = (update.message.text or "").split(maxsplit=1)
    flt = parts[1].strip().lower() if len(parts) > 1 else ""
    db = SessionLocal()
    try:
        q = db.query(Job).filter(Job.is_template == False)  # noqa: E712
        if flt == "done":
            q = q.filter(Job.status == "done")
        elif flt == "pending":
            q = q.filter(Job.status.in_(["pending", "in_progress"]))
        elif flt == "overdue":
            q = q.filter(Job.status == "overdue")
        rows = q.order_by(Job.id.desc()).limit(20).all()
        if not rows:
            await update.message.reply_text("အလုပ် မရှိပါ။")
            return
        lines = [f"{j.code} [{j.status}] {j.title}" for j in rows]
        await update.message.reply_text("\n".join(lines))
    finally:
        db.close()


@guard()
async def list_assistants(update, ctx, bot, owner):
    db = SessionLocal()
    try:
        rows = db.query(Assistant).all()
        if not rows:
            await update.message.reply_text("Assistant မရှိပါ။")
            return
        today_utc = _today_utc_midnight()
        lines = []
        for a in rows:
            done_today = db.query(JobAssignment).filter(
                JobAssignment.assistant_id == a.id,
                JobAssignment.status == "done",
                JobAssignment.finished_at.isnot(None),
                JobAssignment.finished_at >= today_utc,
            ).count()
            pending = db.query(JobAssignment).filter(
                JobAssignment.assistant_id == a.id,
                JobAssignment.status == "pending",
            ).count()
            in_prog = db.query(JobAssignment).filter(
                JobAssignment.assistant_id == a.id,
                JobAssignment.status.in_(["accepted", "in_progress"]),
            ).count()
            tg = f" @{a.telegram_username}" if a.telegram_username else ""
            lines.append(
                f"#{a.id} {a.name}{tg} [{a.status}]\n"
                f"   ✅ today: {done_today}  ⏳ pending: {pending}  🔄 active: {in_prog}"
            )
        await update.message.reply_text("\n".join(lines))
    finally:
        db.close()


@guard()
async def stats(update, ctx, bot, owner):
    parts = (update.message.text or "").split(maxsplit=1)
    rng = parts[1].strip().lower() if len(parts) > 1 else "today"
    if rng not in ("today", "week", "month"):
        rng = "today"
    now = datetime.utcnow()
    if rng == "today":
        start = _today_utc_midnight()
    elif rng == "week":
        start = now - timedelta(days=7)
    else:
        start = now - timedelta(days=30)
    db = SessionLocal()
    try:
        jobs = db.query(Job).filter(Job.created_at >= start, Job.is_template == False).all()  # noqa: E712
        total = len(jobs)
        done = sum(1 for j in jobs if j.status == "done")
        pend = sum(1 for j in jobs if j.status in ("pending", "in_progress"))
        over = sum(1 for j in jobs if j.status == "overdue")
        await update.message.reply_text(
            f"📊 Stats ({rng})\n"
            f"စုစုပေါင်း: {total}\nပြီးစီး: {done}\nစောင့်ဆိုင်း: {pend}\nပြီးချိန် လွန်: {over}"
        )
    finally:
        db.close()


# ---- /newjob inline ----
NEWJOB_HELP = (
    "Format (ပေးချင်တာသာ ပေးပါ — အမှန် required က TITLE နဲ့ TARGETS):\n"
    "/newjob TITLE, [DESC], [DEADLINE], TARGETS [, TYPE] [, MODE]\n\n"
    "💡 Short forms — Bot က field အလိုက် auto-detect:\n"
    "   /newjob X, @all                  ← title + targets\n"
    "   /newjob X, Y, @all               ← Y သည် desc သို့ deadline (auto-detect)\n"
    "   /newjob X, Y, today, @all        ← desc + deadline (full)\n"
    "   /newjob X, , , @all              ← skip slots ',' နဲ့ ထားခဲ့ — default အလုပ်လုပ်\n\n"
    "DEADLINE —\n"
    "  (empty) → ခုလက်ရှိ + ၂ နာရီ\n"
    "  today → ဒီနေ့ ၁၇:၀၀\n"
    "  tomorrow / tmr → နက်ဖြန် ၁၇:၀၀\n"
    "  17:00 / 5pm → ဒီနေ့ အဲ့အချိန်\n"
    "  +2h / +30m → relative offset\n"
    "  2026-05-12 18:00 → အပြည့်အစုံ\n\n"
    "TARGETS —\n"
    "  @all  @shop1  @alice  @alice @bob (multi/union)\n\n"
    "TYPE (report လိုအပ်တဲ့ပုံစံ — optional, order-agnostic) —\n"
    "  photo / pic (default) → ဓာတ်ပုံ\n"
    "  video / vid           → ဗီဒီယို\n"
    "  document / doc / file → ဖိုင်\n"
    "  text / txt            → စာသားပဲ\n"
    "  any                   → မည်သည့်ပုံစံမဆို\n\n"
    "MODE (multi-target အတွက် — optional, order-agnostic) —\n"
    "  any  (default) → တစ်ယောက် လက်ခံတာနဲ့ in_progress\n"
    "  all            → အကုန် လက်ခံမှ in_progress\n\n"
    "RECURRENCE (optional, order-agnostic) —\n"
    "  daily   → နေ့စဉ် auto-spawn\n"
    "  weekly  → အပတ်စဉ် auto-spawn\n"
    "  monthly → လစဉ် auto-spawn\n"
    "  (မပါ)   → one-off job (default)\n\n"
    "ဥပမာ:\n"
    "/newjob ဈေးဝယ်, နှင်းရည် ၂ ဘူး, today, @alice         ← TYPE=photo (default), MODE=any\n"
    "/newjob အပေါ်ထပ် ရှင်း, , 17:00, @shop1, video, all\n"
    "/newjob inventory, , +2h, @all, doc\n"
    "/newjob meeting note, , today, @bob, text\n"
    "/newjob inspection, , , @shop2, any        ← any pic/video/doc/text လည်း OK\n"
    "/newjob ဆိုင်သန့်ရှင်း, , 9am, @shop1, daily      ← နေ့စဉ် auto-spawn\n"
    "/newjob inventory check, , today, @all, weekly, all"
)


# Token sets for optional fields after TARGETS. Order-agnostic; each token is
# classified by content into TYPE / MODE / RECURRENCE.
_TYPE_ALIASES = {
    "photo": "photo", "pic": "photo", "image": "photo", "img": "photo",
    "video": "video", "vid": "video",
    "document": "document", "doc": "document", "file": "document",
    "text": "text", "txt": "text",
    "any": "any",
}
_RECURRENCE_ALIASES = {
    "daily": "daily", "day": "daily",
    "weekly": "weekly", "week": "weekly",
    "monthly": "monthly", "month": "monthly",
    "none": "none", "once": "none",
}


def _classify_extras(extras: list[str]) -> tuple[str, str, str, bool, bool, bool]:
    """From the optional fields after TARGETS, extract:
      (report_type, accept_mode, recurrence,
       type_was_default, mode_was_default, recurrence_was_default)

    Defaults: ('photo', 'any', 'none'). Tokens are matched case-insensitively
    so the user can write them in any order."""
    report_type = "photo"
    accept_mode = "any"
    recurrence = "none"
    type_explicit = False
    mode_explicit = False
    recurrence_explicit = False
    for raw in extras:
        f = (raw or "").strip().lower()
        if not f:
            continue
        if f == "all":
            accept_mode = "all"
            mode_explicit = True
            continue
        if f in _RECURRENCE_ALIASES:
            recurrence = _RECURRENCE_ALIASES[f]
            recurrence_explicit = recurrence != "none"
            continue
        if f in _TYPE_ALIASES:
            report_type = _TYPE_ALIASES[f]
            type_explicit = True
            continue
        # unknown tokens silently ignored — user might add freeform notes
    return (
        report_type, accept_mode, recurrence,
        not type_explicit, not mode_explicit, not recurrence_explicit,
    )


def _parse_deadline(s: str) -> Optional[datetime]:
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _looks_like_deadline(s: str) -> bool:
    """Heuristic: does this text plausibly look like a deadline shorthand?
    Used to disambiguate the single field between TITLE and TARGETS — is it the
    user's description or their deadline?"""
    t = (s or "").strip().lower()
    if not t:
        return False
    if t in ("today", "tomorrow", "tmr"):
        return True
    if re.match(r"^\+\d+\s*[hm]$", t):  # +2h, +30m
        return True
    if re.match(r"^\d{1,2}(:\d{2})?\s*(am|pm)?$", t):  # 17:00, 5pm, 14:30
        return True
    if re.match(r"^\d{4}-\d{2}-\d{2}", t):  # 2026-05-12 [HH:MM]
        return True
    return False


def _smart_deadline(s: str) -> tuple[Optional[datetime], str]:
    """Smart, human-friendly deadline parsing. Returns (utc_datetime, label) or (None, error).

    Inputs are interpreted in the configured TIMEZONE; the returned datetime is naive UTC
    matching how Job.deadline_at is stored elsewhere. Supported forms:
      ""              → now + 2h
      "today"         → today 17:00 local
      "tomorrow"/"tmr"→ tomorrow 17:00 local
      "+2h" / "+30m"  → relative
      "5pm" / "17:00" → today at that time local
      "YYYY-MM-DD HH:MM" → exact local datetime
    """
    raw = (s or "").strip().lower()
    tz = _local_tz()
    now_local = datetime.now(tz)

    def _to_utc_naive(local_dt_naive: datetime) -> datetime:
        return local_dt_naive.replace(tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    # 1) Empty → +2h from now
    if not raw:
        return (datetime.utcnow() + timedelta(hours=2), "ခုလက်ရှိ + ၂ နာရီ")

    # 2) "today" / "tomorrow" / "tmr" → 17:00 local
    if raw == "today":
        target = now_local.replace(hour=17, minute=0, second=0, microsecond=0)
        return (_to_utc_naive(target.replace(tzinfo=None)), "ဒီနေ့ ၁၇:၀၀")
    if raw in ("tomorrow", "tmr"):
        target = (now_local + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
        return (_to_utc_naive(target.replace(tzinfo=None)), "နက်ဖြန် ၁၇:၀၀")

    # 3) +Nh / +Nm
    m = re.match(r"^\+(\d+)\s*([hm])$", raw)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        delta = timedelta(hours=amount) if unit == "h" else timedelta(minutes=amount)
        return (datetime.utcnow() + delta, f"ခု + {amount}{unit}")

    # 4) Time-only — "17:00", "5pm", "5:30pm", "5"
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", raw)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2)) if m.group(2) else 0
        ampm = m.group(3)
        if ampm == "pm" and hh < 12:
            hh += 12
        elif ampm == "am" and hh == 12:
            hh = 0
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            target_local = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            # If time has already passed today, assume next-day occurrence
            if target_local < now_local:
                target_local += timedelta(days=1)
                label = f"နက်ဖြန် {hh:02d}:{mm:02d}"
            else:
                label = f"ဒီနေ့ {hh:02d}:{mm:02d}"
            return (_to_utc_naive(target_local.replace(tzinfo=None)), label)

    # 5) Full datetime fallback
    dt = _parse_deadline(s)
    if dt:
        return (dt, dt.strftime("%Y-%m-%d %H:%M"))

    return (None, f"format မှားပါသည် — '{s}'")


@guard()
async def newjob(update, ctx, bot, owner):
    text = update.message.text or ""
    body = text.split(None, 1)[1] if " " in text else ""
    if not body:
        await update.message.reply_text(NEWJOB_HELP)
        return
    # comma-separated. Required: TITLE + TARGETS (TARGETS = first field starting with @).
    # Everything between is DESC and/or DEADLINE; everything after is TYPE/MODE.
    # Examples accepted:
    #   /newjob X, @all                          (title + targets)
    #   /newjob X, Y, @all                       (title + desc + targets)        ← Y free text
    #   /newjob X, today, @all                   (title + deadline + targets)    ← Y deadline-like
    #   /newjob X, Y, today, @all                (title + desc + deadline + targets)
    #   /newjob X, Y, today, @all, photo, all    (full)
    parts = [p.strip() for p in body.split(",", 5)]
    targets_idx = next((i for i, p in enumerate(parts) if p.startswith("@")), None)
    if targets_idx is None or targets_idx == 0:
        await update.message.reply_text(
            "❌ TARGETS မပါ — @all / @group / @username လိုအပ်တယ်။\n\n" + NEWJOB_HELP
        )
        return

    title = parts[0]
    who = parts[targets_idx]
    between = parts[1:targets_idx]
    desc = ""
    deadline_str = ""
    if len(between) == 1:
        # Ambiguous single field — disambiguate by content
        val = between[0]
        if _looks_like_deadline(val):
            deadline_str = val
        else:
            desc = val
    elif len(between) >= 2:
        desc = between[0]
        deadline_str = between[1]

    extras = parts[targets_idx + 1:]
    (report_type, accept_mode, recurrence,
     type_was_default, mode_was_default, _recur_was_default) = _classify_extras(extras)
    deadline_was_default = not deadline_str.strip()

    if not title:
        await update.message.reply_text("❌ Title မပါ။")
        return

    deadline, deadline_label = _smart_deadline(deadline_str)
    if not deadline:
        await update.message.reply_text(f"❌ Deadline {deadline_label}\n\n{NEWJOB_HELP}")
        return

    tokens = targets_svc.split_tokens(who)
    if not tokens:
        await update.message.reply_text("❌ Target မပါ — @all / @shop1 / @username")
        return

    # late import to avoid circular dependency
    from ..routers.jobs import _next_code, _notify_assignment

    db = SessionLocal()
    try:
        ids, matched, unknown = targets_svc.resolve(db, tokens)
        if not ids:
            # Build a helpful "what IS available" hint so the user can fix the typo
            available_groups = [g.name for g in db.query(Group).order_by(Group.name).all()]
            available_users = [
                a.telegram_username for a in
                db.query(Assistant).filter(
                    Assistant.status == "active",
                    Assistant.telegram_username.isnot(None),
                ).all()
                if a.telegram_username
            ]
            msg = "❌ Target မတွေ့ပါ။"
            if unknown:
                msg += f"\n  မသိ token: {', '.join('@' + u for u in unknown)}"
            msg += "\n\nရွေးနိုင်တာ —"
            msg += f"\n  @all  (active staff အားလုံး)"
            if available_groups:
                msg += f"\n  Groups: {', '.join('@' + g.replace(' ', '') for g in available_groups)}"
            else:
                msg += f"\n  Groups: (web admin → Groups page မှ create)"
            if available_users:
                msg += f"\n  Staff: {', '.join('@' + u for u in available_users[:8])}"
                if len(available_users) > 8:
                    msg += f" (+{len(available_users) - 8} more)"
            await update.message.reply_text(msg)
            return

        # Auto-default to all-mode only when user explicitly typed "all"; single-target
        # jobs always end up "any" (only one accepter possible).
        effective_mode = "all" if (accept_mode == "all" and len(ids) > 1) else "any"
        is_recurring = recurrence in ("daily", "weekly", "monthly")
        spawn_delta = {
            "daily": timedelta(days=1),
            "weekly": timedelta(days=7),
            "monthly": timedelta(days=30),
        }.get(recurrence)

        # If recurring: create a template Job first, then the first instance with
        # parent_template_id pointing back. Scheduler tick spawns later instances.
        template_id = None
        if is_recurring and spawn_delta:
            tcode = _next_code(db)
            tmpl = Job(
                code=tcode, title=title, description=desc,
                report_type=report_type, deadline_at=deadline, status="pending",
                created_via="admin_bot",
                accept_mode=effective_mode,
                completion_mode="all",
                recurrence=recurrence,
                is_template=True,
                next_spawn_at=datetime.utcnow() + spawn_delta,
                created_by_user_id=owner.id if owner else None,
            )
            db.add(tmpl); db.flush()
            for aid in ids:
                db.add(JobAssignment(job_id=tmpl.id, assistant_id=aid, status="pending"))
            template_id = tmpl.id
            db.commit()

        code = _next_code(db)
        job = Job(code=code, title=title, description=desc,
                  report_type=report_type, deadline_at=deadline, status="pending",
                  created_via="admin_bot",
                  accept_mode=effective_mode,
                  recurrence="none",  # this is the INSTANCE, not the template
                  parent_template_id=template_id,
                  created_by_user_id=owner.id if owner else None)
        db.add(job); db.flush()

        rows: list[tuple[Assistant, JobAssignment]] = []
        for aid in ids:
            asst = db.query(Assistant).filter(Assistant.id == aid).first()
            if not asst:
                continue
            row = JobAssignment(job_id=job.id, assistant_id=aid, status="pending")
            db.add(row); db.flush()
            rows.append((asst, row))
        db.commit(); db.refresh(job)

        audit.write(db, actor_type="admin_bot", actor_id=bot.id,
                    action="job_created", target_type="job", target_id=job.id,
                    payload={"title": title, "assignees": ids,
                             "tokens": tokens, "matched": matched})

        for asst, row in rows:
            try:
                await _notify_assignment(asst, row, job, header="📋 အလုပ်အသစ်")
            except Exception:
                pass
        try:
            await hub.broadcast("job.created", {"job_id": job.id, "code": job.code})
        except Exception:
            pass
        notion_sync.enqueue(job.id)

        names = ", ".join(a.name for a, _ in rows[:8])
        if len(rows) > 8:
            names += f" … (+{len(rows) - 8} more)"
        match_lines = "\n  ".join(matched) if matched else "—"
        unknown_msg = ""
        if unknown:
            unknown_msg = f"\n⚠️ skipped: {', '.join('@' + u for u in unknown)}"
        type_label_mm = {
            "photo": "ဓာတ်ပုံ", "video": "ဗီဒီယို", "document": "ဖိုင်",
            "text": "စာသား", "any": "မည်သည့်ပုံစံမဆို",
        }.get(report_type, report_type)
        type_tag = " (default)" if type_was_default else ""
        mode_str = "all (လူတိုင်း လက်ခံမှ in_progress)" if effective_mode == "all" \
                   else "any (တစ်ယောက် လက်ခံတာနဲ့ in_progress)"
        mode_tag = " (default)" if mode_was_default else ""
        deadline_tag = " (default)" if deadline_was_default else ""
        defaults_used = []
        if deadline_was_default: defaults_used.append("Deadline")
        if type_was_default:     defaults_used.append("Type")
        if mode_was_default and len(rows) > 1: defaults_used.append("Mode")
        defaults_line = ""
        if defaults_used:
            defaults_line = f"\n💡 Default applied: {', '.join(defaults_used)}"
        if is_recurring:
            recur_mm = {"daily": "နေ့စဉ်", "weekly": "အပတ်စဉ်", "monthly": "လစဉ်"}.get(recurrence, recurrence)
            recurrence_line = (
                f"\n🔁 Action: {recurrence} ({recur_mm}) — auto-spawn နောက်တစ်ခါ "
                f"~{(datetime.utcnow() + spawn_delta):%Y-%m-%d %H:%M} UTC"
            )
        else:
            recurrence_line = "\n🔁 Action: one-time (default) — တစ်ခါတည်းသာ ပို့မယ်"
        await update.message.reply_text(
            f"✅ Job ဖန်တီးပြီး။\n"
            f"🆔 {job.code}\n"
            f"📌 {job.title}\n"
            f"👥 {len(rows)} ယောက်: {names}\n"
            f"  {match_lines}\n"
            f"⏰ Deadline: {deadline_label}{deadline_tag} ({deadline:%Y-%m-%d %H:%M} UTC)\n"
            f"📎 Type: {type_label_mm}{type_tag}\n"
            f"🤝 Mode: {mode_str}{mode_tag}"
            f"{recurrence_line}"
            f"{defaults_line}{unknown_msg}"
        )
    finally:
        db.close()


# ---- /broadcast ----

@guard()
async def broadcast(update, ctx, bot, owner):
    text = update.message.text or ""
    body = text.split(None, 1)[1] if " " in text else ""
    if not body.strip():
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    from ..routers.announcements import create_announcement_internal

    db = SessionLocal()
    try:
        ann, n = await create_announcement_internal(
            db,
            title="📢 Broadcast",
            body=body.strip(),
            cadence="once",
            created_via="admin_bot",
        )
        audit.write(db, actor_type="admin_bot", actor_id=bot.id,
                    action="broadcast", target_type="announcement", target_id=ann.id,
                    payload={"recipients": n})
        await update.message.reply_text(f"📡 ပို့ပြီး — {n} ယောက်ထံ ({ann.code}).")
    finally:
        db.close()


# ---- /report ----

@guard()
async def report(update, ctx, bot, owner):
    text = update.message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /report <JOB-CODE>")
        return
    code = parts[1].strip()
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.code.ilike(code)).first()
        if not job:
            await update.message.reply_text("❌ Job မတွေ့ပါ။")
            return
        rep = (db.query(Report)
               .filter(Report.job_id == job.id)
               .order_by(Report.id.desc())
               .first())
        if not rep:
            await update.message.reply_text(f"{job.code} အတွက် report မရှိသေးပါ။")
            return
        if rep.type == "text" and rep.content_text:
            await update.message.reply_text(f"📄 {job.code}\n\n{rep.content_text}")
            return
        if not rep.file_path:
            await update.message.reply_text("ဖိုင် မတွေ့ပါ။")
            return
        p = Path(rep.file_path)
        if not p.is_absolute():
            p = (UPLOAD_PATH.parent / rep.file_path).resolve()
        if not p.exists():
            await update.message.reply_text("ဖိုင် ပျောက်နေပါသည်။")
            return
        try:
            with p.open("rb") as f:
                if rep.type == "photo":
                    await update.message.reply_photo(InputFile(f, filename=rep.file_name or p.name),
                                                     caption=f"{job.code}")
                elif rep.type == "video":
                    await update.message.reply_video(InputFile(f, filename=rep.file_name or p.name),
                                                     caption=f"{job.code}")
                else:
                    await update.message.reply_document(InputFile(f, filename=rep.file_name or p.name),
                                                        caption=f"{job.code}")
        except Exception as e:
            await update.message.reply_text(f"❌ ပို့ပေး၍ မရပါ — {e}")
    finally:
        db.close()


# ---- /reassign ----

@guard()
async def reassign(update, ctx, bot, owner):
    text = update.message.text or ""
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await update.message.reply_text(
            "Usage: /reassign JOB-CODE TARGETS\n"
            "TARGETS — @all / @shop1 / @alice (multi OK)"
        )
        return
    code = parts[1]
    tokens = targets_svc.split_tokens(parts[2])
    if not tokens:
        await update.message.reply_text("❌ Target မပါ။")
        return

    from ..routers.jobs import _notify_assignment

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.code.ilike(code)).first()
        if not job:
            await update.message.reply_text("❌ Job မတွေ့ပါ။")
            return
        ids, matched, unknown = targets_svc.resolve(db, tokens)
        if not ids:
            msg = "❌ Target မတွေ့ပါ။"
            if unknown:
                msg += f"\nrecognise မရ: {', '.join('@' + u for u in unknown)}"
            await update.message.reply_text(msg)
            return

        # Skip assistants that already have a non-terminal assignment on this job
        already = {a.assistant_id for a in job.assignments
                   if a.status in ("pending", "accepted", "in_progress", "done")}
        new_ids = [aid for aid in ids if aid not in already]
        if not new_ids:
            await update.message.reply_text("❌ ရွေးထားသူများ အကုန် ဒီ job မှာ assigned ဖြစ်နေပြီ။")
            return

        # Mark prior non-terminal assignments as transferred (to first new id)
        first_new = new_ids[0]
        for a in job.assignments:
            if a.status in ("pending", "accepted", "in_progress"):
                a.status = "transferred"
                a.transferred_to_id = first_new

        new_rows: list[tuple[Assistant, JobAssignment]] = []
        for aid in new_ids:
            asst = db.query(Assistant).filter(Assistant.id == aid).first()
            if not asst:
                continue
            row = JobAssignment(job_id=job.id, assistant_id=aid, status="pending")
            db.add(row); db.flush()
            new_rows.append((asst, row))

        if job.status in ("cancelled", "overdue", "done"):
            job.status = "pending"
            job.completed_at = None
        db.commit()

        audit.write(db, actor_type="admin_bot", actor_id=bot.id,
                    action="job_reassigned", target_type="job", target_id=job.id,
                    payload={"to": new_ids, "tokens": tokens, "matched": matched})

        for asst, row in new_rows:
            try:
                await _notify_assignment(asst, row, job, header="📋 အလုပ် ပြန်တာဝန်ပေး")
            except Exception:
                pass
        await hub.broadcast("job.status_changed", {"job_id": job.id, "status": job.status})
        notion_sync.enqueue(job.id)

        names = ", ".join(a.name for a, _ in new_rows[:8])
        if len(new_rows) > 8:
            names += f" … (+{len(new_rows) - 8} more)"
        unknown_msg = f"\n⚠️ skipped: {', '.join('@' + u for u in unknown)}" if unknown else ""
        await update.message.reply_text(
            f"➡️ {job.code} → {len(new_rows)} ယောက်: {names}{unknown_msg}"
        )
    finally:
        db.close()


# ---- /cancel ----

@guard()
async def cancel_job(update, ctx, bot, owner):
    """When CJ flow is in progress, /cancel cancels that flow; otherwise cancels a job."""
    cj = ctx.user_data.get(CJ_KEY)
    if cj:
        ctx.user_data.pop(CJ_KEY, None)
        await update.message.reply_text("❌ Createjob flow ပယ်ဖျက်ပြီး။")
        return
    text = update.message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Usage: /cancel <JOB-CODE>")
        return
    code = parts[1].strip()
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.code.ilike(code)).first()
        if not job:
            await update.message.reply_text("❌ Job မတွေ့ပါ။")
            return
        job.status = "cancelled"
        for a in job.assignments:
            if a.status in ("pending", "accepted", "in_progress"):
                a.status = "superseded"
        db.commit()
        audit.write(db, actor_type="admin_bot", actor_id=bot.id,
                    action="job_cancelled", target_type="job", target_id=job.id, payload={})
        await hub.broadcast("job.status_changed", {"job_id": job.id, "status": "cancelled"})
        notion_sync.enqueue(job.id)
        await update.message.reply_text(f"🛑 {job.code} ပယ်ဖျက်ပြီး။")
    finally:
        db.close()


# ---- /reminder ----

@guard()
async def reminder(update, ctx, bot, owner):
    text = update.message.text or ""
    parts = text.split()
    if len(parts) < 3:
        await update.message.reply_text("Usage: /reminder <JOB-CODE> <minutes>")
        return
    code = parts[1]
    try:
        minutes = int(parts[2])
    except ValueError:
        await update.message.reply_text("minutes must be an integer")
        return
    if minutes <= 0 or minutes > 24 * 60 * 30:
        await update.message.reply_text("minutes out of range")
        return

    from .reminders import schedule_one_shot

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.code.ilike(code)).first()
        if not job:
            await update.message.reply_text("❌ Job မတွေ့ပါ။")
            return
        when = datetime.utcnow() + timedelta(minutes=minutes)
        schedule_one_shot(job.id, when)
        audit.write(db, actor_type="admin_bot", actor_id=bot.id,
                    action="reminder_scheduled", target_type="job", target_id=job.id,
                    payload={"minutes": minutes})
        await update.message.reply_text(f"⏰ {job.code} အတွက် {minutes} မိနစ်အတွင်း သတိပေးပေးမည်။")
    finally:
        db.close()


# ---- /pause /resume ----

async def _set_frozen(value: str, bot_id: int, action: str):
    db = SessionLocal()
    try:
        s = db.query(Setting).filter(Setting.key == "bots_frozen").first()
        if not s:
            s = Setting(key="bots_frozen", value=value)
            db.add(s)
        else:
            s.value = value
        db.commit()
        audit.write(db, actor_type="admin_bot", actor_id=bot_id, action=action)
    finally:
        db.close()


@guard(allow_frozen=True)
async def pause_bots(update, ctx, bot, owner):
    await _set_frozen("1", bot.id, "bots_paused")
    await update.message.reply_text("⏸ Bots paused. /resume to continue.")


@guard(allow_frozen=True)
async def resume_bots(update, ctx, bot, owner):
    await _set_frozen("0", bot.id, "bots_resumed")
    await update.message.reply_text("▶️ Bots resumed.")


# --------------- Multi-step /createjob ---------------

CJ_KEY = "cj"


@guard()
async def createjob(update, ctx, bot, owner):
    ctx.user_data[CJ_KEY] = {"step": "type", "ids": set()}
    await update.message.reply_text(
        "📋 Report အမျိုးအစား ရွေးပါ —\n"
        "/photo  /video  /document  /text\n\n"
        "ပယ်ဖျက်ရန် /cjcancel"
    )


async def cj_cancel(update, ctx):
    ctx.user_data.pop(CJ_KEY, None)
    await update.message.reply_text("❌ Createjob ပယ်ဖျက်ပြီး။")


def _cj_targets_menu(db) -> str:
    """Build the assistants/groups picker menu."""
    asst_rows = db.query(Assistant).filter(Assistant.status == "active").order_by(Assistant.id).all()
    grp_rows = db.query(Group).order_by(Group.name).all()
    lines = ["👥 Targets — တစ်ခုထက်များများ ရွေးနိုင်တယ်:"]
    lines.append("\n— Wildcards —\n/all — active staff အားလုံး")
    if grp_rows:
        lines.append("\n— Groups —")
        for g in grp_rows:
            lines.append(f"/g_{g.id} — {g.name}")
    if asst_rows:
        lines.append("\n— Individuals —")
        for a in asst_rows:
            handle = f" @{a.telegram_username}" if a.telegram_username else ""
            lines.append(f"/asst_{a.id} — {a.name}{handle}")
    lines.append("\nရွေးပြီး /proceed (သို့) /who (လက်ရှိ ရွေးထား) ၊ /cjcancel")
    return "\n".join(lines)


@guard()
async def cj_type(update, ctx, bot, owner):
    cj = ctx.user_data.get(CJ_KEY)
    if not cj or cj.get("step") != "type":
        return
    rt = (update.message.text or "").lstrip("/").split()[0].lower()
    if rt == "file":
        rt = "document"
    if rt not in ("photo", "video", "document", "text"):
        return
    cj["type"] = rt
    cj["step"] = "targets"
    cj["ids"] = set()
    db = SessionLocal()
    try:
        if not db.query(Assistant).filter(Assistant.status == "active").first():
            await update.message.reply_text("Active assistant မရှိ။ ပယ်ဖျက်ပြီး။")
            ctx.user_data.pop(CJ_KEY, None)
            return
        await update.message.reply_text(f"📎 ပုံစံ: {rt}\n\n" + _cj_targets_menu(db))
    finally:
        db.close()


@guard()
async def cj_all(update, ctx, bot, owner):
    cj = ctx.user_data.get(CJ_KEY)
    if not cj or cj.get("step") != "targets":
        return
    db = SessionLocal()
    try:
        ids = {a.id for a in db.query(Assistant).filter(Assistant.status == "active").all()}
        cj["ids"] |= ids
        await update.message.reply_text(
            f"✅ Active staff အားလုံး ({len(ids)}) ကို ထည့်ပြီး။ "
            f"စုစုပေါင်း ရွေးထား: {len(cj['ids'])}\n/proceed သို့မဟုတ် ဆက်ရွေးပါ။"
        )
    finally:
        db.close()


@guard()
async def cj_group(update, ctx, bot, owner):
    cj = ctx.user_data.get(CJ_KEY)
    if not cj or cj.get("step") != "targets":
        return
    m = re.match(r"^/g_(\d+)", update.message.text or "")
    if not m:
        return
    gid = int(m.group(1))
    db = SessionLocal()
    try:
        from ..models import AssistantGroup
        g = db.query(Group).filter(Group.id == gid).first()
        if not g:
            await update.message.reply_text("Group မတွေ့ပါ။")
            return
        mems = db.query(AssistantGroup).filter(AssistantGroup.group_id == gid).all()
        member_ids = {m.assistant_id for m in mems}
        active_ids = {a.id for a in db.query(Assistant).filter(
            Assistant.id.in_(member_ids) if member_ids else False,
            Assistant.status == "active"
        ).all()} if member_ids else set()
        cj["ids"] |= active_ids
        await update.message.reply_text(
            f"✅ {g.name} ({len(active_ids)} ယောက်) ထည့်ပြီး။ "
            f"စုစုပေါင်း: {len(cj['ids'])}\n/proceed သို့ ဆက်ရွေး။"
        )
    finally:
        db.close()


@guard()
async def cj_asst(update, ctx, bot, owner):
    cj = ctx.user_data.get(CJ_KEY)
    if not cj or cj.get("step") != "targets":
        return
    m = re.match(r"^/asst_(\d+)", update.message.text or "")
    if not m:
        return
    aid = int(m.group(1))
    db = SessionLocal()
    try:
        a = db.query(Assistant).filter(Assistant.id == aid, Assistant.status == "active").first()
        if not a:
            await update.message.reply_text("Assistant မတွေ့/active မဟုတ်ပါ။")
            return
        if aid in cj["ids"]:
            cj["ids"].discard(aid)
            verb = "ဖယ်ပြီး"
        else:
            cj["ids"].add(aid)
            verb = "ထည့်ပြီး"
        await update.message.reply_text(
            f"✅ {a.name} ကို {verb}။ စုစုပေါင်း: {len(cj['ids'])}\n/proceed သို့ ဆက်ရွေး။"
        )
    finally:
        db.close()


@guard()
async def cj_who(update, ctx, bot, owner):
    cj = ctx.user_data.get(CJ_KEY)
    if not cj:
        return
    ids = cj.get("ids") or set()
    if not ids:
        await update.message.reply_text("ရွေးထား မရှိသေးပါ။")
        return
    db = SessionLocal()
    try:
        rows = db.query(Assistant).filter(Assistant.id.in_(ids)).all()
        names = ", ".join(a.name for a in rows)
        await update.message.reply_text(f"👥 လက်ရှိ ရွေးထား ({len(rows)}): {names}")
    finally:
        db.close()


@guard()
async def cj_proceed(update, ctx, bot, owner):
    cj = ctx.user_data.get(CJ_KEY)
    if not cj or cj.get("step") != "targets":
        await update.message.reply_text("Step မမှန်ပါ။")
        return
    ids = cj.get("ids") or set()
    if not ids:
        await update.message.reply_text("Target မရွေးရသေး။ /all (သို့) /asst_X (သို့) /g_X.")
        return
    cj["step"] = "text"
    db = SessionLocal()
    try:
        rows = db.query(Assistant).filter(Assistant.id.in_(ids)).all()
        names = ", ".join(a.name for a in rows)
    finally:
        db.close()
    mode = cj.get("mode", "any")
    mode_hint = ""
    if len(ids) > 1:
        mode_hint = (
            f"\n🤝 Mode: {mode}  ({'one-accept' if mode == 'any' else 'all-accept'})\n"
            "   Mode ပြောင်းချင်ရင် /mode_all (သို့) /mode_any\n"
        )
    await update.message.reply_text(
        f"👥 ({len(ids)}) — {names}{mode_hint}\n"
        "✍️ Title နဲ့ Description ကို comma (,) ခံရေးပါ။\n"
        "ဥပမာ:  ဈေးဝယ်ပါ, နှင်းရည် ၂ ဘူး\n\nပြီးရင် /done"
    )


@guard()
async def cj_mode_all(update, ctx, bot, owner):
    cj = ctx.user_data.get(CJ_KEY)
    if not cj:
        return
    cj["mode"] = "all"
    await update.message.reply_text("🤝 Mode = all (လူတိုင်း လက်ခံမှ in_progress)。 /done ဆက်လုပ်ပါ။")


@guard()
async def cj_mode_any(update, ctx, bot, owner):
    cj = ctx.user_data.get(CJ_KEY)
    if not cj:
        return
    cj["mode"] = "any"
    await update.message.reply_text("⚡ Mode = any (တစ်ယောက် လက်ခံတာနဲ့ in_progress)。 /done ဆက်လုပ်ပါ။")


async def cj_capture_text(update, ctx):
    cj = ctx.user_data.get(CJ_KEY)
    if not cj or cj.get("step") not in ("text", "ready"):
        return
    cj["raw"] = (update.message.text or "").strip()
    cj["step"] = "ready"
    await update.message.reply_text("✅ မှတ်ပြီး။ /done ၊ ပယ်ရန် /cjcancel")


@guard()
async def cj_done(update, ctx, bot, owner):
    cj = ctx.user_data.get(CJ_KEY)
    if not cj or cj.get("step") != "ready" or "raw" not in cj or not cj.get("ids"):
        await update.message.reply_text("အဆင့် မပြည့်စုံပါ။ /createjob မှ ပြန်စပါ။")
        return
    title, _, desc = cj["raw"].partition(",")
    title = title.strip() or "(အမည်မဲ့)"
    desc = desc.strip()
    deadline = datetime.utcnow() + timedelta(hours=24)

    from ..routers.jobs import _next_code, _notify_assignment

    db = SessionLocal()
    try:
        ids = sorted(cj["ids"])
        asst_rows = db.query(Assistant).filter(Assistant.id.in_(ids)).all()
        if not asst_rows:
            await update.message.reply_text("Assistants မတွေ့ပါ။")
            ctx.user_data.pop(CJ_KEY, None)
            return
        wizard_mode = cj.get("mode", "any")
        effective_mode = "all" if (wizard_mode == "all" and len(asst_rows) > 1) else "any"
        code = _next_code(db)
        job = Job(code=code, title=title, description=desc,
                  report_type=cj["type"], deadline_at=deadline, status="pending",
                  created_via="admin_bot",
                  accept_mode=effective_mode,
                  created_by_user_id=owner.id if owner else None)
        db.add(job); db.flush()
        rows = []
        for a in asst_rows:
            row = JobAssignment(job_id=job.id, assistant_id=a.id, status="pending")
            db.add(row); db.flush()
            rows.append((a, row))
        db.commit(); db.refresh(job)

        audit.write(db, actor_type="admin_bot", actor_id=bot.id,
                    action="job_created", target_type="job", target_id=job.id,
                    payload={"title": title, "via": "createjob", "assignees": ids})

        for asst, row in rows:
            try:
                await _notify_assignment(asst, row, job, header="📋 အလုပ်အသစ်")
            except Exception:
                pass
        try:
            await hub.broadcast("job.created", {"job_id": job.id, "code": job.code})
        except Exception:
            pass
        notion_sync.enqueue(job.id)

        names = ", ".join(a.name for a, _ in rows[:8])
        if len(rows) > 8:
            names += f" … (+{len(rows) - 8} more)"
        await update.message.reply_text(
            f"✅ Job ဖန်တီးပြီး။\n🆔 {job.code}\n📌 {job.title}\n"
            f"👥 ({len(rows)}) — {names}\n⏰ {deadline:%Y-%m-%d %H:%M} UTC"
        )
    finally:
        db.close()
    ctx.user_data.pop(CJ_KEY, None)
