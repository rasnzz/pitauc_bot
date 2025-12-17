from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from sqlalchemy import select, desc, func
from sqlalchemy.orm import selectinload
import logging
import datetime

from database.database import get_db
from database.models import User, Bid, Auction, Notification
from keyboards.inline import get_confirmation_keyboard, get_user_menu_keyboard, get_bot_auction_keyboard, get_cancel_bid_keyboard
from utils.formatters import format_user_bids, format_notifications
from config import Config

router = Router()
logger = logging.getLogger(__name__)

@router.message(Command("start"))
async def cmd_start(message: Message):
    """Обработка команды /start"""
    async with get_db() as session:
        # Проверяем, есть ли пользователь в базе
        stmt = select(User).where(User.telegram_id == message.from_user.id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if not user:
            # Создаем нового пользователя
            user = User(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name,
                is_confirmed=False
            )
            session.add(user)
            await session.commit()
        
        if user.is_confirmed:
            await message.answer(
                "👋 Добро пожаловать в бот аукционов P.I.T. Store Оренбург!\n\n"
                "📢 Для участия в аукционах перейдите в канал и нажимайте на кнопки под постами.\n\n"
                "📋 Ваши команды:\n"
                "/auctions - Активные аукционы\n"
                "/my_bids - Мои ставки\n"
                "/my_wins - Мои выигрыши\n"
                "/notifications - Уведомления\n"
                "/help - Помощь",
                reply_markup=get_user_menu_keyboard()
            )
        else:
            await message.answer(
                "👋 Добро пожаловать в бот аукционов P.I.T. Store Оренбург!\n\n"
                "📋 Для участия в аукционах необходимо подтвердить согласие с правилами:\n\n"
                "1. Ставка — это обязательство купить лот по указанной цене\n"
                "2. Оплата в течение 24 часов после завершения аукциона\n"
                "3. Самовывоз и магазина PIT Store, ул. Монтажников 37/3\n"
                "4. Претензии по состоянию инструмента принимаются только при осмотре\n\n"
                "⚠️ Несоблюдение правил ведет к блокировке!",
                reply_markup=get_confirmation_keyboard()
            )

@router.message(Command("auctions"))
async def cmd_auctions(message: Message):
    """Показать активные аукционы"""
    async with get_db() as session:
        stmt = select(Auction).where(
            Auction.status == 'active'
        ).order_by(desc(Auction.created_at))
        
        result = await session.execute(stmt)
        auctions = result.scalars().all()
        
        if not auctions:
            await message.answer("📭 Нет активных аукционов.")
            return
        
        for auction in auctions:
            # Получаем количество ставок
            stmt_bids = select(func.count(Bid.id)).where(Bid.auction_id == auction.id)
            result_bids = await session.execute(stmt_bids)
            bids_count = result_bids.scalar()
            
            text = f"🏷 <b>{auction.title}</b>\n\n"
            text += f"📝 Описание: {auction.description[:100]}...\n" if auction.description else ""
            text += f"💰 Стартовая цена: {auction.start_price} ₽\n"
            text += f"📈 Шаг ставки: {auction.step_price} ₽\n"
            text += f"🏆 Текущая цена: {auction.current_price} ₽\n"
            text += f"📊 Количество ставок: {bids_count}\n"
            text += f"⏳ Создан: {auction.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            
            next_bid_amount = auction.current_price + auction.step_price
            
            # Пытаемся отправить фото, если оно есть
            try:
                if auction.photo_list and auction.photo_list[0]:
                    await message.bot.send_photo(
                        chat_id=message.chat.id,
                        photo=auction.photo_list[0],
                        caption=text,
                        reply_markup=get_bot_auction_keyboard(auction.id, next_bid_amount),
                        parse_mode='HTML'
                    )
                else:
                    await message.answer(
                        text,
                        parse_mode="HTML",
                        reply_markup=get_bot_auction_keyboard(auction.id, next_bid_amount)
                    )
            except Exception as e:
                logger.error(f"Ошибка при отправке аукциона: {e}")
                await message.answer(
                    text,
                    parse_mode="HTML",
                    reply_markup=get_bot_auction_keyboard(auction.id, next_bid_amount)
                )

@router.message(Command("my_bids"))
async def cmd_my_bids(message: Message):
    """Показать все ставки пользователя"""
    await show_user_bids(message)

@router.callback_query(F.data == "my_bids")
async def callback_my_bids(callback: CallbackQuery):
    """Показать все ставки пользователя (обработчик кнопки)"""
    await show_user_bids(callback.message)
    await callback.answer()

async def show_user_bids(message: Message):
    """Общая функция показа ставок пользователя"""
    async with get_db() as session:
        # Сначала находим пользователя по telegram_id
        stmt_user = select(User).where(User.telegram_id == message.from_user.id)
        result_user = await session.execute(stmt_user)
        user = result_user.scalar_one_or_none()
        
        if not user:
            # Создаем пользователя, если его нет
            user = User(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name,
                is_confirmed=False
            )
            session.add(user)
            await session.commit()
            await message.answer("⚠️ Вы были автоматически зарегистрированы. Подтвердите правила через /start")
            return
        
        # Теперь находим ставки по user.id (ID в базе данных)
        stmt = select(Bid).join(Auction).where(
            Bid.user_id == user.id
        ).order_by(desc(Bid.created_at)).options(
            selectinload(Bid.auction)
        )
        
        result = await session.execute(stmt)
        bids = result.scalars().all()
        
        if not bids:
            await message.answer("📭 У вас пока нет ставок.")
            return
        
        await message.answer(
            format_user_bids(bids),
            parse_mode="HTML"
        )

@router.message(Command("my_wins"))
async def cmd_my_wins(message: Message):
    """Показать выигранные аукционы"""
    await show_user_wins(message)

@router.callback_query(F.data == "my_wins")
async def callback_my_wins(callback: CallbackQuery):
    """Показать выигранные аукционы (обработчик кнопки)"""
    await show_user_wins(callback.message)
    await callback.answer()

async def show_user_wins(message: Message):
    """Общая функция показа выигранных аукционов"""
    async with get_db() as session:
        # Сначала находим пользователя по telegram_id
        stmt_user = select(User).where(User.telegram_id == message.from_user.id)
        result_user = await session.execute(stmt_user)
        user = result_user.scalar_one_or_none()
        
        if not user:
            # Создаем пользователя, если его нет
            user = User(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name,
                is_confirmed=False
            )
            session.add(user)
            await session.commit()
            await message.answer("⚠️ Вы были автоматически зарегистрированы. Подтвердите правила через /start")
            return
        
        # Теперь находим выигранные аукционы по user.id
        stmt = select(Auction).where(
            Auction.status == 'ended',
            Auction.winner_id == user.id
        ).order_by(desc(Auction.ended_at))
        
        result = await session.execute(stmt)
        auctions = result.scalars().all()
        
        if not auctions:
            await message.answer("📭 У вас пока нет выигранных аукционов.")
            return
        
        wins_text = "🏆 <b>Ваши выигранные аукционы:</b>\n\n"
        for auction in auctions:
            wins_text += f"• <b>{auction.title}</b>\n"
            wins_text += f"  💰 Цена: {auction.current_price} ₽\n"
            wins_text += f"  ⏰ Завершен: {auction.ended_at.strftime('%d.%m.%Y %H:%M')}\n\n"
        
        await message.answer(wins_text, parse_mode="HTML")

@router.message(Command("notifications"))
async def cmd_notifications(message: Message):
    """Показать уведомления пользователя"""
    await show_user_notifications(message)

@router.callback_query(F.data == "notifications")
async def callback_notifications(callback: CallbackQuery):
    """Показать уведомления пользователя (обработчик кнопки)"""
    await show_user_notifications(callback.message)
    await callback.answer()

async def show_user_notifications(message: Message):
    """Общая функция показа уведомлений"""
    async with get_db() as session:
        # Сначала находим пользователя по telegram_id
        stmt_user = select(User).where(User.telegram_id == message.from_user.id)
        result_user = await session.execute(stmt_user)
        user = result_user.scalar_one_or_none()
        
        if not user:
            # Создаем пользователя, если его нет
            user = User(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name,
                is_confirmed=False
            )
            session.add(user)
            await session.commit()
            await message.answer("⚠️ Вы были автоматически зарегистрированы. Подтвердите правила через /start")
            return
        
        stmt = select(Notification).where(
            Notification.user_id == user.id
        ).order_by(desc(Notification.created_at)).limit(20)
        
        result = await session.execute(stmt)
        notifications = result.scalars().all()
        
        if not notifications:
            await message.answer("📭 У вас пока нет уведомлений.")
            return
        
        # Помечаем как прочитанные
        for notification in notifications:
            if not notification.is_read:
                notification.is_read = True
        
        await session.commit()
        
        await message.answer(
            format_notifications(notifications),
            parse_mode="HTML"
        )

@router.message(Command("help"))
async def cmd_help(message: Message):
    """Помощь"""
    await show_help(message)

@router.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery):
    """Помощь (обработчик кнопки)"""
    await show_help(callback.message)
    await callback.answer()

