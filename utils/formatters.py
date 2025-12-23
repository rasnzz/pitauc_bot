from datetime import datetime, timedelta
import json
from database.models import Auction, Bid, Notification
from config import Config
import logging

logger = logging.getLogger(__name__)

def format_ended_auction_message(auction: Auction, top_bids=None, bids_count=0) -> str:
    """Форматирование сообщения о завершенном аукционе для канала"""
    
    # Форматируем топ ставок
    top_bids_text = ""
    if top_bids:
        places = ["🥇", "🥈", "🥉"]
        for i, bid in enumerate(top_bids[:3]):
            if i < len(places):
                emoji = places[i]
                username = format_username(bid.user)
                time_ago = format_time_ago(bid.created_at)
                top_bids_text += f"{emoji} {username}: {bid.amount} ₽ ({time_ago})\n"
    
    # Информация о победителе
    winner_text = ""
    if auction.winner:
        winner = auction.winner
        winner_name = format_username(winner)
        winner_text = f"🏆 Победитель: {winner_name} - {auction.current_price} ₽\n"
    else:
        winner_text = "🏆 Победитель: Не определен\n"
    
    # Форматируем дату завершения
    ended_at_text = "Время не указано"
    if auction.ended_at:
        try:
            ended_at_text = auction.ended_at.strftime('%d.%m.%Y %H:%M')
        except:
            pass
    
    message = f"""
🔔 АУКЦИОН ЗАВЕРШЕН!

{auction.title}

{auction.description if auction.description else ''}

Стартовая цена: {auction.start_price} ₽
Шаг ставки: {auction.step_price} ₽

Финальная цена: {auction.current_price} ₽
📊 Количество ставок: {bids_count}

{winner_text}
{top_bids_text}

📅 Аукцион завершен: {ended_at_text}

Спасибо всем за участие!
""".strip()
    
    # Проверяем длину сообщения (ограничение Telegram)
    if len(message) > 1024:
        # Если сообщение слишком длинное, укорачиваем его
        logger.warning(f"Сообщение слишком длинное ({len(message)} символов), укорачиваю")
        message = f"""
🔔 АУКЦИОН ЗАВЕРШЕН!

{auction.title}

Финальная цена: {auction.current_price} ₽
📊 Количество ставок: {bids_count}

{winner_text}

📅 Аукцион завершен: {ended_at_text}

Спасибо всем за участие!
""".strip()
    
    logger.debug(f"Сформировано сообщение для завершенного аукциона #{auction.id}, длина: {len(message)} символов")
    return message

def format_auction_message(auction: Auction, top_bids=None, bids_count=0) -> str:
    """Форматирование сообщения об аукционе для канала"""
    
    # Определяем статус аукциона
    if auction.status == 'ended':
        return format_ended_auction_message(auction, top_bids, bids_count)
    
    # Рассчитываем оставшееся время
    time_remaining = format_time_remaining(auction.last_bid_time, auction.ends_at)
    
    # Форматируем топ ставок
    top_bids_text = ""
    if top_bids:
        places = ["🥇", "🥈", "🥉"]
        for i, bid in enumerate(top_bids[:3]):
            if i < len(places):
                emoji = places[i]
                username = format_username(bid.user)
                time_ago = format_time_ago(bid.created_at)
                top_bids_text += f"{emoji} {username}: {bid.amount} ₽ ({time_ago})\n"
    
    # Конвертируем минуты в часы для отображения в сообщении
    timeout_hours = Config.BID_TIMEOUT_MINUTES // 60
    
    message = f"""
📢 🎰 Внимание, аукцион от P.I.T Store Оренбург!

{auction.title}

{auction.description if auction.description else ''}

Стартовая цена: {auction.start_price} ₽
Шаг ставки: {auction.step_price} ₽

👉 Аукцион считается законченным, если после последней ставки прошло {timeout_hours} часов ({Config.BID_TIMEOUT_MINUTES} минут)

👉 Для участия в наших аукционах - подтвердите свое согласие нашему 🤖 <a href="https://t.me/pitauc_bot">Боту-аукционисту</a>

👉 <a href='https://telegra.ph/Pravila-provedeniya-aukcionov-12-16'>Общие правила проведения аукционов</a>

👉 ⚠️ Лот может быть снят с продажи на усмотрение администрации

Не является публичной офертой.

⏳ Таймер: {time_remaining}
💰 Текущая цена: {auction.current_price} ₽
📊 Количество ставок: {bids_count}

{top_bids_text}
""".strip()
    
    return message

