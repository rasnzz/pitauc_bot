import asyncio
import shutil
import datetime
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class DatabaseBackup:
    def __init__(self, backup_dir: str = "backups", keep_days: int = 7):
        self.backup_dir = Path(backup_dir)
        self.keep_days = keep_days
        self.backup_dir.mkdir(exist_ok=True)
    
    async def create_backup(self, db_path: str = "auctions.db"):
        """Создание резервной копии базы данных"""
        try:
            if not os.path.exists(db_path):
                logger.warning(f"Файл базы данных {db_path} не найден")
                return
            
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"auctions_{timestamp}.db"
            backup_path = self.backup_dir / backup_name
            
            # Копируем файл базы данных
            shutil.copy2(db_path, backup_path)
            
            logger.info(f"Создана резервная копия: {backup_path}")
            
            # Очищаем старые бэкапы
            await self._cleanup_old_backups()
            
            return backup_path
            
        except Exception as e:
            logger.error(f"Ошибка при создании бэкапа: {e}")
    
    async def _cleanup_old_backups(self):
        """Удаление старых резервных копий"""
        try:
            now = datetime.datetime.now()
            for backup_file in self.backup_dir.glob("auctions_*.db"):
                file_time = datetime.datetime.fromtimestamp(backup_file.stat().st_mtime)
                if (now - file_time).days > self.keep_days:
                    backup_file.unlink()
                    logger.info(f"Удален старый бэкап: {backup_file.name}")
        except Exception as e:
            logger.error(f"Ошибка при очистке старых бэкапов: {e}")
    
    async def schedule_backups(self, interval_hours: int = 24):
        """Планирование регулярных бэкапов"""
        while True:
            await asyncio.sleep(interval_hours * 3600)
            await self.create_backup()

# Глобальный экземпляр
backup_manager = DatabaseBackup()