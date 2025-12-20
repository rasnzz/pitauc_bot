import asyncio
from datetime import datetime, timedelta
from typing import Dict
import logging
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import selectinload

from database.database import get_db
from database.models import Auction, User, Bid
from utils.formatters import format_auction_message, format_ended_auction_message
from keyboards.inline import get_channel_auction_keyboard
from utils.periodic_updater import periodic_updater
from config import Config

logger = logging.getLogger(__name__)

class AuctionTimerManager:
    """Менеджер таймеров для аукционов"""
    
    def __init__(self):
        self.active_timers: Dict[int, asyncio.Task] = {}
        self.lock = asyncio.Lock()
        self.bot = None  # Добавляем хранение бота
    
    def set_bot(self, bot):
        """Установить бота для таймеров"""
        self.bot = bot
    
    async def start_auction_timer(self, auction_id: int, ends_at: datetime):
        """Запуск таймера для аукциона"""
        async with self.lock:
            # Отменяем старый таймер, если есть
            if auction_id in self.active_timers:
                self.active_timers[auction_id].cancel()
                await asyncio.sleep(0.1)  # Даем время для отмены
            
            # Создаем новую задачу
            task = asyncio.create_task(
                self._auction_timer_task(auction_id, ends_at)
            )
            self.active_timers[auction_id] = task
            logger.info(f"Таймер запущен для аукциона #{auction_id}")
            
            # Запускаем немедленное обновление таймера в канале
            await periodic_updater.force_update_auction(auction_id)
    
    async def _auction_timer_task(self, auction_id: int, ends_at: datetime):
        """Фоновая задача таймера"""
        try:
            now = datetime.utcnow()
            delay = (ends_at - now).total_seconds()
            
            if delay > 0:
                await asyncio.sleep(delay)
                await self._end_auction(auction_id)
            else:
                await self._end_auction(auction_id)
                
        except asyncio.CancelledError:
            logger.info(f"Таймер для аукциона #{auction_id} отменен")
            return
        except Exception as e:
            logger.error(f"Ошибка в таймере аукциона #{auction_id}: {e}", exc_info=True)
        finally:
            async with self.lock:
                if auction_id in self.active_timers:
                    del self.active_timers[auction_id]
                # Очищаем историю обновлений для этого аукциона
                periodic_updater.clear_update_history(auction_id)
    
    async def _end_auction(self, auction_id: int):
        """Завершение аукциона"""
        try:
            from utils.notifications import send_winner_notification
            
            if not self.bot:
                logger.error(f"Бот не установлен для завершения аукциона #{auction_id}")
                return
            
            async with get_db() as session:
                async with session.begin():
                    # Получаем аукцион
                    stmt = select(Auction).where(Auction.id == auction_id)
                    result = await session.execute(stmt)
                    auction = result.scalar_one_or_none()
                    
                    if not auction or auction.status != 'active':
                        return
                    
                    logger.info(f"Завершаю аукцион #{auction_id}")
                    
                    # Обновляем статус
                    auction.status = 'ended'
                    auction.ended_at = datetime.utcnow()
                    
                    # Получаем победителя
                    stmt_winner = select(Bid).where(
                        Bid.auction_id == auction_id
                    ).order_by(desc(Bid.amount)).limit(1)
                    result_winner = await session.execute(stmt_winner)
                    winning_bid = result_winner.scalar_one_or_none()
                    
                    if winning_bid:
                        auction.winner_id = winning_bid.user_id
                        auction.current_price = winning_bid.amount
            
            # Получаем данные для обновления сообщения
            async with get_db() as session:
                # Получаем аукцион с победителем
                stmt = select(Auction).where(Auction.id == auction_id).options(
                    selectinload(Auction.winner)
                )
                result = await session.execute(stmt)
                auction = result.scalar_one()
                
                # Получаем топ-3 ставки
                stmt_top_bids = select(Bid).where(
                    Bid.auction_id == auction_id
                ).order_by(desc(Bid.amount)).limit(3).options(
                    selectinload(Bid.user)
                )
                result_top = await session.execute(stmt_top_bids)
                top_bids = result_top.scalars().all()
                
                # Получаем количество ставок
                stmt_count = select(Bid).where(Bid.auction_id == auction_id).count()
                result_count = await session.execute(stmt_count)
                bids_count = result_count.scalar()
                
                # Обновляем сообщение в канале
                await self._update_channel_message(auction, top_bids, bids_count)
            
            # Уведомляем победителя
            if winning_bid:
                await self._notify_winner(auction_id, winning_bid.user_id)
            
            logger.info(f"Аукцион #{auction_id} завершен, сообщение в канале обновлено")
            
        except Exception as e:
            logger.error(f"Ошибка при завершении аукциона #{auction_id}: {e}", exc_info=True)
    
    async def _update_channel_message(self, auction: Auction, top_bids=None, bids_count=0):
        """Обновление сообщения в канале после завершения аукциона"""
        try:
            # Формируем сообщение о завершенном аукционе
            message_text = format_ended_auction_message(auction, top_bids, bids_count)
            
            # Обновляем сообщение в канале (без клавиатуры)
            try:
                # Сначала пробуем обновить подпись (если было фото)
                await self.bot.edit_message_caption(
                    chat_id=Config.CHANNEL_ID,
                    message_id=auction.channel_message_id,
                    caption=message_text,
                    parse_mode='HTML'
                )
            except:
                # Если не получилось (например, сообщение без фото), обновляем текст
                await self.bot.edit_message_text(
                    chat_id=Config.CHANNEL_ID,
                    message_id=auction.channel_message_id,
                    text=message_text,
                    parse_mode='HTML'
                )
            
            logger.info(f"Сообщение в канале для аукциона #{auction.id} обновлено (завершен)")
            
        except Exception as e:
            logger.error(f"Ошибка при обновлении сообщения в канале для завершенного аукциона #{auction.id}: {e}")
    
    async def _notify_winner(self, auction_id: int, winner_user_id: int):
        """Уведомление победителя"""
        try:
            from utils.notifications import send_winner_notification
            
            async with get_db() as session:
                stmt = select(Auction).where(Auction.id == auction_id)
                result = await session.execute(stmt)
                auction = result.scalar_one_or_none()
                
                if not auction:
                    return
                
                stmt_user = select(User).where(User.id == winner_user_id)
                result_user = await session.execute(stmt_user)
                winner = result_user.scalar_one_or_none()
                
                if not winner:
                    return
                
                await send_winner_notification(self.bot, auction, winner)
                    
        except Exception as e:
            logger.error(f"Ошибка при уведомлении победителя: {e}")
    
    async def restore_timers(self):
        """Восстановление таймеров после перезапуска бота"""
        try:
            async with get_db() as session:
                # Восстанавливаем активные аукционы
                stmt = select(Auction).where(
                    and_(
                        Auction.status == 'active',
                        Auction.ends_at.isnot(None),
                        Auction.ends_at > datetime.utcnow()
                    )
                )
                result = await session.execute(stmt)
                active_auctions = result.scalars().all()
                
                for auction in active_auctions:
                    await self.start_auction_timer(auction.id, auction.ends_at)
                
                # Завершаем аукционы, время которых истекло
                stmt_expired = select(Auction).where(
                    and_(
                        Auction.status == 'active',
                        Auction.ends_at.isnot(None),
                        Auction.ends_at <= datetime.utcnow()
                    )
                )
                result_expired = await session.execute(stmt_expired)
                expired_auctions = result_expired.scalars().all()
                
                for auction in expired_auctions:
                    logger.info(f"Аукцион #{auction.id} истек во время простоя бота")
                    await self._end_auction(auction.id)
                
                logger.info(f"Восстановлено {len(active_auctions)} таймеров, завершено {len(expired_auctions)} аукционов")
                
        except Exception as e:
            logger.error(f"Ошибка при восстановлении таймеров: {e}")
    
    async def stop_all_timers(self):
        """Остановка всех таймеров"""
        async with self.lock:
            tasks = list(self.active_timers.values())
            for task in tasks:
                task.cancel()
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
            self.active_timers.clear()
            # Очищаем всю историю обновлений
            periodic_updater.clear_update_history()
            logger.info("Все таймеры остановлены")

# Глобальный экземпляр менеджера
auction_timer_manager = AuctionTimerManager()
