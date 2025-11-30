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

# Тарифы
TARIFFS = {
    "basic": {
        "name": "🌟 Базовый",
        "price": "1$",
        "channels_limit": 1,
        "posts_per_day": 2,
        "duration_days": 30,
        "payment_link": "https://t.me/+oPfRjMNXvH42YTgy"
    },
    "standard": {
        "name": "💎 Стандарт", 
        "price": "3$",
        "channels_limit": 3,
        "posts_per_day": 6,
        "duration_days": 30,
        "payment_link": "https://t.me/+ieTyNl3xdApjMDgy"
    },
    "premium": {
        "name": "🚀 Премиум",
        "price": "5$", 
        "channels_limit": 999,  # безлимит
        "posts_per_day": 999,   # безлимит
        "duration_days": 30,
        "payment_link": "https://t.me/+Dl9roZ3JY2AwNGI6"
    }
}

# Админ ID
ADMIN_ID = 6646433980

def get_moscow_time():
    """Получить текущее время в Москве"""
    return datetime.now(MOSCOW_TZ)

def format_moscow_time(dt=None):
    """Форматировать время в Москве"""
    if dt is None:
        dt = get_moscow_time()
    return dt.strftime('%d.%m.%Y %H:%M')

def parse_custom_time(time_str: str):
    """Парсинг пользовательского времени с правильной обработкой часового пояса"""
    try:
        naive_dt = datetime.strptime(time_str, '%d.%m.%Y-%H.%M')
        moscow_dt = MOSCOW_TZ.localize(naive_dt)
        return moscow_dt
    except ValueError as e:
        raise ValueError(f"Неверный формат времени: {time_str}") from e

