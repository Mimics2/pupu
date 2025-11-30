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

    # ДОБАВЛЕННЫЕ МЕТОДЫ ДЛЯ РАБОТЫ С КАНАЛАМИ И ПОСТАМИ

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
        # Устанавливаем флаг, что ждем ID канала
        query._bot_data = {'waiting_for_channel': True, 'user_id': user_id}

    async def list_channels_menu(self, query, user_id: int):
        """Меню списка каналов пользователя"""
        user_channels = {cid: cname for cid, cname in self.channels.items() if str(user_id) in str(cname)}
        
        if not user_channels:
            await query.edit_message_text(
                "📭 У вас нет добавленных каналов",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                ])
            )
            return
        
        text = "📋 Ваши каналы:\n\n"
        keyboard = []
        
        for channel_id, channel_name in user_channels.items():
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

    async def delete_channel(self, query, user_id: int, channel_id: str):
        """Удаление канала"""
        if channel_id in self.channels:
            channel_name = self.channels[channel_id]
            # Проверяем, что канал принадлежит пользователю
            if str(user_id) in str(channel_name):
                del self.channels[channel_id]
                await query.edit_message_text(
                    f"✅ Канал {channel_name} удален",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 К списку каналов", callback_data="list_channels")]
                    ])
                )
            else:
                await query.edit_message_text("❌ Вы не можете удалить этот канал")
        else:
            await query.edit_message_text("❌ Канал не найден")

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
        
        user_channels = {cid: cname for cid, cname in self.channels.items() if str(user_id) in str(cname)}
        
        if not user_channels:
            await query.edit_message_text(
                "❌ У вас нет добавленных каналов",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")]
                ])
            )
            return
        
        keyboard = []
        for channel_id, channel_name in user_channels.items():
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
        channel_name = self.channels.get(channel_id, "Неизвестный канал")
        current_time = format_moscow_time()
        
        # УПРОЩЕННЫЕ КНОПКИ ВРЕМЕНИ
        keyboard = [
            [InlineKeyboardButton("🚀 Опубликовать сейчас", callback_data="publish_now")],
            [InlineKeyboardButton("⏰ 1 час", callback_data="time_60")],
            [InlineKeyboardButton("⏰ 3 часа", callback_data="time_180")],
            [InlineKeyboardButton("⏰ 6 часов", callback_data="time_360")],
            [InlineKeyboardButton("⏰ 24 часа", callback_data="time_1440")],
            [InlineKeyboardButton("🕒 Другое время", callback_data="custom_time")],
            [InlineKeyboardButton("🔙 Назад", callback_data="create_post")]
        ]
        
        await query.edit_message_text(
            f"⏰ Выберите время публикации для канала <b>{channel_name}</b>\n"
            f"🕐 Текущее время в Москве: <b>{current_time}</b>\n\n"
            "Теперь отправьте сообщение (текст, фото, видео или документ) которое нужно опубликовать:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def publish_now(self, query, user_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Публикация поста сразу"""
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
        
        try:
            # Отправляем пост сразу
            await self._send_post_immediately(post_data, channel_id)
            
            # Очистка временных данных
            context.user_data.pop('post_data', None)
            context.user_data.pop('selected_channel', None)
            context.user_data.pop('waiting_for_content', None)
            
            current_time = format_moscow_time()
            
            await query.edit_message_text(
                f"✅ Пост опубликован!\n\n"
                f"📢 Канал: <b>{self.channels.get(channel_id, 'Неизвестный канал')}</b>\n"
                f"🕐 Время публикации: <b>{current_time}</b>\n"
                f"📝 Тип: <b>{post_data.get('type', 'текст')}</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 Создать новый пост", callback_data="create_post")],
                    [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                ])
            )
            
        except Exception as e:
            logger.error(f"Ошибка публикации поста: {e}")
            await query.edit_message_text(
                f"❌ Ошибка публикации: {str(e)}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data="create_post")]
                ])
            )

    async def _send_post_immediately(self, post_data: Dict, channel_id: str):
        """Немедленная отправка поста"""
        try:
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
            
            logger.info(f"Пост немедленно отправлен в канал {channel_id}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки поста в канал {channel_id}: {e}")
            raise e

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
        # Используем московское время для расчета
        schedule_time = get_moscow_time() + timedelta(minutes=time_minutes)
        
        await self._create_scheduled_post(query, context, post_data, channel_id, schedule_time)

    async def _create_scheduled_post(self, query, context, post_data, channel_id, schedule_time):
        """Создание запланированного поста"""
        user_id = context.user_data.get('user_id', query.from_user.id)
        post_id = f"post_{user_id}_{len(self.scheduled_posts)}_{datetime.now().timestamp()}"
        
        scheduled_post = {
            'id': post_id,
            'user_id': user_id,
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

    async def scheduled_posts_menu(self, query, user_id: int):
        """Меню запланированных постов"""
        active_posts = [p for p in self.scheduled_posts if p.get('user_id') == user_id and p.get('status') != 'sent']
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
        
        text = f"⏰ Ваши запланированные посты:\n🕐 Текущее время: <b>{current_time}</b>\n\n"
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

    async def cancel_scheduled_post(self, query, user_id: int, post_id: str):
        """Отмена запланированного поста"""
        post = next((p for p in self.scheduled_posts if p['id'] == post_id and p.get('user_id') == user_id), None)
        if post:
            post['status'] = 'cancelled'
            await query.edit_message_text(
                "✅ Пост отменен",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 К запланированным", callback_data="scheduled_posts")]
                ])
            )
        else:
            await query.edit_message_text("❌ Пост не найден")

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
            post = next((p for p in self.scheduled_posts if p['id'] == post_id and p.get('status') == 'scheduled'), None)
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
        
        # Обработка добавления канала
        if message.text and (message.text.startswith('@') or message.text.startswith('-100')):
            channel_id = message.text.strip()
            
            # Проверяем лимиты
            if not self.can_user_add_channel(user_id):
                tariff = self.get_user_tariff(user_id)
                user_channels = [c for c in self.channels.values() if str(user_id) in str(c)]
                await message.reply_text(
                    f"❌ Лимит каналов исчерпан\n"
                    f"📢 Ваш лимит: {tariff['channels_limit']} каналов\n"
                    f"📊 Использовано: {len(user_channels)} каналов",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💳 Сменить тариф", callback_data="tariffs")]
                    ])
                )
                return
            
            # Сохраняем канал с привязкой к пользователю
            self.channels[channel_id] = f"{channel_id} (user:{user_id})"
            
            await message.reply_text(
                f"✅ Канал {channel_id} добавлен!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Мои каналы", callback_data="list_channels")],
                    [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                ])
            )
            return
        
        # Обработка пользовательского времени (только одна попытка)
        if context.user_data.get('waiting_for_custom_time'):
            time_str = message.text.strip()
            
            # Сразу очищаем флаг
            context.user_data.pop('waiting_for_custom_time', None)
            
            try:
                # Используем правильный парсинг времени
                schedule_time = parse_custom_time(time_str)
                
                current_time = get_moscow_time()
                
                # Проверяем что время в будущем (с запасом в 1 минуту)
                time_difference = (schedule_time - current_time).total_seconds()
                if time_difference < 60:  # Меньше 1 минуты
                    await message.reply_text(
                        f"❌ Время должно быть в будущем (минимум на 1 минуту позже).\n"
                        f"🕐 Введенное время: <b>{schedule_time.strftime('%d.%m.%Y %H:%M')}</b>\n"
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
                    
                    post_id = f"post_{user_id}_{len(self.scheduled_posts)}_{datetime.now().timestamp()}"
                    
                    scheduled_post = {
                        'id': post_id,
                        'user_id': user_id,
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
                    
            except ValueError as e:
                current_time = format_moscow_time()
                await message.reply_text(
                    f"❌ Ошибка: {str(e)}\n\n"
                    f"Используйте формат: <code>ДД.ММ.ГГГГ-ЧЧ.ММ</code>\n"
                    f"🕐 Текущее время: <b>{current_time}</b>\n\n"
                    f"Начните создание поста заново.",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                    ])
                )
            return
        
        # Проверяем, ждем ли мы контент для поста
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
            [InlineKeyboardButton("🚀 Опубликовать сейчас", callback_data="publish_now")],
            [InlineKeyboardButton("⏰ 1 час", callback_data="time_60")],
            [InlineKeyboardButton("⏰ 3 часа", callback_data="time_180")],
            [InlineKeyboardButton("⏰ 6 часов", callback_data="time_360")],
            [InlineKeyboardButton("⏰ 24 часа", callback_data="time_1440")],
            [InlineKeyboardButton("🕒 Другое время", callback_data="custom_time")],
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

def main():
    """Основная функция запуска"""
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не установлен")
    
    bot = ChannelBot(BOT_TOKEN)
    print("Бот запущен с полной системой тарифов и каналов...")
    bot.application.run_polling()

if __name__ == "__main__":
    main()
