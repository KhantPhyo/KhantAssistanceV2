from datetime import datetime, timedelta
from typing import Optional
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from .config import settings
from .db import get_db
from .models import User

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)
ALG = "HS256"


def hash_password(p: str) -> str:
    return pwd_ctx.hash(p)


def verify_password(p: str, h: str) -> bool:
    return pwd_ctx.verify(p, h)


def create_access_token(sub: str, hours: int = 12) -> str:
    payload = {"sub": sub, "exp": datetime.utcnow() + timedelta(hours=hours)}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALG)


def decode_token(token: str) -> Optional[str]:
    try:
        data = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALG])
        return data.get("sub")
    except JWTError:
        return None


def get_current_user(token: Optional[str] = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    sub = decode_token(token)
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.email == sub).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


def get_current_user_qs(
    header_token: Optional[str] = Depends(oauth2),
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> User:
    """JWT from Authorization header OR ?token= query param. For <a href> / <img src> links."""
    t = header_token or token
    if not t:
        raise HTTPException(status_code=401, detail="Not authenticated")
    sub = decode_token(t)
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.email == sub).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


def require_web_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "web_admin":
        raise HTTPException(status_code=403, detail="Web admin only")
    return user
