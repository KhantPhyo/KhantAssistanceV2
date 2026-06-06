from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import User
from ..schemas import LoginIn, TokenOut, MeOut
from ..security import verify_password, create_access_token, get_current_user
from ..services import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(user.email)
    audit.write(db, actor_type="web_admin", actor_id=user.id, action="login")
    return TokenOut(access_token=token, email=user.email, role=user.role)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    return MeOut(id=user.id, email=user.email, role=user.role,
                 telegram_username=user.telegram_username, is_active=user.is_active)
