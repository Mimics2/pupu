import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json
import redis.asyncio as redis

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
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379')

class Database:
    def __init__(self):
        self.redis = None
    
    async def connect(self):
        """Подключение к Redis"""
        self.redis = await redis.from_url(REDIS_URL, decode_responses=True)
    
    async def get_user_channels(self, user_id: int) -> Dict[str, str]:
        """Получить каналы пользователя"""
        key = f"user:{user_id}:channels"
        data = await self.redis.get(key)
        return json.loads(data) if data else {}
    
    async def save_user_channels(self, user_id: int, channels: Dict[str, str]):
        """Сохранить каналы пользователя"""
        key = f"user:{user_id}:channels"
        await self.redis.set(key, json.dumps(channels))
    
    async def get_scheduled_posts(self, user_id: int) -> List[Dict]:
        """Получить запланированные посты пользователя"""
        key = f"user:{user_id}:scheduled_posts"
        data = await self.redis.get(key)
        return json.loads(data) if data else []
    
    async def save_scheduled_posts(self, user_id: int, posts: List[Dict]):
        """Сохранить запланированные посты пользователя"""
        key = f"user:{user_id}:scheduled_posts"
        await self.redis.set(key, json.dumps(posts))
    
    async def add_scheduled_post(self, user_id: int, post: Dict):
        """Добавить запланированный пост"""
        posts = await self.get_scheduled_posts(user_id)
        posts.append(post)
        await self.save_scheduled_posts(user_id, posts)
        return post
    
    async def remove_scheduled_post(self, user_id: int, post_id: str):
        """Удалить запланированный пост"""
        posts = await self.get_scheduled_posts(user_id)
        posts = [p for p in posts if p['id'] != post_id]
        await self.save_scheduled_posts(user_id, posts)

