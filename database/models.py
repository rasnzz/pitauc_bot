from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import json

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String(100))
    first_name = Column(String(100))
    last_name = Column(String(100))
    phone = Column(String(20))
    is_confirmed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Связи
    bids = relationship("Bid", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user")
    subscriptions = relationship("AuctionSubscription", back_populates="user")
    
    __table_args__ = (
        Index('ix_users_is_confirmed', 'is_confirmed'),
    )

class Auction(Base):
    __tablename__ = 'auctions'
    
    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    photos = Column(Text)
    start_price = Column(Float, nullable=False)
    step_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=False)
    status = Column(String(20), default='active')
    winner_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    channel_message_id = Column(Integer)
    last_bid_time = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    ends_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)  # ДОБАВЛЕНО
    
    # Связи
    winner = relationship("User", foreign_keys=[winner_id])
    bids = relationship("Bid", back_populates="auction", order_by="Bid.amount.desc()", cascade="all, delete-orphan")
    subscriptions = relationship("AuctionSubscription", back_populates="auction")
    notifications = relationship("Notification", back_populates="auction")
    
    __table_args__ = (
        Index('ix_auctions_status', 'status'),
        Index('ix_auctions_ends_at', 'ends_at'),
        Index('ix_auctions_last_bid_time', 'last_bid_time'),
        Index('ix_auctions_created_at', 'created_at'),
    )
    
    @property
    def photo_list(self):
        """Получить список фото из JSON строки"""
        if not self.photos:
            return []
        try:
            return json.loads(self.photos)
        except:
            return []
    
    @photo_list.setter
    def photo_list(self, value):
        """Установить список фото как JSON строку"""
        self.photos = json.dumps(value) if value else None

class Bid(Base):
    __tablename__ = 'bids'
    
    id = Column(Integer, primary_key=True)
    auction_id = Column(Integer, ForeignKey('auctions.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Связи
    auction = relationship("Auction", back_populates="bids")
    user = relationship("User", back_populates="bids")
    
    __table_args__ = (
        Index('ix_bids_auction_user', 'auction_id', 'user_id'),
        Index('ix_bids_amount', 'amount'),
        Index('ix_bids_created_at', 'created_at'),
    )

class AuctionSubscription(Base):
    __tablename__ = 'auction_subscriptions'
    
    id = Column(Integer, primary_key=True)
    auction_id = Column(Integer, ForeignKey('auctions.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Связи
    auction = relationship("Auction", back_populates="subscriptions")
    user = relationship("User", back_populates="subscriptions")
    
    __table_args__ = (
        Index('ix_subscriptions_auction_user', 'auction_id', 'user_id', unique=True),
    )

class Notification(Base):
    __tablename__ = 'notifications'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    auction_id = Column(Integer, ForeignKey('auctions.id'), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Связи
    user = relationship("User", back_populates="notifications")
    auction = relationship("Auction", back_populates="notifications")
    
    __table_args__ = (
        Index('ix_notifications_user_read', 'user_id', 'is_read'),
        Index('ix_notifications_created_at', 'created_at'),
    )