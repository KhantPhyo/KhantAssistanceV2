from datetime import datetime
from sqlalchemy import String, DateTime, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from ..db import Base


class Bot(Base):
    __tablename__ = "bots"
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_type: Mapped[str] = mapped_column(String(16), index=True)  # admin_bot | assistant_bot
    token_enc: Mapped[str] = mapped_column(String(512))
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | active | revoked
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    assistant_id: Mapped[int | None] = mapped_column(ForeignKey("assistants.id"), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
