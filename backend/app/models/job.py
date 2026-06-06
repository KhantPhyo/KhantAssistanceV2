from datetime import datetime
from sqlalchemy import String, DateTime, Text, ForeignKey, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ..db import Base


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, index=True)  # YYMon#### e.g. 26May0001
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    report_type: Mapped[str] = mapped_column(String(16))  # photo|video|document|text|any
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # status ∈ {pending, in_progress, done, declined, overdue, cancelled, superseded}
    created_via: Mapped[str] = mapped_column(String(16), default="web")  # web | admin_bot
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # recurrence (templates spawn child instances)
    recurrence: Mapped[str] = mapped_column(String(16), default="none")  # none|daily|weekly|monthly|frequent
    recurrence_every: Mapped[int] = mapped_column(Integer, default=0)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_template_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    next_spawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # accept_mode controls when job.status flips to in_progress on multi-assignee jobs:
    #   any = first staff to click ✅ Accept moves it (default)
    #   all = every non-leave/declined assignee must Accept first
    accept_mode: Mapped[str] = mapped_column(String(8), default="any")
    # completion_mode controls when job.status flips to done:
    #   any = first finisher closes job; all = every active assignee must finish
    completion_mode: Mapped[str] = mapped_column(String(8), default="all")

    # Notion sync — populated when first pushed to Notion, then re-used for subsequent updates
    notion_page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notion_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    assignments: Mapped[list["JobAssignment"]] = relationship(back_populates="job", cascade="all,delete")
    reports: Mapped[list["Report"]] = relationship(back_populates="job", cascade="all,delete")


class JobAssignment(Base):
    __tablename__ = "job_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    assistant_id: Mapped[int] = mapped_column(ForeignKey("assistants.id"))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    declined_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    transferred_to_id: Mapped[int | None] = mapped_column(ForeignKey("assistants.id"), nullable=True)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    job: Mapped["Job"] = relationship(back_populates="assignments")


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    assistant_id: Mapped[int] = mapped_column(ForeignKey("assistants.id"))
    type: Mapped[str] = mapped_column(String(16))
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    job: Mapped["Job"] = relationship(back_populates="reports")
