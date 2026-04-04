import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from shop_bot.data_manager import database
from shop_bot.data_manager.database import get_admin_ids
from shop_bot.support_bot.handlers import get_support_router

logger = logging.getLogger(__name__)

class SupportBotController:
    def __init__(self):
        self._dp: Dispatcher | None = None
        self._bot: Bot | None = None
        self._task = None
        self._is_running = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        logger.info("Цикл событий установлен.")

    def get_bot_instance(self) -> Bot | None:
        return self._bot

    async def _start_polling(self):
        self._is_running = True
        logger.info("Запущен опрос Telegram (polling)...")
        try:
            await self._dp.start_polling(self._bot)
        except asyncio.CancelledError:
            logger.info("Опрос остановлен (задача отменена).")
        except Exception as e:
            logger.error(f"Ошибка во время опроса: {e}", exc_info=True)
        finally:
            logger.info("Опрос корректно остановлен.")
            self._is_running = False
            self._task = None

    def start(self):
        logger.info(f"SupportBotController.start() вызван. _is_running={self._is_running}, _loop={self._loop is not None}, _dp={self._dp is not None}")

        # Проверяем по _dp - если он не None, значит бот ещё работает или останавливается
        if self._dp:
            return {"status": "error", "message": "Support-бот уже запущен или останавливается."}

        if not self._loop or not self._loop.is_running():
            logger.error(f"Support-бот: цикл событий не установлен. _loop={self._loop}")
            return {"status": "error", "message": "Критическая ошибка: цикл событий не установлен."}

        token = database.get_setting("support_bot_token")
        bot_username = database.get_setting("support_bot_username")
        # допускаем отсутствие одиночного admin_telegram_id, если настроены admin_telegram_ids
        admin_id = database.get_setting("admin_telegram_id")
        admin_ids = get_admin_ids()

        if not all([token, bot_username]) or (not admin_id and not admin_ids):
            return {
                "status": "error",
                "message": "Невозможно запустить support-бот: заполните support_bot_token, support_bot_username и хотя бы одного администратора (admin_telegram_id или admin_telegram_ids)."
            }

        try:
            self._bot = Bot(
                token=token,
                session=AiohttpSession(timeout=120),
                default=DefaultBotProperties(parse_mode=ParseMode.HTML)
            )
            self._dp = Dispatcher()
            
            router = get_support_router()
            self._dp.include_router(router)
            
            try:
                asyncio.run_coroutine_threadsafe(self._bot.delete_webhook(drop_pending_updates=True), self._loop)
            except Exception as e:
                logger.warning(f"Не удалось удалить вебхук перед запуском опроса: {e}")

            # Помечаем что бот запускается (для корректной работы stop())
            self._is_running = True

            self._task = asyncio.run_coroutine_threadsafe(self._start_polling(), self._loop)
            logger.info("Команда на запуск передана в цикл событий.")
            return {"status": "success", "message": "Команда на запуск support-бота отправлена."}
        except Exception as e:
            logger.error(f"Ошибка запуска support-бота: {e}", exc_info=True)
            self._bot = None
            self._dp = None
            return {"status": "error", "message": f"Ошибка при запуске support-бота: {e}"}

    def stop(self):
        logger.info(f"SupportBotController.stop() вызван. _is_running={self._is_running}, _loop={self._loop is not None}, _dp={self._dp is not None}")

        if not self._dp:
            logger.error("Support-бот: _dp отсутствует (бот не запущен или уже остановлен)")
            return {"status": "error", "message": "Support-бот не запущен."}

        if not self._loop:
            logger.error("Support-бот: _loop отсутствует")
            return {"status": "error", "message": "Критическая ошибка: цикл событий недоступен."}

        logger.info("Support-бот: отправляю сигнал на корректную остановку...")
        future = asyncio.run_coroutine_threadsafe(self._dp.stop_polling(), self._loop)
        # Ждём завершения polling (таймаут 10 сек)
        try:
            future.result(timeout=10)
        except asyncio.TimeoutError:
            logger.error("Support-бот: таймаут остановки polling")
        except Exception as e:
            logger.error(f"Support-бот: ошибка при остановке: {e}")

        # Гарантированно очищаем ресурсы
        self._is_running = False
        self._task = None
        self._dp = None
        if self._bot:
            asyncio.run_coroutine_threadsafe(self._bot.close(), self._loop)
            self._bot = None

        logger.info("Support-бот: остановлен.")
        return {"status": "success", "message": "Support-бот остановлен."}

    def get_status(self):
        return {"is_running": self._is_running}
