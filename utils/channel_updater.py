"""
Модуль для обновления всех сообщений в канале.
Проверяет и обновляет как активные, так и завершенные аукционы.
"""
import asyncio
import logging
from datetime import datetime
import json

from sqlalchemy import select, desc, func
from sqlalchemy.orm import selectinload

from database.database import get_db
from database.models import Auction, Bid, User
from config import Config
from utils.formatters import format_auction_message, format_ended_auction_message
from keyboards.inline import get_channel_auction_keyboard

logger = logging.getLogger(__name__)

class ChannelUpdater:
    """Класс для обновления всех сообщений в канале"""
    
    def __init__(self, bot):
        self.bot = bot
        self.is_updating = False
    
    async def update_all_channel_messages(self):
        """Обновить ВСЕ сообщения в канале (активные и завершенные)"""
        if self.is_updating:
            logger.warning("Обновление уже выполняется, пропускаю...")
            return
        
        self.is_updating = True
        try:
            logger.info("🔄 Начинаю обновление ВСЕХ сообщений в канале...")
            
            async with get_db() as session:
                stmt = select(Auction).where(
                    Auction.channel_message_id.isnot(None)
                ).order_by(Auction.created_at.desc())
                
                result = await session.execute(stmt)
                auctions = result.scalars().all()
                
                if not auctions:
                    logger.warning("❌ Нет аукционов с сообщениями в канале")
                    return
                
                logger.info(f"📊 Найдено {len(auctions)} аукционов с сообщениями")
                
                updated_count = 0
                error_count = 0
                
                for i, auction in enumerate(auctions, 1):
                    try:
                        # Получаем топ-3 ставки с пользователями
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
                        
                        # Получаем количество ставок
                        stmt_count = select(func.count(Bid.id)).where(Bid.auction_id == auction.id)
                        result_count = await session.execute(stmt_count)
                        bids_count = result_count.scalar()
                        
                        # Обновляем сообщение
                        await self._update_single_message(auction, prepared_top_bids, bids_count)
                        
                        updated_count += 1
                        logger.info(f"✅ Обновлен аукцион #{auction.id} ({i}/{len(auctions)})")
                        
                        # Задержка между обновлениями
                        if i < len(auctions):
                            await asyncio.sleep(2)
                            
                    except Exception as e:
                        error_count += 1
                        logger.error(f"❌ Ошибка при обновлении аукциона #{auction.id}: {e}")
                
                logger.info(f"🎉 Обновление завершено: {updated_count} успешно, {error_count} ошибок")
                
        except Exception as e:
            logger.error(f"❌ Критическая ошибка при обновлении канала: {e}")
        finally:
            self.is_updating = False
    
    async def update_expired_messages(self):
        """Обновить только просроченные сообщения"""
        try:
            logger.info("🔍 Поиск просроченных аукционов...")
            
            async with get_db() as session:
                now = datetime.utcnow()
                
                stmt = select(Auction).where(
                    Auction.status == 'active',
                    Auction.ends_at <= now,
                    Auction.channel_message_id.isnot(None)
                )
                
                result = await session.execute(stmt)
                expired_auctions = result.scalars().all()
                
                if not expired_auctions:
                    logger.info("✅ Просроченных аукционов не найдено")
                    return 0
                
                logger.info(f"🔄 Найдено {len(expired_auctions)} просроченных аукционов")
                
                updated_count = 0
                
                for auction in expired_auctions:
                    try:
                        # Помечаем как завершенные
                        auction.status = 'ended'
                        auction.ended_at = now
                        
                        # Находим победителя
                        stmt_winner = select(Bid).where(
                            Bid.auction_id == auction.id
                        ).order_by(desc(Bid.amount)).limit(1)
                        result_winner = await session.execute(stmt_winner)
                        winning_bid = result_winner.scalar_one_or_none()
                        
                        if winning_bid:
                            auction.winner_id = winning_bid.user_id
                            auction.current_price = winning_bid.amount
                        
                        # Получаем топ-3 ставки
                        stmt_top_bids = select(Bid).where(
                            Bid.auction_id == auction.id
                        ).order_by(desc(Bid.amount)).limit(3).options(
                            selectinload(Bid.user)
                        )
                        result_top = await session.execute(stmt_top_bids)
                        top_bids = result_top.scalars().all()
                        
                        prepared_top_bids = []
                        for bid in top_bids:
                            prepared_top_bids.append({
                                'amount': bid.amount,
                                'created_at': bid.created_at,
                                'user': bid.user
                            })
                        
                        stmt_count = select(func.count(Bid.id)).where(Bid.auction_id == auction.id)
                        result_count = await session.execute(stmt_count)
                        bids_count = result_count.scalar()
                        
                        # Обновляем сообщение
                        await self._update_single_message(auction, prepared_top_bids, bids_count)
                        
                        await session.commit()
                        updated_count += 1
                        
                        logger.info(f"✅ Завершен и обновлен аукцион #{auction.id}")
                        await asyncio.sleep(1)
                        
                    except Exception as e:
                        logger.error(f"❌ Ошибка при обновлении аукциона #{auction.id}: {e}")
                        await session.rollback()
                
                return updated_count
                
        except Exception as e:
            logger.error(f"❌ Ошибка при обновлении просроченных сообщений: {e}")
            return 0
    
    async def _update_single_message(self, auction: Auction, top_bids=None, bids_count=0):
        """Обновить одно сообщение в канале"""
        try:
            if not auction.channel_message_id:
                logger.warning(f"⚠️ У аукциона #{auction.id} нет ID сообщения")
                return False
            
            # Определяем тип сообщения (активное/завершенное)
            if auction.status == 'ended':
                message_text = format_ended_auction_message(auction, top_bids, bids_count)
                keyboard = None
            else:
                message_text = format_auction_message(auction, top_bids, bids_count)
                next_bid_amount = auction.current_price + auction.step_price
                keyboard = get_channel_auction_keyboard(auction.id, next_bid_amount)
            
            # Определяем, есть ли фото
            has_photo = False
            try:
                if auction.photos:
                    photos_list = json.loads(auction.photos)
                    if photos_list and photos_list[0]:
                        has_photo = True
            except Exception as e:
                logger.error(f"❌ Ошибка при проверке фото: {e}")
            
            # Пытаемся обновить сообщение
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    if has_photo:
                        if auction.status == 'ended' or keyboard is None:
                            # Обновляем подпись к фото без клавиатуры
                            await self.bot.edit_message_caption(
                                chat_id=Config.CHANNEL_ID,
                                message_id=auction.channel_message_id,
                                caption=message_text,
                                parse_mode='HTML'
                            )
                        else:
                            # Обновляем подпись к фото с клавиатурой
                            await self.bot.edit_message_caption(
                                chat_id=Config.CHANNEL_ID,
                                message_id=auction.channel_message_id,
                                caption=message_text,
                                reply_markup=keyboard,
                                parse_mode='HTML'
                            )
                    else:
                        if auction.status == 'ended' or keyboard is None:
                            # Обновляем текст без клавиатуры
                            await self.bot.edit_message_text(
                                chat_id=Config.CHANNEL_ID,
                                message_id=auction.channel_message_id,
                                text=message_text,
                                parse_mode='HTML'
                            )
                        else:
                            # Обновляем текст с клавиатурой
                            await self.bot.edit_message_text(
                                chat_id=Config.CHANNEL_ID,
                                message_id=auction.channel_message_id,
                                text=message_text,
                                reply_markup=keyboard,
                                parse_mode='HTML'
                            )
                    
                    return True
                    
                except Exception as e:
                    error_msg = str(e)
                    logger.warning(f"❌ Попытка {attempt + 1}/{max_retries} не удалась: {error_msg}")
                    
                    if attempt < max_retries - 1:
                        # Экспоненциальная задержка
                        delay = 2 ** (attempt + 1)
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"❌ Не удалось обновить сообщение #{auction.channel_message_id} для аукциона #{auction.id}")
                        return False
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка при обновлении сообщения: {e}")
            return False
    
    async def check_and_fix_all_messages(self):
        """Проверить и исправить все сообщения в канале"""
        logger.info("🔍 Начинаю проверку всех сообщений...")
        
        try:
            expired_count = await self.update_expired_messages()
            await self.update_all_channel_messages()
            
            logger.info(f"✅ Проверка завершена. Исправлено {expired_count} просроченных аукционов.")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка при проверке сообщений: {e}")
            return False

# Глобальный экземпляр
channel_updater = None

def get_channel_updater(bot=None):
    """Получить или создать экземпляр ChannelUpdater"""
    global channel_updater
    if channel_updater is None and bot is not None:
        channel_updater = ChannelUpdater(bot)
    return channel_updater
