from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import Setting, User
from ..schemas import SettingIn
from ..security import get_current_user
from ..services import audit

router = APIRouter(prefix="/api/control", tags=["control"])


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), _u=Depends(get_current_user)):
    rows = db.query(Setting).all()
    return {r.key: r.value for r in rows}


@router.post("/settings")
def set_setting(body: SettingIn, db: Session = Depends(get_db),
                me: User = Depends(get_current_user)):
    s = db.query(Setting).filter(Setting.key == body.key).first()
    if not s:
        s = Setting(key=body.key, value=body.value)
        db.add(s)
    else:
        s.value = body.value
    db.commit()
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="setting_updated",
                target_type="setting", payload={"key": body.key, "value": body.value})
    return {"ok": True, "key": body.key, "value": body.value}
