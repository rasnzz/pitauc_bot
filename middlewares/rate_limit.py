from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Dict, Any, Callable, Awaitable
from datetime import datetime, timedelta
import asyncio

class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, rate_limit_period: int = 2):
        self.rate_limit_period = rate_limit_period  # секунды между действиями
        self.user_timestamps: Dict[int, datetime] = {}
    
    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        user_id = event.from_user.id
        
        # Пропускаем администраторов
        from config import Config
        if user_id in Config.ADMIN_IDS:
            return await handler(event, data)
        
        now = datetime.now()
        
        if user_id in self.user_timestamps:
            last_action = self.user_timestamps[user_id]
            time_diff = (now - last_action).total_seconds()
            
            if time_diff < self.rate_limit_period:
                if isinstance(event, CallbackQuery):
                    await event.answer(
                        f"⏳ Подождите {self.rate_limit_period - int(time_diff)} секунд перед следующим действием",
                        show_alert=True
                    )
                return
        
        self.user_timestamps[user_id] = now
        
        # Очистка старых записей (раз в 100 вызовов)
        if len(self.user_timestamps) > 1000:
            to_delete = []
            for uid, timestamp in self.user_timestamps.items():
                if (now - timestamp).total_seconds() > 300:  # 5 минут
                    to_delete.append(uid)
            for uid in to_delete:
                del self.user_timestamps[uid]
        
        return await handler(event, data)