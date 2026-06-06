"""Multi-bot orchestrator. One PTB Application per Bot row, dispatched by bot_type."""
import logging
from datetime import datetime
from typing import Dict, Optional
from telegram.ext import Application

from ..db import SessionLocal
from ..models import Bot as BotModel
from ..config import decrypt

log = logging.getLogger("bot_manager")


class BotManager:
    def __init__(self):
        self.apps: Dict[int, Application] = {}
        self.started = False

    async def start_all(self):
        self.started = True
        db = SessionLocal()
        try:
            bots = db.query(BotModel).all()
            for b in bots:
                try:
                    await self.start_bot(b.id)
                except Exception as e:
                    log.exception("Failed to start bot %s: %s", b.id, e)
        finally:
            db.close()

    async def stop_all(self):
        for bid in list(self.apps.keys()):
            await self.stop_bot(bid)

    async def start_bot(self, bot_id: int) -> Optional[Application]:
        if bot_id in self.apps:
            return self.apps[bot_id]
        db = SessionLocal()
        try:
            b = db.query(BotModel).filter(BotModel.id == bot_id).first()
            if not b:
                return None
            token = decrypt(b.token_enc)
            bot_type = b.bot_type
            if not token:
                log.error("Bot %s token cannot be decrypted (Fernet key mismatch).", bot_id)
                b.status = "revoked"
                db.commit()
                return None
        finally:
            db.close()

        # Late imports avoid a circular dependency: handlers import bot_manager.manager.
        from .assistant_bot import build_assistant_handlers
        from .admin_bot import build_admin_handlers

        app = Application.builder().token(token).build()
        if bot_type == "admin_bot":
            build_admin_handlers(app, bot_id)
        else:
            build_assistant_handlers(app, bot_id)

        await app.initialize()
        await app.start()
        # `drop_pending_updates=True` clears Telegram's queue, releasing any
        # prior getUpdates lock from a crashed previous instance — much faster
        # than waiting for Telegram's 5s server-side timeout.
        try:
            await app.bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass
        await app.updater.start_polling(drop_pending_updates=True)

        try:
            me = await app.bot.get_me()
            db = SessionLocal()
            try:
                bb = db.query(BotModel).filter(BotModel.id == bot_id).first()
                if bb:
                    bb.username = me.username
                    bb.last_seen_at = datetime.utcnow()
                    db.commit()
            finally:
                db.close()
        except Exception:
            pass

        self.apps[bot_id] = app
        log.info("Started bot %s (type=%s)", bot_id, bot_type)
        return app

    async def stop_bot(self, bot_id: int):
        app = self.apps.pop(bot_id, None)
        if not app:
            return
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception as e:
            log.warning("Error stopping bot %s: %s", bot_id, e)

    async def send_message(self, bot_id: int, chat_id: str, text: str, reply_markup=None, parse_mode=None):
        app = self.apps.get(bot_id)
        if not app:
            await self.start_bot(bot_id)
            app = self.apps.get(bot_id)
        if not app:
            return None
        return await app.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)


manager = BotManager()
