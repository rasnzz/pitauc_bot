# utils/validators.py - ЗАМЕНИТЕ ВЕСЬ ФАЙЛ

import re
from typing import Tuple, Optional

class AuctionValidator:
    @staticmethod
    def validate_title(title: str) -> Tuple[bool, str]:
        """Валидация названия аукциона"""
        if not title or len(title.strip()) == 0:
            return False, "Название не может быть пустым"
        
        if len(title) > 255:
            return False, "Название слишком длинное (макс. 255 символов)"
        
        if len(title) < 5:
            return False, "Название слишком короткое (мин. 5 символов)"
        
        # Проверка на запрещенные символы
        forbidden_pattern = r'[<>\[\]{}]'
        if re.search(forbidden_pattern, title):
            return False, "Название содержит запрещенные символы"
        
        return True, "OK"
    
    @staticmethod
    def validate_price(price_str: str) -> Tuple[bool, Optional[float], str]:
        """Валидация цены - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        if not price_str:
            return False, None, "Цена не может быть пустой"
        
        try:
            # Удаляем все пробелы (поддержка формата "1 000" или "1 000.50")
            cleaned = price_str.replace(' ', '')
            
            # Заменяем запятую на точку (поддержка "1,000" или "1,000.50")
            cleaned = cleaned.replace(',', '.')
            
            # Проверяем, что остались только цифры и одна точка
            if not cleaned.replace('.', '').isdigit():
                return False, None, "Неверный формат цены. Используйте цифры и точку (например: 1000 или 1000.50)"
            
            # Преобразуем в число
            price = float(cleaned)
            
            if price <= 0:
                return False, None, "Цена должна быть положительной"
            
            if price > 1_000_000_000:  # 1 миллиард
                return False, None, "Цена слишком большая (максимум 1 000 000 000 ₽)"
            
            # Проверяем, что не более 2 знаков после запятой
            if abs(price - round(price, 2)) > 0.001:
                return False, None, "Используйте максимум 2 знака после запятой"
            
            return True, price, "OK"
            
        except ValueError:
            return False, None, "Неверный формат цены"
    
    @staticmethod
    def validate_step_price(start_price: float, step_price: float) -> Tuple[bool, str]:
        """Валидация шага ставки относительно стартовой цены"""
        if step_price <= 0:
            return False, "Шаг ставки должен быть положительным"
        
        if step_price > start_price * 10:
            return False, "Шаг ставки слишком большой (макс. 10х от стартовой цены)"
        
        if step_price < start_price * 0.01:
            return False, "Шаг ставки слишком маленький (мин. 1% от стартовой цены)"
        
        return True, "OK"

class BidValidator:
    @staticmethod
    def validate_bid_amount(amount: str) -> Tuple[bool, Optional[float], str]:
        """Валидация суммы ставки из строки"""
        return AuctionValidator.validate_price(amount)
    
    @staticmethod
    def validate_bid_amount_numeric(amount: float, current_price: float, step_price: float) -> Tuple[bool, str]:
        """Валидация суммы ставки (уже числовой)"""
        # Проверяем, что ставка не меньше минимальной
        min_bid = current_price + step_price
        
        if amount < min_bid:
            return False, f"Минимальная ставка: {min_bid:.2f} ₽"
        
        # Проверяем, что ставка не слишком большая
        if amount > 1_000_000_000:
            return False, "Слишком большая ставка (максимум 1 000 000 000 ₽)"
        
        return True, "OK"

class UserValidator:
    @staticmethod
    def validate_username(username: Optional[str]) -> str:
        """Валидация имени пользователя"""
        if not username:
            return "Аноним"
        
        # Очистка от потенциально опасных символов
        cleaned = re.sub(r'[<>\[\]{}]', '', username)
        
        return cleaned[:50]  # Ограничение длины
