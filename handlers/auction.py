from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select, desc, func
from sqlalchemy.orm import selectinload, joinedload
import datetime
import logging
import traceback
import asyncio

from database.database import get_db
from database.models import Auction, Bid, User, AuctionSubscription, Notification
from keyboards.inline import get_channel_auction_keyboard, get_bot_auction_keyboard, get_auction_history_keyboard
from utils.formatters import format_auction_message, format_ended_auction_message, format_bid_history, format_username, format_time_ago
from utils.notifications import send_outbid_notification, send_subscription_notification
from config import Config
from utils.timer import auction_timer_manager
from utils.periodic_updater import periodic_updater

router = Router()
logger = logging.getLogger(__name__)

async def process_bid_safe(auction_id: int, user_id: int, amount: float, bot):
    """Безопасная обработка ставки с защитой от гонок"""
    max_retries = 3
    retry_delay = 0.1
    
    for attempt in range(max_retries):
        try:
            async with get_db() as session:
                async with session.begin():
                    # Блокируем аукцион для обновления
                    stmt = select(Auction).where(
                        Auction.id == auction_id, 
                        Auction.status == 'active'
                    ).with_for_update()
                    
                    result = await session.execute(stmt)
                    auction = result.scalar_one_or_none()
                    
                    if not auction:
                        return {"success": False, "message": "Аукцион не найден или завершен!"}
                    
                    # Получаем пользователя
                    stmt_user = select(User).where(User.telegram_id == user_id)
                    result_user = await session.execute(stmt_user)
                    user = result_user.scalar_one_or_none()
                    
                    if not user or not user.is_confirmed:
                        return {"success": False, "message": "Вы не подтвердили правила! Напишите /start боту для подтверждения."}
                    
                    # Проверяем минимальную ставку
                    min_next_bid = auction.current_price + auction.step_price
                    if amount < min_next_bid:
                        return {"success": False, "message": f"Минимальная ставка: {min_next_bid} ₽"}
                    
                    # ВОССТАНОВЛЕНА ПРОВЕРКА: пользователь не может ставить, если уже лидирует
                    # Получаем текущую лучшую ставку
                    stmt_top_bid = select(Bid).where(
                        Bid.auction_id == auction_id
                    ).order_by(desc(Bid.amount)).limit(1)
                    
                    result_top_bid = await session.execute(stmt_top_bid)
                    top_bid = result_top_bid.scalar_one_or_none()
                    
                    # Если есть лучшая ставка и она от этого пользователя
                    if top_bid and top_bid.user_id == user.id:
                        return {"success": False, "message": "Вы уже лидируете в этом аукционе! Дождитесь, пока кто-то перебьет вашу ставку."}
                    
                    # Создаем ставку
                    bid = Bid(
                        auction_id=auction_id,
                        user_id=user.id,
                        amount=amount
                    )
                    session.add(bid)
                    
                    # Обновляем аукцион
                    auction.current_price = amount
                    auction.last_bid_time = datetime.datetime.utcnow()
                    auction.ends_at = auction.last_bid_time + datetime.timedelta(minutes=Config.BID_TIMEOUT_MINUTES)
                    
                    # Получаем предыдущую лучшую ставку (для уведомления)
                    stmt_prev_top = select(Bid).where(
                        Bid.auction_id == auction_id,
                        Bid.user_id != user.id
                    ).order_by(desc(Bid.amount)).limit(1)
                    
                    result_prev_top = await session.execute(stmt_prev_top)
                    previous_top_bid = result_prev_top.scalar_one_or_none()
                    
                    # Возвращаем данные для дальнейшей обработки
                    return {
                        "success": True,
                        "auction": auction,
                        "bid": bid,
                        "user": user,
                        "previous_top_bid": previous_top_bid
                    }
                    
        except Exception as e:
            logger.error(f"Попытка {attempt + 1} неудачна: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay * (2 ** attempt))  # Экспоненциальная задержка
                continue
            else:
                logger.error(f"Не удалось обработать ставку после {max_retries} попыток")
                return {"success": False, "message": "Ошибка при обработке ставки. Попробуйте еще раз."}
    
    return {"success": False, "message": "Ошибка при обработке ставки"}

@router.callback_query(F.data.startswith("bid:"))
async def process_bid(callback: CallbackQuery):
    """Обработка ставки пользователя"""
    try:
        # Парсим данные
        _, auction_id_str, amount_str = callback.data.split(":")
        auction_id = int(auction_id_str)
        amount = float(amount_str)
        
        logger.info(f"Новая ставка: аукцион={auction_id}, сумма={amount}, пользователь={callback.from_user.id}")
        
        # Обрабатываем ставку
        result = await process_bid_safe(
            auction_id=auction_id,
            user_id=callback.from_user.id,
            amount=amount,
            bot=callback.bot
        )
        
        if not result["success"]:
            await callback.answer(result["message"], show_alert=True)
            return
        
        # Успешная ставка - выполняем дополнительные действия
        auction = result["auction"]
        user = result["user"]
        previous_top_bid = result["previous_top_bid"]
        
        try:
            # Получаем данные для обновления сообщения
            async with get_db() as session:
                # Получаем топ-3 ставки
                stmt_top_bids = select(Bid).where(
                    Bid.auction_id == auction_id
                ).order_by(desc(Bid.amount)).limit(3).options(
                    selectinload(Bid.user)
                )
                result_top = await session.execute(stmt_top_bids)
                top_bids = result_top.scalars().all()
                
                # Получаем количество ставок
                stmt_count = select(func.count(Bid.id)).where(Bid.auction_id == auction_id)
                result_count = await session.execute(stmt_count)
                bids_count = result_count.scalar()
                
                # Обновляем сообщение в канале
                await update_channel_message(callback.bot, auction, top_bids, bids_count)
                
                # Отправляем уведомление предыдущему лидеру (если он не текущий пользователь)
                if previous_top_bid and previous_top_bid.user_id != user.id:
                    try:
                        stmt_prev_user = select(User).where(User.id == previous_top_bid.user_id)
                        result_prev_user = await session.execute(stmt_prev_user)
                        prev_user = result_prev_user.scalar_one_or_none()
                        
                        if prev_user:
                            await send_outbid_notification(callback.bot, prev_user, auction, amount)
                    except Exception as e:
                        logger.error(f"Ошибка при отправке уведомления о перебитии: {e}")
                
                # Уведомляем подписчиков (кроме сделавшего ставку)
                try:
                    await send_subscription_notification(callback.bot, auction, user, amount)
                except Exception as e:
                    logger.error(f"Ошибка при уведомлении подписчиков: {e}")
                
                # Создаем уведомление для пользователя
                notification = Notification(
                    user_id=user.id,
                    auction_id=auction_id,
                    message=f"Вы сделали ставку {amount} ₽ в аукционе '{auction.title}'"
                )
                session.add(notification)
                await session.commit()
                
                # Запускаем/обновляем таймер
                await auction_timer_manager.start_auction_timer(auction_id, auction.ends_at)
                
                # Немедленно обновляем в канале
                await periodic_updater.force_update_auction(auction_id)
                
        except Exception as e:
            logger.error(f"Ошибка после успешной ставки: {e}")
        
        await callback.answer(f"✅ Ваша ставка {amount} ₽ принята!")
        
    except ValueError as e:
        logger.error(f"Неверный формат данных: {e}")
        await callback.answer("Ошибка формата данных", show_alert=True)
    except Exception as e:
        logger.error(f"Неожиданная ошибка при обработке ставки: {e}")
        logger.error(traceback.format_exc())
        await callback.answer("Ошибка при обработке ставки", show_alert=True)

@router.callback_query(F.data.startswith("top3:"))
async def show_top3_bids(callback: CallbackQuery):
    """Показать топ-3 ставки"""
    auction_id = int(callback.data.split(":")[1])
    
    async with get_db() as session:
        stmt = select(Bid).where(
            Bid.auction_id == auction_id
        ).order_by(desc(Bid.amount)).limit(3).options(
            selectinload(Bid.user)
        )
        
        result = await session.execute(stmt)
        top_bids = result.scalars().all()
        
        if not top_bids:
            await callback.answer("Нет ставок!", show_alert=True)
            return
        
        text = "🥇 <b>Топ-3 ставки:</b>\n\n"
        places = ["🥇", "🥈", "🥉"]
        for i, bid in enumerate(top_bids):
            if i < len(places):
                emoji = places[i]
                username = format_username(bid.user)
                time_ago = format_time_ago(bid.created_at)
                text += f"{emoji} {username}: {bid.amount} ₽ ({time_ago})\n"
        
        await callback.message.answer(text, parse_mode="HTML")
        await callback.answer()

@router.callback_query(F.data.startswith("history:"))
async def show_bid_history(callback: CallbackQuery):
    """Показать историю ставок"""
    auction_id = int(callback.data.split(":")[1])
    
    async with get_db() as session:
        stmt = select(Bid).where(
            Bid.auction_id == auction_id
        ).order_by(desc(Bid.created_at)).limit(20).options(
            selectinload(Bid.user)
        )
        
        result = await session.execute(stmt)
        bids = result.scalars().all()
        
        if not bids:
            await callback.answer("История ставок пуста!", show_alert=True)
            return
        
        history_text = format_bid_history(bids)
        
        await callback.message.answer(
            history_text,
            parse_mode="HTML",
            reply_markup=get_auction_history_keyboard(auction_id)
        )
        await callback.answer()

@router.callback_query(F.data.startswith("subscribe:"))
async def subscribe_to_auction(callback: CallbackQuery):
    """Подписаться на уведомления об аукционе"""
    auction_id = int(callback.data.split(":")[1])
    
    async with get_db() as session:
        stmt_user = select(User).where(User.telegram_id == callback.from_user.id)
        result_user = await session.execute(stmt_user)
        user = result_user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Сначала напишите /start!", show_alert=True)
            return
        
        stmt_sub = select(AuctionSubscription).where(
            AuctionSubscription.auction_id == auction_id,
            AuctionSubscription.user_id == user.id
        )
        result_sub = await session.execute(stmt_sub)
        existing_sub = result_sub.scalar_one_or_none()
        
        if existing_sub:
            await callback.answer("Вы уже подписаны на этот аукцион!", show_alert=True)
            return
        
        subscription = AuctionSubscription(
            auction_id=auction_id,
            user_id=user.id
        )
        session.add(subscription)
        await session.commit()
        
        await callback.answer("✅ Вы подписались на уведомления об этом аукционе!")

@router.callback_query(F.data.startswith("back_to_auction:"))
async def back_to_auction(callback: CallbackQuery):
    """Вернуться к аукциону"""
    auction_id = int(callback.data.split(":")[1])
    
    async with get_db() as session:
        stmt = select(Auction).where(Auction.id == auction_id)
        result = await session.execute(stmt)
        auction = result.scalar_one_or_none()
        
        if not auction:
            await callback.answer("Аукцион не найден!", show_alert=True)
            return
        
        stmt_top_bids = select(Bid).where(
            Bid.auction_id == auction_id
        ).order_by(desc(Bid.amount)).limit(3).options(
            selectinload(Bid.user)
        )
        result_top = await session.execute(stmt_top_bids)
        top_bids = result_top.scalars().all()
        
        stmt_count = select(func.count(Bid.id)).where(Bid.auction_id == auction_id)
        result_count = await session.execute(stmt_count)
        bids_count = result_count.scalar()
        
        if auction.status == 'ended':
            message_text = format_ended_auction_message(auction, top_bids, bids_count)
        else:
            message_text = format_auction_message(auction, top_bids, bids_count)
        
        if auction.status == 'active':
            next_bid_amount = auction.current_price + auction.step_price
            await callback.message.edit_text(
                message_text,
                parse_mode="HTML",
                reply_markup=get_bot_auction_keyboard(auction.id, next_bid_amount)
            )
        else:
            await callback.message.edit_text(
                message_text,
                parse_mode="HTML"
            )
        await callback.answer()

async def update_channel_message(bot, auction: Auction, top_bids=None, bids_count=0):
    """Обновление сообщения в канале"""
    
    if auction.status == 'ended':
        message_text = format_ended_auction_message(auction, top_bids, bids_count)
    else:
        message_text = format_auction_message(auction, top_bids, bids_count)
    
    if auction.status == 'active':
        next_bid_amount = auction.current_price + auction.step_price
        
        try:
            try:
                await bot.edit_message_caption(
                    chat_id=Config.CHANNEL_ID,
                    message_id=auction.channel_message_id,
                    caption=message_text,
                    reply_markup=get_channel_auction_keyboard(auction.id, next_bid_amount),
                    parse_mode='HTML'
                )
            except:
                await bot.edit_message_text(
                    chat_id=Config.CHANNEL_ID,
                    message_id=auction.channel_message_id,
                    text=message_text,
                    reply_markup=get_channel_auction_keyboard(auction.id, next_bid_amount),
                    parse_mode='HTML'
                )
        except Exception as e:
            logger.error(f"Ошибка при обновлении сообщения в канале: {e}")
    else:
        try:
            try:
                await bot.edit_message_caption(
                    chat_id=Config.CHANNEL_ID,
                    message_id=auction.channel_message_id,
                    caption=message_text,
                    parse_mode='HTML'
                )
            except:
                await bot.edit_message_text(
                    chat_id=Config.CHANNEL_ID,
                    message_id=auction.channel_message_id,
                    text=message_text,
                    parse_mode='HTML'
                )
        except Exception as e:
            logger.error(f"Ошибка при обновлении завершенного аукциона в канале: {e}")

