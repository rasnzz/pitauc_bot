from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func, desc
from sqlalchemy.orm import selectinload
import json
import datetime
import logging
import asyncio

from sqlalchemy import delete

from config import Config
from database.database import get_db
from database.models import Auction, User, Bid, Notification
from keyboards.inline import get_admin_main_keyboard, get_admin_auction_keyboard, get_admin_stats_keyboard, get_channel_auction_keyboard
from utils.formatters import format_auction_message, format_ended_auction_message, format_admin_stats, format_username
from utils.notifications import send_winner_notification, send_subscription_notification
from utils.timer import auction_timer_manager
from utils.validators import AuctionValidator
from utils.periodic_updater import periodic_updater

router = Router()
logger = logging.getLogger(__name__)

def is_admin(user_id: int) -> bool:
    return user_id in Config.ADMIN_IDS

# Состояния для FSM
class CreateAuction(StatesGroup):
    title = State()
    description = State()
    photo = State()
    start_price = State()
    step_price = State()

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    """Панель администратора"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора!")
        return
    
    # Проверяем количество активных аукционов
    async with get_db() as session:
        stmt_active = select(func.count(Auction.id)).where(Auction.status == 'active')
        result = await session.execute(stmt_active)
        active_count = result.scalar()
        
        max_active = 20  # Максимальное количество активных аукционов
        
        if active_count >= max_active:
            await message.answer(
                f"⚠️ <b>Достигнут лимит активных аукционов!</b>\n\n"
                f"Активных аукционов: {active_count}/{max_active}\n"
                f"Завершите некоторые аукционы перед созданием новых.",
                parse_mode="HTML"
            )
            return
    
    await message.answer(
        "👑 Панель администратора\n\n"
        "Выберите действия:",
        reply_markup=get_admin_main_keyboard()
    )

@router.callback_query(F.data == "admin_create")
async def admin_create_start(callback: CallbackQuery, state: FSMContext):
    """Начало создания аукциона"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав!", show_alert=True)
        return
    
    # Проверяем количество активных аукционов
    async with get_db() as session:
        stmt_active = select(func.count(Auction.id)).where(Auction.status == 'active')
        result = await session.execute(stmt_active)
        active_count = result.scalar()
        
        max_active = 20
        
        if active_count >= max_active:
            await callback.answer(
                f"⚠️ Достигнут лимит активных аукционов ({active_count}/{max_active})",
                show_alert=True
            )
            return
    
    await callback.message.answer(
        "🛠 Создание нового аукциона\n\n"
        "Введите название лота (5-255 символов):"
    )
    await state.set_state(CreateAuction.title)
    await callback.answer()

@router.message(CreateAuction.title)
async def process_title(message: Message, state: FSMContext):
    """Обработка названия с валидацией"""
    title = message.text
    
    # Валидация названия
    is_valid, error_msg = AuctionValidator.validate_title(title)
    
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПожалуйста, введите название еще раз:")
        return
    
    await state.update_data(title=title)
    await message.answer(
        "Введите описание лота (не обязательно):\n"
        "Или отправьте 'нет' если описание не требуется"
    )
    await state.set_state(CreateAuction.description)

@router.message(CreateAuction.description)
async def process_description(message: Message, state: FSMContext):
    """Обработка описания"""
    description = message.text if message.text.lower() != 'нет' else None
    
    # Ограничение длины описания
    if description and len(description) > 2000:
        await message.answer(
            "❌ Описание слишком длинное (максимум 2000 символов)\n\n"
            "Введите более короткое описание:"
        )
        return
    
    await state.update_data(description=description)
    await message.answer(
        "Отправьте ОДНО фото лота (не файл, а именно фото):\n"
        "Или отправьте 'нет' если фото нет"
    )
    await state.set_state(CreateAuction.photo)

@router.message(CreateAuction.photo)
async def process_photo(message: Message, state: FSMContext):
    """Обработка фото"""
    if message.photo:
        largest_photo = message.photo[-1]
        photo_id = largest_photo.file_id
        
        await state.update_data(photo=photo_id)
        await message.answer(
            "Введите стартовую цену (в рублях):\n"
            "Минимум: 1 ₽, Максимум: 1 000 000 000 ₽"
        )
        await state.set_state(CreateAuction.start_price)
    
    elif message.text and message.text.lower() == 'нет':
        await state.update_data(photo=None)
        await message.answer(
            "Введите стартовую цену (в рублях):\n"
            "Минимум: 1 ₽, Максимум: 1 000 000 000 ₽"
        )
        await state.set_state(CreateAuction.start_price)
    
    else:
        await message.answer(
            "Пожалуйста, отправьте фото или напишите 'нет':"
        )

