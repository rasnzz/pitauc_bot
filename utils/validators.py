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
        """Валидация цены"""
        try:
            price = float(price_str.replace(',', '.'))
            
            if price <= 0:
                return False, None, "Цена должна быть положительной"
            
            if price > 1_000_000_000:  # 1 миллиард
                return False, None, "Цена слишком большая"
            
            # Проверка на разумность (не более 2 знаков после запятой)
            if round(price, 2) != price:
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

class UserValidator:
    @staticmethod
    def validate_username(username: Optional[str]) -> str:
        """Валидация имени пользователя"""
        if not username:
            return "Аноним"
        
        # Очистка от потенциально опасных символов
        cleaned = re.sub(r'[<>\[\]{}]', '', username)
        
        return cleaned[:50]  # Ограничение длины