import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings, UPLOAD_PATH
from .db import engine, Base, SessionLocal
from .models import User, Setting
from .security import hash_password
from .routers import (
    auth, admins, admin_bots, assistants, jobs, groups,
    dashboard, control, audit as audit_router, announcements, ws, notion,
)
from .services.bot_manager import manager
from .services.reminders import start_scheduler
from .services import audit
from .migrations import migrate as _migrate

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")


def seed(db):
    existing = db.query(User).filter(User.email == settings.DEFAULT_ADMIN_EMAIL).first()
    if not existing:
        u = User(email=settings.DEFAULT_ADMIN_EMAIL,
                 password_hash=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
                 role="web_admin",
                 telegram_username=(settings.DEFAULT_ADMIN_TG_USERNAME or None) or None,
                 is_active=True)
        db.add(u); db.commit(); db.refresh(u)
        audit.write(db, actor_type="system", actor_id=None, action="seed_admin",
                    target_type="user", target_id=u.id,
                    payload={"email": u.email})
        log.info("Seeded admin: %s", settings.DEFAULT_ADMIN_EMAIL)
    defaults = {
        "reminder_minutes": str(settings.DEFAULT_REMINDER_MINUTES),
        "timezone": settings.TIMEZONE,
        "bots_frozen": "0",
    }
    for k, v in defaults.items():
        if not db.query(Setting).filter(Setting.key == k).first():
            db.add(Setting(key=k, value=v))
    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    _migrate(engine)
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
    await manager.start_all()
    start_scheduler()
    yield
    await manager.stop_all()


app = FastAPI(title="Khant Assistance v2 API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_PATH)), name="uploads")

app.include_router(auth.router)
app.include_router(admins.router)
app.include_router(admin_bots.router)
app.include_router(assistants.router)
app.include_router(jobs.router)
app.include_router(groups.router)
app.include_router(dashboard.router)
app.include_router(control.router)
app.include_router(audit_router.router)
app.include_router(announcements.router)
app.include_router(notion.router)
app.include_router(ws.router)


@app.get("/api/health")
def health():
    return {"ok": True}
