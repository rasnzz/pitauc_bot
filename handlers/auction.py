from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select, desc, func
from sqlalchemy.orm import selectinload
import datetime
import logging

from database.database import get_db
from database.models import Auction, Bid, User, AuctionSubscription, Notification
from keyboards.inline import get_channel_auction_keyboard, get_bot_auction_keyboard, get_auction_history_keyboard
from utils.formatters import format_auction_message, format_ended_auction_message, format_bid_history, format_username, format_time_ago
from utils.notifications import send_outbid_notification, send_subscription_notification
from config import Config
from utils.timer import auction_timer_manager

router = Router()
logger = logging.getLogger(__name__)

@router.callback_query(F.data.startswith("bid:"))
async def process_bid(callback: CallbackQuery):
    """Обработка ставки пользователя"""
    try:
        _, auction_id, amount_str = callback.data.split(":")
        auction_id = int(auction_id)
        amount = float(amount_str)
        
        async with get_db() as session:
            async with session.begin():
                stmt = select(Auction).where(
                    Auction.id == auction_id, 
                    Auction.status == 'active'
                )
                result = await session.execute(stmt)
                auction = result.scalar_one_or_none()
                
                if not auction:
                    await callback.answer("Аукцион не найден или завершен!", show_alert=True)
                    return
                
                stmt_user = select(User).where(User.telegram_id == callback.from_user.id)
                result_user = await session.execute(stmt_user)
                user = result_user.scalar_one_or_none()
                
                if not user or not user.is_confirmed:
                    await callback.answer(
                        "❌ Вы не подтвердили правила!\n\n"
                        "Напишите /start боту для подтверждения.",
                        show_alert=True
                    )
                    return
                
                if amount <= auction.current_price:
                    await callback.answer(
                        f"Ставка должна быть выше текущей ({auction.current_price} ₽)!",
                        show_alert=True
                    )
                    return
                
                min_next_bid = auction.current_price + auction.step_price
                if amount < min_next_bid:
                    await callback.answer(
                        f"Минимальная ставка: {min_next_bid} ₽",
                        show_alert=True
                    )
                    return
                
                stmt_last_bid = select(Bid).where(
                    Bid.auction_id == auction_id
                ).order_by(desc(Bid.id))
                result_last_bid = await session.execute(stmt_last_bid)
                last_bid = result_last_bid.scalar_one_or_none()
                
                if last_bid and last_bid.user_id == user.id:
                    await callback.answer(
                        "Вы уже лидируете в этом аукционе!",
                        show_alert=True
                    )
                    return
                
                bid = Bid(
                    auction_id=auction_id,
                    user_id=user.id,
                    amount=amount
                )
                session.add(bid)
                
                auction.current_price = amount
                auction.last_bid_time = datetime.datetime.utcnow()
                # Теперь добавляем только 240 минут (4 часа) вместо 480
                auction.ends_at = auction.last_bid_time + datetime.timedelta(minutes=Config.BID_TIMEOUT_MINUTES)
        
        async with get_db() as session:
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
            
            stmt_auction = select(Auction).where(Auction.id == auction_id)
            result_auction = await session.execute(stmt_auction)
            auction_for_message = result_auction.scalar_one()
            
            await update_channel_message(callback.bot, auction_for_message, top_bids, bids_count)
            
            if last_bid and last_bid.user_id != user.id:
                stmt_prev_user = select(User).where(User.id == last_bid.user_id)
                result_prev_user = await session.execute(stmt_prev_user)
                prev_user = result_prev_user.scalar_one_or_none()
                
                if prev_user:
                    await send_outbid_notification(callback.bot, prev_user, auction, amount)
            
            await send_subscription_notification(callback.bot, auction, user, amount)
            
            notification = Notification(
                user_id=user.id,
                auction_id=auction_id,
                message=f"Вы сделали ставку {amount} ₽ в аукционе '{auction.title}'"
            )
            session.add(notification)
            await session.commit()
        
        await auction_timer_manager.start_auction_timer(auction_id, auction.ends_at)
        
        await callback.answer(f"✅ Ваша ставка {amount} ₽ принята!")
        from utils.periodic_updater import periodic_updater
        await periodic_updater.force_update_auction(auction_id)
            
    except Exception as e:
        logger.error(f"Ошибка при обработке ставки: {e}", exc_info=True)
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
        
        # Используем правильную функцию форматирования в зависимости от статуса
        if auction.status == 'ended':
            message_text = format_ended_auction_message(auction, top_bids, bids_count)
        else:
            message_text = format_auction_message(auction, top_bids, bids_count)
        
        # Для завершенных аукционов не показываем клавиатуру для ставок
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
    
    # Используем правильную функцию форматирования в зависимости от статуса
    if auction.status == 'ended':
        message_text = format_ended_auction_message(auction, top_bids, bids_count)
    else:
        message_text = format_auction_message(auction, top_bids, bids_count)
    
    # Для завершенных аукционов не показываем клавиатуру
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
        # Для завершенных аукционов обновляем без клавиатуры
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