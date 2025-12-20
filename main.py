import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from handlers import admin, subscription
from database.database import init_db
from handlers.subscription import check_expired_subscriptions

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Токен вашего бота
BOT_TOKEN = "YOUR_BOT_TOKEN"

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Регистрация роутеров
dp.include_router(admin.router)
dp.include_router(subscription.router)

async def main():
    # Инициализация базы данных
    init_db()
    
    # Запуск фоновой задачи для проверки подписок
    asyncio.create_task(check_expired_subscriptions())
    
    logging.info("Бот запущен!")
    
    # Запуск поллинга
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
