from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ..db import get_db
from ..models import Announcement, AnnouncementRecipient, Assistant, AssistantGroup, User
from ..schemas import AnnouncementIn, AnnouncementOut, AnnouncementRecipientOut
from ..security import get_current_user
from ..services.bot_manager import manager
from ..services.ws_hub import hub
from ..services import audit
from ..config import settings as _settings

router = APIRouter(prefix="/api/announcements", tags=["announcements"])


def _next_code(db: Session) -> str:
    try:
        tz = ZoneInfo(_settings.TIMEZONE)
    except Exception:
        tz = ZoneInfo("UTC")
    prefix = "ANN" + datetime.now(tz).strftime("%y%b")
    n = db.query(Announcement).filter(Announcement.code.like(f"{prefix}%")).count() + 1
    return f"{prefix}{n:04d}"


def _serialize(a: Announcement, db: Session) -> AnnouncementOut:
    recips = []
    for r in a.recipients:
        asst = db.query(Assistant).filter(Assistant.id == r.assistant_id).first()
        recips.append(AnnouncementRecipientOut(
            id=r.id, assistant_id=r.assistant_id,
            assistant_name=asst.name if asst else None,
            acked_at=r.acked_at, last_sent_at=r.last_sent_at,
        ))
    return AnnouncementOut(
        id=a.id, code=a.code, title=a.title, body=a.body,
        cadence=a.cadence, status=a.status, created_at=a.created_at,
        recipients=recips,
    )


def _expand_targets(db: Session, assistant_ids: list[int], group_ids: list[int]) -> list[int]:
    aids = set(assistant_ids or [])
    if group_ids:
        rows = db.query(AssistantGroup).filter(AssistantGroup.group_id.in_(group_ids)).all()
        for r in rows:
            aids.add(r.assistant_id)
    return sorted(aids)


async def send_announcement_to(asst: Assistant, ann: Announcement,
                               rec: AnnouncementRecipient, header: str = "📢 ကြေညာချက်"):
    if not (asst.bot_id and asst.chat_id and asst.status == "active"):
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ သိရှိပြီ", callback_data=f"ann:ack:{rec.id}")]])
    text = f"{header} {ann.code}\n📌 {ann.title}\n\n{ann.body}"
    try:
        await manager.send_message(asst.bot_id, asst.chat_id, text, reply_markup=kb)
    except Exception:
        pass


async def create_announcement_internal(db: Session, *, title: str, body: str,
                                       cadence: str = "once",
                                       assistant_ids: list[int] | None = None,
                                       group_ids: list[int] | None = None,
                                       created_via: str = "web"):
    """Used by both web POST and admin-bot /broadcast.
    If no targets given, broadcasts to all active assistants."""
    if cadence not in ("once", "2h", "daily"):
        cadence = "once"
    ids = _expand_targets(db, assistant_ids or [], group_ids or [])
    if not ids:
        ids = [a.id for a in db.query(Assistant).filter(Assistant.status == "active").all()]
    code = _next_code(db)
    ann = Announcement(code=code, title=title, body=body,
                       cadence=cadence, status="active", created_via=created_via)
    db.add(ann); db.flush()
    pairs = []
    now = datetime.utcnow()
    for aid in ids:
        rec = AnnouncementRecipient(announcement_id=ann.id, assistant_id=aid, last_sent_at=now)
        db.add(rec); db.flush()
        asst = db.query(Assistant).filter(Assistant.id == aid).first()
        if asst:
            pairs.append((asst, rec))
    db.commit()
    for asst, rec in pairs:
        await send_announcement_to(asst, ann, rec)
    try:
        await hub.broadcast("announcement.created", {"id": ann.id, "code": ann.code})
    except Exception:
        pass
    return ann, len(pairs)


@router.get("", response_model=list[AnnouncementOut])
def list_announcements(db: Session = Depends(get_db), _u=Depends(get_current_user)):
    rows = db.query(Announcement).order_by(Announcement.id.desc()).all()
    return [_serialize(a, db) for a in rows]


@router.get("/{aid}", response_model=AnnouncementOut)
def get_announcement(aid: int, db: Session = Depends(get_db), _u=Depends(get_current_user)):
    a = db.query(Announcement).filter(Announcement.id == aid).first()
    if not a:
        raise HTTPException(404)
    return _serialize(a, db)


@router.post("", response_model=AnnouncementOut)
async def create_announcement(body: AnnouncementIn, db: Session = Depends(get_db),
                              me: User = Depends(get_current_user)):
    ann, n = await create_announcement_internal(
        db, title=body.title, body=body.body, cadence=body.cadence,
        assistant_ids=body.assistant_ids, group_ids=body.group_ids,
        created_via="web",
    )
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="announcement_created",
                target_type="announcement", target_id=ann.id, payload={"recipients": n})
    return _serialize(ann, db)


@router.post("/{aid}/close")
def close_announcement(aid: int, db: Session = Depends(get_db),
                       me: User = Depends(get_current_user)):
    a = db.query(Announcement).filter(Announcement.id == aid).first()
    if not a:
        raise HTTPException(404)
    a.status = "closed"; db.commit()
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="announcement_closed",
                target_type="announcement", target_id=aid)
    return {"ok": True}


@router.delete("/{aid}")
def delete_announcement(aid: int, db: Session = Depends(get_db),
                        me: User = Depends(get_current_user)):
    a = db.query(Announcement).filter(Announcement.id == aid).first()
    if not a:
        raise HTTPException(404)
    db.delete(a); db.commit()
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="announcement_deleted",
                target_type="announcement", target_id=aid)
    return {"ok": True}
