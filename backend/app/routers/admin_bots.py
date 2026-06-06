from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import Bot, User
from ..schemas import AdminBotBindIn, AdminBotOut
from ..security import get_current_user
from ..config import encrypt
from ..services.bot_manager import manager
from ..services import audit

router = APIRouter(prefix="/api/admin-bots", tags=["admin-bots"])


def _to_out(b: Bot, instructions: str | None = None) -> AdminBotOut:
    return AdminBotOut(id=b.id, bot_type=b.bot_type, username=b.username,
                       chat_id=b.chat_id, status=b.status,
                       owner_user_id=b.owner_user_id, last_seen_at=b.last_seen_at,
                       instructions=instructions)


@router.get("", response_model=list[AdminBotOut])
def list_admin_bots(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    rows = db.query(Bot).filter(Bot.bot_type == "admin_bot").order_by(Bot.id.asc()).all()
    return [_to_out(b) for b in rows]


@router.post("/bind", response_model=AdminBotOut)
async def bind_admin_bot(body: AdminBotBindIn, db: Session = Depends(get_db),
                         me: User = Depends(get_current_user)):
    owner_id = body.owner_user_id or me.id
    owner = db.query(User).filter(User.id == owner_id).first()
    if not owner:
        raise HTTPException(404, "Owner user not found")
    # one admin bot per owner — replace existing
    existing = db.query(Bot).filter(Bot.bot_type == "admin_bot",
                                     Bot.owner_user_id == owner_id).first()
    if existing:
        await manager.stop_bot(existing.id)
        db.delete(existing); db.commit()

    b = Bot(bot_type="admin_bot", token_enc=encrypt(body.bot_token),
            status="pending", owner_user_id=owner_id)
    db.add(b); db.commit(); db.refresh(b)
    try:
        await manager.start_bot(b.id)
    except Exception as e:
        raise HTTPException(400, f"Failed to start admin bot: {e}")
    db.refresh(b)
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="admin_bot_bound",
                target_type="bot", target_id=b.id,
                payload={"owner_user_id": owner_id, "username": b.username})
    instr = (f"Open https://t.me/{b.username} and send /start "
             f"from the Telegram account whose @username matches owner.telegram_username.") if b.username else None
    return _to_out(b, instructions=instr)


@router.delete("/{bot_id}")
async def delete_admin_bot(bot_id: int, db: Session = Depends(get_db),
                           me: User = Depends(get_current_user)):
    b = db.query(Bot).filter(Bot.id == bot_id, Bot.bot_type == "admin_bot").first()
    if not b:
        raise HTTPException(404)
    await manager.stop_bot(b.id)
    db.delete(b); db.commit()
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="admin_bot_deleted",
                target_type="bot", target_id=bot_id)
    return {"ok": True}
