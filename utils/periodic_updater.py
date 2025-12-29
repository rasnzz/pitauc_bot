import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Set
import random

from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload

from database.database import get_db
from database.models import Auction, Bid, User
from config import Config
from utils.formatters import format_auction_message
from keyboards.inline import get_channel_auction_keyboard

logger = logging.getLogger(__name__)

class PeriodicUpdater:
    """Менеджер для периодического обновления таймеров в канале"""
    
    def __init__(self, update_interval: int = 60):  # 1 минута вместо 5
        self.update_interval = update_interval
        self.is_running = False
        self.task = None
        self.bot = None
        self.last_update_time: Dict[int, datetime] = {}
        
    def set_bot(self, bot):
        """Установить бота для обновления сообщений"""
        self.bot = bot
    
    async def start(self):
        """Запуск периодического обновления"""
        if self.is_running:
            return
        
        self.is_running = True
        self.task = asyncio.create_task(self._periodic_update_task())
        logger.info(f"Запущено периодическое обновление таймеров (интервал: {self.update_interval} сек)")
    
    async def stop(self):
        """Остановка периодического обновления"""
        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Периодическое обновление таймеров остановлено")
    
    async def _periodic_update_task(self):
        """Фоновая задача для периодического обновления"""
        try:
            while self.is_running:
                try:
                    await self._update_all_active_auctions()
                except Exception as e:
                    logger.error(f"Ошибка при периодическом обновлении: {e}")
                
                # Ждем указанный интервал
                await asyncio.sleep(self.update_interval)
                
        except asyncio.CancelledError:
            logger.info("Задача периодического обновления отменена")
        except Exception as e:
            logger.error(f"Критическая ошибка в периодическом обновлении: {e}")
            self.is_running = False
    
    async def _update_all_active_auctions(self):
        """Обновить все активные аукционы"""
        if not self.bot:
            return
        
        try:
            async with get_db() as session:
                # Получаем все активные аукционы
                stmt = select(Auction).where(
                    Auction.status == 'active',
                    Auction.channel_message_id.isnot(None)
                ).order_by(Auction.ends_at.asc())
                
                result = await session.execute(stmt)
                auctions = result.scalars().all()
                
                if not auctions:
                    return
                
                logger.debug(f"Периодическое обновление: найдено {len(auctions)} активных аукционов")
                
                # Обновляем каждый аукцион (убираем проверку времени)
                for i, auction in enumerate(auctions):
                    try:
                        await self._update_single_auction_safe(session, auction)
                        
                        # Небольшая задержка между обновлениями (0.5-1.5 секунды)
                        if i < len(auctions) - 1:
                            delay = random.uniform(0.5, 1.5)
                            await asyncio.sleep(delay)
                            
                    except Exception as e:
                        logger.error(f"Ошибка при обновлении аукциона #{auction.id}: {e}")
                        
        except Exception as e:
            logger.error(f"Ошибка при получении списка аукционов: {e}")
    
    async def _update_single_auction_safe(self, session, auction: Auction):
        """Безопасное обновление одного аукциона (использует переданную сессию)"""
        try:
            # Перезагружаем аукцион в текущей сессии
            stmt_reload = select(Auction).where(Auction.id == auction.id)
            result_reload = await session.execute(stmt_reload)
            current_auction = result_reload.scalar_one()
            
            # Получаем топ-3 ставки с пользователями
            stmt_top_bids = select(Bid).where(
                Bid.auction_id == auction.id
            ).order_by(Bid.amount.desc()).limit(3)
            result_top = await session.execute(stmt_top_bids)
            top_bids = result_top.scalars().all()
            
            # Подготавливаем данные топ ставок
            prepared_top_bids = []
            for bid in top_bids:
                # Загружаем пользователя для каждой ставки
                stmt_user = select(User).where(User.id == bid.user_id)
                result_user = await session.execute(stmt_user)
                user = result_user.scalar_one_or_none()
                
                if user:
                    prepared_top_bids.append({
                        'amount': bid.amount,
                        'created_at': bid.created_at,
                        'user': user
                    })
            
            # Получаем количество ставок
            stmt_count = select(func.count(Bid.id)).where(Bid.auction_id == auction.id)
            result_count = await session.execute(stmt_count)
            bids_count = result_count.scalar()
            
            # Формируем сообщение
            message_text = format_auction_message(current_auction, prepared_top_bids, bids_count)
            next_bid_amount = current_auction.current_price + current_auction.step_price
            
            # Обновляем сообщение в канале
            await self._edit_channel_message(current_auction, message_text, next_bid_amount)
            
            logger.debug(f"Периодическое обновление: аукцион #{current_auction.id} обновлен")
            
        except Exception as e:
            logger.error(f"Ошибка при подготовке данных аукциона #{auction.id}: {e}")
            raise
    
    async def _edit_channel_message(self, auction: Auction, message_text: str, next_bid_amount: float):
        """Обновить сообщение в канале"""
        try:
            # Пробуем обновить подпись (если было фото)
            try:
                await self.bot.edit_message_caption(
                    chat_id=Config.CHANNEL_ID,
                    message_id=auction.channel_message_id,
                    caption=message_text,
                    reply_markup=get_channel_auction_keyboard(auction.id, next_bid_amount),
                    parse_mode='HTML'
                )
            except:
                # Если не получилось, обновляем текст
                await self.bot.edit_message_text(
                    chat_id=Config.CHANNEL_ID,
                    message_id=auction.channel_message_id,
                    text=message_text,
                    reply_markup=get_channel_auction_keyboard(auction.id, next_bid_amount),
                    parse_mode='HTML'
                )
                
        except Exception as e:
            # Если сообщение не найдено (например, удалено), логируем
            logger.warning(f"Не удалось обновить сообщение для аукциона #{auction.id}: {e}")
    
    async def force_update_auction(self, auction_id: int):
        """Принудительно обновить конкретный аукцион"""
        try:
            async with get_db() as session:
                stmt = select(Auction).where(
                    Auction.id == auction_id,
                    Auction.status == 'active',
                    Auction.channel_message_id.isnot(None)
                )
                result = await session.execute(stmt)
                auction = result.scalar_one_or_none()
                
                if auction:
                    await self._update_single_auction_safe(session, auction)
                    logger.debug(f"Принудительно обновлен аукцион #{auction_id}")
                    
        except Exception as e:
            logger.error(f"Ошибка при принудительном обновлении аукциона #{auction_id}: {e}")
    
    def clear_update_history(self, auction_id: int = None):
        """Очистить историю обновлений"""
        if auction_id:
            self.last_update_time.pop(auction_id, None)
        else:
            self.last_update_time.clear()

# Глобальный экземпляр
periodic_updater = PeriodicUpdater(update_interval=60)  # 1 минута
