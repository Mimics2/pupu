import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from datetime import datetime, timedelta
import asyncio

from database.database import (
    get_user, update_user_tariff, get_tariff_by_channel_id,
    get_all_monitored_channels, check_subscription_expiry
)
from keyboards.user_kb import subscription_keyboard

router = Router()

@router.message(Command("start"))
async def start_command(message: Message):
    from database.database import add_user
    add_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    
    user = get_user(message.from_user.id)
    
    if user and user[3]:  # Если есть активный тариф
        await message.answer(
            f"👋 Привет, {message.from_user.first_name}!\n"
            f"✅ У вас есть активная подписка!\n"
            f"📊 Осталось сообщений: {user[4]}\n"
            f"📅 Подписка до: {user[5]}"
        )
    else:
        await message.answer(
            f"👋 Привет, {message.from_user.first_name}!\n"
            "🤖 Я бот для управления доступом к приватным каналам.\n\n"
            "📋 Чтобы получить доступ:\n"
            "1. Подпишитесь на один из платных каналов\n"
            "2. Нажмите кнопку 'Проверить подписку'\n"
            "3. Получите доступ к функциям бота",
            reply_markup=subscription_keyboard()
        )

@router.callback_query(F.data == "check_subscription")
async def check_subscription(callback: CallbackQuery):
    await callback.message.edit_text(
        "🔍 Проверяю ваши подписки...\n\n"
        "Бот проверяет все отслеживаемые каналы. Это может занять несколько секунд."
    )
    
    channels = get_all_monitored_channels()
    if not channels:
        await callback.message.edit_text(
            "❌ Нет доступных каналов для проверки.\n"
            "Обратитесь к администратору."
        )
        return
    
    user_id = callback.from_user.id
    
    # Здесь должна быть логика проверки подписки через Telegram API
    # Для примера - просто проверяем первый канал
    
    if channels:
        channel = channels[0]
        tariff = get_tariff_by_channel_id(channel[0])
        
        if tariff:
            # Обновляем тариф пользователя
            update_user_tariff(user_id, tariff[0])
            
            await callback.message.edit_text(
                f"✅ Подписка подтверждена!\n\n"
                f"🎉 Вы получили доступ к тарифу: {tariff[1]}\n"
                f"💬 Лимит сообщений: {tariff[4]}\n"
                f"⏳ Длительность: {tariff[5]} дней\n"
                f"📅 Доступ до: {(datetime.now() + timedelta(days=tariff[5])).strftime('%Y-%m-%d')}\n\n"
                f"Теперь вы можете пользоваться функциями бота!"
            )
        else:
            await callback.message.edit_text(
                "❌ Вы не подписаны ни на один платный канал.\n\n"
                "📋 Доступные каналы можно посмотреть через команду /tariffs"
            )

@router.message(Command("tariffs"))
async def show_tariffs(message: Message):
    from database.database import get_tariffs
    
    tariffs = get_tariffs()
    if tariffs:
        text = "📋 Доступные тарифы:\n\n"
        for tariff in tariffs:
            text += f"📛 {tariff[1]}\n"
            text += f"🔗 Ссылка: {tariff[2]}\n"
            text += f"💬 Лимит: {tariff[4]} сообщений\n"
            text += f"⏳ Срок: {tariff[5]} дней\n"
            text += "─" * 30 + "\n"
        
        text += "\n📌 Для получения доступа:\n"
        text += "1. Подпишитесь на канал по ссылке\n"
        text += "2. Нажмите 'Проверить подписку' в меню"
    else:
        text = "📭 Нет доступных тарифов."
    
    await message.answer(text)

@router.message(Command("my_subscription"))
async def my_subscription(message: Message):
    user = get_user(message.from_user.id)
    
    if user and user[3]:
        tariff_id = user[3]
        from database.database import get_tariff_by_id
        tariff = get_tariff_by_id(tariff_id)
        
        if tariff:
            await message.answer(
                f"📋 Ваша подписка:\n\n"
                f"📛 Тариф: {tariff[1]}\n"
                f"💬 Осталось сообщений: {user[4]}\n"
                f"📅 Подписка до: {user[5]}\n"
                f"🔗 Канал: {tariff[2]}"
            )
        else:
            await message.answer("❌ Информация о тарифе не найдена.")
    else:
        await message.answer(
            "❌ У вас нет активной подписки.\n"
            "Используйте кнопку 'Проверить подписку' для получения доступа."
        )

# Функция для периодической проверки истекших подписок
async def check_expired_subscriptions():
    while True:
        try:
            expired_users = check_subscription_expiry()
            
            if expired_users:
                logging.info(f"Found {len(expired_users)} expired subscriptions")
                # Здесь можно добавить логику исключения из каналов
                # через Telegram API
            
            # Проверяем раз в час
            await asyncio.sleep(3600)
            
        except Exception as e:
            logging.error(f"Error checking expired subscriptions: {e}")
            await asyncio.sleep(300)
