from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import Group, AssistantGroup, Assistant, User
from ..schemas import GroupIn, GroupOut
from ..security import get_current_user
from ..services import audit

router = APIRouter(prefix="/api/groups", tags=["groups"])


def _serialize(g: Group, db: Session) -> GroupOut:
    mems = db.query(AssistantGroup).filter(AssistantGroup.group_id == g.id).all()
    ids = [m.assistant_id for m in mems]
    names = []
    if ids:
        rows = db.query(Assistant).filter(Assistant.id.in_(ids)).all()
        names = [a.name for a in rows]
    return GroupOut(id=g.id, name=g.name, description=g.description or "",
                    assistant_ids=ids, assistant_names=names)


@router.get("", response_model=list[GroupOut])
def list_groups(db: Session = Depends(get_db), _u=Depends(get_current_user)):
    rows = db.query(Group).order_by(Group.name).all()
    return [_serialize(g, db) for g in rows]


@router.post("", response_model=GroupOut)
def create_group(body: GroupIn, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    if not body.name.strip():
        raise HTTPException(400, "Name required")
    if db.query(Group).filter(Group.name == body.name).first():
        raise HTTPException(400, "Name already exists")
    g = Group(name=body.name.strip(), description=body.description or "")
    db.add(g); db.flush()
    for aid in body.assistant_ids:
        db.add(AssistantGroup(assistant_id=aid, group_id=g.id))
    db.commit()
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="group_created",
                target_type="group", target_id=g.id, payload={"name": g.name, "members": body.assistant_ids})
    return _serialize(g, db)


@router.patch("/{gid}", response_model=GroupOut)
def update_group(gid: int, body: GroupIn, db: Session = Depends(get_db),
                 me: User = Depends(get_current_user)):
    g = db.query(Group).filter(Group.id == gid).first()
    if not g:
        raise HTTPException(404)
    if body.name.strip() and body.name != g.name:
        if db.query(Group).filter(Group.name == body.name, Group.id != gid).first():
            raise HTTPException(400, "Name already exists")
        g.name = body.name.strip()
    g.description = body.description or ""
    db.query(AssistantGroup).filter(AssistantGroup.group_id == gid).delete()
    for aid in body.assistant_ids:
        db.add(AssistantGroup(assistant_id=aid, group_id=gid))
    db.commit()
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="group_updated",
                target_type="group", target_id=gid)
    return _serialize(g, db)


@router.delete("/{gid}")
def delete_group(gid: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    g = db.query(Group).filter(Group.id == gid).first()
    if not g:
        raise HTTPException(404)
    db.delete(g); db.commit()
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="group_deleted",
                target_type="group", target_id=gid)
    return {"ok": True}
