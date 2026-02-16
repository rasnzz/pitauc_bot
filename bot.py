import asyncio
import logging
import uvloop
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from config import Config
from database.database import init_db
from middlewares.rate_limit import RateLimitMiddleware
from middlewares.user_check import UserCheckMiddleware
from utils.backup import backup_manager
from utils.periodic_updater import periodic_updater
from utils.timer import auction_timer_manager
# Импорт может быть ошибочным, если файл не создан
try:
    from utils.channel_updater import get_channel_updater
    CHANNEL_UPDATER_AVAILABLE = True
except ImportError:
    CHANNEL_UPDATER_AVAILABLE = False
    logging.warning("ChannelUpdater не найден. Обновление сообщений в канале недоступно.")

# Используем uvloop для лучшей производительности асинхронных операций
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

# Настройка логирования
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
    """Создание бэкапа при запуске"""
    logger.info("Создание резервной копии базы данных...")
    await backup_manager.create_backup()
    logger.info("Резервная копия создана")

async def schedule_backups():
    """Запуск планировщика бэкапов"""
    await backup_manager.schedule_backups(interval_hours=24)

async def check_expired_auctions_on_startup():
    """Проверка просроченных аукционов при запуске"""
    logger.info("Проверка просроченных аукционов...")
    expired_count = await auction_timer_manager.check_and_complete_expired_auctions()
    if expired_count > 0:
        logger.info(f"Завершено {expired_count} просроченных аукционов при запуске")
    else:
        logger.info("Просроченных аукционов не найдено")

async def fix_all_channel_messages_on_startup(bot):
    """Исправление всех сообщений в канале при запуске"""
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

async def main():
    """Основная функция запуска бота"""
    # Инициализация базы данных
    await init_db()
    logger.info("База данных инициализирована")
    
    # Создаем бэкап при запуске
    await create_backup_on_startup()
    
    # Создаем бота и диспетчер
    bot = Bot(
        token=Config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Проверяем просроченные аукционы
    await check_expired_auctions_on_startup()
    
    # Исправляем все сообщения в канале
    await fix_all_channel_messages_on_startup(bot)
    
    # Устанавливаем бота для периодического обновления
    periodic_updater.set_bot(bot)
    
    # Устанавливаем бота для менеджера таймеров
    auction_timer_manager.set_bot(bot)
    asyncio.create_task(auction_timer_manager.periodic_check())
    
    # Инициализируем ChannelUpdater
    if CHANNEL_UPDATER_AVAILABLE:
        get_channel_updater(bot)
    
    # Используем MemoryStorage для FSM
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    # Регистрируем middleware
    rate_limit_middleware = RateLimitMiddleware(rate_limit_period=1)
    user_check_middleware = UserCheckMiddleware()
    
    dp.callback_query.middleware(rate_limit_middleware)
    dp.message.middleware(rate_limit_middleware)
    dp.callback_query.middleware(user_check_middleware)
    dp.message.middleware(user_check_middleware)
    
    # Регистрируем роутеры
    from handlers.user import router as user_router
    from handlers.admin import router as admin_router
    from handlers.auction import router as auction_router
    
    dp.include_router(user_router)
    dp.include_router(admin_router)
    dp.include_router(auction_router)
    
    # Инициализируем планировщик таймеров
    await auction_timer_manager.restore_timers_improved()
    logger.info("Планировщик таймеров запущен")
    
    # Запускаем планировщик бэкапов
    asyncio.create_task(schedule_backups())
    
    # Запускаем периодическое обновление таймеров
    await periodic_updater.start()
    logger.info("Периодическое обновление таймеров запущено")
    
    logger.info("Бот запущен")
    
    # Запуск бота
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    finally:
        # Создаем бэкап перед выключением
        await backup_manager.create_backup()
        
        # Останавливаем периодическое обновление
        await periodic_updater.stop()
        
        # Останавливаем все таймеры
        await auction_timer_manager.stop_all_timers()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
