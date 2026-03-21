import asyncio
import logging
import uvloop
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import ClientSession, ClientTimeout, TCPConnector

# Для SOCKS5 прокси нужен дополнительный модуль
try:
    from aiohttp_socks import ProxyConnector
    PROXY_AVAILABLE = True
except ImportError:
    PROXY_AVAILABLE = False
    logging.warning("aiohttp_socks не установлен. Установите: pip install aiohttp-socks")

from config import Config
from database.database import init_db
from middlewares.rate_limit import RateLimitMiddleware
from middlewares.user_check import UserCheckMiddleware
from utils.backup import backup_manager
from utils.periodic_updater import periodic_updater
from utils.timer import auction_timer_manager

try:
    from utils.channel_updater import get_channel_updater
    CHANNEL_UPDATER_AVAILABLE = True
except ImportError:
    CHANNEL_UPDATER_AVAILABLE = False
    logging.warning("ChannelUpdater не найден. Обновление сообщений в канале недоступно.")

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

async def create_backup_on_startup():
    logger.info("Создание резервной копии базы данных...")
    await backup_manager.create_backup()
    logger.info("Резервная копия создана")

async def schedule_backups():
    await backup_manager.schedule_backups(interval_hours=24)

async def check_expired_auctions_on_startup():
    logger.info("Проверка просроченных аукционов...")
    expired_count = await auction_timer_manager.check_and_complete_expired_auctions()
    if expired_count > 0:
        logger.info(f"Завершено {expired_count} просроченных аукционов при запуске")
    else:
        logger.info("Просроченных аукционов не найдено")

async def fix_all_channel_messages_on_startup(bot):
    if not CHANNEL_UPDATER_AVAILABLE:
        logger.warning("ChannelUpdater недоступен, пропускаю обновление сообщений в канале")
        return
    
    logger.info("🔄 Проверка и исправление всех сообщений в канале...")
    try:
        updater = get_channel_updater(bot)
        if updater:
            await updater.check_and_fix_all_messages()
            logger.info("✅ Проверка сообщений в канале завершена")
        else:
            logger.error("❌ Не удалось создать ChannelUpdater")
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке сообщений в канале: {e}")

async def create_bot():
    """Создание бота с поддержкой прокси"""
    connector = None
    
    if Config.PROXY_URL and PROXY_AVAILABLE:
        try:
            logger.info(f"Используется прокси: {Config.PROXY_URL.split('@')[-1] if '@' in Config.PROXY_URL else Config.PROXY_URL}")
            connector = ProxyConnector.from_url(Config.PROXY_URL)
            timeout = ClientTimeout(total=60, connect=30, sock_read=30)
            session = ClientSession(connector=connector, timeout=timeout)
            
            bot = Bot(
                token=Config.BOT_TOKEN,
                session=session,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML)
            )
            logger.info("✅ Бот настроен через SOCKS5 прокси")
            return bot
        except Exception as e:
            logger.error(f"❌ Ошибка настройки прокси: {e}, пробую без прокси")
    
    # Без прокси или если прокси недоступен
    bot = Bot(
        token=Config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    logger.info("✅ Бот запущен без прокси")
    return bot

async def main():
    await init_db()
    logger.info("База данных инициализирована")
    
    await create_backup_on_startup()
    
    bot = await create_bot()
    
    await check_expired_auctions_on_startup()
    await fix_all_channel_messages_on_startup(bot)
    
    periodic_updater.set_bot(bot)
    auction_timer_manager.set_bot(bot)
    asyncio.create_task(auction_timer_manager.periodic_check())
    
    if CHANNEL_UPDATER_AVAILABLE:
        get_channel_updater(bot)
    
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    rate_limit_middleware = RateLimitMiddleware(rate_limit_period=1)
    user_check_middleware = UserCheckMiddleware()
    
    dp.callback_query.middleware(rate_limit_middleware)
    dp.message.middleware(rate_limit_middleware)
    dp.callback_query.middleware(user_check_middleware)
    dp.message.middleware(user_check_middleware)
    
    from handlers.user import router as user_router
    from handlers.admin import router as admin_router
    from handlers.auction import router as auction_router
    
    dp.include_router(user_router)
    dp.include_router(admin_router)
    dp.include_router(auction_router)
    
    await auction_timer_manager.restore_timers_improved()
    logger.info("Планировщик таймеров запущен")
    
    asyncio.create_task(schedule_backups())
    await periodic_updater.start()
    logger.info("Периодическое обновление таймеров запущено")
    
    logger.info("Бот запущен")
    
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    finally:
        await backup_manager.create_backup()
        await periodic_updater.stop()
        await auction_timer_manager.stop_all_timers()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
