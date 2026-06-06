from datetime import datetime
from sqlalchemy import String, DateTime, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from ..db import Base


class Assistant(Base):
    __tablename__ = "assistants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    position: Mapped[str | None] = mapped_column(String(128), nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    bot_id: Mapped[int | None] = mapped_column(ForeignKey("bots.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|active|paused|removed
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
