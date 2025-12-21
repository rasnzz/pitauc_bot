from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from config import Config
from .models import Base
import asyncio
import logging

logger = logging.getLogger(__name__)

# Создаем движок для асинхронной работы с БД с оптимизациями для многопользовательской работы
engine = create_async_engine(
    Config.DATABASE_URL,
    echo=False,  # Выключаем echo для производительности
    connect_args={
        "check_same_thread": False,  # Разрешаем доступ из разных потоков
        "timeout": 60,               # Увеличиваем таймаут до 60 секунд
    },
    pool_pre_ping=True,              # Проверяем соединение перед использованием
    pool_recycle=3600                # Пересоздаем соединение каждый час
)

# Создаем фабрику сессий
AsyncSessionLocal = sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

async def init_db():
    """Инициализация базы данных"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Настраиваем SQLite для многопользовательской работы
    async with engine.connect() as conn:
        # ИСПРАВЛЕНО: используем text() для SQL запросов
        await conn.execute(text("PRAGMA journal_mode=WAL"))  # Включаем WAL режим
        await conn.execute(text("PRAGMA synchronous=NORMAL"))  # Оптимизируем синхронизацию
        await conn.execute(text("PRAGMA busy_timeout=5000"))  # Таймаут при блокировке 5 секунд
        await conn.execute(text("PRAGMA cache_size=-2000"))  # Увеличиваем кэш
        await conn.execute(text("PRAGMA foreign_keys=ON"))  # Включаем внешние ключи
        await conn.commit()
    
    logger.info("База данных инициализирована с оптимизациями для многопользовательской работы")

@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Получение сессии БД с обработкой ошибок"""
    session = AsyncSessionLocal()
    try:
        yield session
        await session.commit()
    except Exception as e:
        await session.rollback()
        logger.error(f"Ошибка в сессии БД: {e}")
        raise
    finally:
        await session.close()

# Новая функция для безопасного использования сессии в асинхронных задачах
async def get_db_session():
    """Получение сессии для использования в асинхронных задачах"""
    session = AsyncSessionLocal()
    try:
        return session
    except Exception as e:
        await session.close()
        raise