class ChannelBot:
    def __init__(self, token: str):
        self.token = token
        self.application = Application.builder().token(token).build()
        self.channels: Dict[str, str] = {}
        self.scheduled_posts: List[Dict] = []
        self.user_tariffs: Dict[int, Dict] = {}  # user_id -> tariff_data
        self.user_stats: Dict[int, Dict] = {}    # user_id -> stats
        self.setup_handlers()
        
        # Админ получает безлимит навсегда
        self.user_tariffs[ADMIN_ID] = {
            'tariff': 'admin',
            'name': '👑 Админ',
            'channels_limit': 999,
            'posts_per_day': 999,
            'expires_at': None,  # навсегда
            'activated_at': datetime.now().isoformat(),
            'is_trial': False
        }
    
    def setup_handlers(self):
        """Настройка обработчиков команд"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("time", self.current_time))
        self.application.add_handler(CommandHandler("stats", self.user_stats_command))
        self.application.add_handler(CommandHandler("admin", self.admin_panel))
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        self.application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, self.message_handler))
    
    def get_user_tariff(self, user_id: int) -> Dict:
        """Получить тариф пользователя"""
        if user_id == ADMIN_ID:
            return self.user_tariffs.get(user_id, {
                'tariff': 'admin',
                'name': '👑 Админ',
                'channels_limit': 999,
                'posts_per_day': 999,
                'expires_at': None,
                'is_trial': False
            })
        
        user_tariff = self.user_tariffs.get(user_id)
        
        # Если у пользователя нет тарифа, даем пробный стандарт на 7 дней
        if not user_tariff:
            trial_expires = datetime.now() + timedelta(days=7)
            user_tariff = {
                'tariff': 'trial',
                'name': '🆓 Пробный Стандарт',
                'channels_limit': 3,
                'posts_per_day': 6,
                'expires_at': trial_expires.isoformat(),
                'activated_at': datetime.now().isoformat(),
                'is_trial': True
            }
            self.user_tariffs[user_id] = user_tariff
            return user_tariff
        
        # Проверяем срок действия тарифа
        if user_tariff.get('expires_at'):
            expires_at = datetime.fromisoformat(user_tariff['expires_at'])
            if expires_at < datetime.now():
                # Тариф истек, даем пробный если еще не было
                if not user_tariff.get('had_trial'):
                    trial_expires = datetime.now() + timedelta(days=7)
                    new_trial = {
                        'tariff': 'trial',
                        'name': '🆓 Пробный Стандарт',
                        'channels_limit': 3,
                        'posts_per_day': 6,
                        'expires_at': trial_expires.isoformat(),
                        'activated_at': datetime.now().isoformat(),
                        'is_trial': True,
                        'had_trial': True
                    }
                    self.user_tariffs[user_id] = new_trial
                    return new_trial
                else:
                    # Пробный уже был, удаляем тариф
                    del self.user_tariffs[user_id]
                    return None
        
        return user_tariff
    
    def can_user_add_channel(self, user_id: int) -> bool:
        """Может ли пользователь добавить канал"""
        tariff = self.get_user_tariff(user_id)
        if not tariff:
            return False
        
        user_channels = [c for c in self.channels.values() if str(user_id) in str(c)]
        return len(user_channels) < tariff['channels_limit']
    
    def can_user_schedule_post(self, user_id: int) -> bool:
        """Может ли пользователь запланировать пост"""
        tariff = self.get_user_tariff(user_id)
        if not tariff:
            return False
        
        # Проверяем лимит постов за сегодня
        today = datetime.now().date()
        today_posts = [p for p in self.scheduled_posts 
                      if p.get('user_id') == user_id 
                      and datetime.fromisoformat(p['scheduled_time']).date() == today
                      and p.get('status') != 'cancelled']
        
        return len(today_posts) < tariff['posts_per_day']
    
    async def current_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать текущее время в Москве"""
        current_time = format_moscow_time()
        await update.message.reply_text(
            f"🕐 Текущее время в Москве:\n<b>{current_time}</b>",
            parse_mode="HTML"
        )
    
    async def user_stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Статистика пользователя"""
        user_id = update.effective_user.id
        tariff = self.get_user_tariff(user_id)
        
        if not tariff:
            await update.message.reply_text(
                "❌ У вас нет активного тарифа\n"
                "Выберите тариф в меню для начала работы",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Выбрать тариф", callback_data="tariffs")]
                ])
            )
            return
        
        # Считаем статистику
        user_channels = [c for c in self.channels.values() if str(user_id) in str(c)]
        today = datetime.now().date()
        today_posts = [p for p in self.scheduled_posts 
                      if p.get('user_id') == user_id 
                      and datetime.fromisoformat(p['scheduled_time']).date() == today
                      and p.get('status') != 'cancelled']
        
        text = (
            f"📊 Ваша статистика:\n\n"
            f"💳 Тариф: <b>{tariff['name']}</b>\n"
            f"📢 Каналов: {len(user_channels)}/{tariff['channels_limit']}\n"
            f"📤 Постов сегодня: {len(today_posts)}/{tariff['posts_per_day']}\n"
        )
        
        if tariff.get('expires_at'):
            expires_at = datetime.fromisoformat(tariff['expires_at'])
            days_left = (expires_at - datetime.now()).days
            text += f"⏰ Осталось дней: <b>{days_left}</b>\n"
        
        if tariff.get('is_trial'):
            text += "\n⚠️ Это пробный период. После окончания выберите тариф\n"
        
        await update.message.reply_text(text, parse_mode="HTML")
    
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Админ панель"""
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            await update.message.reply_text("❌ Доступ запрещен")
            return
        
        # Статистика бота
        all_users = set([p.get('user_id') for p in self.scheduled_posts] + list(self.user_tariffs.keys()))
        total_users = len(all_users)
        active_users = len([uid for uid in all_users if self.get_user_tariff(uid)])
        
        today_posts = len([p for p in self.scheduled_posts 
                          if datetime.fromisoformat(p['scheduled_time']).date() == datetime.now().date()])
        
        text = (
            f"👑 Админ панель\n\n"
            f"📊 Общая статистика:\n"
            f"• Всего пользователей: {total_users}\n"
            f"• Активных пользователей: {active_users}\n"
            f"• Постов сегодня: {today_posts}\n"
            f"• Всего каналов: {len(self.channels)}\n\n"
            f"💳 Тарифы:\n"
        )
        
        for tariff_name, count in self.get_tariff_stats().items():
            text += f"• {tariff_name}: {count} пользователей\n"
        
        keyboard = [
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("📊 Детальная статистика", callback_data="admin_detailed_stats")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
        ]
        
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    def get_tariff_stats(self) -> Dict[str, int]:
        """Статистика по тарифам"""
        stats = {'trial': 0, 'basic': 0, 'standard': 0, 'premium': 0, 'admin': 0}
        for user_id, tariff in self.user_tariffs.items():
            tariff_type = tariff.get('tariff', 'trial')
            stats[tariff_type] += 1
        return stats
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        
        # Очищаем временные данные при старте
        if context.user_data:
            context.user_data.clear()
            
        current_time = format_moscow_time()
        tariff = self.get_user_tariff(user_id)
        
        if not tariff:
            # Показываем меню тарифов
            keyboard = [
                [InlineKeyboardButton("💳 Выбрать тариф", callback_data="tariffs")],
                [InlineKeyboardButton("ℹ️ О тарифах", callback_data="tariff_info")]
            ]
        else:
            # Основное меню для пользователей с тарифом
            keyboard = [
                [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
                [InlineKeyboardButton("📋 Мои каналы", callback_data="list_channels")],
                [InlineKeyboardButton("📤 Создать пост", callback_data="create_post")],
                [InlineKeyboardButton("⏰ Запланированные посты", callback_data="scheduled_posts")],
                [InlineKeyboardButton("📊 Статистика", callback_data="user_stats")],
                [InlineKeyboardButton("🕐 Текущее время", callback_data="current_time")]
            ]
            
            if user_id == ADMIN_ID:
                keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = f"🤖 Бот для управления публикациями в каналах\n🕐 Московское время: <b>{current_time}</b>\n\n"
        
        if tariff:
            welcome_text += f"💳 Ваш тариф: <b>{tariff['name']}</b>\n"
            if tariff.get('expires_at'):
                expires_at = datetime.fromisoformat(tariff['expires_at'])
                days_left = (expires_at - datetime.now()).days
                welcome_text += f"⏰ Осталось дней: <b>{days_left}</b>\n"
            
            if tariff.get('is_trial'):
                welcome_text += "🆓 Это пробный период на 7 дней\n"
        else:
            welcome_text += "❌ У вас нет активного тарифа\n"
        
        welcome_text += "\nВыберите действие:"
        
        if update.message:
            await update.message.reply_text(welcome_text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await update.callback_query.edit_message_text(welcome_text, parse_mode="HTML", reply_markup=reply_markup)
    
    async def start_from_query(self, query, user_id: int):
        """Старт из callback query"""
        current_time = format_moscow_time()
        tariff = self.get_user_tariff(user_id)
        
        if not tariff:
            # Показываем меню тарифов
            keyboard = [
                [InlineKeyboardButton("💳 Выбрать тариф", callback_data="tariffs")],
                [InlineKeyboardButton("ℹ️ О тарифах", callback_data="tariff_info")]
            ]
        else:
            # Основное меню для пользователей с тарифом
            keyboard = [
                [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
                [InlineKeyboardButton("📋 Мои каналы", callback_data="list_channels")],
                [InlineKeyboardButton("📤 Создать пост", callback_data="create_post")],
                [InlineKeyboardButton("⏰ Запланированные посты", callback_data="scheduled_posts")],
                [InlineKeyboardButton("📊 Статистика", callback_data="user_stats")],
                [InlineKeyboardButton("🕐 Текущее время", callback_data="current_time")]
            ]
            
            if user_id == ADMIN_ID:
                keyboard.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = f"🤖 Бот для управления публикациями в каналах\n🕐 Московское время: <b>{current_time}</b>\n\n"
        
        if tariff:
            welcome_text += f"💳 Ваш тариф: <b>{tariff['name']}</b>\n"
            if tariff.get('expires_at'):
                expires_at = datetime.fromisoformat(tariff['expires_at'])
                days_left = (expires_at - datetime.now()).days
                welcome_text += f"⏰ Осталось дней: <b>{days_left}</b>\n"
            
            if tariff.get('is_trial'):
                welcome_text += "🆓 Это пробный период на 7 дней\n"
        else:
            welcome_text += "❌ У вас нет активного тарифа\n"
        
        welcome_text += "\nВыберите действие:"
        
        await query.edit_message_text(welcome_text, parse_mode="HTML", reply_markup=reply_markup)
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        data = query.data
        
        if data == "add_channel":
            await self.add_channel_menu(query, user_id)
        elif data == "list_channels":
            await self.list_channels_menu(query, user_id)
        elif data == "create_post":
            await self.create_post_menu(query, user_id)
        elif data == "scheduled_posts":
            await self.scheduled_posts_menu(query, user_id)
        elif data == "current_time":
            await self.show_current_time(query)
        elif data == "user_stats":
            await self.show_user_stats(query, user_id)
        elif data == "tariffs":
            await self.show_tariffs(query)
        elif data == "tariff_info":
            await self.show_tariff_info(query)
        elif data.startswith("select_tariff_"):
            tariff_name = data.replace("select_tariff_", "")
            await self.select_tariff(query, user_id, tariff_name)
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
        elif data == "publish_now":
            await self.publish_now(query, user_id, context)
        elif data == "custom_time":
            await self.request_custom_time(query, context)
        elif data.startswith("cancel_post_"):
            post_id = data.replace("cancel_post_", "")
            await self.cancel_scheduled_post(query, user_id, post_id)
        elif data == "admin_panel":
            await self.show_admin_panel(query)
        elif data == "admin_broadcast":
            await self.start_broadcast(query, context)
        elif data == "admin_detailed_stats":
            await self.show_detailed_stats(query)
        elif data == "back_to_main":
            await self.start_from_query(query, user_id)
    
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
    
    async def show_tariffs(self, query):
        """Показать тарифы"""
        text = "💳 Выберите тариф:\n\n"
        
        for tariff_key, tariff in TARIFFS.items():
            text += (
                f"{tariff['name']} - {tariff['price']}\n"
                f"• Каналов: {tariff['channels_limit']}\n"
                f"• Постов в день: {tariff['posts_per_day']}\n"
                f"• Длительность: {tariff['duration_days']} дней\n\n"
            )
        
        text += "🆓 Каждый новый пользователь получает пробный период:\n"
        text += "• Стандарт тариф на 7 дней\n• 3 канала\n• 6 постов в день\n"
        
        keyboard = []
        for tariff_key in TARIFFS.keys():
            keyboard.append([InlineKeyboardButton(
                f"Выбрать {TARIFFS[tariff_key]['name']}", 
                callback_data=f"select_tariff_{tariff_key}"
            )])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def select_tariff(self, query, user_id: int, tariff_name: str):
        """Выбор тарифа"""
        if tariff_name not in TARIFFS:
            await query.edit_message_text("❌ Тариф не найден")
            return
        
        tariff = TARIFFS[tariff_name]
        payment_link = tariff['payment_link']
        
        text = (
            f"💳 Вы выбрали: {tariff['name']}\n"
            f"💰 Стоимость: {tariff['price']}\n"
            f"📢 Каналов: {tariff['channels_limit']}\n"
            f"📤 Постов в день: {tariff['posts_per_day']}\n"
            f"⏰ Длительность: {tariff['duration_days']} дней\n\n"
            f"Для оплаты перейдите по ссылке:\n{payment_link}\n\n"
            f"После оплаты напишите @username_admin для активации"
        )
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Назад", callback_data="tariffs")]
        ]))
    
    async def show_tariff_info(self, query):
        """Информация о тарифах"""
        text = "ℹ️ Информация о тарифах:\n\n"
        
        for tariff in TARIFFS.values():
            text += (
                f"{tariff['name']} - {tariff['price']}\n"
                f"• Каналов: {tariff['channels_limit']}\n"
                f"• Постов в день: {tariff['posts_per_day']}\n"
                f"• Длительность: {tariff['duration_days']} дней\n\n"
            )
        
        text += "🆓 Пробный период:\n"
        text += "• Стандарт тариф на 7 дней\n• 3 канала\n• 6 постов в день\n\n"
        text += "💡 После оплаты свяжитесь с админом для активации тарифа"
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Выбрать тариф", callback_data="tariffs")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
        ]))
    
    async def show_user_stats(self, query, user_id: int):
        """Показать статистику пользователя"""
        tariff = self.get_user_tariff(user_id)
        
        if not tariff:
            await query.edit_message_text(
                "❌ У вас нет активного тарифа",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Выбрать тариф", callback_data="tariffs")]
                ])
            )
            return
        
        user_channels = [c for c in self.channels.values() if str(user_id) in str(c)]
        today = datetime.now().date()
        today_posts = [p for p in self.scheduled_posts 
                      if p.get('user_id') == user_id 
                      and datetime.fromisoformat(p['scheduled_time']).date() == today
                      and p.get('status') != 'cancelled']
        
        text = (
            f"📊 Ваша статистика:\n\n"
            f"💳 Тариф: <b>{tariff['name']}</b>\n"
            f"📢 Каналов: {len(user_channels)}/{tariff['channels_limit']}\n"
            f"📤 Постов сегодня: {len(today_posts)}/{tariff['posts_per_day']}\n"
        )
        
        if tariff.get('expires_at'):
            expires_at = datetime.fromisoformat(tariff['expires_at'])
            days_left = (expires_at - datetime.now()).days
            text += f"⏰ Осталось дней: <b>{days_left}</b>\n"
        
        if tariff.get('is_trial'):
            text += "\n⚠️ Это пробный период. После окончания выберите тариф\n"
        
        await query.edit_message_text(text, parse_mode="HTML")
    
    async def show_admin_panel(self, query):
        """Показать админ панель"""
        user_id = query.from_user.id
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ Доступ запрещен")
            return
        
        all_users = set([p.get('user_id') for p in self.scheduled_posts] + list(self.user_tariffs.keys()))
        total_users = len(all_users)
        active_users = len([uid for uid in all_users if self.get_user_tariff(uid)])
        today_posts = len([p for p in self.scheduled_posts 
                          if datetime.fromisoformat(p['scheduled_time']).date() == datetime.now().date()])
        
        text = (
            f"👑 Админ панель\n\n"
            f"📊 Общая статистика:\n"
            f"• Всего пользователей: {total_users}\n"
            f"• Активных пользователей: {active_users}\n"
            f"• Постов сегодня: {today_posts}\n"
            f"• Всего каналов: {len(self.channels)}\n"
        )
        
        keyboard = [
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("📊 Детальная статистика", callback_data="admin_detailed_stats")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
        ]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    async def start_broadcast(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Начать рассылку"""
        user_id = query.from_user.id
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ Доступ запрещен")
            return
        
        await query.edit_message_text(
            "📢 Отправьте сообщение для рассылки всем пользователям:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отмена", callback_data="admin_panel")]
            ])
        )
        context.user_data['waiting_for_broadcast'] = True
    
    async def show_detailed_stats(self, query):
        """Детальная статистика"""
        user_id = query.from_user.id
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ Доступ запрещен")
            return
        
        tariff_stats = self.get_tariff_stats()
        text = "📊 Детальная статистика по тарифам:\n\n"
        
        tariff_display_names = {
            'trial': '🆓 Пробный',
            'basic': '🌟 Базовый',
            'standard': '💎 Стандарт', 
            'premium': '🚀 Премиум',
            'admin': '👑 Админ'
        }
        
        for tariff_name, count in tariff_stats.items():
            display_name = tariff_display_names.get(tariff_name, tariff_name)
            text += f"• {display_name}: {count} пользователей\n"
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
        ]))

    # Остальные методы (add_channel_menu, create_post_menu, list_channels_menu и т.д.)
    # остаются аналогичными предыдущей версии с проверками тарифов

    async def add_channel_menu(self, query, user_id: int):
        """Меню добавления канала"""
        if not self.can_user_add_channel(user_id):
            tariff = self.get_user_tariff(user_id)
            if not tariff:
                await query.edit_message_text(
                    "❌ У вас нет активного тарифа",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💳 Выбрать тариф", callback_data="tariffs")]
                    ])
                )
            else:
                user_channels = [c for c in self.channels.values() if str(user_id) in str(c)]
                await query.edit_message_text(
                    f"❌ Лимит каналов исчерпан\n"
                    f"📢 Ваш лимит: {tariff['channels_limit']} каналов\n"
                    f"📊 Использовано: {len(user_channels)} каналов",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💳 Сменить тариф", callback_data="tariffs")]
                    ])
                )
            return
        
        await query.edit_message_text(
            "📝 Чтобы добавить канал:\n\n"
            "1. Добавьте бота в канал как администратора\n"
            "2. Отправьте ID канала в формате:\n"
            "<code>@username_channel</code> или <code>-1001234567890</code>\n\n"
            "Отправьте ID канала:",
            parse_mode="HTML"
        )
    
    async def create_post_menu(self, query, user_id: int):
        """Меню создания поста"""
        if not self.can_user_schedule_post(user_id):
            tariff = self.get_user_tariff(user_id)
            if not tariff:
                await query.edit_message_text(
                    "❌ У вас нет активного тарифа",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💳 Выбрать тариф", callback_data="tariffs")]
                    ])
                )
            else:
                today = datetime.now().date()
                today_posts = [p for p in self.scheduled_posts 
                              if p.get('user_id') == user_id 
                              and datetime.fromisoformat(p['scheduled_time']).date() == today
                              and p.get('status') != 'cancelled']
                await query.edit_message_text(
                    f"❌ Лимит постов на сегодня исчерпан\n"
                    f"📤 Ваш лимит: {tariff['posts_per_day']} постов в день\n"
                    f"📊 Использовано: {len(today_posts)} постов",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📊 Статистика", callback_data="user_stats")]
                    ])
                )
            return
        
        user_channels = [c for c in self.channels.items() if str(user_id) in str(c)]
        
        if not user_channels:
            await query.edit_message_text(
                "❌ У вас нет добавленных каналов",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")]
                ])
            )
            return
        
        keyboard = []
        for channel_id, channel_name in user_channels:
            keyboard.append([
                InlineKeyboardButton(f"📢 {channel_name}", 
                                   callback_data=f"select_channel_{channel_id}")
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        await query.edit_message_text(
            "🎯 Выберите канал для публикации:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # Остальные методы (list_channels_menu, select_time_menu, publish_now, schedule_post и т.д.)
    # остаются аналогичными предыдущей версии

    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик сообщений"""
        message = update.message
        user_id = update.effective_user.id
        
        # Обработка рассылки для админа
        if context.user_data.get('waiting_for_broadcast') and user_id == ADMIN_ID:
            # Отправляем сообщение всем пользователям
            users_to_notify = set()
            for post in self.scheduled_posts:
                if post.get('user_id'):
                    users_to_notify.add(post['user_id'])
            for uid in self.user_tariffs.keys():
                users_to_notify.add(uid)
            
            success_count = 0
            for uid in users_to_notify:
                try:
                    if message.text:
                        await context.bot.send_message(chat_id=uid, text=message.text)
                    elif message.photo:
                        await context.bot.send_photo(chat_id=uid, photo=message.photo[-1].file_id, caption=message.caption)
                    elif message.video:
                        await context.bot.send_video(chat_id=uid, video=message.video.file_id, caption=message.caption)
                    elif message.document:
                        await context.bot.send_document(chat_id=uid, document=message.document.file_id, caption=message.caption)
                    success_count += 1
                except Exception as e:
                    logger.error(f"Ошибка рассылки пользователю {uid}: {e}")
            
            context.user_data.pop('waiting_for_broadcast', None)
            await message.reply_text(f"✅ Рассылка завершена\nОтправлено: {success_count} пользователям")
            return
        
        # Остальная логика обработки сообщений...
        # ... (аналогично предыдущей версии)

def main():
    """Основная функция запуска"""
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не установлен")
    
    bot = ChannelBot(BOT_TOKEN)
    print("Бот запущен с системой тарифов и пробным периодом...")
    bot.application.run_polling()

if __name__ == "__main__":
    main()
