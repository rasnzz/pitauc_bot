from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def get_confirmation_keyboard():
    """Клавиатура для подтверждения правил"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="✅ Я согласен с правилами", callback_data="confirm_rules"),
        InlineKeyboardButton(text="❌ Отказаться", callback_data="cancel_rules")
    )
    return builder.as_markup()

def get_user_menu_keyboard():
    """Меню пользователя"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="📋 Мои ставки", callback_data="my_bids"),
        InlineKeyboardButton(text="🏆 Мои выигрыши", callback_data="my_wins"),
        InlineKeyboardButton(text="🔔 Уведомления", callback_data="notifications"),
        InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
        InlineKeyboardButton(text="📞 Связаться", url="https://t.me/pd56oren")
    )
    builder.adjust(1)
    return builder.as_markup()

def get_channel_auction_keyboard(auction_id: int, next_bid_amount: float):
    """Клавиатура для аукциона в КАНАЛЕ (только ставка, подписка и связь)"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text=f"✅ Сделать ставку {next_bid_amount} ₽", 
            callback_data=f"bid:{auction_id}:{next_bid_amount}"
        ),
        InlineKeyboardButton(
            text="🔔 Подписаться на уведомления", 
            callback_data=f"subscribe:{auction_id}"
        ),
        InlineKeyboardButton(
            text="📞 Связаться с админом", 
            url="https://t.me/pd56oren"
        )
    )
    builder.adjust(1)
    return builder.as_markup()

def get_bot_auction_keyboard(auction_id: int, next_bid_amount: float):
    """Клавиатура для аукциона в БОТЕ (полный функционал)"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text=f"✅ Сделать ставку {next_bid_amount} ₽", 
            callback_data=f"bid:{auction_id}:{next_bid_amount}"
        ),
        InlineKeyboardButton(
            text="📊 Топ-3 ставки", 
            callback_data=f"top3:{auction_id}"
        ),
        InlineKeyboardButton(
            text="📋 История ставок", 
            callback_data=f"history:{auction_id}"
        ),
        InlineKeyboardButton(
            text="🔔 Подписаться на уведомления", 
            callback_data=f"subscribe:{auction_id}"
        )
    )
    builder.adjust(1)
    return builder.as_markup()

def get_auction_history_keyboard(auction_id: int):
    """Клавиатура для истории ставок"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="🔙 Назад к аукциону", callback_data=f"back_to_auction:{auction_id}"),
        InlineKeyboardButton(text="📞 Связаться", url="https://t.me/pd56oren")
    )
    return builder.as_markup()

def get_cancel_bid_keyboard(bid_id: int):
    """Клавиатура для отмены ставки"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="✅ Да, отменить ставку", callback_data=f"cancel_bid_confirm:{bid_id}"),
        InlineKeyboardButton(text="❌ Нет, оставить", callback_data="cancel_bid_cancel")
    )
    builder.adjust(1)
    return builder.as_markup()

def get_unsubscribe_keyboard(auction_id: int):
    """Клавиатура для отписки от аукциона"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="🔕 Отписаться от уведомлений", callback_data=f"unsubscribe:{auction_id}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data=f"back_to_auction:{auction_id}")
    )
    return builder.as_markup()

def get_admin_limits_keyboard():
    """Клавиатура для управления лимитами"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="📊 Статистика лимитов", callback_data="admin_limits"),
        InlineKeyboardButton(text="⚙️ Изменить лимиты", callback_data="admin_limits_edit"),
        InlineKeyboardButton(text="📋 Логи действий", callback_data="admin_actions_log"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")
    )
    builder.adjust(1)
    return builder.as_markup()

def get_admin_main_keyboard():
    """Главное меню админа"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="➕ Создать аукцион", callback_data="admin_create"),
        InlineKeyboardButton(text="📋 Активные аукционы", callback_data="admin_active"),
        InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats_all"),
        InlineKeyboardButton(text="⚖️ Лимиты и правила", callback_data="admin_limits"),
        InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users"),
        InlineKeyboardButton(text="💰 Финансы", callback_data="admin_finance"),
        InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_settings")
    )
    builder.adjust(2)
    return builder.as_markup()

def get_admin_stats_keyboard():
    """Клавиатура статистики"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="📈 Графики", callback_data="admin_charts"),
        InlineKeyboardButton(text="📋 Экспорт", callback_data="admin_export"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")
    )
    return builder.as_markup()

def get_admin_auction_keyboard(auction_id: int):
    """Клавиатура для админа управления аукционом"""
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="🛑 Завершить досрочно", callback_data=f"admin_end:{auction_id}"),
        InlineKeyboardButton(text="✏️ Редактировать лот", callback_data=f"admin_edit:{auction_id}"),
        InlineKeyboardButton(text="📊 Статистика", callback_data=f"admin_stats:{auction_id}"),
        InlineKeyboardButton(text="🗑️ Удалить аукцион", callback_data=f"admin_delete:{auction_id}"),
        InlineKeyboardButton(text="📢 Анонсировать", callback_data=f"admin_announce:{auction_id}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")
    )
    builder.adjust(2)
    return builder.as_markup()