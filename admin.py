import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database.database import (
    get_tariffs, add_tariff, update_tariff, delete_tariff,
    get_all_monitored_channels, add_monitored_channel
)
from keyboards.admin_kb import (
    admin_main_keyboard, tariffs_manage_keyboard, 
    back_to_admin_keyboard, edit_tariff_keyboard
)

router = Router()

class TariffStates(StatesGroup):
    waiting_for_tariff_name = State()
    waiting_for_channel_link = State()
    waiting_for_channel_id = State()
    waiting_for_message_limit = State()
    waiting_for_tariff_duration = State()
    waiting_for_edit_tariff_id = State()
    waiting_for_edit_tariff_field = State()
    waiting_for_edit_tariff_value = State()

class ChannelStates(StatesGroup):
    waiting_for_channel_id = State()
    waiting_for_tariff_for_channel = State()

@router.message(Command("admin"))
async def admin_command(message: Message):
    # Замените на ID администраторов
    admin_ids = [123456789, 987654321]  # Ваши ID
    if message.from_user.id in admin_ids:
        await message.answer("👑 Админ панель:", reply_markup=admin_main_keyboard())
    else:
        await message.answer("❌ У вас нет доступа к админ панели.")

@router.callback_query(F.data == "manage_tariffs")
async def manage_tariffs(callback: CallbackQuery):
    tariffs = get_tariffs()
    if tariffs:
        text = "📊 Список тарифов:\n\n"
        for tariff in tariffs:
            text += f"🆔 ID: {tariff[0]}\n"
            text += f"📛 Название: {tariff[1]}\n"
            text += f"🔗 Ссылка: {tariff[2]}\n"
            text += f"📢 ID канала: {tariff[3] or 'Не указан'}\n"
            text += f"💬 Лимит: {tariff[4]} сообщений\n"
            text += f"⏳ Длительность: {tariff[5]} дней\n"
            text += "─" * 30 + "\n"
        await callback.message.edit_text(text, reply_markup=tariffs_manage_keyboard())
    else:
        await callback.message.edit_text("📭 Тарифов пока нет.", reply_markup=tariffs_manage_keyboard())

@router.callback_query(F.data == "add_tariff")
async def add_tariff_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Введите название нового тарифа:")
    await state.set_state(TariffStates.waiting_for_tariff_name)