def format_user_bids(bids) -> str:
    """Форматирование списка ставок пользователя"""
    if not bids:
        return "📭 У вас пока нет ставок."
    
    text = "📋 <b>Ваши ставки:</b>\n\n"
    
    for bid in bids[:20]:
        auction = bid.auction
        status = "🟢" if auction.status == 'active' else "🔴" if auction.status == 'ended' else "⚫"
        
        text += f"{status} <b>{auction.title}</b>\n"
        text += f"   💰 Ваша ставка: {bid.amount} ₽\n"
        text += f"   🏆 Текущая цена: {auction.current_price} ₽\n"
        
        if auction.status == 'active':
            text += f"   ⬆️ Минимальная ставка: {auction.current_price + auction.step_price} ₽\n"
        
        text += f"   📅 Дата ставки: {bid.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        text += f"   🔗 ID аукциона: {auction.id}\n"
        text += "─" * 30 + "\n\n"
    
    if len(bids) > 20:
        text += f"\n📄 Показано 20 из {len(bids)} ставок"
    
    return text

def format_bid_history(bids) -> str:
    """Форматирование истории ставок"""
    if not bids:
        return "📭 История ставок пуста."
    
    text = "📋 <b>История ставок:</b>\n\n"
    
    for i, bid in enumerate(bids, 1):
        username = format_username(bid.user)
        time_ago = format_time_ago(bid.created_at)
        
        text += f"{i}. {username}\n"
        text += f"   💰 {bid.amount} ₽\n"
        text += f"   ⏰ {time_ago}\n"
        text += "─" * 20 + "\n"
    
    return text

def format_notifications(notifications) -> str:
    """Форматирование уведомлений"""
    if not notifications:
        return "📭 У вас нет уведомлений."
    
    text = "🔔 <b>Ваши уведомления:</b>\n\n"
    
    for notification in notifications:
        emoji = "✅" if notification.is_read else "🆕"
        time_ago = format_time_ago(notification.created_at)
        
        text += f"{emoji} {notification.message}\n"
        text += f"   ⏰ {time_ago}\n"
        text += "─" * 30 + "\n"
    
    return text

def format_admin_stats(stats) -> str:
    """Форматирование статистики для админа"""
    text = "📊 <b>Статистика:</b>\n\n"
    
    for key, value in stats.items():
        text += f"• {key}: {value}\n"
    
    return text

def format_username(user) -> str:
    """Форматирование имени пользователя"""
    if not user:
        return "Аноним"
    
    if user.username:
        return f"@{user.username}"
    elif user.first_name:
        return user.first_name
    else:
        return "Аноним"

def format_time_ago(dt) -> str:
    """Форматирование времени в формате 'X минут назад'"""
    if not dt:
        return "давно"
    
    now = datetime.utcnow()
    diff = now - dt
    
    if diff.days > 0:
        return f"{diff.days} дней назад"
    elif diff.seconds > 3600:
        hours = diff.seconds // 3600
        return f"{hours} часов назад"
    elif diff.seconds > 60:
        minutes = diff.seconds // 60
        return f"{minutes} минут назад"
    else:
        return "только что"

def get_channel_link(auction: 'Auction') -> str:
    """Получить правильную ссылку на сообщение в канале"""
    try:
        from config import Config
        
        if not auction.channel_message_id:
            return "Ссылка недоступна"
        
        # Если CHANNEL_ID числовой
        if isinstance(Config.CHANNEL_ID, int):
            # Преобразуем в формат для ссылки (убираем -100 если есть)
            channel_id = str(Config.CHANNEL_ID)
            if channel_id.startswith('-100'):
                chat_id = channel_id[4:]  # Убираем -100
            else:
                chat_id = channel_id.lstrip('-')
            return f"https://t.me/c/{chat_id}/{auction.channel_message_id}"
        else:
            # Если это username (@channel)
            username = str(Config.CHANNEL_ID).lstrip('@')
            return f"https://t.me/{username}/{auction.channel_message_id}"
    except Exception as e:
        logger.error(f"Ошибка формирования ссылки: {e}")
        return "Ссылка недоступна"

def format_channel_message_link(auction: 'Auction') -> str:
    """Форматированная ссылка для сообщений"""
    link = get_channel_link(auction)
    return f"🔗 <a href='{link}'>Ссылка на аукцион</a>"

def format_time_remaining(last_bid_time, ends_at=None):
    """Форматирование оставшегося времени"""
    if ends_at:
        total_seconds = (ends_at - datetime.utcnow()).total_seconds()
    elif last_bid_time:
        diff = datetime.utcnow() - last_bid_time
        total_seconds = Config.BID_TIMEOUT_MINUTES * 60 - diff.total_seconds()
    else:
        return "0 минут"
    
    if total_seconds <= 0:
        return "Аукцион завершён"
    
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    

    return f"{hours}ч {minutes}м"

