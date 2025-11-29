import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json

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

# Конфигурация из переменных окружения
BOT_TOKEN = os.getenv('BOT_TOKEN')
PORT = int(os.getenv('PORT', 8443))
WEBHOOK_URL = os.getenv('WEBHOOK_URL')

class ChannelBot:
    def __init__(self, token: str):
        self.token = token
        self.application = Application.builder().token(token).build()
        self.channels: Dict[str, str] = {}
        self.scheduled_posts: List[Dict] = []
        self.load_data()
        self.setup_handlers()
    
    def load_data(self):
        """Загрузка данных из переменных окружения Railway"""
        try:
            channels_data = os.getenv('CHANNELS_DATA')
            posts_data = os.getenv('SCHEDULED_POSTS')
            
            if channels_data:
                self.channels = json.loads(channels_data)
            if posts_data:
                self.scheduled_posts = json.loads(posts_data)
                
            # Восстановление задач для запланированных постов
            for post in self.scheduled_posts:
                if post.get('status') == 'scheduled':
                    scheduled_time = datetime.fromisoformat(post['scheduled_time'])
                    if scheduled_time > datetime.now():
                        asyncio.create_task(self.send_scheduled_post(post['id'], scheduled_time))
                        
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
    
    def save_data(self):
        """Сохранение данных в переменные окружения Railway"""
        try:
            logger.info(f"CHANNELS_DATA: {json.dumps(self.channels)}")
            logger.info(f"SCHEDULED_POSTS: {json.dumps(self.scheduled_posts)}")
        except Exception as e:
            logger.error(f"Ошибка сохранения данных: {e}")
    
    def setup_handlers(self):
        """Настройка обработчиков команд"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        self.application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, self.message_handler))
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        keyboard = [
            [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
            [InlineKeyboardButton("📋 Список каналов", callback_data="list_channels")],
            [InlineKeyboardButton("📤 Создать пост", callback_data="create_post")],
            [InlineKeyboardButton("⏰ Запланированные посты", callback_data="scheduled_posts")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.message:
            await update.message.reply_text(
                "🤖 Бот для управления публикациями в каналах\n\n"
                "Выберите действие:",
                reply_markup=reply_markup
            )
        else:
            await update.callback_query.edit_message_text(
                "🤖 Бот для управления публикациями в каналах\n\n"
                "Выберите действие:",
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
        elif data.startswith("delete_channel_"):
            channel_id = data.replace("delete_channel_", "")
            await self.delete_channel(query, channel_id)
        elif data.startswith("select_channel_"):
            channel_id = data.replace("select_channel_", "")
            context.user_data['selected_channel'] = channel_id
            await self.select_time_menu(query)
        elif data.startswith("time_"):
            time_minutes = int(data.replace("time_", ""))
            await self.schedule_post(query, time_minutes, context)
        elif data == "custom_time":
            await self.request_custom_time(query, context)
        elif data.startswith("cancel_post_"):
            post_id = data.replace("cancel_post_", "")
            await self.cancel_scheduled_post(query, post_id)
        elif data == "back_to_main":
            await self.start(query, context)
    
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
    
    async def select_time_menu(self, query):
        """Меню выбора времени публикации"""
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
        
        channel_id = query.data.replace("select_channel_", "")
        channel_name = self.channels.get(channel_id, "Неизвестный канал")
        
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
        schedule_time = datetime.now() + timedelta(minutes=time_minutes)
        
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
            'status': 'scheduled'
        }
        
        self.scheduled_posts.append(scheduled_post)
        self.save_data()
        
        # Запуск задачи для отправки
        asyncio.create_task(self.send_scheduled_post(post_id, schedule_time))
        
        # Очистка временных данных
        context.user_data.pop('post_data', None)
        context.user_data.pop('selected_channel', None)
        context.user_data.pop('waiting_for_custom_time', None)
        
        await query.edit_message_text(
            f"✅ Пост запланирован!\n\n"
            f"📢 Канал: <b>{scheduled_post['channel_name']}</b>\n"
            f"⏰ Время отправки: <b>{schedule_time.strftime('%d.%m.%Y %H:%M')}</b>\n"
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
        
        if not active_posts:
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
            await query.edit_message_text(
                "⏰ Нет запланированных постов",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        text = "⏰ Запланированные посты:\n\n"
        keyboard = []
        
        for post in active_posts[:10]:
            scheduled_time = datetime.fromisoformat(post['scheduled_time'])
            time_str = scheduled_time.strftime('%d.%m.%Y %H:%M')
            
            text += (f"📢 {post['channel_name']}\n"
                    f"⏰ {time_str}\n"
                    f"📝 {post['post_data'].get('type', 'текст')}\n"
                    f"ID: <code>{post['id']}</code>\n\n")
            
            keyboard.append([
                InlineKeyboardButton(f"❌ Отменить {post['id'][:8]}...", 
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
        self.save_data()
        
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
            self.save_data()
            
            await query.edit_message_text(
                f"✅ Канал {channel_name} удален",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 К списку каналов", callback_data="list_channels")]
                ])
            )
    
    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик сообщений"""
        message = update.message
        
        # Обработка пользовательского времени (только одна попытка)
        if context.user_data.get('waiting_for_custom_time'):
            time_str = message.text.strip()
            
            # Сразу очищаем флаг, чтобы следующее сообщение не обрабатывалось как время
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
                    
                    # Используем CallbackQuery для единообразия
                    fake_query = type('Obj', (object,), {
                        'edit_message_text': lambda *args, **kwargs: message.reply_text(*args, **kwargs)
                    })()
                    
                    await self._create_scheduled_post(
                        fake_query, context, post_data, channel_id, schedule_time
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
            self.channels[channel_id] = channel_id
            self.save_data()
            
            await message.reply_text(
                f"✅ Канал {channel_id} добавлен!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                ])
            )
            return
        
        # Сохраняем данные поста (только если не ожидаем время)
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
    
    async def send_scheduled_post(self, post_id: str, schedule_time: datetime):
        """Отправка запланированного поста"""
        try:
            # Ожидаем время отправки
            delay = (schedule_time - datetime.now()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            
            # Находим пост
            post = next((p for p in self.scheduled_posts if p['id'] == post_id), None)
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
            self.save_data()
            
            logger.info(f"Пост {post_id} успешно отправлен в канал {channel_id}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки запланированного поста {post_id}: {e}")
            if post:
                post['status'] = 'error'
                self.save_data()

async def main():
    """Основная функция запуска"""
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не установлен")
    
    bot = ChannelBot(BOT_TOKEN)
    
    # Настройка webhook для Railway
    if WEBHOOK_URL:
        await bot.application.bot.set_webhook(
            url=f"{WEBHOOK_URL}/webhook",
            secret_token="WEBHOOK_SECRET"
        )
        logger.info("Webhook настроен")
    
    return bot.application

if __name__ == "__main__":
    # Для локальной разработки
    if not WEBHOOK_URL:
        bot = ChannelBot(BOT_TOKEN)
        print("Бот запущен в режиме polling...")
        bot.application.run_polling()
