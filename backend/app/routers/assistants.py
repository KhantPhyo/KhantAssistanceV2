from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import Assistant, Bot, User
from ..schemas import AssistantIn, AssistantOut
from ..security import get_current_user
from ..config import encrypt
from ..services.bot_manager import manager
from ..services import audit

router = APIRouter(prefix="/api/assistants", tags=["assistants"])


def _to_out(a: Assistant, db: Session) -> AssistantOut:
    bot = db.query(Bot).filter(Bot.id == a.bot_id).first() if a.bot_id else None
    return AssistantOut(
        id=a.id, name=a.name, phone=a.phone, position=a.position,
        telegram_username=a.telegram_username,
        status=a.status, chat_id=a.chat_id,
        bot_username=bot.username if bot else None,
    )


@router.get("", response_model=list[AssistantOut])
def list_assistants(db: Session = Depends(get_db), _u=Depends(get_current_user)):
    return [_to_out(a, db) for a in db.query(Assistant).order_by(Assistant.id.desc()).all()]


@router.post("", response_model=AssistantOut)
async def create_assistant(body: AssistantIn, db: Session = Depends(get_db),
                           me: User = Depends(get_current_user)):
    bot = Bot(bot_type="assistant_bot", token_enc=encrypt(body.bot_token), status="pending")
    db.add(bot); db.flush()
    asst = Assistant(name=body.name, phone=body.phone, position=body.position,
                     telegram_username=(body.telegram_username or None),
                     bot_id=bot.id, status="pending")
    db.add(asst); db.flush()
    bot.assistant_id = asst.id
    db.commit(); db.refresh(asst); db.refresh(bot)
    try:
        await manager.start_bot(bot.id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to start bot: {e}")
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="assistant_created",
                target_type="assistant", target_id=asst.id,
                payload={"name": asst.name, "bot_username": bot.username})
    db.refresh(bot)
    return _to_out(asst, db)


@router.delete("/{assistant_id}")
async def delete_assistant(assistant_id: int, db: Session = Depends(get_db),
                           me: User = Depends(get_current_user)):
    a = db.query(Assistant).filter(Assistant.id == assistant_id).first()
    if not a:
        raise HTTPException(404)
    if a.bot_id:
        await manager.stop_bot(a.bot_id)
        db.query(Bot).filter(Bot.id == a.bot_id).delete()
    db.delete(a); db.commit()
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="assistant_deleted",
                target_type="assistant", target_id=assistant_id)
    return {"ok": True}
