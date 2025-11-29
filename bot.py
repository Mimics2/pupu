import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json
import pytz

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    filters,
    ContextTypes
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Московское время
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

def get_moscow_time():
    """Получить текущее время в Москве"""
    return datetime.now(MOSCOW_TZ)

def format_moscow_time(dt=None):
    """Форматировать время в Москве"""
    if dt is None:
        dt = get_moscow_time()
    return dt.strftime('%d.%m.%Y %H:%M')

class ChannelBot:
    def __init__(self, token: str):
        self.token = token
        self.application = Application.builder().token(token).build()
        self.channels: Dict[str, str] = {}
        self.scheduled_posts: List[Dict] = []
        self.setup_handlers()
    
    def setup_handlers(self):
        """Настройка обработчиков команд"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("time", self.current_time))
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        self.application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, self.message_handler))
    
    async def current_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать текущее время в Москве"""
        current_time = format_moscow_time()
        await update.message.reply_text(
            f"🕐 Текущее время в Москве:\n<b>{current_time}</b>",
            parse_mode="HTML"
        )
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        # Очищаем временные данные при старте
        if context.user_data:
            context.user_data.clear()
            
        current_time = format_moscow_time()
        
        keyboard = [
            [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
            [InlineKeyboardButton("📋 Список каналов", callback_data="list_channels")],
            [InlineKeyboardButton("📤 Создать пост", callback_data="create_post")],
            [InlineKeyboardButton("⏰ Запланированные посты", callback_data="scheduled_posts")],
            [InlineKeyboardButton("🕐 Текущее время Москва", callback_data="current_time")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.message:
            await update.message.reply_text(
                f"🤖 Бот для управления публикациями в каналах\n"
                f"🕐 Московское время: <b>{current_time}</b>\n\n"
                f"Выберите действие:",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        else:
            await update.callback_query.edit_message_text(
                f"🤖 Бот для управления публикациями в каналах\n"
                f"🕐 Московское время: <b>{current_time}</b>\n\n"
                f"Выберите действие:",
                parse_mode="HTML",
                reply_markup=reply_markup
            )
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == "add_channel":
            await self.add_channel_menu(query)
        elif data == "list_channels":
            await self.list_channels_menu(query)
        elif data == "create_post":
            await self.create_post_menu(query)
        elif data == "scheduled_posts":
            await self.scheduled_posts_menu(query)
        elif data == "current_time":
            await self.show_current_time(query)
        elif data.startswith("delete_channel_"):
            channel_id = data.replace("delete_channel_", "")
            await self.delete_channel(query, channel_id)
        elif data.startswith("select_channel_"):
            channel_id = data.replace("select_channel_", "")
            # Сохраняем выбранный канал в user_data
            context.user_data['selected_channel'] = channel_id
            context.user_data['waiting_for_content'] = True  # Флаг что ждем контент
            await self.select_time_menu(query, channel_id)
        elif data.startswith("time_"):
            time_minutes = int(data.replace("time_", ""))
            await self.schedule_post(query, time_minutes, context)
        elif data == "custom_time":
            await self.request_custom_time(query, context)
        elif data.startswith("cancel_post_"):
            post_id = data.replace("cancel_post_", "")
            await self.cancel_scheduled_post(query, post_id)
        elif data == "back_to_main":
            await self.start_from_query(query)
    
    async def show_current_time(self, query):
        """Показать текущее время"""
        current_time = format_moscow_time()
        await query.edit_message_text(
            f"🕐 Текущее время в Москве:\n<b>{current_time}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
            ])
        )
    
    async def start_from_query(self, query):
        """Старт из callback query"""
        current_time = format_moscow_time()
        keyboard = [
            [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
            [InlineKeyboardButton("📋 Список каналов", callback_data="list_channels")],
            [InlineKeyboardButton("📤 Создать пост", callback_data="create_post")],
            [InlineKeyboardButton("⏰ Запланированные посты", callback_data="scheduled_posts")],
            [InlineKeyboardButton("🕐 Текущее время Москва", callback_data="current_time")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🤖 Бот для управления публикациями в каналах\n"
            f"🕐 Московское время: <b>{current_time}</b>\n\n"
            f"Выберите действие:",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    
    async def add_channel_menu(self, query):
        """Меню добавления канала"""
        await query.edit_message_text(
            "📝 Чтобы добавить канал:\n\n"
            "1. Добавьте бота в канал как администратора\n"
            "2. Отправьте ID канала в формате:\n"
            "<code>@username_channel</code> или <code>-1001234567890</code>\n\n"
            "Отправьте ID канала:",
            parse_mode="HTML"
        )
    
    async def list_channels_menu(self, query):
        """Меню списка каналов"""
        if not self.channels:
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
            await query.edit_message_text(
                "📭 Нет добавленных каналов",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        text = "📋 Список каналов:\n\n"
        keyboard = []
        
        for channel_id, channel_name in self.channels.items():
            text += f"• {channel_name} (<code>{channel_id}</code>)\n"
            keyboard.append([
                InlineKeyboardButton(f"❌ Удалить {channel_name}", 
                                   callback_data=f"delete_channel_{channel_id}")
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def create_post_menu(self, query):
        """Меню создания поста"""
        if not self.channels:
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
            await query.edit_message_text(
                "❌ Сначала добавьте каналы",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        keyboard = []
        for channel_id, channel_name in self.channels.items():
            keyboard.append([
                InlineKeyboardButton(f"📢 {channel_name}", 
                                   callback_data=f"select_channel_{channel_id}")
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        await query.edit_message_text(
            "🎯 Выберите канал для публикации:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def select_time_menu(self, query, channel_id: str):
        """Меню выбора времени публикации"""
        channel_name = self.channels.get(channel_id, "Неизвестный канал")
        current_time = format_moscow_time()
        
        keyboard = [
            [InlineKeyboardButton("⏰ 15 минут", callback_data="time_15")],
            [InlineKeyboardButton("⏰ 30 минут", callback_data="time_30")],
            [InlineKeyboardButton("⏰ 1 час", callback_data="time_60")],
            [InlineKeyboardButton("⏰ 3 часа", callback_data="time_180")],
            [InlineKeyboardButton("⏰ 6 часов", callback_data="time_360")],
            [InlineKeyboardButton("⏰ 12 часов", callback_data="time_720")],
            [InlineKeyboardButton("⏰ 24 часа", callback_data="time_1440")],
            [InlineKeyboardButton("🕒 Другое время", callback_data="custom_time")],
            [InlineKeyboardButton("🕐 Текущее время", callback_data="current_time")],
            [InlineKeyboardButton("🔙 Назад", callback_data="create_post")]
        ]
        
        await query.edit_message_text(
            f"⏰ Выберите время публикации для канала <b>{channel_name}</b>\n"
            f"🕐 Текущее время в Москве: <b>{current_time}</b>\n\n"
            "Теперь отправьте сообщение (текст, фото, видео или документ) которое нужно опубликовать:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def request_custom_time(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Запрос пользовательского времени"""
        current_time = format_moscow_time()
        await query.edit_message_text(
            f"🕒 Введите время публикации в формате:\n"
            f"<code>ДД.ММ.ГГГГ-ЧЧ.ММ</code>\n\n"
            f"Пример: <code>27.11.2024-19.30</code>\n"
            f"🕐 Текущее время в Москве: <b>{current_time}</b>\n\n"
            f"Отправьте время в указанном формате:",
            parse_mode="HTML"
        )
        context.user_data['waiting_for_custom_time'] = True
    
    async def schedule_post(self, query, time_minutes: int, context: ContextTypes.DEFAULT_TYPE):
        """Планирование поста"""
        if 'post_data' not in context.user_data:
            await query.edit_message_text(
                "❌ Сначала отправьте сообщение для публикации",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data="create_post")]
                ])
            )
            return
        
        channel_id = context.user_data.get('selected_channel')
        if not channel_id:
            await query.edit_message_text(
                "❌ Канал не выбран",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data="create_post")]
                ])
            )
            return
        
        post_data = context.user_data['post_data']
        # Используем московское время для расчета
        schedule_time = get_moscow_time() + timedelta(minutes=time_minutes)
        
        await self._create_scheduled_post(query, context, post_data, channel_id, schedule_time)
    
    async def _create_scheduled_post(self, query, context, post_data, channel_id, schedule_time):
        """Создание запланированного поста"""
        post_id = f"post_{len(self.scheduled_posts)}_{datetime.now().timestamp()}"
        
        scheduled_post = {
            'id': post_id,
            'channel_id': channel_id,
            'channel_name': self.channels.get(channel_id, "Неизвестный канал"),
            'post_data': post_data,
            'scheduled_time': schedule_time.isoformat(),
            'scheduled_time_moscow': schedule_time.strftime('%d.%m.%Y %H:%M'),
            'status': 'scheduled'
        }
        
        self.scheduled_posts.append(scheduled_post)
        
        # Запуск задачи для отправки
        asyncio.create_task(self.send_scheduled_post(post_id, schedule_time))
        
        # Очистка временных данных
        context.user_data.pop('post_data', None)
        context.user_data.pop('selected_channel', None)
        context.user_data.pop('waiting_for_custom_time', None)
        context.user_data.pop('waiting_for_content', None)
        
        current_time = format_moscow_time()
        
        await query.edit_message_text(
            f"✅ Пост запланирован!\n\n"
            f"📢 Канал: <b>{scheduled_post['channel_name']}</b>\n"
            f"⏰ Время отправки: <b>{scheduled_post['scheduled_time_moscow']}</b>\n"
            f"🕐 Текущее время: <b>{current_time}</b>\n"
            f"📝 Тип: <b>{post_data.get('type', 'текст')}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 К запланированным", callback_data="scheduled_posts")],
                [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
            ])
        )
    
    async def scheduled_posts_menu(self, query):
        """Меню запланированных постов"""
        active_posts = [p for p in self.scheduled_posts if p.get('status') != 'sent']
        current_time = format_moscow_time()
        
        if not active_posts:
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
            await query.edit_message_text(
                f"⏰ Нет запланированных постов\n"
                f"🕐 Текущее время: <b>{current_time}</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        text = f"⏰ Запланированные посты:\n🕐 Текущее время: <b>{current_time}</b>\n\n"
        keyboard = []
        
        for post in active_posts[:10]:
            time_str = post.get('scheduled_time_moscow', 'Неизвестно')
            time_left = ""
            
            try:
                scheduled_dt = datetime.fromisoformat(post['scheduled_time']).replace(tzinfo=MOSCOW_TZ)
                now_moscow = get_moscow_time()
                if scheduled_dt > now_moscow:
                    delta = scheduled_dt - now_moscow
                    hours = delta.seconds // 3600
                    minutes = (delta.seconds % 3600) // 60
                    time_left = f" (осталось: {hours}ч {minutes}м)"
            except:
                pass
            
            text += (f"📢 {post['channel_name']}\n"
                    f"⏰ {time_str}{time_left}\n"
                    f"📝 {post['post_data'].get('type', 'текст')}\n\n")
            
            keyboard.append([
                InlineKeyboardButton(f"❌ Отменить пост", 
                                   callback_data=f"cancel_post_{post['id']}")
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def cancel_scheduled_post(self, query, post_id: str):
        """Отмена запланированного поста"""
        self.scheduled_posts = [post for post in self.scheduled_posts if post['id'] != post_id]
        
        await query.edit_message_text(
            "✅ Пост отменен",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 К запланированным", callback_data="scheduled_posts")]
            ])
        )
    
    async def delete_channel(self, query, channel_id: str):
        """Удаление канала"""
        if channel_id in self.channels:
            channel_name = self.channels[channel_id]
            del self.channels[channel_id]
            
            await query.edit_message_text(
                f"✅ Канал {channel_name} удален",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 К списку каналов", callback_data="list_channels")]
                ])
            )
    
    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик сообщений"""
        message = update.message
        
        # ДЕБАГ: Логируем состояние user_data
        logger.info(f"User data state: {context.user_data}")
        
        # Обработка пользовательского времени (только одна попытка)
        if context.user_data.get('waiting_for_custom_time'):
            time_str = message.text.strip()
            
            # Сразу очищаем флаг
            context.user_data.pop('waiting_for_custom_time', None)
            
            try:
                # Парсим время из формата ДД.ММ.ГГГГ-ЧЧ.ММ
                naive_dt = datetime.strptime(time_str, '%d.%m.%Y-%H.%M')
                # Делаем время московским
                schedule_time = MOSCOW_TZ.localize(naive_dt)
                
                current_time = get_moscow_time()
                if schedule_time <= current_time:
                    await message.reply_text(
                        f"❌ Время должно быть в будущем.\n"
                        f"🕐 Текущее время: <b>{format_moscow_time(current_time)}</b>",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                        ])
                    )
                    return
                
                # Продолжаем создание поста
                if 'post_data' in context.user_data and 'selected_channel' in context.user_data:
                    post_data = context.user_data['post_data']
                    channel_id = context.user_data['selected_channel']
                    channel_name = self.channels.get(channel_id, "Неизвестный канал")
                    
                    post_id = f"post_{len(self.scheduled_posts)}_{datetime.now().timestamp()}"
                    
                    scheduled_post = {
                        'id': post_id,
                        'channel_id': channel_id,
                        'channel_name': channel_name,
                        'post_data': post_data,
                        'scheduled_time': schedule_time.isoformat(),
                        'scheduled_time_moscow': schedule_time.strftime('%d.%m.%Y %H:%M'),
                        'status': 'scheduled'
                    }
                    
                    self.scheduled_posts.append(scheduled_post)
                    asyncio.create_task(self.send_scheduled_post(post_id, schedule_time))
                    
                    # Очистка временных данных
                    context.user_data.pop('post_data', None)
                    context.user_data.pop('selected_channel', None)
                    context.user_data.pop('waiting_for_content', None)
                    
                    current_time_str = format_moscow_time()
                    
                    await message.reply_text(
                        f"✅ Пост запланирован!\n\n"
                        f"📢 Канал: <b>{channel_name}</b>\n"
                        f"⏰ Время отправки: <b>{scheduled_post['scheduled_time_moscow']}</b>\n"
                        f"🕐 Текущее время: <b>{current_time_str}</b>\n"
                        f"📝 Тип: <b>{post_data.get('type', 'текст')}</b>",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("📋 К запланированным", callback_data="scheduled_posts")],
                            [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                        ])
                    )
                else:
                    await message.reply_text(
                        "❌ Ошибка: данные поста не найдены. Начните заново.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                        ])
                    )
                    
            except ValueError:
                current_time = format_moscow_time()
                await message.reply_text(
                    f"❌ Неверный формат времени. Используйте: <code>ДД.ММ.ГГГГ-ЧЧ.ММ</code>\n"
                    f"🕐 Текущее время: <b>{current_time}</b>\n\n"
                    f"Начните создание поста заново.",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                    ])
                )
            return
        
        # Обработка добавления канала
        if message.text and (message.text.startswith('@') or message.text.startswith('-100')):
            channel_id = message.text.strip()
            self.channels[channel_id] = channel_id
            
            await message.reply_text(
                f"✅ Канал {channel_id} добавлен!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                ])
            )
            return
        
        # ОСНОВНОЕ ИСПРАВЛЕНИЕ: Проверяем, ждем ли мы контент для поста
        if not context.user_data.get('waiting_for_content'):
            await message.reply_text(
                "❌ Сначала выберите канал для публикации через меню 'Создать пост'",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 Создать пост", callback_data="create_post")],
                    [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                ])
            )
            return
        
        # Сохраняем данные поста
        post_data = {}
        
        # Определяем тип контента
        if message.text and not (message.photo or message.video or message.document):
            # Только текст
            post_data = {
                'type': 'text',
                'text': message.text,
                'message_id': message.message_id,
                'chat_id': message.chat_id
            }
        elif message.photo:
            # Фото с текстом или без
            post_data = {
                'type': 'photo',
                'file_id': message.photo[-1].file_id,
                'caption': message.caption or '',
                'text': message.caption or '',  # Сохраняем текст подписи
                'message_id': message.message_id,
                'chat_id': message.chat_id
            }
        elif message.video:
            # Видео с текстом или без
            post_data = {
                'type': 'video',
                'file_id': message.video.file_id,
                'caption': message.caption or '',
                'text': message.caption or '',  # Сохраняем текст подписи
                'message_id': message.message_id,
                'chat_id': message.chat_id
            }
        elif message.document:
            # Документ с текстом или без
            post_data = {
                'type': 'document',
                'file_id': message.document.file_id,
                'caption': message.caption or '',
                'text': message.caption or '',  # Сохраняем текст подписи
                'message_id': message.message_id,
                'chat_id': message.chat_id
            }
        else:
            # Неизвестный тип сообщения
            await message.reply_text(
                "❌ Неподдерживаемый тип сообщения. Отправьте текст, фото, видео или документ.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                ])
            )
            return
        
        # Сохраняем данные поста
        context.user_data['post_data'] = post_data
        context.user_data['waiting_for_content'] = False  # Контент получен
        
        current_time = format_moscow_time()
        channel_id = context.user_data.get('selected_channel', 'Неизвестный канал')
        channel_name = self.channels.get(channel_id, "Неизвестный канал")
        
        # Информация о сохраненном контенте
        content_info = ""
        if post_data['type'] == 'text':
            content_info = f"📝 Текст: {post_data['text'][:50]}..."
        elif post_data['type'] in ['photo', 'video', 'document']:
            media_type = {'photo': '🖼 Фото', 'video': '🎥 Видео', 'document': '📎 Документ'}[post_data['type']]
            content_info = f"{media_type}"
            if post_data.get('text'):
                content_info += f" + текст: {post_data['text'][:50]}..."
        
        # Предлагаем выбрать время
        keyboard = [
            [InlineKeyboardButton("⏰ 15 минут", callback_data="time_15")],
            [InlineKeyboardButton("⏰ 30 минут", callback_data="time_30")],
            [InlineKeyboardButton("⏰ 1 час", callback_data="time_60")],
            [InlineKeyboardButton("⏰ 3 часа", callback_data="time_180")],
            [InlineKeyboardButton("⏰ 6 часов", callback_data="time_360")],
            [InlineKeyboardButton("⏰ 12 часов", callback_data="time_720")],
            [InlineKeyboardButton("⏰ 24 часа", callback_data="time_1440")],
            [InlineKeyboardButton("🕒 Другое время", callback_data="custom_time")],
            [InlineKeyboardButton("🕐 Текущее время", callback_data="current_time")],
            [InlineKeyboardButton("🔙 Назад", callback_data="create_post")]
        ]
        
        await message.reply_text(
            f"✅ Сообщение сохранено!\n"
            f"📢 Канал: <b>{channel_name}</b>\n"
            f"{content_info}\n"
            f"🕐 Текущее время: <b>{current_time}</b>\n\n"
            f"Теперь выберите время публикации:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def send_scheduled_post(self, post_id: str, schedule_time: datetime):
        """Отправка запланированного поста"""
        try:
            # Получаем текущее время в Москве
            now_moscow = get_moscow_time()
            
            # Если время уже прошло, отправляем сразу
            if schedule_time <= now_moscow:
                delay = 0
            else:
                # Ждем до указанного времени
                delay = (schedule_time - now_moscow).total_seconds()
            
            if delay > 0:
                logger.info(f"Ожидание {delay} секунд до отправки поста {post_id}")
                await asyncio.sleep(delay)
            
            # Находим пост
            post = next((p for p in self.scheduled_posts if p['id'] == post_id), None)
            if not post:
                logger.warning(f"Пост {post_id} не найден")
                return
            
            post_data = post['post_data']
            channel_id = post['channel_id']
            
            logger.info(f"Отправка поста {post_id} в канал {channel_id}")
            
            # Отправляем сообщение
            if post_data['type'] == 'text':
                await self.application.bot.send_message(
                    chat_id=channel_id,
                    text=post_data['text']
                )
            elif post_data['type'] == 'photo':
                await self.application.bot.send_photo(
                    chat_id=channel_id,
                    photo=post_data['file_id'],
                    caption=post_data.get('caption', '')
                )
            elif post_data['type'] == 'video':
                await self.application.bot.send_video(
                    chat_id=channel_id,
                    video=post_data['file_id'],
                    caption=post_data.get('caption', '')
                )
            elif post_data['type'] == 'document':
                await self.application.bot.send_document(
                    chat_id=channel_id,
                    document=post_data['file_id'],
                    caption=post_data.get('caption', '')
                )
            
            # Помечаем как отправленный
            post['status'] = 'sent'
            current_time = format_moscow_time()
            logger.info(f"Пост {post_id} успешно отправлен в {current_time}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки запланированного поста {post_id}: {e}")
            if post:
                post['status'] = 'error'

def main():
    """Основная функция запуска"""
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не установлен")
    
    bot = ChannelBot(BOT_TOKEN)
    print("Бот запущен с поддержкой московского времени...")
    bot.application.run_polling()

if __name__ == "__main__":
    main()
