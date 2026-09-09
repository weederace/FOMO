import logging

from app.config.settings import Settings

LOGGER = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, settings: Settings) -> None:
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id
        self.enabled = bool(self._token and self._chat_id)
        if not self.enabled:
            LOGGER.info("Telegram notifications disabled.")

    async def send(self, message: str) -> bool:
        if not self.enabled:
            return False
        from aiogram import Bot

        bot = Bot(self._token)
        try:
            await bot.send_message(self._chat_id, message)
            return True
        finally:
            await bot.session.close()
