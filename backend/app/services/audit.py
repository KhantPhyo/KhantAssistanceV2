"""Append-only audit log helper. Call from any router/handler that mutates state."""
import json
import logging
from typing import Any, Optional
from sqlalchemy.orm import Session

from ..models import AuditLog

log = logging.getLogger("audit")


def write(
    db: Session,
    *,
    actor_type: str,
    actor_id: Optional[int],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    payload: Any = None,
    commit: bool = True,
) -> AuditLog:
    """Persist an audit row. `commit=False` lets the caller batch with surrounding work."""
    pj = None
    if payload is not None:
        try:
            pj = json.dumps(payload, default=str)[:8000]
        except Exception:
            pj = str(payload)[:8000]
    row = AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload_json=pj,
    )
    db.add(row)
    if commit:
        try:
            db.commit()
        except Exception:
            log.exception("audit commit failed")
            db.rollback()
    return row