async def show_help(message: Message):
    """Общая функция показа помощи"""
    help_text = """
🤖 <b>Помощь по боту аукционов</b>

📌 <b>Основные команды:</b>
/start - Начать работу с ботом
/auctions - Активные аукционы
/my_bids - Мои ставки
/my_wins - Мои выигрыши
/notifications - Уведомления
/help - Эта справка

📌 <b>Как участвовать:</b>
1. Подтвердите правила через бота
2. Перейдите в канал P.I.T. Store Оренбург
3. Нажимайте на кнопки под постами для ставок
4. Следите за аукционами

📌 <b>Правила:</b>
• Ставка - обязательство купить
• Оплата в течение 72 часов
• Самовывоз
• Вопросы к @pd56oren
    """
    await message.answer(help_text, parse_mode="HTML")

@router.callback_query(F.data == "confirm_rules")
async def confirm_rules(callback: CallbackQuery):
    """Подтверждение правил пользователем"""
    async with get_db() as session:
        stmt = select(User).where(User.telegram_id == callback.from_user.id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if user:
            user.is_confirmed = True
            await session.commit()
            
            await callback.message.edit_text(
                "🎉 Отлично! Теперь вы можете участвовать в аукционах!\n\n"
                "📢 Перейдите в канал(@PIT_Store_Orenburg) и нажимайте на кнопки под постами для участия.\n\n"
                "📋 Ваши команды:\n"
                "/auctions - Активные аукционы\n"
                "/my_bids - Мои ставки\n"
                "/my_wins - Мои выигрыши\n"
                "/notifications - Уведомления\n"
                "/help - Помощь",
                reply_markup=get_user_menu_keyboard()
            )
            await callback.answer("Правила подтверждены!")
        else:
            await callback.answer("Ошибка: пользователь не найден", show_alert=True)

@router.callback_query(F.data == "cancel_rules")
async def cancel_rules(callback: CallbackQuery):
    """Отказ от правил"""
    await callback.message.edit_text(
        "❌ Вы отказались от правил участия в аукционах.\n\n"
        "Если передумаете, просто снова напишите /start"
    )
    await callback.answer()

@router.callback_query(F.data == "user_menu")
async def user_menu(callback: CallbackQuery):
    """Меню пользователя"""
    await callback.message.edit_text(
        "👤 Меню пользователя\n\n"
        "Выберите действие:",
        reply_markup=get_user_menu_keyboard()
    )
    await callback.answer()

@router.message(Command("cancel_bid"))
async def cmd_cancel_bid(message: Message):
    """Отмена последней ставки пользователя"""
    async with get_db() as session:
        # Находим пользователя по telegram_id
        stmt_user = select(User).where(User.telegram_id == message.from_user.id)
        result_user = await session.execute(stmt_user)
        user = result_user.scalar_one_or_none()
        
        if not user:
            # Создаем пользователя, если его нет
            user = User(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name,
                is_confirmed=False
            )
            session.add(user)
            await session.commit()
            await message.answer("⚠️ Вы были автоматически зарегистрированы. Подтвердите правила через /start")
            return
        
        # Находим последнюю ставку пользователя в активных аукционах
        stmt_last_bid = select(Bid).join(Auction).where(
            Bid.user_id == user.id,
            Auction.status == 'active'
        ).order_by(desc(Bid.created_at)).limit(1).options(
            selectinload(Bid.auction)
        )
        
        result_last_bid = await session.execute(stmt_last_bid)
        last_bid = result_last_bid.scalar_one_or_none()
        
        if not last_bid:
            await message.answer("❌ У вас нет ставок в активных аукционах!")
            return
        
        # Проверяем, есть ли другие ставки после этой
        stmt_later_bids = select(func.count(Bid.id)).where(
            Bid.auction_id == last_bid.auction_id,
            Bid.created_at > last_bid.created_at
        )
        
        result_later = await session.execute(stmt_later_bids)
        later_count = result_later.scalar()
        
        if later_count > 0:
            await message.answer(
                "❌ Нельзя отменить ставку, если после нее были другие ставки!\n"
                "Ваша ставка уже была перебита."
            )
            return
        
        auction = last_bid.auction
        
        # Показываем подтверждение отмены
        await message.answer(
            f"⚠️ <b>Подтверждение отмены ставки</b>\n\n"
            f"🏷 Аукцион: {auction.title}\n"
            f"💰 Ваша ставка: {last_bid.amount} ₽\n"
            f"📅 Время ставки: {last_bid.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Вы уверены, что хотите отменить эту ставку?",
            parse_mode="HTML",
            reply_markup=get_cancel_bid_keyboard(last_bid.id)
        )

@router.callback_query(F.data.startswith("cancel_bid_confirm:"))
async def cancel_bid_confirm(callback: CallbackQuery):
    """Подтверждение отмены ставки"""
    bid_id = int(callback.data.split(":")[1])
    
    async with get_db() as session:
        async with session.begin():
            # Находим ставку
            stmt_bid = select(Bid).where(Bid.id == bid_id).options(
                selectinload(Bid.auction),
                selectinload(Bid.user)
            )
            result_bid = await session.execute(stmt_bid)
            bid = result_bid.scalar_one_or_none()
            
            if not bid:
                await callback.answer("Ставка не найдена!", show_alert=True)
                return
            
            # Проверяем, что пользователь отменяет свою ставку
            if bid.user.telegram_id != callback.from_user.id:
                await callback.answer("Это не ваша ставка!", show_alert=True)
                return
            
            # Проверяем, что аукцион активен
            if bid.auction.status != 'active':
                await callback.answer("Аукцион уже завершен!", show_alert=True)
                return
            
            # Удаляем ставку
            await session.delete(bid)
            
            # Обновляем текущую цену аукциона
            stmt_max_bid = select(Bid).where(
                Bid.auction_id == bid.auction_id
            ).order_by(desc(Bid.amount)).limit(1)
            
            result_max = await session.execute(stmt_max_bid)
            new_max_bid = result_max.scalar_one_or_none()
            
            if new_max_bid:
                bid.auction.current_price = new_max_bid.amount
                bid.auction.last_bid_time = new_max_bid.created_at
                bid.auction.ends_at = new_max_bid.created_at + datetime.timedelta(minutes=Config.BID_TIMEOUT_MINUTES)
            else:
                bid.auction.current_price = bid.auction.start_price
                bid.auction.last_bid_time = bid.auction.created_at
                bid.auction.ends_at = bid.auction.created_at + datetime.timedelta(minutes=Config.BID_TIMEOUT_MINUTES)
        
        # Обновляем сообщение в канале
        async with get_db() as session:
            stmt_auction = select(Auction).where(Auction.id == bid.auction_id)
            result_auction = await session.execute(stmt_auction)
            auction = result_auction.scalar_one()
            
            stmt_top_bids = select(Bid).where(
                Bid.auction_id == auction.id
            ).order_by(desc(Bid.amount)).limit(3).options(
                selectinload(Bid.user)
            )
            result_top = await session.execute(stmt_top_bids)
            top_bids = result_top.scalars().all()
            
            stmt_count = select(func.count(Bid.id)).where(Bid.auction_id == auction.id)
            result_count = await session.execute(stmt_count)
            bids_count = result_count.scalar()
            
            from utils.formatters import format_auction_message
            from handlers.auction import update_channel_message
            
            await update_channel_message(callback.bot, auction, top_bids, bids_count)
        
        await callback.message.edit_text(
            "✅ <b>Ваша ставка успешно отменена!</b>\n\n"
            f"🏷 Аукцион: {bid.auction.title}\n"
            f"💰 Сумма ставки: {bid.amount} ₽\n\n"
            f"Текущая цена аукциона обновлена.",
            parse_mode="HTML"
        )
        await callback.answer()