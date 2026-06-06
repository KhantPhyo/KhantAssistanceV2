from datetime import datetime
from sqlalchemy import String, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ..db import Base


class Announcement(Base):
    __tablename__ = "announcements"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    cadence: Mapped[str] = mapped_column(String(16), default="once")  # once|2h|daily
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_via: Mapped[str] = mapped_column(String(16), default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    recipients: Mapped[list["AnnouncementRecipient"]] = relationship(back_populates="announcement", cascade="all,delete")


class AnnouncementRecipient(Base):
    __tablename__ = "announcement_recipients"
    id: Mapped[int] = mapped_column(primary_key=True)
    announcement_id: Mapped[int] = mapped_column(ForeignKey("announcements.id", ondelete="CASCADE"))
    assistant_id: Mapped[int] = mapped_column(ForeignKey("assistants.id", ondelete="CASCADE"))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    announcement: Mapped["Announcement"] = relationship(back_populates="recipients")
