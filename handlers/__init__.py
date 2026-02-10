# handlers/__init__.py
from .user import router as user_router
from .admin import router as admin_router
from .auction import router as auction_router

__all__ = ['user_router', 'admin_router', 'auction_router']
