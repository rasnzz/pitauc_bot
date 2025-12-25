import asyncio
from datetime import datetime
from sqlalchemy import select
import logging
import html

from database.database import get_db
from database.models import User, Auction, AuctionSubscription, Notification
from config import Config
from utils.formatters import get_channel_link, format_username

logger = logging.getLogger(__name__)

def escape_html(text: str) -> str:
    """Экранировать HTML-сущности"""
    if not text:
        return ""
    return html.escape(str(text))

async def send_outbid_notification(bot, user: User, auction: Auction, new_bid: float):
    """Уведомление пользователя, которого перебили"""
    try:
        link = get_channel_link(auction)
        
        message = (
            f"⚠️ <b>Вашу ставку перебили!</b>\n\n"
            f"🏷 Лот: {escape_html(auction.title)}\n"
            f"💰 Ваша ставка: {auction.current_price - auction.step_price} ₽\n"
            f"🆕 Новая ставка: {new_bid} ₽\n"
            f"⬆️ Минимальная ставка: {new_bid + auction.step_price} ₽\n\n"
            f"🔗 {link}"
        )
        
        await bot.send_message(
            chat_id=user.telegram_id,
            text=message,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        
        # Сохраняем уведомление в БД
        async with get_db() as session:
            notification = Notification(
                user_id=user.id,
                auction_id=auction.id,
                message=f"Вашу ставку в аукционе '{auction.title}' перебили. Новая ставка: {new_bid} ₽"
            )
            session.add(notification)
            await session.commit()
            
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о перебитии: {e}")

async def send_subscription_notification(bot, auction: Auction, bid_user: User, amount: float):
    """Уведомление подписчиков аукциона о новой ставке"""
    try:
        async with get_db() as session:
            # Получаем всех подписчиков, кроме сделавшего ставку
            stmt = select(AuctionSubscription).where(
                AuctionSubscription.auction_id == auction.id,
                AuctionSubscription.user_id != bid_user.id
            )
            
            result = await session.execute(stmt)
            subscriptions = result.scalars().all()
            
            link = get_channel_link(auction)
            
            for subscription in subscriptions:
                try:
                    # Получаем пользователя
                    stmt_user = select(User).where(User.id == subscription.user_id)
                    result_user = await session.execute(stmt_user)
                    user = result_user.scalar_one_or_none()
                    
                    if not user:
                        continue
                    
                    message = (
                        f"🎯 <b>Новая ставка в аукционе!</b>\n\n"
                        f"🏷 Лот: {escape_html(auction.title)}\n"
                        f"💰 Новая ставка: {amount} ₽\n"
                        f"👤 Ставку сделал: {format_username(bid_user)}\n"
                        f"⬆️ Минимальная ставка: {amount + auction.step_price} ₽\n\n"
                        f"🔗 {link}"
                    )
                    
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=message,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                    
                    # Сохраняем уведомление в БД
                    notification = Notification(
                        user_id=user.id,
                        auction_id=auction.id,
                        message=f"Новая ставка в аукционе '{auction.title}'. Сумма: {amount} ₽"
                    )
                    session.add(notification)
                    
                except Exception as e:
                    logger.error(f"Ошибка при уведомлении подписчика {subscription.user_id}: {e}")
            
            await session.commit()
            
    except Exception as e:
        logger.error(f"Ошибка при уведомлении подписчиков: {e}")

async def send_winner_notification(bot, auction: Auction, winner: User):
    """Уведомление победителя аукциона"""
    try:
        link = get_channel_link(auction)
        
        message = (
            f"🏆 <b>Поздравляем! Вы выиграли аукцион!</b>\n\n"
            f"🏷 Лот: <b>{escape_html(auction.title)}</b>\n"
            f"💰 Ваша ставка: <b>{auction.current_price} ₽</b>\n"
            f"📅 Завершён: {auction.ended_at.strftime('%d.%m.%Y %H:%M') if auction.ended_at else 'Недавно'}\n\n"
            f"📞 <b>Свяжитесь с администратором для оплаты:</b>\n"
            f"👤 @pd56oren\n"
            f"☎️ 55-44-22\n\n"
            f"⏰ <b>Оплатите в течение 72 часов! </b>\n\n"
            f"📍 <b>Адрес самовывоза:</b>\n"
            f"г. Оренбург, ул. Монтажников 37/3, магазин PIT Store\n\n"
            f"🕐 <b>Режим работы:</b>\n"
            f"9:00-17:00 ежедневно\n\n"
            f"🔗 {link}\n\n"
            f"📋 <b>Важные правила:</b>\n"
            f"• Товар соответствует фото и описанию\n"
            f"• Претензии по состоянию принимаются только при осмотре\n"
            f"• Возврат/обмен согласно законодательству РФ\n"
        )
        
        await bot.send_message(
            chat_id=winner.telegram_id,
            text=message,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        
        # Сохраняем уведомление в БД
        async with get_db() as session:
            notification = Notification(
                user_id=winner.id,
                auction_id=auction.id,
                message=f"Вы выиграли аукцион '{auction.title}'! Сумма: {auction.current_price} ₽"
            )
            session.add(notification)
            await session.commit()
            
    except Exception as e:
        logger.error(f"Ошибка при уведомлении победителя: {e}")

async def send_auction_ending_soon_notification(bot, auction: Auction, minutes_left: int):
    """Уведомление о скором завершении аукциона"""
    try:
        async with get_db() as session:
            # Получаем всех подписчиков
            stmt = select(AuctionSubscription).where(
                AuctionSubscription.auction_id == auction.id
            )
            
            result = await session.execute(stmt)
            subscriptions = result.scalars().all()
            
            link = get_channel_link(auction)
            
            for subscription in subscriptions:
                try:
                    # Получаем пользователя
                    stmt_user = select(User).where(User.id == subscription.user_id)
                    result_user = await session.execute(stmt_user)
                    user = result_user.scalar_one_or_none()
                    
                    if not user:
                        continue
                    
                    message = (
                        f"⏰ <b>Аукцион скоро завершится!</b>\n\n"
                        f"🏷 Лот: {escape_html(auction.title)}\n"
                        f"💰 Текущая цена: {auction.current_price} ₽\n"
                        f"⏳ Осталось: {minutes_left} минут\n\n"
                        f"🔗 {link}"
                    )
                    
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=message,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                    
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления о завершении: {e}")
                    
    except Exception as e:
        logger.error(f"Ошибка при уведомлении о завершении аукциона: {e}")