import asyncio
from datetime import datetime, timedelta
from typing import Dict
import logging
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import selectinload
import json

from database.database import get_db
from database.models import Auction, User, Bid
from utils.formatters import format_ended_auction_message
from utils.periodic_updater import periodic_updater
from config import Config

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
                    return
            
            # Создаем новую задачу
            task = asyncio.create_task(
                self._auction_timer_task(auction_id, ends_at)
            )
            self.active_timers[auction_id] = task
            logger.info(f"Таймер запущен для аукциона #{auction_id}")

    async def restore_timers_improved(self):
        """Улучшенное восстановление таймеров после перезапуска бота"""
        try:
            logger.info("Начинаю восстановление таймеров...")
            
            async with get_db() as session:
                # Находим ВСЕ активные аукционы, даже если время уже истекло
                stmt = select(Auction).where(
                    Auction.status == 'active'
                )
                result = await session.execute(stmt)
                auctions = result.scalars().all()
                
                logger.info(f"Найдено {len(auctions)} активных аукционов в базе")
                
                restored_count = 0
                expired_count = 0
                error_count = 0
                
                for auction in auctions:
                    try:
                        logger.info(f"Проверяю аукцион #{auction.id}: {auction.title}")
                        
                        if auction.ends_at:
                            now = datetime.utcnow()
                            time_diff = (auction.ends_at - now).total_seconds()
                            
                            if time_diff > 0:
                                # Время еще не истекло - запускаем таймер
                                logger.info(f"  Аукцион #{auction.id} активен, завершится через {time_diff:.0f} секунд")
                                await self.start_auction_timer(auction.id, auction.ends_at)
                                restored_count += 1
                            else:
                                # Время истекло - завершаем аукцион
                                logger.warning(f"  Аукцион #{auction.id} просрочен, завершаю...")
                                expired_count += 1
                                
                                # Пометим аукцион как завершенный
                                auction.status = 'ended'
                                auction.ended_at = now
                                
                                # Находим победителя
                                stmt_winner = select(Bid).where(
                                    Bid.auction_id == auction.id
                                ).order_by(desc(Bid.amount)).limit(1)
                                result_winner = await session.execute(stmt_winner)
                                winner_bid = result_winner.scalar_one_or_none()
                                
                                if winner_bid:
                                    auction.winner_id = winner_bid.user_id
                                    auction.current_price = winner_bid.amount
                                    logger.info(f"  Победитель: {winner_bid.user_id}, сумма: {winner_bid.amount}")
                                
                                await session.commit()
                                
                                # Обновляем сообщение в канале
                                await self._update_expired_auction(auction)
                        else:
                            logger.warning(f"  Аукцион #{auction.id} не имеет времени завершения!")
                            # Устанавливаем время завершения по умолчанию
                            auction.ends_at = auction.created_at + timedelta(minutes=Config.BID_TIMEOUT_MINUTES)
                            await session.commit()
                            
                            # Запускаем таймер
                            await self.start_auction_timer(auction.id, auction.ends_at)
                            restored_count += 1
                            
                    except Exception as e:
                        logger.error(f"Ошибка при обработке аукциона #{auction.id}: {e}")
                        error_count += 1
                
                logger.info(f"Восстановление завершено: {restored_count} таймеров запущено, {expired_count} аукционов завершено, {error_count} ошибок")
                
        except Exception as e:
            logger.error(f"Ошибка при восстановлении таймеров: {e}")

    async def _update_expired_auction(self, auction: Auction):
        """Обновление сообщения для просроченного аукциона"""
        try:
            if not auction.channel_message_id:
                logger.warning(f"Аукцион #{auction.id} не имеет сообщения в канале")
                return
            
            # Получаем данные для сообщения
            async with get_db() as session:
                stmt_top_bids = select(Bid).where(
                    Bid.auction_id == auction.id
                ).order_by(desc(Bid.amount)).limit(3).options(
                    selectinload(Bid.user)
                )
                result_top = await session.execute(stmt_top_bids)
                top_bids = result_top.scalars().all()
                
                stmt_count = select(Bid).where(Bid.auction_id == auction.id)
                result_count = await session.execute(stmt_count)
                bids_count = result_count.scalar()
                
                from utils.formatters import format_ended_auction_message
                message_text = format_ended_auction_message(auction, top_bids, bids_count)
            
            # Обновляем сообщение
            try:
                await self.bot.edit_message_caption(
                    chat_id=Config.CHANNEL_ID,
                    message_id=auction.channel_message_id,
                    caption=message_text,
                    parse_mode='HTML'
                )
            except:
                try:
                    await self.bot.edit_message_text(
                        chat_id=Config.CHANNEL_ID,
                        message_id=auction.channel_message_id,
                        text=message_text,
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logger.error(f"Не удалось обновить сообщение для аукциона #{auction.id}: {e}")
                    
        except Exception as e:
            logger.error(f"Ошибка при обновлении просроченного аукциона #{auction.id}: {e}")
    
    async def _auction_timer_task(self, auction_id: int, ends_at: datetime):
        """Фоновая задача таймера"""
        try:
            now = datetime.utcnow()
            delay = (ends_at - now).total_seconds()
            
            if delay > 0:
                logger.info(f"Таймер аукциона #{auction_id}: ждем {delay:.0f} секунд")
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
            
            # Получаем данные аукциона
            async with get_db() as session:
                # Получаем аукцион с блокировкой для обновления
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
                
                # Обновляем статус аукциона
                auction.status = 'ended'
                auction.ended_at = datetime.utcnow()
                
                if winning_bid:
                    auction.winner_id = winning_bid.user_id
                    auction.current_price = winning_bid.amount
                    logger.info(f"Аукцион #{auction_id} - победитель: {winning_bid.user_id}, сумма: {winning_bid.amount}")
                else:
                    logger.info(f"Аукцион #{auction_id} - победителя нет")
                
                await session.commit()
            
            # Получаем полные данные для обновления сообщения
            await asyncio.sleep(0.5)  # Даем время на коммит
            
            async with get_db() as session:
                # Получаем аукцион с победителем
                stmt = select(Auction).where(Auction.id == auction_id).options(
                    selectinload(Auction.winner)
                )
                result = await session.execute(stmt)
                auction = result.scalar_one()
                
                # Получаем топ-3 ставки с пользователями
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
            if winning_bid:
                await self._notify_winner(auction_id, winning_bid.user_id)
            
            logger.info(f"Аукцион #{auction_id} успешно завершен")
            
        except Exception as e:
            logger.error(f"Ошибка при завершении аукциона #{auction_id}: {e}", exc_info=True)
    
    async def _update_channel_message(self, auction: Auction, top_bids=None, bids_count=0):
    """Обновление сообщения в канале после завершения аукциона"""
    try:
        # Используем импортированную функцию из formatters
        from utils.formatters import format_ended_auction_message
        
        if not auction.channel_message_id:
            logger.error(f"Нет channel_message_id для аукциона #{auction.id}")
            return
        
        if not self.bot:
            logger.error(f"Бот не установлен для обновления сообщения #{auction.id}")
            return
        
        logger.info(f"Обновляю сообщение в канале для аукциона #{auction.id}, message_id={auction.channel_message_id}")
        
        # Получаем данные в отдельной сессии
        async with get_db() as session:
            # Загружаем аукцион
            stmt = select(Auction).where(Auction.id == auction.id)
            result = await session.execute(stmt)
            current_auction = result.scalar_one()
            
            # Загружаем победителя, если есть
            if current_auction.winner_id:
                stmt_winner = select(User).where(User.id == current_auction.winner_id)
                result_winner = await session.execute(stmt_winner)
                winner = result_winner.scalar_one_or_none()
                if winner:
                    # Создаем атрибут winner на лету (не сохраняя в БД)
                    current_auction.winner = winner
            
            # Подготавливаем данные топ ставок
            prepared_top_bids = []
            if top_bids:
                for bid in top_bids:
                    stmt_user = select(User).where(User.id == bid.user_id)
                    result_user = await session.execute(stmt_user)
                    user = result_user.scalar_one_or_none()
                    
                    if user:
                        prepared_top_bids.append({
                            'amount': bid.amount,
                            'created_at': bid.created_at,
                            'user': user
                        })
            else:
                # Если топ ставки не переданы, загружаем их
                stmt_top_bids = select(Bid).where(
                    Bid.auction_id == auction.id
                ).order_by(Bid.amount.desc()).limit(3)
                result_top = await session.execute(stmt_top_bids)
                top_bids_db = result_top.scalars().all()
                
                for bid in top_bids_db:
                    stmt_user = select(User).where(User.id == bid.user_id)
                    result_user = await session.execute(stmt_user)
                    user = result_user.scalar_one_or_none()
                    
                    if user:
                        prepared_top_bids.append({
                            'amount': bid.amount,
                            'created_at': bid.created_at,
                            'user': user
                        })
        
        # Теперь формируем сообщение с подготовленными данными
        message_text = format_ended_auction_message(current_auction, prepared_top_bids, bids_count)
        
        logger.info(f"Сообщение для аукциона #{auction.id} сформировано, длина: {len(message_text)} символов")
        
        # Проверяем, есть ли фото у аукциона
        has_photo = False
        try:
            if current_auction.photos:
                photos_list = json.loads(current_auction.photos)
                has_photo = bool(photos_list and photos_list[0])
        except:
            pass
        
        # Пытаемся обновить сообщение
        try:
            if has_photo:
                # Обновляем подпись к фото
                await self.bot.edit_message_caption(
                    chat_id=Config.CHANNEL_ID,
                    message_id=current_auction.channel_message_id,
                    caption=message_text,
                    parse_mode='HTML'
                )
                logger.info(f"Обновлена подпись к фото для аукциона #{auction.id}")
            else:
                # Обновляем текстовое сообщение
                await self.bot.edit_message_text(
                    chat_id=Config.CHANNEL_ID,
                    message_id=current_auction.channel_message_id,
                    text=message_text,
                    parse_mode='HTML'
                )
                logger.info(f"Обновлен текст для аукциона #{auction.id}")
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Ошибка при обновлении сообщения для аукциона #{auction.id}: {error_msg}")
            
            # Пробуем альтернативный метод
            try:
                if "message can't be edited" in error_msg or "message not found" in error_msg:
                    logger.warning(f"Сообщение для аукциона #{auction.id} нельзя отредактировать")
                elif has_photo:
                    # Пробуем обновить текст вместо подписи
                    await self.bot.edit_message_text(
                        chat_id=Config.CHANNEL_ID,
                        message_id=current_auction.channel_message_id,
                        text=message_text,
                        parse_mode='HTML'
                    )
                    logger.info(f"Обновлен текст (альтернативный метод) для аукциона #{auction.id}")
            except Exception as e2:
                logger.error(f"Альтернативный метод также не сработал для аукциона #{auction.id}: {e2}")
        
    except Exception as e:
        logger.error(f"Критическая ошибка при обновлении сообщения в канале для завершенного аукциона #{auction.id}: {e}", exc_info=True)
    
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
                    logger.error(f"Победитель с ID {winner_user_id} не найден для аукциона #{auction_id}")
                    return
                
                logger.info(f"Отправляю уведомление победителю {winner.telegram_id} для аукциона #{auction_id}")
                
                await send_winner_notification(self.bot, auction, winner)
                    
        except Exception as e:
            logger.error(f"Ошибка при уведомлении победителя: {e}", exc_info=True)
    
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
                    logger.info(f"Восстановлен таймер для аукциона #{auction.id}")
                
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
                    logger.info(f"Аукцион #{auction.id} истек во время простоя бота, завершаю")
                    await self._end_auction(auction.id)
                
                logger.info(f"Восстановлено {len(active_auctions)} таймеров, завершено {len(expired_auctions)} аукционов")
                
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