@router.message(TariffStates.waiting_for_tariff_name)
async def process_tariff_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Введите публичную ссылку на канал (например: https://t.me/your_channel):")
    await state.set_state(TariffStates.waiting_for_channel_link)

@router.message(TariffStates.waiting_for_channel_link)
async def process_channel_link(message: Message, state: FSMContext):
    if not message.text.startswith(('http://', 'https://', 't.me/')):
        await message.answer("❌ Пожалуйста, введите корректную ссылку (начинающуюся с http://, https:// или t.me/):")
        return
    
    await state.update_data(channel_link=message.text)
    await message.answer("Введите ID канала (например: -1001234567890):\n\n"
                        "Чтобы получить ID канала:\n"
                        "1. Добавьте бота в канал как администратора\n"
                        "2. Перешлите любое сообщение из канала боту\n"
                        "3. Бот покажет ID канала")
    await state.set_state(TariffStates.waiting_for_channel_id)

@router.message(TariffStates.waiting_for_channel_id)
async def process_channel_id(message: Message, state: FSMContext):
    channel_id = message.text.strip()
    if not channel_id.startswith('-100'):
        await message.answer("❌ ID канала должен начинаться с '-100'. Пример: -1001234567890")
        return
    
    await state.update_data(channel_id=channel_id)
    await message.answer("Введите лимит сообщений для этого тарифа (число):")
    await state.set_state(TariffStates.waiting_for_message_limit)

@router.message(TariffStates.waiting_for_message_limit)
async def process_tariff_limit(message: Message, state: FSMContext):
    try:
        limit = int(message.text)
        if limit <= 0:
            raise ValueError
        await state.update_data(limit=limit)
        await message.answer("Введите длительность тарифа в днях (например, 30 для месяца):")
        await state.set_state(TariffStates.waiting_for_tariff_duration)
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число (больше 0):")

@router.message(TariffStates.waiting_for_tariff_duration)
async def process_tariff_duration(message: Message, state: FSMContext):
    try:
        duration = int(message.text)
        if duration <= 0:
            raise ValueError
        
        data = await state.get_data()
        add_tariff(
            data['name'], 
            data['channel_link'], 
            data['channel_id'], 
            data['limit'], 
            duration
        )
        
        await message.answer(
            f"✅ Тариф успешно добавлен!\n\n"
            f"📛 Название: {data['name']}\n"
            f"🔗 Ссылка: {data['channel_link']}\n"
            f"📢 ID канала: {data['channel_id']}\n"
            f"💬 Лимит сообщений: {data['limit']}\n"
            f"⏳ Длительность: {duration} дней\n\n"
            f"⚠️ Не забудьте:\n"
            f"1. Добавить бота в канал {data['channel_id']} как администратора\n"
            f"2. Дать боту права на просмотр участников\n"
            f"3. Добавить канал в мониторинг через '📢 Мониторинг каналов'",
            reply_markup=back_to_admin_keyboard()
        )
        await state.clear()
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число дней (больше 0):")

@router.callback_query(F.data == "edit_tariff")
async def edit_tariff_start(callback: CallbackQuery):
    await callback.message.edit_text(
        "Выберите, что хотите отредактировать:",
        reply_markup=edit_tariff_keyboard()
    )

@router.callback_query(F.data.startswith("edit_tariff_"))
async def edit_tariff_field(callback: CallbackQuery, state: FSMContext):
    field = callback.data.replace("edit_tariff_", "")
    field_names = {
        "name": "название",
        "link": "ссылку на канал",
        "limit": "лимит сообщений",
        "duration": "длительность"
    }
    
    if field in field_names:
        await state.update_data(edit_field=field)
        await callback.message.edit_text(
            f"Введите ID тарифа для редактирования {field_names[field]}:"
        )
        await state.set_state(TariffStates.waiting_for_edit_tariff_id)

@router.message(TariffStates.waiting_for_edit_tariff_id)
async def process_edit_tariff_id(message: Message, state: FSMContext):
    try:
        tariff_id = int(message.text)
        data = await state.get_data()
        field = data['edit_field']
        
        await state.update_data(tariff_id=tariff_id)
        
        field_prompts = {
            "name": "Введите новое название тарифа:",
            "link": "Введите новую ссылку на канал:",
            "limit": "Введите новый лимит сообщений:",
            "duration": "Введите новую длительность (в днях):"
        }
        
        await message.answer(field_prompts.get(field, "Введите новое значение:"))
        await state.set_state(TariffStates.waiting_for_edit_tariff_value)
    except ValueError:
        await message.answer("❌ Пожалуйста, введите числовой ID тарифа:")

@router.message(TariffStates.waiting_for_edit_tariff_value)
async def process_edit_tariff_value(message: Message, state: FSMContext):
    data = await state.get_data()
    tariff_id = data['tariff_id']
    field = data['edit_field']
    value = message.text
    
    # Валидация в зависимости от поля
    if field == "limit" or field == "duration":
        try:
            value = int(value)
            if value <= 0:
                raise ValueError
        except ValueError:
            await message.answer("❌ Пожалуйста, введите корректное число (больше 0):")
            return
    elif field == "link" and not value.startswith(('http://', 'https://', 't.me/')):
        await message.answer("❌ Пожалуйста, введите корректную ссылку:")
        return
    
    update_tariff(tariff_id, field, value)
    await message.answer(f"✅ Тариф #{tariff_id} успешно обновлен!")
    await state.clear()

@router.callback_query(F.data == "delete_tariff")
async def delete_tariff_start(callback: CallbackQuery):
    await callback.message.edit_text("Введите ID тарифа для удаления:")

@router.message(F.text.regexp(r'^\d+$'))
async def delete_tariff_process(message: Message):
    try:
        tariff_id = int(message.text)
        delete_tariff(tariff_id)
        await message.answer(f"✅ Тариф #{tariff_id} отмечен как неактивный.")
    except ValueError:
        await message.answer("❌ Пожалуйста, введите числовой ID тарифа:")

@router.callback_query(F.data == "monitor_channels")
async def monitor_channels(callback: CallbackQuery, state: FSMContext):
    channels = get_all_monitored_channels()
    
    if channels:
        text = "📢 Отслеживаемые каналы:\n\n"
        for channel in channels:
            text += f"📢 Канал: {channel[2] or 'Без имени'}\n"
            text += f"🆔 ID: {channel[0]}\n"
            text += f"📛 Тариф: {channel[2]}\n"
            text += "─" * 30 + "\n"
    else:
        text = "📭 Нет отслеживаемых каналов."
    
    text += "\nЧтобы добавить канал для мониторинга, введите его ID (начинается с -100):"
    
    await callback.message.edit_text(text)
    await state.set_state(ChannelStates.waiting_for_channel_id)

@router.message(ChannelStates.waiting_for_channel_id)
async def process_channel_for_monitoring(message: Message, state: FSMContext):
    channel_id = message.text.strip()
    
    if not channel_id.startswith('-100'):
        await message.answer("❌ ID канала должен начинаться с '-100'")
        return
    
    await state.update_data(channel_id=channel_id)
    
    # Получаем список тарифов для выбора
    tariffs = get_tariffs()
    if not tariffs:
        await message.answer("❌ Нет доступных тарифов. Сначала создайте тариф.")
        await state.clear()
        return
    
    text = "Выберите тариф для этого канала:\n\n"
    for tariff in tariffs:
        text += f"{tariff[0]}. {tariff[1]} (ID канала: {tariff[3]})\n"
    
    await message.answer(text)
    await state.set_state(ChannelStates.waiting_for_tariff_for_channel)

@router.message(ChannelStates.waiting_for_tariff_for_channel)
async def process_tariff_for_channel(message: Message, state: FSMContext):
    try:
        tariff_id = int(message.text)
        data = await state.get_data()
        channel_id = data['channel_id']
        
        # Проверяем существование тарифа
        from database.database import get_tariff_by_id
        tariff = get_tariff_by_id(tariff_id)
        
        if not tariff:
            await message.answer("❌ Тариф не найден.")
            await state.clear()
            return
        
        # Добавляем канал в мониторинг
        add_monitored_channel(channel_id, tariff_id, "")
        
        await message.answer(
            f"✅ Канал {channel_id} добавлен в мониторинг!\n"
            f"📛 Привязан к тарифу: {tariff[1]}\n\n"
            f"📋 Теперь бот будет:\n"
            f"1. Проверять подписку пользователей на этот канал\n"
            f"2. Выдавать тариф при успешной проверке\n"
            f"3. Автоматически исключать через {tariff[5]} дней"
        )
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите числовой ID тарифа:")
