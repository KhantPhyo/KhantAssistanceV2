import json
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import AuditLog
from ..schemas import AuditLogOut
from ..security import get_current_user

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _to_out(a: AuditLog) -> AuditLogOut:
    payload = None
    if a.payload_json:
        try:
            payload = json.loads(a.payload_json)
        except Exception:
            payload = a.payload_json
    return AuditLogOut(
        id=a.id, ts=a.ts, actor_type=a.actor_type, actor_id=a.actor_id,
        action=a.action, target_type=a.target_type, target_id=a.target_id,
        payload=payload,
    )


@router.get("", response_model=list[AuditLogOut])
def list_audit(
    actor_type: str | None = Query(default=None),
    action: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    _u=Depends(get_current_user),
):
    q = db.query(AuditLog)
    if actor_type:
        q = q.filter(AuditLog.actor_type == actor_type)
    if action:
        q = q.filter(AuditLog.action == action)
    rows = q.order_by(AuditLog.id.desc()).limit(limit).all()
    return [_to_out(r) for r in rows]
