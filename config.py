# config.py - улучшенная версия
import os
import re
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    
    # Обработка CHANNEL_ID
    CHANNEL_ID_STR = os.getenv("CHANNEL_ID", "").strip()
    CHANNEL_ID = None
    
    if CHANNEL_ID_STR:
        # Если это числовой ID (может быть отрицательным для каналов)
        if CHANNEL_ID_STR.lstrip('-').replace('.', '').isdigit():
            CHANNEL_ID = int(CHANNEL_ID_STR)
        # Если это username (@channel)
        elif CHANNEL_ID_STR.startswith('@'):
            CHANNEL_ID = CHANNEL_ID_STR
        else:
            # Пробуем извлечь ID из разных форматов
            match = re.search(r'(-?\d+)', CHANNEL_ID_STR)
            if match:
                CHANNEL_ID = int(match.group(1))
            else:
                CHANNEL_ID = CHANNEL_ID_STR
    
    admin_ids_str = os.getenv("ADMIN_IDS", "")
    if admin_ids_str:
        ADMIN_IDS = [int(id.strip()) for id in admin_ids_str.split(",") if id.strip()]
    else:
        ADMIN_IDS = []
    
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///auctions.db")
    
    BID_TIMEOUT_MINUTES = int(os.getenv("BID_TIMEOUT_MINUTES", "240"))
    BID_STEP_PERCENT = int(os.getenv("BID_STEP_PERCENT", "10"))
    
    # Настройки для многопользовательской работы
    DATABASE_TIMEOUT = int(os.getenv("DATABASE_TIMEOUT", "60"))
    BID_RETRY_ATTEMPTS = int(os.getenv("BID_RETRY_ATTEMPTS", "3"))
    
    if not BOT_TOKEN:
        print("⚠️  ВНИМАНИЕ: BOT_TOKEN не установлен!")
        print("Создайте файл .env с токеном вашего бота")
