from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, EmailStr


# ---------------- Auth ----------------

class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    email: str
    role: str


class MeOut(BaseModel):
    id: int
    email: str
    role: str
    telegram_username: Optional[str] = None
    is_active: bool


# ---------------- Admins (User CRUD) ----------------

class AdminCreateIn(BaseModel):
    email: EmailStr
    password: str
    role: str = "web_admin"  # web_admin | remote_admin
    telegram_username: Optional[str] = None


class AdminUpdateIn(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    telegram_username: Optional[str] = None
    is_active: Optional[bool] = None


class AdminOut(BaseModel):
    id: int
    email: str
    role: str
    telegram_username: Optional[str] = None
    is_active: bool
    created_at: datetime


# ---------------- Bots ----------------

class AdminBotBindIn(BaseModel):
    bot_token: str
    owner_user_id: Optional[int] = None  # defaults to current user


class AdminBotOut(BaseModel):
    id: int
    bot_type: str
    username: Optional[str] = None
    chat_id: Optional[str] = None
    status: str
    owner_user_id: Optional[int] = None
    last_seen_at: Optional[datetime] = None
    instructions: Optional[str] = None


# ---------------- Assistants ----------------

class AssistantIn(BaseModel):
    name: str
    phone: Optional[str] = None
    position: Optional[str] = None
    telegram_username: Optional[str] = None
    bot_token: str


class AssistantOut(BaseModel):
    id: int
    name: str
    phone: Optional[str] = None
    position: Optional[str] = None
    telegram_username: Optional[str] = None
    status: str
    chat_id: Optional[str] = None
    bot_username: Optional[str] = None


# ---------------- Jobs ----------------

class JobIn(BaseModel):
    title: str
    description: str = ""
    report_type: str  # photo|video|document|text|any
    deadline_at: Optional[datetime] = None
    assistant_ids: List[int] = []
    group_ids: List[int] = []
    recurrence: str = "none"
    recurrence_every: int = 0
    completion_mode: str = "all"
    accept_mode: str = "any"


class JobUpdateIn(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    report_type: Optional[str] = None
    deadline_at: Optional[datetime] = None
    completion_mode: Optional[str] = None
    accept_mode: Optional[str] = None
    # Provide BOTH lists to overwrite the assignee set entirely; provide neither
    # to leave assignments untouched. (None vs [] is meaningful — None = skip.)
    assistant_ids: Optional[List[int]] = None
    group_ids: Optional[List[int]] = None
    # Recurrence cascade — see routers/jobs.py for behavior
    recurrence: Optional[str] = None
    recurrence_every: Optional[int] = None


class JobReassignIn(BaseModel):
    assistant_ids: List[int] = []
    group_ids: List[int] = []


class JobAssignmentOut(BaseModel):
    id: int
    assistant_id: int
    assistant_name: Optional[str] = None
    status: str
    accepted_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    declined_reason: Optional[str] = None


class ReportOut(BaseModel):
    id: int
    job_id: int
    assistant_id: int
    type: str
    content_text: Optional[str] = None
    file_name: Optional[str] = None
    submitted_at: datetime


class JobOut(BaseModel):
    id: int
    code: str
    title: str
    description: str
    report_type: str
    deadline_at: Optional[datetime] = None
    status: str
    created_via: str = "web"
    created_at: datetime
    completed_at: Optional[datetime] = None
    recurrence: str = "none"
    recurrence_every: int = 0
    completion_mode: str = "all"
    accept_mode: str = "any"
    is_template: bool = False
    parent_template_id: Optional[int] = None
    next_spawn_at: Optional[datetime] = None
    # Derived: "one-time" | "daily" | "weekly" | "monthly" | "template:<cadence>"
    # — what schedule produced this job (always present for UI display)
    action: str = "one-time"
    group_ids: List[int] = []
    assignments: List[JobAssignmentOut] = []
    reports: List[ReportOut] = []


# ---------------- Groups ----------------

class GroupIn(BaseModel):
    name: str
    description: str = ""
    assistant_ids: List[int] = []


class GroupOut(BaseModel):
    id: int
    name: str
    description: str = ""
    assistant_ids: List[int] = []
    assistant_names: List[str] = []


# ---------------- Announcements ----------------

class AnnouncementIn(BaseModel):
    title: str
    body: str = ""
    cadence: str = "once"
    assistant_ids: List[int] = []
    group_ids: List[int] = []


class AnnouncementRecipientOut(BaseModel):
    id: int
    assistant_id: int
    assistant_name: Optional[str] = None
    acked_at: Optional[datetime] = None
    last_sent_at: Optional[datetime] = None


class AnnouncementOut(BaseModel):
    id: int
    code: str
    title: str
    body: str
    cadence: str
    status: str
    created_at: datetime
    recipients: List[AnnouncementRecipientOut] = []


# ---------------- Settings ----------------

class SettingIn(BaseModel):
    key: str
    value: str


# ---------------- Audit ----------------

class AuditLogOut(BaseModel):
    id: int
    ts: datetime
    actor_type: str
    actor_id: Optional[int] = None
    action: str
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    payload: Optional[Any] = None
