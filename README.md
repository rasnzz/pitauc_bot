# 🎰 Telegram Auction Bot

> **Пример реализации** Telegram-бота для проведения аукционов в канале.  
> Все упоминания магазина, контактов, правил и ссылок можно заменить на свои.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![aiogram](https://img.shields.io/badge/aiogram-3.10-green)](https://docs.aiogram.dev)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-red)](https://sqlalchemy.org)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

## ✨ Основные возможности

- ✅ **Создание аукционов** администратором (название, описание, фото, стартовая цена, шаг ставки)
- ✅ **Публикация в Telegram-канале** с кнопкой «Сделать ставку»
- ✅ **Автообновление сообщения** (текущая цена, таймер, топ‑3 ставок) без перезагрузки
- ✅ **Таймер аукциона** – если после последней ставки прошло заданное время (по умолчанию 3 часа), аукцион завершается автоматически
- ✅ **Уведомления**:
  - пользователю, которого перебили
  - подписчикам аукциона о новой ставке
  - победителю (с инструкцией по оплате и самовывозу)
- ✅ **Личный кабинет** – просмотр своих ставок, выигрышей, уведомлений
- ✅ **Административная панель** – управление аукционами, статистика, досрочное завершение, удаление
- ✅ **Автоматическое резервное копирование** базы данных (при запуске, остановке и каждые 24 часа)
- ✅ **Поддержка прокси** (SOCKS5/HTTP) и работы через WireGuard

## 🧱 Технологии

- Python 3.10+
- [aiogram 3.x](https://docs.aiogram.dev/) – асинхронный фреймворк
- [SQLAlchemy 2.0](https://www.sqlalchemy.org/) (asyncio) + aiosqlite – ORM и БД
- APScheduler – фоновые задачи (бэкапы)
- uvloop – высокопроизводительный event loop
- aiohttp + aiohttp-socks – поддержка прокси

## 🚀 Установка и запуск

### 1. Клонирование
```bash
git clone https://github.com/YOUR_USERNAME/pitauc_bot.git
cd pitauc_bot
```
2. Виртуальное окружение
```bash
python3 -m venv venv
source venv/bin/activate   # для Linux/macOS
# или .\venv\Scripts\activate для Windows
```
3. Зависимости
```bash
pip install --upgrade pip
pip install -r requirements.txt
```
4. Настройка .env
Создайте файл .env и заполните:

# Обязательные параметры
BOT_TOKEN=1234567890:AAH...           # Токен от @BotFather
CHANNEL_ID=@mychannel или -100123456  # ID канала (или username)
ADMIN_IDS=123456789,987654321         # ID администраторов через запятую

# Опциональные параметры (значения по умолчанию)
BID_TIMEOUT_MINUTES=180               # Таймаут аукциона (минут)
DATABASE_URL=sqlite+aiosqlite:///auctions.db
PROXY_URL=socks5://user:pass@ip:port  # Если нужен прокси
⚠️ Важно: бот должен быть администратором канала с правами на отправку сообщений и редактирование.

5. Запуск
```bash
python bot.py
```
