import asyncio
from datetime import datetime, timedelta
from typing import Dict
import logging
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import selectinload
import json

from database.database import get_db
from database.models import Auction, User, Bid
from utils.formatters import format_ended_auction_message, get_channel_link
from utils.periodic_updater import periodic_updater
from config import Config
from utils.notifications import send_winner_notification

logger = logging.getLogger(__name__)

class AuctionTimerManager:
    """Менеджер таймеров для аукционов"""
    
    def __init__(self):
        self.active_timers: Dict[int, asyncio.Task] = {}
        self.lock = asyncio.Lock()
        self.bot = None
    
    def set_bot(self, bot):
        """Установить бота для таймеров"""
        self.bot = bot
    
    async def start_auction_timer(self, auction_id: int, ends_at: datetime):
        """Запуск таймера для аукциона"""
        async with self.lock:
            # Отменяем старый таймер, если есть
            if auction_id in self.active_timers:
                try:
                    self.active_timers[auction_id].cancel()
                    await asyncio.sleep(0.1)  # Даем время на отмену
                except:
                    pass
            
            # Проверяем, что аукцион еще активен
            async with get_db() as session:
                stmt = select(Auction).where(
                    Auction.id == auction_id,
                    Auction.status == 'active'
                )
                result = await session.execute(stmt)
                auction = result.scalar_one_or_none()
                
                if not auction:
                    logger.warning(f"Аукцион #{auction_id} не найден или уже завершен")
                    return
            
            # Рассчитываем время до завершения
            now = datetime.utcnow()
            time_to_end = (ends_at - now).total_seconds()
            
            if time_to_end <= 0:
                # Если время уже вышло, завершаем сразу
                logger.info(f"Аукцион #{auction_id} уже должен быть завершен, завершаю")
                asyncio.create_task(self._end_auction(auction_id))
                return
            
            # Создаем новую задачу
            task = asyncio.create_task(
                self._auction_timer_task(auction_id, time_to_end)
            )
            self.active_timers[auction_id] = task
            logger.info(f"Таймер запущен для аукциона #{auction_id}, завершение через {time_to_end:.0f} секунд")
    
    async def _auction_timer_task(self, auction_id: int, delay: float):
        """Фоновая задача таймера"""
        try:
            # Ждем указанное время
            if delay > 0:
                logger.info(f"Таймер аукциона #{auction_id}: ожидание {delay:.0f} секунд")
                await asyncio.sleep(delay)
            
            # Завершаем аукцион
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
                periodic_updater.clear_update_history(auction_id)
    
    async def _end_auction(self, auction_id: int):
        """Завершение аукциона"""
        try:
            logger.info(f"Начинаю завершение аукциона #{auction_id}")
            
            if not self.bot:
                logger.error(f"Бот не установлен для завершения аукциона #{auction_id}")
                return
            
            async with get_db() as session:
                # Получаем аукцион с блокировкой
                stmt = select(Auction).where(
                    Auction.id == auction_id,
                    Auction.status == 'active'
                )
                result = await session.execute(stmt)
                auction = result.scalar_one_or_none()
                
                if not auction:
                    logger.info(f"Аукцион #{auction_id} уже завершен или не найден")
                    return
                
                # Получаем победителя
                stmt_winner = select(Bid).where(
                    Bid.auction_id == auction_id
                ).order_by(desc(Bid.amount)).limit(1)
                result_winner = await session.execute(stmt_winner)
                winning_bid = result_winner.scalar_one_or_none()
                
                # Получаем пользователя-победителя
                winner = None
                if winning_bid:
                    stmt_user = select(User).where(User.id == winning_bid.user_id)
                    result_user = await session.execute(stmt_user)
                    winner = result_user.scalar_one_or_none()
                
                # Обновляем статус аукциона
                auction.status = 'ended'
                auction.ended_at = datetime.utcnow()
                
                if winner:
                    auction.winner_id = winner.id
                    auction.current_price = winning_bid.amount
                    logger.info(f"Аукцион #{auction_id} - победитель: {winner.telegram_id}, сумма: {winning_bid.amount}")
                else:
                    logger.info(f"Аукцион #{auction_id} - победителя нет")
                
                await session.commit()
                
                # Получаем топ-3 ставки
                stmt_top_bids = select(Bid).where(
                    Bid.auction_id == auction_id
                ).order_by(desc(Bid.amount)).limit(3).options(
                    selectinload(Bid.user)
                )
                result_top = await session.execute(stmt_top_bids)
                top_bids = result_top.scalars().all()
                
                # Получаем количество ставок
                stmt_count = select(Bid).where(Bid.auction_id == auction_id)
                result_count = await session.execute(stmt_count)
                bids_count = result_count.scalar()
                
                # Обновляем сообщение в канале
                await self._update_channel_message(auction, top_bids, bids_count)
                
                # Уведомляем победителя, если есть
                if winner:
                    await send_winner_notification(self.bot, auction, winner)
            
            logger.info(f"Аукцион #{auction_id} успешно завершен")
            
        except Exception as e:
            logger.error(f"Ошибка при завершении аукциона #{auction_id}: {e}", exc_info=True)
    
    async def _update_channel_message(self, auction: Auction, top_bids=None, bids_count=0):
        """Обновление сообщения в канале после завершения аукциона"""
        try:
            from utils.formatters import format_ended_auction_message
            
            if not auction.channel_message_id:
                logger.error(f"Нет channel_message_id для аукциона #{auction.id}")
                return
            
            if not self.bot:
                logger.error(f"Бот не установлен для обновления сообщения #{auction.id}")
                return
            
            logger.info(f"Обновляю сообщение в канале для аукциона #{auction.id}")
            
            # Формируем сообщение
            message_text = format_ended_auction_message(auction, top_bids, bids_count)
            
            # Пытаемся обновить сообщение разными способами
            try:
                # Сначала пробуем обновить подпись (если есть фото)
                await self.bot.edit_message_caption(
                    chat_id=Config.CHANNEL_ID,
                    message_id=auction.channel_message_id,
                    caption=message_text,
                    parse_mode='HTML'
                )
                logger.info(f"Обновлена подпись для аукциона #{auction.id}")
            except Exception as e1:
                try:
                    # Если не получилось, пробуем обновить текст
                    await self.bot.edit_message_text(
                        chat_id=Config.CHANNEL_ID,
                        message_id=auction.channel_message_id,
                        text=message_text,
                        parse_mode='HTML'
                    )
                    logger.info(f"Обновлен текст для аукциона #{auction.id}")
                except Exception as e2:
                    logger.error(f"Не удалось обновить сообщение для аукциона #{auction.id}: {e2}")
                    # Пробуем отправить новое сообщение
                    try:
                        new_message = await self.bot.send_message(
                            chat_id=Config.CHANNEL_ID,
                            text=message_text,
                            parse_mode='HTML'
                        )
                        # Обновляем ID сообщения в базе
                        async with get_db() as session:
                            stmt = select(Auction).where(Auction.id == auction.id)
                            result = await session.execute(stmt)
                            auction_to_update = result.scalar_one()
                            auction_to_update.channel_message_id = new_message.message_id
                            await session.commit()
                        logger.info(f"Отправлено новое сообщение для аукциона #{auction.id}")
                    except Exception as e3:
                        logger.error(f"Не удалось отправить новое сообщение: {e3}")
                    
        except Exception as e:
            logger.error(f"Критическая ошибка при обновлении сообщения: {e}")
    
    async def restore_timers(self):
        """Восстановление таймеров после перезапуска бота"""
        try:
            async with get_db() as session:
                # Восстанавливаем активные аукционы
                stmt = select(Auction).where(
                    and_(
                        Auction.status == 'active',
                        Auction.ends_at.isnot(None)
                    )
                )
                result = await session.execute(stmt)
                active_auctions = result.scalars().all()
                
                restored_count = 0
                for auction in active_auctions:
                    now = datetime.utcnow()
                    if auction.ends_at and auction.ends_at > now:
                        await self.start_auction_timer(auction.id, auction.ends_at)
                        restored_count += 1
                        logger.info(f"Восстановлен таймер для аукциона #{auction.id}")
                    else:
                        # Аукцион должен был завершиться
                        logger.info(f"Аукцион #{auction.id} истек, завершаю")
                        await self._end_auction(auction.id)
                
                logger.info(f"Восстановлено {restored_count} таймеров")
                
        except Exception as e:
            logger.error(f"Ошибка при восстановлении таймеров: {e}")
    
    async def stop_all_timers(self):
        """Остановка всех таймеров"""
        async with self.lock:
            for auction_id, task in list(self.active_timers.items()):
                try:
                    task.cancel()
                except:
                    pass
            
            self.active_timers.clear()
            periodic_updater.clear_update_history()
            logger.info("Все таймеры остановлены")

# Глобальный экземпляр менеджера
auction_timer_manager = AuctionTimerManager()
