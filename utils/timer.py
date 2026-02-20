import asyncio
from datetime import datetime, timedelta
from typing import Dict
import logging
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
import json

from database.database import get_db
from database.models import Auction, User, Bid
from utils.formatters import format_ended_auction_message
from utils.periodic_updater import periodic_updater
from utils.notifications import send_winner_notification
from config import Config

logger = logging.getLogger(__name__)

class AuctionTimerManager:
    """Менеджер таймеров для аукционов"""
    
    def __init__(self):
        self.active_timers: Dict[int, asyncio.Task] = {}
        self.lock = asyncio.Lock()
        self.bot = None
        self._stopping = False
    
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
                    await asyncio.sleep(0.1)
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
            time_diff = (ends_at - now).total_seconds()
            
            if time_diff <= 0:
                # Время уже истекло - завершаем немедленно
                logger.info(f"Аукцион #{auction_id} уже просрочен, завершаю...")
                await self._end_auction(auction_id)
                return
            
            # Создаем новую задачу
            task = asyncio.create_task(
                self._auction_timer_task(auction_id, ends_at)
            )
            self.active_timers[auction_id] = task
            logger.info(f"Таймер запущен для аукциона #{auction_id}, завершится через {time_diff:.0f} секунд")
    
    async def restore_timers_improved(self):
        """Улучшенное восстановление таймеров после перезапуска бота"""
        try:
            logger.info("Начинаю восстановление таймеров...")
            
            async with get_db() as session:
                # Находим ВСЕ активные аукционы
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
                        
                        # Определяем время завершения
                        end_time = auction.ends_at
                        if not end_time:
                            # Если нет времени завершения, устанавливаем по умолчанию
                            end_time = auction.created_at + timedelta(minutes=Config.BID_TIMEOUT_MINUTES)
                            auction.ends_at = end_time
                            await session.commit()
                            logger.info(f"  Установлено время завершения по умолчанию: {end_time}")
                        
                        now = datetime.utcnow()
                        time_diff = (end_time - now).total_seconds()
                        
                        if time_diff > 0:
                            # Время еще не истекло - запускаем таймер
                            logger.info(f"  Аукцион #{auction.id} активен, завершится через {time_diff:.0f} секунд")
                            await self.start_auction_timer(auction.id, end_time)
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
                
                # Подготавливаем данные топ ставок
                prepared_top_bids = []
                for bid in top_bids:
                    prepared_top_bids.append({
                        'amount': bid.amount,
                        'created_at': bid.created_at,
                        'user': bid.user
                    })
                
                stmt_count = select(Bid).where(Bid.auction_id == auction.id)
                result_count = await session.execute(stmt_count)
                bids_count = result_count.scalar()
                
                message_text = format_ended_auction_message(auction, prepared_top_bids, bids_count)
            
            # Обновляем сообщение ТОЛЬКО редактированием
            if self.bot:
                # Определяем тип сообщения (с фото или без)
                has_photo = False
                try:
                    if auction.photos:
                        photos_list = json.loads(auction.photos)
                        if photos_list and photos_list[0]:
                            has_photo = True
                except Exception as e:
                    logger.error(f"Ошибка при проверке фото: {e}")
                
                try:
                    if has_photo:
                        await self.bot.edit_message_caption(
                            chat_id=Config.CHANNEL_ID,
                            message_id=auction.channel_message_id,
                            caption=message_text,
                            parse_mode='HTML'
                        )
                    else:
                        await self.bot.edit_message_text(
                            chat_id=Config.CHANNEL_ID,
                            message_id=auction.channel_message_id,
                            text=message_text,
                            parse_mode='HTML'
                        )
                    logger.info(f"Сообщение в канале для аукциона #{auction.id} обновлено")
                except Exception as e:
                    logger.error(f"Ошибка при обновлении сообщения в канале: {e}")
                    
        except Exception as e:
            logger.error(f"Ошибка при обновлении просроченного аукциона #{auction.id}: {e}")
    
    async def _auction_timer_task(self, auction_id: int, ends_at: datetime):
        """Фоновая задача таймера"""
        try:
            # Рассчитываем время ожидания
            now = datetime.utcnow()
            wait_time = (ends_at - now).total_seconds()
            
            if wait_time > 0:
                logger.info(f"Таймер аукциона #{auction_id}: ждем {wait_time:.0f} секунд")
                await asyncio.sleep(wait_time)
            
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
        """Завершение аукциона (ИСПРАВЛЕНО: добавлена загрузка winner)"""
        try:
            logger.info(f"Начинаю завершение аукциона #{auction_id}")
            
            if not self.bot:
                logger.error(f"Бот не установлен для завершения аукциона #{auction_id}")
                return
            
            # Используем одну сессию для всей операции
            async with get_db() as session:
                # Получаем аукцион с блокировкой для обновления и загружаем winner
                stmt = select(Auction).where(
                    Auction.id == auction_id,
                    Auction.status == 'active'
                ).options(selectinload(Auction.winner)).with_for_update()
                
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
                
                # Получаем топ-3 ставки и количество ставок в той же сессии
                stmt_top_bids = select(Bid).where(
                    Bid.auction_id == auction_id
                ).order_by(desc(Bid.amount)).limit(3).options(
                    selectinload(Bid.user)
                )
                result_top = await session.execute(stmt_top_bids)
                top_bids = result_top.scalars().all()
                
                # Подготавливаем данные топ ставок
                prepared_top_bids = []
                for bid in top_bids:
                    prepared_top_bids.append({
                        'amount': bid.amount,
                        'created_at': bid.created_at,
                        'user': bid.user
                    })
                
                # Получаем количество ставок
                stmt_count = select(Bid).where(Bid.auction_id == auction_id)
                result_count = await session.execute(stmt_count)
                bids_count = result_count.scalar()
                
                # Коммитим изменения (после коммита auction станет detached, но winner и другие поля уже загружены)
                await session.commit()
                
                # Обновляем сообщение в канале
                await self._update_channel_message(auction, prepared_top_bids, bids_count)
            
            # Уведомляем победителя, если есть
            if winning_bid and self.bot:
                await self._notify_winner(auction_id, winning_bid.user_id)
            
            logger.info(f"Аукцион #{auction_id} успешно завершен")
            
        except Exception as e:
            logger.error(f"Ошибка при завершении аукциона #{auction_id}: {e}", exc_info=True)
    
    async def _update_channel_message(self, auction: Auction, top_bids=None, bids_count=0):
        """Обновление сообщения в канале после завершения аукциона"""
        try:
            logger.info(f"🔄 Начинаю обновление сообщения для аукциона #{auction.id}")
            
            if not auction.channel_message_id:
                logger.error(f"❌ Нет channel_message_id для аукциона #{auction.id}")
                return
            
            if not self.bot:
                logger.error(f"❌ Бот не установлен для обновления сообщения #{auction.id}")
                return
            
            logger.info(f"📝 Обновляю сообщение в канале: ID={Config.CHANNEL_ID}, message_id={auction.channel_message_id}")
            
            # Получаем данные для сообщения
            message_text = format_ended_auction_message(auction, top_bids, bids_count)
            
            # ОБРЕЗАЕМ сообщение если слишком длинное
            if len(message_text) > 1024:
                logger.warning(f"⚠️ Сообщение слишком длинное ({len(message_text)} символов), обрезаю...")
                import re
                truncated = message_text[:1024]
                open_tags = re.findall(r'<([^/][^>]*)>', truncated)
                
                tags_to_close = []
                for tag in open_tags:
                    tag_name = tag.split()[0] if ' ' in tag else tag
                    if f'</{tag_name}>' not in truncated:
                        tags_to_close.append(tag_name)
                
                for tag in reversed(tags_to_close):
                    truncated += f'</{tag}>'
                
                truncated += "..."
                message_text = truncated
            
            logger.info(f"✅ Сообщение подготовлено, длина: {len(message_text)} символов")
            
            # Пытаемся определить тип сообщения (фото или текст)
            try:
                original_message = await self.bot.get_message(
                    chat_id=Config.CHANNEL_ID,
                    message_id=auction.channel_message_id
                )
                
                has_photo = original_message.photo is not None
                logger.info(f"📸 Тип сообщения: {'ФОТО' if has_photo else 'ТЕКСТ'}")
                
            except Exception as e:
                logger.warning(f"⚠️ Не удалось получить сообщение: {e}, пробую угадать тип...")
                has_photo = False
                try:
                    if auction.photos:
                        photos_list = json.loads(auction.photos)
                        has_photo = bool(photos_list and photos_list[0])
                except:
                    pass
            
            # Пытаемся обновить сообщение
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.info(f"🔄 Попытка {attempt + 1} из {max_retries}")
                    
                    if has_photo:
                        await self.bot.edit_message_caption(
                            chat_id=Config.CHANNEL_ID,
                            message_id=auction.channel_message_id,
                            caption=message_text,
                            parse_mode='HTML'
                        )
                        logger.info(f"✅ Обновлена подпись к фото для аукциона #{auction.id}")
                    else:
                        await self.bot.edit_message_text(
                            chat_id=Config.CHANNEL_ID,
                            message_id=auction.channel_message_id,
                            text=message_text,
                            parse_mode='HTML'
                        )
                        logger.info(f"✅ Обновлен текст для аукциона #{auction.id}")
                    
                    break
                    
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"❌ Попытка {attempt + 1} не удалась: {error_msg}")
                    
                    if attempt == 0:
                        logger.info(f"🔄 Меняю метод (было {'фото' if has_photo else 'текст'})")
                        has_photo = not has_photo
                    elif attempt == 1:
                        logger.info("🔄 Пробую обновить без клавиатуры...")
                        try:
                            await self.bot.edit_message_text(
                                chat_id=Config.CHANNEL_ID,
                                message_id=auction.channel_message_id,
                                text=message_text,
                                parse_mode='HTML',
                                reply_markup=None
                            )
                            logger.info(f"✅ Обновлено без клавиатуры")
                            break
                        except Exception as e2:
                            logger.error(f"❌ Не удалось обновить без клавиатуры: {e2}")
                    
                    if attempt < max_retries - 1:
                        wait_time = 2 ** (attempt + 1)
                        logger.info(f"⏳ Жду {wait_time} секунд...")
                        await asyncio.sleep(wait_time)
            
            logger.info(f"✅ Обновление завершено для аукциона #{auction.id}")
                
        except Exception as e:
            logger.error(f"❌ Критическая ошибка при обновлении сообщения: {e}")
    
    async def _notify_winner(self, auction_id: int, winner_user_id: int):
        """Уведомление победителя"""
        try:
            async with get_db() as session:
                stmt = select(Auction).where(Auction.id == auction_id).options(selectinload(Auction.winner))
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
    
    async def check_and_complete_expired_auctions(self):
        """Проверка и завершение просроченных аукционов"""
        try:
            logger.info("🔍 Проверка просроченных аукционов...")
            
            async with get_db() as session:
                now = datetime.utcnow()
                
                stmt = select(Auction).where(
                    Auction.status == 'active',
                    Auction.ends_at <= now
                )
                
                result = await session.execute(stmt)
                expired_auctions = result.scalars().all()
                
                logger.info(f"Найдено {len(expired_auctions)} просроченных аукционов")
                
                for auction in expired_auctions:
                    try:
                        logger.info(f"🔄 Завершаю просроченный аукцион #{auction.id}...")
                        await self._end_auction(auction.id)
                        logger.info(f"✅ Аукцион #{auction.id} завершен")
                    except Exception as e:
                        logger.error(f"❌ Ошибка при завершении аукциона #{auction.id}: {e}")
                
                return len(expired_auctions)
                
        except Exception as e:
            logger.error(f"Ошибка при проверке просроченных аукционов: {e}")
            return 0
    
    async def stop_all_timers(self):
        """Остановка всех таймеров"""
        self._stopping = True
        async with self.lock:
            for auction_id, task in list(self.active_timers.items()):
                try:
                    task.cancel()
                except:
                    pass
            
            self.active_timers.clear()
            periodic_updater.clear_update_history()
            logger.info("Все таймеры остановлены")
    
    async def periodic_check(self):
        """Периодическая проверка просроченных аукционов (запускается как фоновая задача)"""
        while not self._stopping:
            try:
                await self.check_and_complete_expired_auctions()
            except Exception as e:
                logger.error(f"Periodic completion check failed: {e}")
            await asyncio.sleep(30)

auction_timer_manager = AuctionTimerManager()
