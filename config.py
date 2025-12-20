import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    
    CHANNEL_ID = os.getenv("CHANNEL_ID", "")
    try:
        if CHANNEL_ID and CHANNEL_ID.lstrip('-').isdigit():
            CHANNEL_ID = int(CHANNEL_ID)
    except:
        pass
    
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
