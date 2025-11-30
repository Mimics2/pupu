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
ADMIN_ID = 6646433980  # Ваш ID администратора

# Московское время
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# Тарифные планы и каналы для подписки
SUBSCRIPTION_PLANS = {
    "basic": {
        "name": "💰 Базовый - $1/месяц",
        "price": 1,
        "posts_per_day": 2,
        "channels_limit": 1,
        "subscribe_url": "https://t.me/+oPfRjMNXvH42YTgy"
    },
    "standard": {
        "name": "💎 Стандартный - $3/месяц",
        "price": 3,
        "posts_per_day": 6,
        "channels_limit": 3,
        "subscribe_url": "https://t.me/+ieTyNl3xdApjMDgy"
    },
    "premium": {
        "name": "🚀 Премиум - $5/месяц",
        "price": 5,
        "posts_per_day": -1,
        "channels_limit": -1,
        "subscribe_url": "https://t.me/+Dl9roZ3JY2AwNGI6"
    }
}

# Юзернеймы каналов для проверки подписки (БОТ ДОЛЖЕН БЫТЬ АДМИНОМ В ЭТИХ КАНАЛАХ!)
SUBSCRIPTION_CHANNELS = {
    "basic": "@pupu_basic_channel",    # Замени на реальный юзернейм канала
    "standard": "@pupu_standard_channel", 
    "premium": "@pupu_premium_channel"   
}

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
        self.user_subscriptions: Dict[int, Dict] = {}
        self.user_stats: Dict[int, Dict] = {}
        self.waiting_for_broadcast = False
        self.setup_handlers()
    
    def is_admin(self, user_id: int) -> bool:
        """Проверить является ли пользователь администратором"""
        return user_id == ADMIN_ID
    
    async def check_channel_subscription(self, user_id: int, plan_type: str) -> bool:
        """Проверить подписку пользователя на канал (бот должен быть админом в канале)"""
        try:
            channel_username = SUBSCRIPTION_CHANNELS[plan_type]
            
            # Получаем информацию о участнике канала
            chat_member = await self.application.bot.get_chat_member(
                chat_id=channel_username,
                user_id=user_id
            )
            
            # Проверяем статус пользователя в канале
            return chat_member.status in ['member', 'administrator', 'creator']
            
        except Exception as e:
            logger.error(f"Ошибка проверки подписки пользователя {user_id} на канал {plan_type}: {e}")
            return False
    
    def setup_handlers(self):
        """Настройка обработчиков команд"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("time", self.current_time))
        self.application.add_handler(CommandHandler("admin", self.admin_panel))
        self.application.add_handler(CommandHandler("check_subscription", self.check_subscription))
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        self.application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, self.message_handler))
    
    async def check_subscription(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда для проверки текущей подписки"""
        user_id = update.effective_user.id
        
        # Админ всегда имеет безлимит
        if self.is_admin(user_id):
            await update.message.reply_text(
                "👑 Вы администратор - у вас полный безлимит навсегда!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👑 Админ Панель", callback_data="admin_panel")],
                    [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                ])
            )
            return
        
        user_plan = self.get_user_plan(user_id)
        
        if user_plan["plan"] == "free":
            await update.message.reply_text(
                "❌ У вас нет активной подписки\n"
                "💳 Используйте меню тарифов для оформления подписки",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")]
                ])
            )
            return
        
        plan_config = SUBSCRIPTION_PLANS[user_plan["plan"]]
        
        # Проверяем актуальность подписки
        is_subscribed = await self.check_channel_subscription(user_id, user_plan["plan"])
        
        if not is_subscribed:
            # Если пользователь отписался, снимаем подписку
            del self.user_subscriptions[user_id]
            await update.message.reply_text(
                "❌ Ваша подписка деактивирована\n"
                "Вы отписались от канала подписки\n"
                "💳 Для возобновления доступа оформите подписку заново",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")]
                ])
            )
            return
        
        # Показываем информацию о подписке
        text = f"✅ Активная подписка:\n{plan_config['name']}\n\n"
        
        if user_id in self.user_stats:
            posts_today = self.user_stats[user_id]["posts_today"]
            if plan_config["posts_per_day"] == -1:
                text += f"📊 Использовано постов сегодня: {posts_today} (безлимит)\n"
            else:
                text += f"📊 Использовано постов сегодня: {posts_today}/{plan_config['posts_per_day']}\n"
        
        text += f"📢 Каналов: {len(self.channels)}"
        if plan_config["channels_limit"] != -1:
            text += f"/{plan_config['channels_limit']}"
        
        await update.message.reply_text(text)
    
    def get_user_plan(self, user_id: int) -> Dict:
        """Получить тарифный план пользователя"""
        # Админ всегда имеет безлимит
        if self.is_admin(user_id):
            return {"plan": "admin", "subscribed_at": get_moscow_time().isoformat()}
        
        return self.user_subscriptions.get(user_id, {"plan": "free"})
    
    def can_user_post(self, user_id: int) -> bool:
        """Может ли пользователь создать пост"""
        # Админ всегда может постить
        if self.is_admin(user_id):
            return True
        
        user_plan = self.get_user_plan(user_id)
        
        if user_plan["plan"] == "free":
            return False
        
        plan_config = SUBSCRIPTION_PLANS[user_plan["plan"]]
        
        # Проверка лимита каналов
        if plan_config["channels_limit"] != -1 and len(self.channels) >= plan_config["channels_limit"]:
            return False
        
        # Проверка лимита постов
        if plan_config["posts_per_day"] == -1:
            return True
        
        # Сброс счетчика если новый день
        if user_id not in self.user_stats:
            self.user_stats[user_id] = {"posts_today": 0, "last_reset": get_moscow_time().date()}
        
        user_stat = self.user_stats[user_id]
        today = get_moscow_time().date()
        
        if user_stat["last_reset"] != today:
            user_stat["posts_today"] = 0
            user_stat["last_reset"] = today
        
        return user_stat["posts_today"] < plan_config["posts_per_day"]
    
    def increment_user_posts(self, user_id: int):
        """Увеличить счетчик постов пользователя"""
        # Админу не нужно считать посты
        if self.is_admin(user_id):
            return
        
        if user_id not in self.user_stats:
            self.user_stats[user_id] = {"posts_today": 0, "last_reset": get_moscow_time().date()}
        
        self.user_stats[user_id]["posts_today"] += 1
    
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Админ панель"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ У вас нет доступа к админ панели")
            return
        
        total_users = len(set(list(self.user_subscriptions.keys()) + 
                            [post.get('user_id') for post in self.scheduled_posts if post.get('user_id')]))
        active_subscriptions = len([sub for sub in self.user_subscriptions.values() if sub["plan"] != "free"])
        
        keyboard = [
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("👥 Управление подписками", callback_data="admin_subscriptions")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
        ]
        
        await update.message.reply_text(
            f"👑 Админ Панель\n\n"
            f"📊 Всего пользователей: {total_users}\n"
            f"💳 Активных подписок: {active_subscriptions}\n"
            f"⏰ Запланированных постов: {len([p for p in self.scheduled_posts if p.get('status') != 'sent'])}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def current_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать текущее время в Москве"""
        current_time = format_moscow_time()
        await update.message.reply_text(
            f"🕐 Текущее время в Москве:\n<b>{current_time}</b>",
            parse_mode="HTML"
        )
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        
        # Очищаем временные данные при старте
        if context.user_data:
            context.user_data.clear()
            
        current_time = format_moscow_time()
        user_plan = self.get_user_plan(user_id)
        
        # Основное меню
        keyboard = [
            [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
            [InlineKeyboardButton("📋 Список каналов", callback_data="list_channels")],
            [InlineKeyboardButton("📤 Создать пост", callback_data="create_post")],
            [InlineKeyboardButton("⏰ Запланированные посты", callback_data="scheduled_posts")],
            [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")],
            [InlineKeyboardButton("🕐 Текущее время", callback_data="current_time")]
        ]
        
        # Добавляем админ панель для администратора
        if self.is_admin(user_id):
            keyboard.append([InlineKeyboardButton("👑 Админ Панель", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = f"🤖 Бот для управления публикациями в каналах\n"
        welcome_text += f"🕐 Московское время: <b>{current_time}</b>\n\n"
        
        if self.is_admin(user_id):
            welcome_text += "👑 Вы администратор - полный безлимит навсегда! 🚀\n"
        elif user_plan["plan"] == "free":
            welcome_text += "❌ У вас нет активной подписки\n"
            welcome_text += "💳 Выберите тарифный план для начала работы\n"
        else:
            plan_config = SUBSCRIPTION_PLANS[user_plan["plan"]]
            welcome_text += f"✅ Ваш тариф: {plan_config['name']}\n"
            
            # Показываем статистику использования
            if user_id in self.user_stats:
                posts_today = self.user_stats[user_id]["posts_today"]
                if plan_config["posts_per_day"] == -1:
                    welcome_text += f"📊 Использовано постов сегодня: {posts_today} (безлимит)\n"
                else:
                    welcome_text += f"📊 Использовано постов сегодня: {posts_today}/{plan_config['posts_per_day']}\n"
            
            welcome_text += f"📢 Каналов: {len(self.channels)}"
            if plan_config["channels_limit"] != -1:
                welcome_text += f"/{plan_config['channels_limit']}"
            welcome_text += "\n"
        
        welcome_text += "\nВыберите действие:"
        
        if update.message:
            await update.message.reply_text(
                welcome_text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        else:
            await update.callback_query.edit_message_text(
                welcome_text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = query.from_user.id
        
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
        elif data == "subscription_plans":
            await self.subscription_plans_menu(query)
        elif data.startswith("subscribe_"):
            plan_type = data.replace("subscribe_", "")
            await self.subscribe_menu(query, plan_type, user_id)
        elif data.startswith("confirm_subscribe_"):
            plan_type = data.replace("confirm_subscribe_", "")
            await self.confirm_subscription(query, plan_type, user_id)
        elif data.startswith("delete_channel_"):
            channel_id = data.replace("delete_channel_", "")
            await self.delete_channel(query, channel_id)
        elif data.startswith("select_channel_"):
            channel_id = data.replace("select_channel_", "")
            context.user_data['selected_channel'] = channel_id
            context.user_data['waiting_for_content'] = True
            await self.select_time_menu(query, channel_id, user_id)
        elif data.startswith("time_"):
            time_minutes = int(data.replace("time_", ""))
            await self.schedule_post(query, time_minutes, context, user_id)
        elif data == "publish_now":
            await self.publish_now(query, context, user_id)
        elif data == "custom_time":
            await self.request_custom_time(query, context)
        elif data.startswith("cancel_post_"):
            post_id = data.replace("cancel_post_", "")
            await self.cancel_scheduled_post(query, post_id)
        elif data == "back_to_main":
            await self.start_from_query(query)
        elif data == "admin_panel":
            await self.admin_panel_from_query(query)
        elif data == "admin_stats":
            await self.admin_stats(query)
        elif data == "admin_broadcast":
            await self.admin_broadcast_menu(query)
        elif data == "admin_subscriptions":
            await self.admin_subscriptions_menu(query)
        elif data.startswith("set_subscription_"):
            parts = data.replace("set_subscription_", "").split("_")
            target_user_id = int(parts[0])
            plan_type = parts[1]
            await self.admin_set_subscription(query, target_user_id, plan_type)
    
    async def admin_panel_from_query(self, query):
        """Админ панель из callback"""
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ У вас нет доступа к админ панели")
            return
        
        total_users = len(set(list(self.user_subscriptions.keys()) + 
                            [post.get('user_id') for post in self.scheduled_posts if post.get('user_id')]))
        active_subscriptions = len([sub for sub in self.user_subscriptions.values() if sub["plan"] != "free"])
        
        keyboard = [
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("👥 Управление подписками", callback_data="admin_subscriptions")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
        ]
        
        await query.edit_message_text(
            f"👑 Админ Панель\n\n"
            f"📊 Всего пользователей: {total_users}\n"
            f"💳 Активных подписок: {active_subscriptions}\n"
            f"⏰ Запланированных постов: {len([p for p in self.scheduled_posts if p.get('status') != 'sent'])}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def admin_stats(self, query):
        """Статистика админа"""
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        total_users = len(set(list(self.user_subscriptions.keys()) + 
                            [post.get('user_id') for post in self.scheduled_posts if post.get('user_id')]))
        
        plan_stats = {}
        for plan in SUBSCRIPTION_PLANS:
            plan_stats[plan] = len([sub for sub in self.user_subscriptions.values() if sub["plan"] == plan])
        
        free_users = total_users - sum(plan_stats.values())
        
        stats_text = "📊 Статистика бота:\n\n"
        stats_text += f"👥 Всего пользователей: {total_users}\n"
        stats_text += f"👤 Без подписки: {free_users}\n"
        for plan, config in SUBSCRIPTION_PLANS.items():
            stats_text += f"{config['name']}: {plan_stats.get(plan, 0)}\n"
        
        stats_text += f"\n⏰ Активных постов: {len([p for p in self.scheduled_posts if p.get('status') != 'sent'])}"
        stats_text += f"\n📢 Всего каналов: {len(self.channels)}"
        
        await query.edit_message_text(
            stats_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 В админ панель", callback_data="admin_panel")]
            ])
        )
    
    async def admin_broadcast_menu(self, query):
        """Меню рассылки"""
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        await query.edit_message_text(
            "📢 Рассылка сообщения всем пользователям\n\n"
            "Отправьте сообщение (текст, фото, видео или документ) для рассылки:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 В админ панель", callback_data="admin_panel")]
            ])
        )
        # Устанавливаем флаг ожидания сообщения для рассылки
        self.waiting_for_broadcast = True
    
    async def admin_subscriptions_menu(self, query):
        """Управление подписками пользователей"""
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        # Получаем список пользователей с подписками
        subscribed_users = []
        for user_id, sub_data in self.user_subscriptions.items():
            if sub_data["plan"] != "free":
                try:
                    user = await self.application.bot.get_chat(user_id)
                    username = f"@{user.username}" if user.username else f"ID: {user_id}"
                    subscribed_users.append((user_id, username, sub_data["plan"]))
                except:
                    subscribed_users.append((user_id, f"ID: {user_id}", sub_data["plan"]))
        
        if not subscribed_users:
            await query.edit_message_text(
                "❌ Нет активных подписок",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 В админ панель", callback_data="admin_panel")]
                ])
            )
            return
        
        text = "👥 Управление подписками:\n\n"
        keyboard = []
        
        for user_id, username, plan in subscribed_users[:10]:  # Ограничиваем первые 10
            plan_name = SUBSCRIPTION_PLANS[plan]["name"]
            text += f"👤 {username}\n📦 {plan_name}\n\n"
            
            keyboard.append([
                InlineKeyboardButton(f"❌ Отменить {username}", callback_data=f"set_subscription_{user_id}_free")
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 В админ панель", callback_data="admin_panel")])
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def admin_set_subscription(self, query, user_id: int, plan_type: str):
        """Установка подписки пользователю"""
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        if plan_type == "free":
            if user_id in self.user_subscriptions:
                del self.user_subscriptions[user_id]
            message = "✅ Подписка отменена"
        else:
            self.user_subscriptions[user_id] = {
                "plan": plan_type,
                "subscribed_at": get_moscow_time().isoformat()
            }
            message = f"✅ Установлен тариф: {SUBSCRIPTION_PLANS[plan_type]['name']}"
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 К управлению подписками", callback_data="admin_subscriptions")]
            ])
        )
    
    async def subscription_plans_menu(self, query):
        """Меню тарифных планов"""
        text = "💳 Выберите тарифный план:\n\n"
        
        for plan_key, plan_config in SUBSCRIPTION_PLANS.items():
            text += f"{plan_config['name']}\n"
            text += f"📊 Постов в день: {'∞' if plan_config['posts_per_day'] == -1 else plan_config['posts_per_day']}\n"
            text += f"📢 Каналов: {'∞' if plan_config['channels_limit'] == -1 else plan_config['channels_limit']}\n"
            text += f"💵 Цена: ${plan_config['price']}/месяц\n\n"
        
        keyboard = []
        for plan_key in SUBSCRIPTION_PLANS:
            keyboard.append([
                InlineKeyboardButton(
                    SUBSCRIPTION_PLANS[plan_key]["name"], 
                    callback_data=f"subscribe_{plan_key}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def subscribe_menu(self, query, plan_type: str, user_id: int):
        """Меню подписки на тариф"""
        plan_config = SUBSCRIPTION_PLANS[plan_type]
        
        text = f"📋 Детали тарифа:\n\n"
        text += f"{plan_config['name']}\n"
        text += f"📊 Постов в день: {'∞' if plan_config['posts_per_day'] == -1 else plan_config['posts_per_day']}\n"
        text += f"📢 Каналов: {'∞' if plan_config['channels_limit'] == -1 else plan_config['channels_limit']}\n"
        text += f"💵 Цена: ${plan_config['price']}/месяц\n\n"
        text += f"Для активации подписки:\n"
        text += f"1. Перейдите по ссылке: {plan_config['subscribe_url']}\n"
        text += f"2. Подпишитесь на канал\n"
        text += f"3. Нажмите кнопку 'Проверить подписку'\n\n"
        text += f"После подписки бот проверит ваш статус в канале."
        
        keyboard = [
            [InlineKeyboardButton("🔗 Перейти к подписке", url=plan_config['subscribe_url'])],
            [InlineKeyboardButton("✅ Проверить подписку", callback_data=f"confirm_subscribe_{plan_type}")],
            [InlineKeyboardButton("🔙 К тарифам", callback_data="subscription_plans")]
        ]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )
    
    async def confirm_subscription(self, query, plan_type: str, user_id: int):
        """Проверить подписку и активировать тариф"""
        await query.edit_message_text("🔍 Проверяем вашу подписку...")
        
        is_subscribed = await self.check_channel_subscription(user_id, plan_type)
        
        if not is_subscribed:
            await query.edit_message_text(
                "❌ Подписка не обнаружена!\n\n"
                "Убедитесь что:\n"
                "1. Вы подписались на канал\n"
                "2. Не выходили из канала\n"
                "3. Канал не является приватным\n\n"
                "Попробуйте снова:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Попробовать снова", callback_data=f"confirm_subscribe_{plan_type}")],
                    [InlineKeyboardButton("🔙 К тарифам", callback_data="subscription_plans")]
                ])
            )
            return
        
        # Активируем подписку
        self.user_subscriptions[user_id] = {
            "plan": plan_type,
            "subscribed_at": get_moscow_time().isoformat()
        }
        
        plan_config = SUBSCRIPTION_PLANS[plan_type]
        
        await query.edit_message_text(
            f"✅ Подписка активирована!\n\n"
            f"Тариф: {plan_config['name']}\n"
            f"📊 Постов в день: {'∞' if plan_config['posts_per_day'] == -1 else plan_config['posts_per_day']}\n"
            f"📢 Каналов: {'∞' if plan_config['channels_limit'] == -1 else plan_config['channels_limit']}\n\n"
            f"Можете начинать работу!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Начать работу", callback_data="back_to_main")]
            ])
        )
    
    async def add_channel_menu(self, query, user_id: int):
        """Меню добавления канала"""
        user_plan = self.get_user_plan(user_id)
        
        # Админ всегда может добавлять каналы
        if self.is_admin(user_id):
            await query.edit_message_text(
                "📝 Чтобы добавить канал:\n\n"
                "1. Добавьте бота в канал как администратора\n"
                "2. Отправьте ID канала в формате:\n"
                "<code>@username_channel</code> или <code>-1001234567890</code>\n\n"
                "Отправьте ID канала:",
                parse_mode="HTML"
            )
            return
        
        if user_plan["plan"] == "free":
            await query.edit_message_text(
                "❌ Для добавления каналов нужна активная подписка\n"
                "💳 Выберите тарифный план в меню",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                ])
            )
            return
        
        # Только для обычных пользователей с подпиской
        plan_config = SUBSCRIPTION_PLANS[user_plan["plan"]]
        
        if plan_config["channels_limit"] != -1 and len(self.channels) >= plan_config["channels_limit"]:
            await query.edit_message_text(
                f"❌ Достигнут лимит каналов для вашего тарифа\n"
                f"📢 Максимум: {plan_config['channels_limit']} каналов\n"
                f"💳 Для увеличения лимита смените тарифный план",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
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
    
    async def list_channels_menu(self, query, user_id: int):
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
    
    async def create_post_menu(self, query, user_id: int):
        """Меню создания поста"""
        user_plan = self.get_user_plan(user_id)
        
        # Админ всегда может создавать посты
        if not self.is_admin(user_id) and user_plan["plan"] == "free":
            await query.edit_message_text(
                "❌ Для создания постов нужна активная подписка\n"
                "💳 Выберите тарифный план в меню",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                ])
            )
            return
        
        if not self.can_user_post(user_id):
            # Для админа всегда можно постить
            if not self.is_admin(user_id):
                plan_config = SUBSCRIPTION_PLANS[user_plan["plan"]]
                
                if user_id in self.user_stats:
                    posts_today = self.user_stats[user_id]["posts_today"]
                    if posts_today >= plan_config["posts_per_day"] and plan_config["posts_per_day"] != -1:
                        await query.edit_message_text(
                            f"❌ Достигнут лимит постов на сегодня\n"
                            f"📊 Использовано: {posts_today}/{plan_config['posts_per_day']}\n"
                            f"🕐 Лимит сбросится в 00:00 по Москве",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                            ])
                        )
                        return
                
                if plan_config["channels_limit"] != -1 and len(self.channels) >= plan_config["channels_limit"]:
                    await query.edit_message_text(
                        f"❌ Достигнут лимит каналов\n"
                        f"📢 Максимум: {plan_config['channels_limit']} каналов\n"
                        f"💳 Для увеличения лимита смените тариф",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")],
                            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                        ])
                    )
                    return
        
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
    
    async def select_time_menu(self, query, channel_id: str, user_id: int):
        """Меню выбора времени публикации"""
        channel_name = self.channels.get(channel_id, "Неизвестный канал")
        current_time = format_moscow_time()
        
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
    
    async def publish_now(self, query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
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
            
            # Увеличиваем счетчик постов
            self.increment_user_posts(user_id)
            
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
    
    async def schedule_post(self, query, time_minutes: int, context: ContextTypes.DEFAULT_TYPE, user_id: int):
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
        schedule_time = get_moscow_time() + timedelta(minutes=time_minutes)
        
        await self._create_scheduled_post(query, context, post_data, channel_id, schedule_time, user_id)
    
    async def _create_scheduled_post(self, query, context, post_data, channel_id, schedule_time, user_id):
        """Создание запланированного поста"""
        post_id = f"post_{len(self.scheduled_posts)}_{datetime.now().timestamp()}"
        
        scheduled_post = {
            'id': post_id,
            'channel_id': channel_id,
            'channel_name': self.channels.get(channel_id, "Неизвестный канал"),
            'post_data': post_data,
            'scheduled_time': schedule_time.isoformat(),
            'scheduled_time_moscow': schedule_time.strftime('%d.%m.%Y %H:%M'),
            'status': 'scheduled',
            'user_id': user_id
        }
        
        self.scheduled_posts.append(scheduled_post)
        
        # Запуск задачи для отправки
        asyncio.create_task(self.send_scheduled_post(post_id, schedule_time))
        
        # Увеличиваем счетчик постов
        self.increment_user_posts(user_id)
        
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
        user_posts = [p for p in self.scheduled_posts if p.get('user_id') == user_id and p.get('status') != 'sent']
        current_time = format_moscow_time()
        
        if not user_posts:
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
        
        for post in user_posts[:10]:
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
        user_id = query.from_user.id
        current_time = format_moscow_time()
        user_plan = self.get_user_plan(user_id)
        
        keyboard = [
            [InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel")],
            [InlineKeyboardButton("📋 Список каналов", callback_data="list_channels")],
            [InlineKeyboardButton("📤 Создать пост", callback_data="create_post")],
            [InlineKeyboardButton("⏰ Запланированные посты", callback_data="scheduled_posts")],
            [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")],
            [InlineKeyboardButton("🕐 Текущее время", callback_data="current_time")]
        ]
        
        if self.is_admin(user_id):
            keyboard.append([InlineKeyboardButton("👑 Админ Панель", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = f"🤖 Бот для управления публикациями в каналах\n"
        welcome_text += f"🕐 Московское время: <b>{current_time}</b>\n\n"
        
        if self.is_admin(user_id):
            welcome_text += "👑 Вы администратор - полный безлимит навсегда! 🚀\n"
        elif user_plan["plan"] == "free":
            welcome_text += "❌ У вас нет активной подписки\n"
            welcome_text += "💳 Выберите тарифный план для начала работы\n"
        else:
            plan_config = SUBSCRIPTION_PLANS[user_plan["plan"]]
            welcome_text += f"✅ Ваш тариф: {plan_config['name']}\n"
            
            if user_id in self.user_stats:
                posts_today = self.user_stats[user_id]["posts_today"]
                if plan_config["posts_per_day"] == -1:
                    welcome_text += f"📊 Использовано постов сегодня: {posts_today} (безлимит)\n"
                else:
                    welcome_text += f"📊 Использовано постов сегодня: {posts_today}/{plan_config['posts_per_day']}\n"
            
            welcome_text += f"📢 Каналов: {len(self.channels)}"
            if plan_config["channels_limit"] != -1:
                welcome_text += f"/{plan_config['channels_limit']}"
            welcome_text += "\n"
        
        welcome_text += "\nВыберите действие:"
        
        await query.edit_message_text(
            welcome_text,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    
    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик сообщений"""
        message = update.message
        user_id = message.from_user.id
        
        # Обработка рассылки от админа
        if self.waiting_for_broadcast and user_id == ADMIN_ID:
            self.waiting_for_broadcast = False
            
            # Получаем всех пользователей
            all_users = set(list(self.user_subscriptions.keys()) + 
                          [post.get('user_id') for post in self.scheduled_posts if post.get('user_id')])
            
            success_count = 0
            error_count = 0
            
            # Отправляем сообщение всем пользователям
            for user_id in all_users:
                try:
                    if message.text:
                        await self.application.bot.send_message(
                            chat_id=user_id,
                            text=message.text
                        )
                    elif message.photo:
                        await self.application.bot.send_photo(
                            chat_id=user_id,
                            photo=message.photo[-1].file_id,
                            caption=message.caption or ''
                        )
                    elif message.video:
                        await self.application.bot.send_video(
                            chat_id=user_id,
                            video=message.video.file_id,
                            caption=message.caption or ''
                        )
                    elif message.document:
                        await self.application.bot.send_document(
                            chat_id=user_id,
                            document=message.document.file_id,
                            caption=message.caption or ''
                        )
                    success_count += 1
                    await asyncio.sleep(0.1)  # Задержка чтобы не превысить лимиты
                except Exception as e:
                    logger.error(f"Ошибка отправки рассылки пользователю {user_id}: {e}")
                    error_count += 1
            
            await message.reply_text(
                f"📢 Рассылка завершена:\n"
                f"✅ Успешно: {success_count}\n"
                f"❌ Ошибок: {error_count}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("👑 В админ панель", callback_data="admin_panel")]
                ])
            )
            return
        
        # Обработка пользовательского времени
        if context.user_data.get('waiting_for_custom_time'):
            time_str = message.text.strip()
            context.user_data.pop('waiting_for_custom_time', None)
            
            try:
                schedule_time = parse_custom_time(time_str)
                current_time = get_moscow_time()
                
                time_difference = (schedule_time - current_time).total_seconds()
                if time_difference < 60:
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
                        'status': 'scheduled',
                        'user_id': user_id
                    }
                    
                    self.scheduled_posts.append(scheduled_post)
                    asyncio.create_task(self.send_scheduled_post(post_id, schedule_time))
                    
                    # Увеличиваем счетчик постов
                    self.increment_user_posts(user_id)
                    
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
        
        # Обработка добавления канала
        if message.text and (message.text.startswith('@') or message.text.startswith('-100')):
            user_plan = self.get_user_plan(user_id)
            
            # Админ всегда может добавлять каналы
            if not self.is_admin(user_id) and user_plan["plan"] == "free":
                await message.reply_text(
                    "❌ Для добавления каналов нужна активная подписка",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")],
                        [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                    ])
                )
                return
            
            # Для обычных пользователей проверяем лимиты
            if not self.is_admin(user_id):
                plan_config = SUBSCRIPTION_PLANS[user_plan["plan"]]
                
                if plan_config["channels_limit"] != -1 and len(self.channels) >= plan_config["channels_limit"]:
                    await message.reply_text(
                        f"❌ Достигнут лимит каналов для вашего тарифа\n"
                        f"📢 Максимум: {plan_config['channels_limit']} каналов",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("💳 Сменить тариф", callback_data="subscription_plans")],
                            [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                        ])
                    )
                    return
            
            channel_id = message.text.strip()
            self.channels[channel_id] = channel_id
            
            await message.reply_text(
                f"✅ Канал {channel_id} добавлен!",
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
        
        # Проверяем может ли пользователь создать пост
        if not self.can_user_post(user_id):
            user_plan = self.get_user_plan(user_id)
            
            # Админ всегда может создавать посты
            if not self.is_admin(user_id):
                plan_config = SUBSCRIPTION_PLANS[user_plan["plan"]]
                
                if user_id in self.user_stats:
                    posts_today = self.user_stats[user_id]["posts_today"]
                    if posts_today >= plan_config["posts_per_day"] and plan_config["posts_per_day"] != -1:
                        await message.reply_text(
                            f"❌ Достигнут лимит постов на сегодня\n"
                            f"📊 Использовано: {posts_today}/{plan_config['posts_per_day']}\n"
                            f"🕐 Лимит сбросится в 00:00 по Москве",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                            ])
                        )
                        return
                
                await message.reply_text(
                    "❌ Не удалось создать пост. Проверьте лимиты вашего тарифа",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")],
                        [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                    ])
                )
                return
        
        # Сохраняем данные поста
        post_data = {}
        
        if message.text and not (message.photo or message.video or message.document):
            post_data = {
                'type': 'text',
                'text': message.text,
                'message_id': message.message_id,
                'chat_id': message.chat_id
            }
        elif message.photo:
            post_data = {
                'type': 'photo',
                'file_id': message.photo[-1].file_id,
                'caption': message.caption or '',
                'text': message.caption or '',
                'message_id': message.message_id,
                'chat_id': message.chat_id
            }
        elif message.video:
            post_data = {
                'type': 'video',
                'file_id': message.video.file_id,
                'caption': message.caption or '',
                'text': message.caption or '',
                'message_id': message.message_id,
                'chat_id': message.chat_id
            }
        elif message.document:
            post_data = {
                'type': 'document',
                'file_id': message.document.file_id,
                'caption': message.caption or '',
                'text': message.caption or '',
                'message_id': message.message_id,
                'chat_id': message.chat_id
            }
        else:
            await message.reply_text(
                "❌ Неподдерживаемый тип сообщения. Отправьте текст, фото, видео или документ.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                ])
            )
            return
        
        context.user_data['post_data'] = post_data
        context.user_data['waiting_for_content'] = False
        
        current_time = format_moscow_time()
        channel_id = context.user_data.get('selected_channel', 'Неизвестный канал')
        channel_name = self.channels.get(channel_id, "Неизвестный канал")
        
        content_info = ""
        if post_data['type'] == 'text':
            content_info = f"📝 Текст: {post_data['text'][:50]}..."
        elif post_data['type'] in ['photo', 'video', 'document']:
            media_type = {'photo': '🖼 Фото', 'video': '🎥 Видео', 'document': '📎 Документ'}[post_data['type']]
            content_info = f"{media_type}"
            if post_data.get('text'):
                content_info += f" + текст: {post_data['text'][:50]}..."
        
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
    
    async def send_scheduled_post(self, post_id: str, schedule_time: datetime):
        """Отправка запланированного поста"""
        try:
            now_moscow = get_moscow_time()
            
            if schedule_time <= now_moscow:
                delay = 0
            else:
                delay = (schedule_time - now_moscow).total_seconds()
            
            if delay > 0:
                logger.info(f"Ожидание {delay} секунд до отправки поста {post_id}")
                await asyncio.sleep(delay)
            
            post = next((p for p in self.scheduled_posts if p['id'] == post_id), None)
            if not post:
                logger.warning(f"Пост {post_id} не найден")
                return
            
            post_data = post['post_data']
            channel_id = post['channel_id']
            
            logger.info(f"Отправка поста {post_id} в канал {channel_id}")
            
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
    print("Бот запущен с полной системой подписок и админ-панелью...")
    bot.application.run_polling()

if __name__ == "__main__":
    main()