class ChannelBot:
    def __init__(self, token: str):
        self.token = token
        self.application = Application.builder().token(token).build()
        self.db = Database()
        self.setup_handlers()
    
    async def initialize(self):
        """Инициализация бота"""
        await self.db.connect()
    
    def setup_handlers(self):
        """Настройка обработчиков команд"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        self.application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, self.message_handler))
        self.application.add_error_handler(self.error_handler)
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок"""
        logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user = update.effective_user
        keyboard = [
            [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
            [InlineKeyboardButton("📋 Мои каналы", callback_data="list_channels")],
            [InlineKeyboardButton("📤 Создать пост", callback_data="create_post")],
            [InlineKeyboardButton("⏰ Мои запланированные посты", callback_data="scheduled_posts")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"🤖 Бот для управления публикациями в каналах\n"
            f"👤 Пользователь: {user.first_name}\n\n"
            f"Выберите действие:",
            reply_markup=reply_markup
        )
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        data = query.data
        
        try:
            if data == "add_channel":
                await self.add_channel_menu(query)
            elif data == "list_channels":
                await self.list_channels_menu(query, user_id)
            elif data == "create_post":
                await self.create_post_menu(query, user_id)
            elif data == "scheduled_posts":
                await self.scheduled_posts_menu(query, user_id)
            elif data.startswith("delete_channel_"):
                channel_id = data.replace("delete_channel_", "")
                await self.delete_channel(query, user_id, channel_id)
            elif data.startswith("select_channel_"):
                channel_id = data.replace("select_channel_", "")
                context.user_data['selected_channel'] = channel_id
                await self.select_time_menu(query, user_id, channel_id)
            elif data.startswith("time_"):
                time_minutes = int(data.replace("time_", ""))
                await self.schedule_post(query, user_id, time_minutes, context)
            elif data == "custom_time":
                await self.request_custom_time(query, context)
            elif data.startswith("cancel_post_"):
                post_id = data.replace("cancel_post_", "")
                await self.cancel_scheduled_post(query, user_id, post_id)
            elif data == "back_to_main":
                await self.start_from_query(query, update.effective_user)
                
        except Exception as e:
            logger.error(f"Ошибка у пользователя {user_id}: {e}")
            await query.edit_message_text(
                "❌ Произошла ошибка. Попробуйте еще раз.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                ])
            )
    
    async def start_from_query(self, query, user):
        """Старт из callback query"""
        keyboard = [
            [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
            [InlineKeyboardButton("📋 Мои каналы", callback_data="list_channels")],
            [InlineKeyboardButton("📤 Создать пост", callback_data="create_post")],
            [InlineKeyboardButton("⏰ Мои запланированные посты", callback_data="scheduled_posts")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🤖 Бот для управления публикациями в каналах\n"
            f"👤 Пользователь: {user.first_name}\n\n"
            f"Выберите действие:",
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
    
    async def list_channels_menu(self, query, user_id: int):
        """Меню списка каналов пользователя"""
        channels = await self.db.get_user_channels(user_id)
        
        if not channels:
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
            await query.edit_message_text(
                "📭 У вас нет добавленных каналов",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        text = "📋 Ваши каналы:\n\n"
        keyboard = []
        
        for channel_id, channel_name in channels.items():
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
    
    async def create_post_menu(self, query, user_id: int):
        """Меню создания поста"""
        channels = await self.db.get_user_channels(user_id)
        
        if not channels:
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
            await query.edit_message_text(
                "❌ Сначала добавьте каналы",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        keyboard = []
        for channel_id, channel_name in channels.items():
            keyboard.append([
                InlineKeyboardButton(f"📢 {channel_name}", 
                                   callback_data=f"select_channel_{channel_id}")
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        await query.edit_message_text(
            "🎯 Выберите канал для публикации:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def select_time_menu(self, query, user_id: int, channel_id: str):
        """Меню выбора времени публикации"""
        channels = await self.db.get_user_channels(user_id)
        channel_name = channels.get(channel_id, "Неизвестный канал")
        
        keyboard = [
            [InlineKeyboardButton("⏰ 15 минут", callback_data="time_15")],
            [InlineKeyboardButton("⏰ 30 минут", callback_data="time_30")],
            [InlineKeyboardButton("⏰ 1 час", callback_data="time_60")],
            [InlineKeyboardButton("⏰ 3 часа", callback_data="time_180")],
            [InlineKeyboardButton("⏰ 6 часов", callback_data="time_360")],
            [InlineKeyboardButton("⏰ 12 часов", callback_data="time_720")],
            [InlineKeyboardButton("⏰ 24 часа", callback_data="time_1440")],
            [InlineKeyboardButton("🕒 Другое время", callback_data="custom_time")],
            [InlineKeyboardButton("🔙 Назад", callback_data="create_post")]
        ]
        
        await query.edit_message_text(
            f"⏰ Выберите время публикации для канала <b>{channel_name}</b>\n\n"
            "Теперь отправьте сообщение (текст, фото, видео или документ) которое нужно опубликовать:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def request_custom_time(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Запрос пользовательского времени"""
        await query.edit_message_text(
            "🕒 Введите время публикации в формате:\n"
            "<code>ДД.ММ.ГГГГ-ЧЧ.ММ</code>\n\n"
            "Пример: <code>27.11.2024-19.30</code>\n\n"
            "Отправьте время в указанном формате:",
            parse_mode="HTML"
        )
        context.user_data['waiting_for_custom_time'] = True
    
    async def schedule_post(self, query, user_id: int, time_minutes: int, context: ContextTypes.DEFAULT_TYPE):
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
        schedule_time = datetime.now() + timedelta(minutes=time_minutes)
        
        await self._create_scheduled_post(query, user_id, context, post_data, channel_id, schedule_time)
    
    async def _create_scheduled_post(self, query, user_id: int, context: ContextTypes.DEFAULT_TYPE, 
                                   post_data: Dict, channel_id: str, schedule_time: datetime):
        """Создание запланированного поста"""
        try:
            channels = await self.db.get_user_channels(user_id)
            channel_name = channels.get(channel_id, "Неизвестный канал")
            
            post_id = f"post_{user_id}_{int(datetime.now().timestamp())}"
            
            scheduled_post = {
                'id': post_id,
                'user_id': user_id,
                'channel_id': channel_id,
                'channel_name': channel_name,
                'post_data': post_data,
                'scheduled_time': schedule_time.isoformat(),
                'status': 'scheduled'
            }
            
            await self.db.add_scheduled_post(user_id, scheduled_post)
            
            # Запуск задачи для отправки
            asyncio.create_task(self.send_scheduled_post(user_id, post_id, schedule_time))
            
            # Очистка временных данных
            context.user_data.pop('post_data', None)
            context.user_data.pop('selected_channel', None)
            context.user_data.pop('waiting_for_custom_time', None)
            
            await query.edit_message_text(
                f"✅ Пост запланирован!\n\n"
                f"📢 Канал: <b>{channel_name}</b>\n"
                f"⏰ Время отправки: <b>{schedule_time.strftime('%d.%m.%Y %H:%M')}</b>\n"
                f"📝 Тип: <b>{post_data.get('type', 'текст')}</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 К запланированным", callback_data="scheduled_posts")],
                    [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                ])
            )
            
        except Exception as e:
            logger.error(f"Ошибка создания поста для пользователя {user_id}: {e}")
            await query.edit_message_text(
                "❌ Ошибка при создании поста",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                ])
            )
    
    async def scheduled_posts_menu(self, query, user_id: int):
        """Меню запланированных постов пользователя"""
        posts = await self.db.get_scheduled_posts(user_id)
        active_posts = [p for p in posts if p.get('status') == 'scheduled']
        
        if not active_posts:
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
            await query.edit_message_text(
                "⏰ У вас нет запланированных постов",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        text = "⏰ Ваши запланированные посты:\n\n"
        keyboard = []
        
        for post in active_posts[:10]:
            scheduled_time = datetime.fromisoformat(post['scheduled_time'])
            time_str = scheduled_time.strftime('%d.%m.%Y %H:%M')
            
            text += (f"📢 {post['channel_name']}\n"
                    f"⏰ {time_str}\n"
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
    
    async def cancel_scheduled_post(self, query, user_id: int, post_id: str):
        """Отмена запланированного поста"""
        await self.db.remove_scheduled_post(user_id, post_id)
        
        await query.edit_message_text(
            "✅ Пост отменен",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 К запланированным", callback_data="scheduled_posts")]
            ])
        )
    
    async def delete_channel(self, query, user_id: int, channel_id: str):
        """Удаление канала"""
        channels = await self.db.get_user_channels(user_id)
        if channel_id in channels:
            channel_name = channels[channel_id]
            del channels[channel_id]
            await self.db.save_user_channels(user_id, channels)
            
            await query.edit_message_text(
                f"✅ Канал {channel_name} удален",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 К списку каналов", callback_data="list_channels")]
                ])
            )
    
    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик сообщений"""
        message = update.message
        user_id = update.effective_user.id
        
        try:
            # Обработка пользовательского времени (только одна попытка)
            if context.user_data.get('waiting_for_custom_time'):
                time_str = message.text.strip()
                
                # Сразу очищаем флаг
                context.user_data.pop('waiting_for_custom_time', None)
                
                try:
                    # Парсим время из формата ДД.ММ.ГГГГ-ЧЧ.ММ
                    schedule_time = datetime.strptime(time_str, '%d.%m.%Y-%H.%M')
                    
                    if schedule_time <= datetime.now():
                        await message.reply_text(
                            "❌ Время должно быть в будущем. Начните создание поста заново.",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                            ])
                        )
                        return
                    
                    # Продолжаем создание поста
                    if 'post_data' in context.user_data and 'selected_channel' in context.user_data:
                        post_data = context.user_data['post_data']
                        channel_id = context.user_data['selected_channel']
                        channels = await self.db.get_user_channels(user_id)
                        channel_name = channels.get(channel_id, "Неизвестный канал")
                        
                        post_id = f"post_{user_id}_{int(datetime.now().timestamp())}"
                        
                        scheduled_post = {
                            'id': post_id,
                            'user_id': user_id,
                            'channel_id': channel_id,
                            'channel_name': channel_name,
                            'post_data': post_data,
                            'scheduled_time': schedule_time.isoformat(),
                            'status': 'scheduled'
                        }
                        
                        await self.db.add_scheduled_post(user_id, scheduled_post)
                        asyncio.create_task(self.send_scheduled_post(user_id, post_id, schedule_time))
                        
                        # Очистка временных данных
                        context.user_data.pop('post_data', None)
                        context.user_data.pop('selected_channel', None)
                        
                        await message.reply_text(
                            f"✅ Пост запланирован!\n\n"
                            f"📢 Канал: <b>{channel_name}</b>\n"
                            f"⏰ Время отправки: <b>{schedule_time.strftime('%d.%m.%Y %H:%M')}</b>\n"
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
                    await message.reply_text(
                        "❌ Неверный формат времени. Используйте формат: <code>ДД.ММ.ГГГГ-ЧЧ.ММ</code>\n\n"
                        "Начните создание поста заново.",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                        ])
                    )
                return
            
            # Обработка добавления канала
            if message.text and (message.text.startswith('@') or message.text.startswith('-100')):
                channel_id = message.text.strip()
                channels = await self.db.get_user_channels(user_id)
                channels[channel_id] = channel_id
                await self.db.save_user_channels(user_id, channels)
                
                await message.reply_text(
                    f"✅ Канал {channel_id} добавлен!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                    ])
                )
                return
            
            # Сохраняем данные поста
            post_data = {
                'type': 'text',
                'text': message.text or '',
                'message_id': message.message_id,
                'chat_id': message.chat_id
            }
            
            # Обработка медиа
            if message.photo:
                post_data.update({
                    'type': 'photo',
                    'file_id': message.photo[-1].file_id,
                    'caption': message.caption or ''
                })
            elif message.video:
                post_data.update({
                    'type': 'video', 
                    'file_id': message.video.file_id,
                    'caption': message.caption or ''
                })
            elif message.document:
                post_data.update({
                    'type': 'document',
                    'file_id': message.document.file_id,
                    'caption': message.caption or ''
                })
            
            context.user_data['post_data'] = post_data
            
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
                [InlineKeyboardButton("🔙 Назад", callback_data="create_post")]
            ]
            
            await message.reply_text(
                "✅ Сообщение сохранено! Теперь выберите время публикации:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        except Exception as e:
            logger.error(f"Ошибка в message_handler для пользователя {user_id}: {e}")
            await message.reply_text(
                "❌ Произошла ошибка. Попробуйте еще раз.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                ])
            )
    
    async def send_scheduled_post(self, user_id: int, post_id: str, schedule_time: datetime):
        """Отправка запланированного поста"""
        try:
            # Ожидаем время отправки
            delay = (schedule_time - datetime.now()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            
            # Находим пост в базе
            posts = await self.db.get_scheduled_posts(user_id)
            post = next((p for p in posts if p['id'] == post_id and p.get('status') == 'scheduled'), None)
            
            if not post:
                return
            
            post_data = post['post_data']
            channel_id = post['channel_id']
            
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
            await self.db.save_scheduled_posts(user_id, posts)
            
            logger.info(f"Пост {post_id} пользователя {user_id} отправлен в канал {channel_id}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки поста {post_id} пользователя {user_id}: {e}")
            # Помечаем как ошибку
            posts = await self.db.get_scheduled_posts(user_id)
            for p in posts:
                if p['id'] == post_id:
                    p['status'] = 'error'
                    break
            await self.db.save_scheduled_posts(user_id, posts)

async def main():
    """Основная функция запуска"""
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не установлен")
    
    bot = ChannelBot(BOT_TOKEN)
    await bot.initialize()
    
    print("Бот запущен с поддержкой множества пользователей...")
    bot.application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