@router.message(CreateAuction.start_price)
async def process_start_price(message: Message, state: FSMContext):
    """Обработка стартовой цены с валидацией"""
    price_str = message.text
    
    # Валидация цены
    is_valid, price, error_msg = AuctionValidator.validate_price(price_str)
    
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nВведите стартовую цену:")
        return
    
    await state.update_data(start_price=price)
    await message.answer(
        "Введите шаг ставки (в рублях):\n"
        f"Рекомендуется: от {price * 0.01:.2f} ₽ до {price * 0.1:.2f} ₽"
    )
    await state.set_state(CreateAuction.step_price)

@router.message(CreateAuction.step_price)
async def process_step_price(message: Message, state: FSMContext):
    """Обработка шага ставки и создание аукциона"""
    try:
        # Валидация шага ставки
        is_valid, step, error_msg = AuctionValidator.validate_price(message.text)
        
        if not is_valid:
            await message.answer(f"❌ {error_msg}\n\nВведите шаг ставки:")
            return
        
        data = await state.get_data()
        start_price = data['start_price']
        
        # Валидация шага относительно стартовой цены
        is_step_valid, step_error = AuctionValidator.validate_step_price(start_price, step)
        
        if not is_step_valid:
            await message.answer(f"❌ {step_error}\n\nВведите шаг ставки:")
            return
        
        # Создаем аукцион в базе данных
        async with get_db() as session:
            async with session.begin():
                auction = Auction(
                    title=data['title'],
                    description=data['description'],
                    photos=json.dumps([data['photo']] if data.get('photo') else []),
                    start_price=start_price,
                    step_price=step,
                    current_price=start_price,
                    status='active',
                    ends_at=datetime.datetime.utcnow() + datetime.timedelta(minutes=Config.BID_TIMEOUT_MINUTES)
                )
                
                session.add(auction)
        
        # Получаем ID созданного аукциона
        async with get_db() as session:
            stmt = select(Auction).where(
                Auction.title == data['title']
            ).order_by(desc(Auction.created_at)).limit(1)
            result = await session.execute(stmt)
            auction = result.scalar_one()
            
            logger.info(f"Аукцион создан с ID: {auction.id}")
            
            # Запускаем таймер для аукциона
            asyncio.create_task(
                auction_timer_manager.start_auction_timer(auction.id, auction.ends_at)
            )
            
            # Для нового аукциона нет ставок
            message_text = format_auction_message(auction, top_bids=[], bids_count=0)
            next_bid_amount = auction.current_price + auction.step_price
        
        try:
            # Отправляем сообщение в канал
            if data.get('photo'):
                channel_message = await message.bot.send_photo(
                    chat_id=Config.CHANNEL_ID,
                    photo=data['photo'],
                    caption=message_text,
                    reply_markup=get_channel_auction_keyboard(auction.id, next_bid_amount),
                    parse_mode='HTML'
                )
            else:
                channel_message = await message.bot.send_message(
                    chat_id=Config.CHANNEL_ID,
                    text=message_text,
                    reply_markup=get_channel_auction_keyboard(auction.id, next_bid_amount),
                    parse_mode='HTML'
                )
            
            # Сохраняем ID сообщения в БД
            async with get_db() as session:
                async with session.begin():
                    stmt = select(Auction).where(Auction.id == auction.id)
                    result = await session.execute(stmt)
                    auction_to_update = result.scalar_one()
                    auction_to_update.channel_message_id = channel_message.message_id
            
            timeout_hours = Config.BID_TIMEOUT_MINUTES // 60
            await message.answer(
                f"✅ <b>Аукцион создан и опубликован в канале!</b>\n\n"
                f"🆔 ID: <code>{auction.id}</code>\n"
                f"🏷 Название: {data['title']}\n"
                f"💰 Стартовая цена: {start_price:,.2f} ₽\n"
                f"📈 Шаг ставки: {step:,.2f} ₽\n"
                f"⏰ Время аукциона: {timeout_hours} часов\n"
                f"📸 Фото: {'✅ Есть' if data.get('photo') else '❌ Нет'}\n"
                f"🔗 Ссылка на пост: https://t.me/{str(Config.CHANNEL_ID).replace('@', '')}/{channel_message.message_id}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка публикации в канал: {e}")
            await message.answer(
                f"✅ Аукцион создан, но не опубликован в канале (ошибка: {e})\n\n"
                f"🆔 ID: {auction.id}\n"
                f"🏷 Название: {data['title']}\n\n"
                f"Проверьте:\n"
                f"1. Бот добавлен в канал как администратор\n"
                f"2. ID канала указан верно\n"
                f"3. Бот имеет права на отправку сообщений"
            )
        
        await state.clear()
        
    except ValueError as e:
        logger.error(f"Ошибка преобразования числа: {e}")
        await message.answer("❌ Неверный формат шага ставки. Введите число:")
    except Exception as e:
        logger.error(f"Неизвестная ошибка: {e}", exc_info=True)
        await message.answer(f"❌ Произошла ошибка при создании аукциона: {str(e)}")

