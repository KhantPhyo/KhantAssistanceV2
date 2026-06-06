from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import User
from ..schemas import AdminCreateIn, AdminUpdateIn, AdminOut
from ..security import get_current_user, hash_password, require_web_admin
from ..services import audit

router = APIRouter(prefix="/api/admins", tags=["admins"])


def _to_out(u: User) -> AdminOut:
    return AdminOut(id=u.id, email=u.email, role=u.role,
                    telegram_username=u.telegram_username, is_active=u.is_active,
                    created_at=u.created_at)


@router.get("", response_model=list[AdminOut])
def list_admins(db: Session = Depends(get_db), _u=Depends(get_current_user)):
    return [_to_out(u) for u in db.query(User).order_by(User.id.asc()).all()]


@router.post("", response_model=AdminOut)
def create_admin(body: AdminCreateIn, db: Session = Depends(get_db), me: User = Depends(require_web_admin)):
    if body.role not in ("web_admin", "remote_admin"):
        raise HTTPException(400, "Invalid role")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(400, "Email already exists")
    u = User(email=body.email, password_hash=hash_password(body.password),
             role=body.role, telegram_username=(body.telegram_username or None),
             is_active=True)
    db.add(u); db.commit(); db.refresh(u)
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="admin_created",
                target_type="user", target_id=u.id, payload={"email": u.email, "role": u.role})
    return _to_out(u)


@router.patch("/{user_id}", response_model=AdminOut)
def update_admin(user_id: int, body: AdminUpdateIn, db: Session = Depends(get_db),
                 me: User = Depends(get_current_user)):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404)
    # Only web_admin may change others. Anyone may edit their own telegram_username/password.
    if u.id != me.id and me.role != "web_admin":
        raise HTTPException(403, "Web admin only")
    changed = {}
    if body.password:
        u.password_hash = hash_password(body.password); changed["password"] = "***"
    if body.role is not None and me.role == "web_admin":
        if body.role not in ("web_admin", "remote_admin"):
            raise HTTPException(400, "Invalid role")
        u.role = body.role; changed["role"] = body.role
    if body.telegram_username is not None:
        u.telegram_username = (body.telegram_username or None) or None
        changed["telegram_username"] = u.telegram_username
    if body.is_active is not None and me.role == "web_admin":
        u.is_active = body.is_active; changed["is_active"] = body.is_active
    db.commit(); db.refresh(u)
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="admin_updated",
                target_type="user", target_id=u.id, payload=changed)
    return _to_out(u)


@router.delete("/{user_id}")
def delete_admin(user_id: int, db: Session = Depends(get_db), me: User = Depends(require_web_admin)):
    if user_id == me.id:
        raise HTTPException(400, "Cannot delete yourself")
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404)
    # If deleting the last web_admin, refuse
    web_admins = db.query(User).filter(User.role == "web_admin", User.is_active == True).count()  # noqa: E712
    if u.role == "web_admin" and web_admins <= 1:
        raise HTTPException(400, "Cannot delete the last web admin")
    db.delete(u); db.commit()
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="admin_deleted",
                target_type="user", target_id=user_id, payload={"email": u.email})
    return {"ok": True}
