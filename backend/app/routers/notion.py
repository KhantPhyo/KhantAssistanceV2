from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import Job, User
from ..security import get_current_user
from ..services import notion_sync, audit
from ..config import settings

router = APIRouter(prefix="/api/notion", tags=["notion"])


@router.get("/status")
def status(db: Session = Depends(get_db), _u=Depends(get_current_user)):
    enabled = notion_sync.is_enabled()
    total = db.query(Job).filter(Job.is_template == False).count()  # noqa: E712
    synced = db.query(Job).filter(Job.is_template == False,  # noqa: E712
                                  Job.notion_page_id.isnot(None)).count()
    return {
        "enabled": enabled,
        "database_id": settings.NOTION_JOBS_DATABASE_ID if enabled else None,
        "total_jobs": total,
        "synced_jobs": synced,
        "unsynced_jobs": total - synced,
    }


@router.post("/sync-all")
async def sync_all(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    result = await notion_sync.sync_all()
    audit.write(db, actor_type="web_admin", actor_id=me.id, action="notion_full_sync",
                payload=result)
    return result