@router.callback_query(F.data == "admin_active")
async def admin_active_auctions(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав!", show_alert=True)
        return
    
    async with get_db() as session:
        stmt = select(Auction).where(
            Auction.status == 'active'
        ).order_by(desc(Auction.created_at))
        
        result = await session.execute(stmt)
        auctions = result.scalars().all()
        
        if not auctions:
            await callback.message.answer("📭 Нет активных аукционов.")
            await callback.answer()
            return
        
        # Вместо простого текста отправляем каждый аукцион с кнопками управления
        for auction in auctions:
            stmt_bids = select(func.count(Bid.id)).where(Bid.auction_id == auction.id)
            result_bids = await session.execute(stmt_bids)
            bids_count = result_bids.scalar()
            
            time_remaining = "Завершен"
            if auction.ends_at:
                time_left = auction.ends_at - datetime.datetime.utcnow()
                if time_left.total_seconds() > 0:
                    hours = int(time_left.total_seconds() // 3600)
                    minutes = int((time_left.total_seconds() % 3600) // 60)
                    time_remaining = f"{hours}ч {minutes}м"
            
            text = (
                f"🆔 ID: <code>{auction.id}</code>\n"
                f"📦 <b>{auction.title}</b>\n"
                f"💰 Текущая цена: {auction.current_price} ₽\n"
                f"👥 Ставок: {bids_count}\n"
                f"⏳ Осталось: {time_remaining}\n\n"
                f"<b>Выберите действие:</b>"
            )
            
            await callback.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=get_admin_auction_keyboard(auction.id)
            )
        
        await callback.answer()

@router.callback_query(F.data == "admin_stats_all")
async def admin_stats_all(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав!", show_alert=True)
        return
    
    async with get_db() as session:
        stmt_auctions = select(func.count(Auction.id))
        result = await session.execute(stmt_auctions)
        total_auctions = result.scalar()
        
        stmt_active = select(func.count(Auction.id)).where(Auction.status == 'active')
        result = await session.execute(stmt_active)
        active_auctions = result.scalar()
        
        stmt_ended = select(func.count(Auction.id)).where(Auction.status == 'ended')
        result = await session.execute(stmt_ended)
        ended_auctions = result.scalar()
        
        stmt_users = select(func.count(User.id))
        result = await session.execute(stmt_users)
        total_users = result.scalar()
        
        stmt_confirmed = select(func.count(User.id)).where(User.is_confirmed == True)
        result = await session.execute(stmt_confirmed)
        confirmed_users = result.scalar()
        
        stmt_bids = select(func.count(Bid.id))
        result = await session.execute(stmt_bids)
        total_bids = result.scalar()
        
        stmt_total_money = select(func.sum(Auction.current_price)).where(Auction.status == 'ended')
        result = await session.execute(stmt_total_money)
        total_money = result.scalar() or 0
        
        timeout_hours = Config.BID_TIMEOUT_MINUTES // 60
        
        stats_text = f"""
📊 <b>Общая статистика</b>

🏷 <b>Аукционы:</b>
• Всего: {total_auctions}
• Активных: {active_auctions}
• Завершённых: {ended_auctions}

👥 <b>Пользователи:</b>
• Всего: {total_users}
• Подтвердивших: {confirmed_users}

💰 <b>Финансы:</b>
• Всего ставок: {total_bids}
• Общая сумма: {total_money:.2f} ₽
• Средняя ставка: {total_money/max(ended_auctions, 1):.2f} ₽

⏰ <b>Система:</b>
• Таймер: {Config.BID_TIMEOUT_MINUTES} минут ({timeout_hours} часов)
• Шаг ставки: {Config.BID_STEP_PERCENT}%
"""
        
        await callback.message.answer(
            stats_text,
            parse_mode="HTML",
            reply_markup=get_admin_stats_keyboard()
        )
        await callback.answer()

@router.callback_query(F.data.startswith("admin_end:"))
async def admin_end_auction(callback: CallbackQuery):
    """Завершить аукцион досрочно"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав!", show_alert=True)
        return
    
    auction_id = int(callback.data.split(":")[1])
    
    async with get_db() as session:
        async with session.begin():
            stmt = select(Auction).where(Auction.id == auction_id)
            result = await session.execute(stmt)
            auction = result.scalar_one_or_none()
            
            if not auction:
                await callback.answer("Аукцион не найден!", show_alert=True)
                return
            
            if auction.status != 'active':
                await callback.answer("Аукцион уже завершён!", show_alert=True)
                return
            
            auction.status = 'ended'
            auction.ended_at = datetime.datetime.utcnow()
            
            # ИСПРАВЛЕНИЕ: добавлен .limit(1) к запросу
            stmt_bids = select(Bid).where(Bid.auction_id == auction_id).order_by(desc(Bid.amount)).limit(1)
            result_bids = await session.execute(stmt_bids)
            winner_bid = result_bids.scalar_one_or_none()
            
            if winner_bid:
                auction.winner_id = winner_bid.user_id
                auction.current_price = winner_bid.amount
        
        # Получаем данные для обновления сообщения
        stmt_full = select(Auction).where(Auction.id == auction_id).options(
            selectinload(Auction.winner)
        )
        result_full = await session.execute(stmt_full)
        full_auction = result_full.scalar_one()
        
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
        try:
            # Формируем сообщение о завершенном аукционе
            message_text = format_ended_auction_message(full_auction, top_bids, bids_count)
            
            # Обновляем сообщение в канале (без клавиатуры)
            try:
                # Сначала пробуем обновить подпись (если было фото)
                await callback.bot.edit_message_caption(
                    chat_id=Config.CHANNEL_ID,
                    message_id=auction.channel_message_id,
                    caption=message_text,
                    parse_mode='HTML'
                )
            except:
                # Если не получилось (например, сообщение без фото), обновляем текст
                await callback.bot.edit_message_text(
                    chat_id=Config.CHANNEL_ID,
                    message_id=auction.channel_message_id,
                    text=message_text,
                    parse_mode='HTML'
                )
            
            logger.info(f"Сообщение в канале для аукциона #{auction.id} обновлено (админское завершение)")
        except Exception as e:
            logger.error(f"Ошибка при обновлении сообщения в канале: {e}")
        
        if winner_bid:
            async with get_db() as inner_session:
                stmt_winner = select(User).where(User.id == winner_bid.user_id)
                result_winner = await inner_session.execute(stmt_winner)
                winner = result_winner.scalar_one_or_none()
                
                if winner:
                    try:
                        # Используем функцию send_winner_notification с новым контактом @pd56oren
                        await send_winner_notification(callback.bot, auction, winner)
                        
                        notification = Notification(
                            user_id=winner.id,
                            auction_id=auction_id,
                            message=f"Вы выиграли аукцион '{auction.title}'! Сумма: {auction.current_price} ₽"
                        )
                        inner_session.add(notification)
                        await inner_session.commit()
                    except Exception as e:
                        logger.error(f"Ошибка при уведомлении победителя: {e}")
        
        if auction_id in auction_timer_manager.active_timers:
            auction_timer_manager.active_timers[auction_id].cancel()
        periodic_updater.clear_update_history(auction_id)
        
        await callback.message.answer(
            f"✅ Аукцион #{auction.id} завершён досрочно.\n"
            f"Победитель: {'Есть' if auction.winner_id else 'Нет'}"
        )
        await callback.answer("Аукцион завершён!")
        
@router.callback_query(F.data.startswith("admin_delete:"))
async def admin_delete_auction(callback: CallbackQuery):
    """Удалить аукцион без победителя"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав!", show_alert=True)
        return
    
    auction_id = int(callback.data.split(":")[1])
    
    async with get_db() as session:
        async with session.begin():
            # Получаем аукцион
            stmt = select(Auction).where(Auction.id == auction_id)
            result = await session.execute(stmt)
            auction = result.scalar_one_or_none()
            
            if not auction:
                await callback.answer("Аукцион не найден!", show_alert=True)
                return
            
            # Сохраняем ID сообщения в канале для удаления
            channel_message_id = auction.channel_message_id
            
            # Удаляем все связанные записи в правильном порядке
            # 1. Уведомления об этом аукционе
            await session.execute(
                delete(Notification).where(Notification.auction_id == auction_id)
            )
            
            # 2. Подписки на этот аукцион
            await session.execute(
                delete(AuctionSubscription).where(AuctionSubscription.auction_id == auction_id)
            )
            
            # 3. Ставки на этот аукцион
            await session.execute(
                delete(Bid).where(Bid.auction_id == auction_id)
            )
            
            # 4. Сам аукцион
            await session.execute(
                delete(Auction).where(Auction.id == auction_id)
            )
            
        # Останавливаем таймер, если он активен
        from utils.timer import auction_timer_manager
        if auction_id in auction_timer_manager.active_timers:
            auction_timer_manager.active_timers[auction_id].cancel()
            del auction_timer_manager.active_timers[auction_id]
        
        # Очищаем историю обновлений
        from utils.periodic_updater import periodic_updater
        periodic_updater.clear_update_history(auction_id)
        
        # Удаляем сообщение в канале
        try:
            await callback.bot.delete_message(
                chat_id=Config.CHANNEL_ID,
                message_id=channel_message_id
            )
            logger.info(f"Сообщение в канале для аукциона #{auction_id} удалено")
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения в канале: {e}")
            # Продолжаем, даже если не удалось удалить сообщение
        
        await callback.message.answer(
            f"✅ Аукцион #{auction_id} удалён без выявления победителя.\n"
            f"Сообщение в канале удалено."
        )
        await callback.answer("Аукцион удалён!")


@router.callback_query(F.data == "admin_limits")
async def admin_limits(callback: CallbackQuery):
    """Показать текущие лимиты и статистику"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет прав!", show_alert=True)
        return
    
    async with get_db() as session:
        # Активные аукционы
        stmt_active = select(func.count(Auction.id)).where(Auction.status == 'active')
        result = await session.execute(stmt_active)
        active_count = result.scalar()
        
        # Всего аукционов за последние 24 часа
        day_ago = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
        stmt_today = select(func.count(Auction.id)).where(Auction.created_at >= day_ago)
        result = await session.execute(stmt_today)
        today_count = result.scalar()
        
        # Среднее количество ставок на аукцион
        stmt_avg_bids = select(func.avg(func.count(Bid.id))).join(Auction).group_by(Bid.auction_id)
        result = await session.execute(stmt_avg_bids)
        avg_bids = result.scalar() or 0
        
        limits_text = f"""
📊 <b>Лимиты и статистика</b>

🏷 <b>Аукционы:</b>
• Активных: {active_count}/20
• Создано за 24ч: {today_count}
• Среднее время аукциона: {Config.BID_TIMEOUT_MINUTES // 60} ч

👥 <b>Активность:</b>
• Среднее ставок на аукцион: {avg_bids:.1f}
• Ограничение ставок: 1 в 3 секунды
• Макс. фото: 1 на аукцион

💰 <b>Финансовые лимиты:</b>
• Мин. цена: 1 ₽
• Макс. цена: 1 000 000 000 ₽
• Мин. шаг: 1% от цены
• Макс. шаг: 10% от цены

🔧 <b>Системные настройки:</b>
• Таймер: {Config.BID_TIMEOUT_MINUTES} минут
• Шаг ставки: {Config.BID_STEP_PERCENT}%
"""
        
        await callback.message.answer(limits_text, parse_mode="HTML")

        await callback.answer()
