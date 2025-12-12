import os
import asyncio
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pytz

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    ChatInviteLink,
    ChatJoinRequest
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

# Тарифные планы
DEFAULT_SUBSCRIPTION_PLANS = {
    "basic": {
        "name": "💰 Базовый - $1/месяц",
        "price": 1,
        "posts_per_day": 2,
        "channels_limit": 1,
        "channel_id": "",      # ID приватного канала
        "channel_name": "",    # Название канала для отображения
        "duration_days": 30
    },
    "standard": {
        "name": "💎 Стандартный - $3/месяц",
        "price": 3,
        "posts_per_day": 6,
        "channels_limit": 3,
        "channel_id": "",
        "channel_name": "",
        "duration_days": 30
    },
    "premium": {
        "name": "🚀 Премиум - $5/месяц",
        "price": 5,
        "posts_per_day": -1,
        "channels_limit": -1,
        "channel_id": "",
        "channel_name": "",
        "duration_days": 30
    }
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
    """Парсинг пользовательского времени"""
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
        
        # Хранилища данных
        self.channels: Dict[str, str] = {}  # Каналы для публикаций
        self.scheduled_posts: List[Dict] = []  # Запланированные посты
        self.user_subscriptions: Dict[int, Dict] = {}  # Подписки пользователей
        self.user_stats: Dict[int, Dict] = {}  # Статистика пользователей
        self.invite_links: Dict[str, ChatInviteLink] = {}  # Ссылки-приглашения
        self.pending_checks: Dict[str, datetime] = {}  # Ожидающие проверки
        
        # Настройки тарифов
        self.subscription_plans = self.load_settings()
        
        # Флаги состояния
        self.waiting_for_broadcast = False
        self.waiting_for_plan_settings = None
        
        self.setup_handlers()
        self.setup_job_queue()
    
    def load_settings(self):
        """Загрузить настройки тарифов"""
        try:
            with open('subscription_settings.json', 'r', encoding='utf-8') as f:
                settings = json.load(f)
                # Проверяем структуру
                for plan in DEFAULT_SUBSCRIPTION_PLANS:
                    if plan not in settings:
                        settings[plan] = DEFAULT_SUBSCRIPTION_PLANS[plan]
                return settings
        except FileNotFoundError:
            # Создаем файл с дефолтными настройками
            self.save_settings(DEFAULT_SUBSCRIPTION_PLANS)
            return DEFAULT_SUBSCRIPTION_PLANS.copy()
        except Exception as e:
            logger.error(f"Ошибка загрузки настроек: {e}")
            return DEFAULT_SUBSCRIPTION_PLANS.copy()
    
    def save_settings(self, settings=None):
        """Сохранить настройки тарифов"""
        if settings is None:
            settings = self.subscription_plans
            
        try:
            with open('subscription_settings.json', 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек: {e}")
    
    def is_admin(self, user_id: int) -> bool:
        """Проверить является ли пользователь администратором"""
        return user_id == ADMIN_ID
    
    def setup_handlers(self):
        """Настройка обработчиков команд"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("time", self.current_time))
        self.application.add_handler(CommandHandler("admin", self.admin_panel))
        self.application.add_handler(CommandHandler("check", self.check_subscription))
        self.application.add_handler(CommandHandler("setup", self.setup_channel))
        self.application.add_handler(CommandHandler("test", self.test_channel))
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        self.application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, self.message_handler))
    
    def setup_job_queue(self):
        """Настройка фоновых задач"""
        job_queue = self.application.job_queue
        if job_queue:
            job_queue.run_repeating(self.cleanup_expired_invites, interval=3600, first=10)
            job_queue.run_repeating(self.check_pending_subscriptions, interval=60, first=30)
    
    async def cleanup_expired_invites(self, context):
        """Очистка просроченных ссылок-приглашений"""
        now = datetime.now()
        expired_keys = []
        
        for key, timestamp in self.pending_checks.items():
            if now - timestamp > timedelta(hours=2):
                expired_keys.append(key)
        
        for key in expired_keys:
            del self.pending_checks[key]
            
        if expired_keys:
            logger.info(f"Очищено {len(expired_keys)} просроченных проверок")
    
    async def check_pending_subscriptions(self, context):
        """Фоновая проверка ожидающих подписок"""
        try:
            for plan_key, plan_config in self.subscription_plans.items():
                channel_id = plan_config.get('channel_id')
                if not channel_id:
                    continue
                
                # Ищем пользователей, которые недавно проверяли подписку
                check_key = f"{plan_key}_last_check"
                if check_key in self.pending_checks:
                    last_check = self.pending_checks[check_key]
                    if datetime.now() - last_check < timedelta(minutes=5):
                        continue
                
                # Здесь можно добавить периодическую проверку всех активных подписок
                self.pending_checks[check_key] = datetime.now()
                
        except Exception as e:
            logger.error(f"Ошибка в фоновой проверке: {e}")
    
    async def create_invite_link(self, plan_type: str, user_id: int) -> Optional[str]:
        """Создать ссылку-приглашение в приватный канал"""
        try:
            plan_config = self.subscription_plans[plan_type]
            channel_id = plan_config.get('channel_id')
            
            if not channel_id:
                logger.error(f"ID канала для тарифа {plan_type} не настроен")
                return None
            
            # Проверяем, есть ли у бота доступ к каналу
            try:
                bot_member = await self.application.bot.get_chat_member(
                    chat_id=channel_id,
                    user_id=self.application.bot.id
                )
                
                if bot_member.status not in ['administrator', 'creator']:
                    logger.error(f"Бот не является администратором канала {channel_id}")
                    
                    # Уведомляем администратора
                    try:
                        await self.application.bot.send_message(
                            chat_id=ADMIN_ID,
                            text=f"⚠️ Для тарифа {plan_type} бот не является администратором!\n"
                                 f"Канал: {channel_id}\n"
                                 f"Добавьте бота @{self.application.bot.username} как администратора"
                        )
                    except:
                        pass
                    
                    return None
                    
            except Exception as e:
                error_msg = str(e).lower()
                if 'bot was kicked' in error_msg or 'bot is not a member' in error_msg:
                    logger.error(f"Бот был удален из канала {channel_id} или не является участником")
                    
                    try:
                        await self.application.bot.send_message(
                            chat_id=ADMIN_ID,
                            text=f"🚨 СРОЧНО: Бот удален из канала для тарифа {plan_type}!\n"
                                 f"Канал: {channel_id}\n"
                                 f"Добавьте бота обратно как администратора"
                        )
                    except:
                        pass
                    
                    return None
                else:
                    logger.error(f"Ошибка проверки доступа бота: {e}")
                    return None
            
            # Создаем уникальную ссылку-приглашение
            try:
                invite_link = await self.application.bot.create_chat_invite_link(
                    chat_id=channel_id,
                    name=f"Sub_{plan_type}_{user_id}_{int(datetime.now().timestamp())}",
                    expire_date=datetime.now() + timedelta(hours=24),
                    member_limit=1,
                    creates_join_request=False  # False = прямой доступ, True = запрос на вступление
                )
                
                # Сохраняем информацию о ссылке
                self.invite_links[f"{user_id}_{plan_type}"] = invite_link
                
                logger.info(f"Создана ссылка для пользователя {user_id} на тариф {plan_type}")
                return invite_link.invite_link
                
            except Exception as e:
                logger.error(f"Ошибка создания ссылки: {e}")
                
                if 'not enough rights' in str(e).lower():
                    try:
                        await self.application.bot.send_message(
                            chat_id=ADMIN_ID,
                            text=f"⚠️ Боту не хватает прав для создания ссылок!\n"
                                 f"Тариф: {plan_type}\n"
                                 f"Канал: {channel_id}\n"
                                 f"Дайте боту права: 'Приглашать пользователей'"
                        )
                    except:
                        pass
                
                return None
                
        except Exception as e:
            logger.error(f"Ошибка в create_invite_link: {e}")
            return None
    
    async def check_channel_subscription(self, user_id: int, plan_type: str) -> bool:
        """Проверить подписку пользователя на приватный канал"""
        try:
            plan_config = self.subscription_plans[plan_type]
            channel_id = plan_config.get('channel_id')
            
            if not channel_id:
                logger.error(f"ID канала для тарифа {plan_type} не настроен")
                return False
            
            # Пытаемся получить информацию о пользователе в канале
            chat_member = await self.application.bot.get_chat_member(
                chat_id=channel_id,
                user_id=user_id
            )
            
            # Проверяем статус
            status = chat_member.status
            logger.info(f"Пользователь {user_id} в канале {channel_id}: статус {status}")
            
            # Допустимые статусы
            return status in ['member', 'administrator', 'creator', 'restricted']
            
        except Exception as e:
            error_msg = str(e).lower()
            logger.warning(f"Ошибка проверки подписки {user_id} на {plan_type}: {error_msg}")
            
            # Анализируем ошибку
            if 'user not found' in error_msg or 'user not participant' in error_msg:
                # Пользователь точно не в канале
                return False
            elif 'bot was kicked' in error_msg or 'bot is not a member' in error_msg:
                # Бота нет в канале
                logger.error(f"Бот не является участником канала {plan_config.get('channel_id')}")
                return False
            elif 'chat not found' in error_msg:
                # Канал не существует или бот не имеет доступа
                logger.error(f"Канал не найден или доступ запрещен: {plan_config.get('channel_id')}")
                return False
            else:
                # Другие ошибки
                logger.error(f"Неизвестная ошибка при проверке: {e}")
                return False
    
    async def setup_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Настройка приватного канала для тарифа"""
        user_id = update.effective_user.id
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ Только администратор может настраивать каналы")
            return
        
        if not context.args:
            await update.message.reply_text(
                "🔧 Настройка приватного канала:\n\n"
                "Использование: /setup <тариф> <id_канала> <название>\n\n"
                "Пример:\n"
                "/setup basic -1001234567890 Мой_Приватный_Канал\n\n"
                "Доступные тарифы:\n" +
                "\n".join([f"• {key}: {self.subscription_plans[key]['name']}" for key in self.subscription_plans])
            )
            return
        
        if len(context.args) < 3:
            await update.message.reply_text("❌ Недостаточно аргументов. Формат: /setup <тариф> <id_канала> <название>")
            return
        
        plan_type = context.args[0].lower()
        channel_id = context.args[1]
        channel_name = " ".join(context.args[2:])
        
        if plan_type not in self.subscription_plans:
            await update.message.reply_text(f"❌ Неизвестный тариф: {plan_type}")
            return
        
        # Проверяем формат ID канала
        if not (channel_id.startswith('-100') or channel_id.startswith('@')):
            await update.message.reply_text(
                "❌ Неверный формат ID канала\n"
                "Должно начинаться с '-100' для супергрупп или '@' для публичных каналов"
            )
            return
        
        # Проверяем доступ бота к каналу
        try:
            chat = await self.application.bot.get_chat(channel_id)
            chat_type = chat.type
            
            if chat_type not in ['channel', 'supergroup']:
                await update.message.reply_text(f"❌ Это не канал/супергруппа. Тип: {chat_type}")
                return
            
            # Проверяем, является ли бот администратором
            try:
                bot_member = await self.application.bot.get_chat_member(
                    chat_id=channel_id,
                    user_id=self.application.bot.id
                )
                
                if bot_member.status not in ['administrator', 'creator']:
                    await update.message.reply_text(
                        f"⚠️ Бот не является администратором этого канала!\n\n"
                        f"Добавьте @{self.application.bot.username} в канал как администратора и дайте права:\n"
                        f"1. ✅ Приглашать пользователей\n"
                        f"2. ✅ Просмотр участников\n"
                        f"3. ✅ Отправка сообщений"
                    )
                    return
                    
            except Exception as e:
                if 'bot is not a member' in str(e).lower():
                    await update.message.reply_text(
                        f"❌ Бот не является участником канала\n"
                        f"Добавьте @{self.application.bot.username} в канал как администратора"
                    )
                    return
                else:
                    raise e
            
            # Сохраняем настройки
            self.subscription_plans[plan_type]['channel_id'] = channel_id
            self.subscription_plans[plan_type]['channel_name'] = channel_name
            
            self.save_settings()
            
            await update.message.reply_text(
                f"✅ Канал настроен для тарифа {plan_type}!\n\n"
                f"📋 Тариф: {self.subscription_plans[plan_type]['name']}\n"
                f"🆔 ID канала: {channel_id}\n"
                f"📢 Название: {channel_name}\n"
                f"👥 Тип: {chat_type}\n\n"
                f"Теперь можно продавать подписки на этот приватный канал!"
            )
            
        except Exception as e:
            error_msg = str(e).lower()
            if 'chat not found' in error_msg:
                await update.message.reply_text(
                    "❌ Канал не найден\n"
                    "Убедитесь что:\n"
                    "1. Канал существует\n"
                    "2. ID канала правильный\n"
                    "3. Бот имеет доступ к каналу"
                )
            else:
                await update.message.reply_text(f"❌ Ошибка настройки канала: {str(e)[:200]}")
    
    async def test_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Тестирование доступа к каналу"""
        user_id = update.effective_user.id
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ Только администратор может тестировать каналы")
            return
        
        if not context.args:
            await update.message.reply_text(
                "🔍 Тестирование доступа к каналу:\n\n"
                "Использование: /test <тариф>\n\n"
                "Пример: /test basic\n\n"
                "Доступные тарифы:\n" +
                "\n".join([f"• {key}: {self.subscription_plans[key]['name']}" for key in self.subscription_plans])
            )
            return
        
        plan_type = context.args[0].lower()
        
        if plan_type not in self.subscription_plans:
            await update.message.reply_text(f"❌ Неизвестный тариф: {plan_type}")
            return
        
        plan_config = self.subscription_plans[plan_type]
        channel_id = plan_config.get('channel_id')
        
        if not channel_id:
            await update.message.reply_text(f"❌ Для тарифа {plan_type} не настроен канал")
            return
        
        await update.message.reply_text("🔍 Проверяем доступ к каналу...")
        
        try:
            # Проверяем информацию о канале
            chat = await self.application.bot.get_chat(channel_id)
            
            # Проверяем статус бота
            bot_member = await self.application.bot.get_chat_member(
                chat_id=channel_id,
                user_id=self.application.bot.id
            )
            
            # Проверяем права бота
            can_invite = bot_member.can_invite_users if hasattr(bot_member, 'can_invite_users') else False
            can_restrict = bot_member.can_restrict_members if hasattr(bot_member, 'can_restrict_members') else False
            
            # Пытаемся создать тестовую ссылку
            test_link = None
            try:
                invite_link = await self.application.bot.create_chat_invite_link(
                    chat_id=channel_id,
                    name="TEST_LINK",
                    expire_date=datetime.now() + timedelta(minutes=5),
                    member_limit=1
                )
                test_link = invite_link.invite_link
            except Exception as e:
                test_link_error = str(e)
            
            # Формируем отчет
            report = f"📊 Отчет по каналу для тарифа {plan_type}:\n\n"
            report += f"📋 Тариф: {plan_config['name']}\n"
            report += f"🆔 ID канала: {channel_id}\n"
            report += f"📢 Название: {chat.title}\n"
            report += f"👥 Тип: {chat.type}\n"
            report += f"👤 Участников: {chat.member_count if chat.member_count else 'Неизвестно'}\n\n"
            
            report += f"🤖 Статус бота: {bot_member.status}\n"
            report += f"🔗 Может приглашать: {'✅ Да' if can_invite else '❌ Нет'}\n"
            report += f"👁 Может просматривать участников: {'✅ Да' if can_restrict else '❌ Нет'}\n\n"
            
            if test_link:
                report += f"🔗 Тестовая ссылка (действует 5 мин):\n{test_link}\n\n"
                report += f"✅ Канал настроен правильно! Можно продавать подписки."
            else:
                report += f"❌ Не удалось создать ссылку: {test_link_error}\n\n"
                report += f"⚠️ Проверьте права бота в канале!"
            
            await update.message.reply_text(report, disable_web_page_preview=True)
            
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка тестирования: {str(e)[:300]}")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        
        # Очищаем временные данные
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
            plan_config = self.subscription_plans[user_plan["plan"]]
            welcome_text += f"✅ Ваш тариф: {plan_config['name']}\n"
            
            # Проверяем актуальность подписки
            is_expired = self.is_subscription_expired(user_id)
            if is_expired:
                welcome_text += "❌ Подписка истекла. Продлите для продолжения работы.\n"
            else:
                if "expires_at" in user_plan:
                    expires_at = datetime.fromisoformat(user_plan["expires_at"]).replace(tzinfo=MOSCOW_TZ)
                    days_left = (expires_at - get_moscow_time()).days
                    welcome_text += f"⏳ Дней осталось: {days_left}\n"
                
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
        
        plan_config = self.subscription_plans[user_plan["plan"]]
        
        # Проверяем актуальность подписки
        is_subscribed = await self.check_channel_subscription(user_id, user_plan["plan"])
        is_expired = self.is_subscription_expired(user_id)
        
        if not is_subscribed or is_expired:
            # Если пользователь отписался или подписка истекла
            if user_id in self.user_subscriptions:
                del self.user_subscriptions[user_id]
            
            if is_expired:
                message = "❌ Ваша подписка истекла"
            else:
                message = "❌ Вы отписались от приватного канала"
            
            await update.message.reply_text(
                f"{message}\n💳 Для возобновления доступа оформите подписку заново",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")]
                ])
            )
            return
        
        # Показываем информацию о подписке
        expires_at = datetime.fromisoformat(user_plan["expires_at"]).replace(tzinfo=MOSCOW_TZ)
        days_left = (expires_at - get_moscow_time()).days
        
        text = f"✅ Активная подписка:\n{plan_config['name']}\n"
        text += f"📢 Канал: {plan_config.get('channel_name', 'Приватный канал')}\n"
        text += f"⏳ Дней осталось: {days_left}\n"
        
        if user_id in self.user_stats:
            posts_today = self.user_stats[user_id]["posts_today"]
            if plan_config["posts_per_day"] == -1:
                text += f"📊 Использовано постов сегодня: {posts_today} (безлимит)\n"
            else:
                text += f"📊 Использовано постов сегодня: {posts_today}/{plan_config['posts_per_day']}\n"
        
        text += f"📢 Добавлено каналов: {len(self.channels)}"
        if plan_config["channels_limit"] != -1:
            text += f"/{plan_config['channels_limit']}"
        
        await update.message.reply_text(text)
    
    def get_user_plan(self, user_id: int) -> Dict:
        """Получить тарифный план пользователя"""
        # Админ всегда имеет безлимит
        if self.is_admin(user_id):
            return {"plan": "admin", "subscribed_at": get_moscow_time().isoformat()}
        
        return self.user_subscriptions.get(user_id, {"plan": "free"})
    
    def is_subscription_expired(self, user_id: int) -> bool:
        """Проверить истекла ли подписка пользователя"""
        if user_id not in self.user_subscriptions:
            return True
        
        user_plan = self.user_subscriptions[user_id]
        if "expires_at" not in user_plan:
            return True
        
        try:
            expires_at = datetime.fromisoformat(user_plan["expires_at"]).replace(tzinfo=MOSCOW_TZ)
            return get_moscow_time() > expires_at
        except:
            return True
    
    def can_user_post(self, user_id: int) -> bool:
        """Может ли пользователь создать пост"""
        # Админ всегда может постить
        if self.is_admin(user_id):
            return True
        
        user_plan = self.get_user_plan(user_id)
        
        if user_plan["plan"] == "free":
            return False
        
        # Проверяем не истекла ли подписка
        if self.is_subscription_expired(user_id):
            return False
        
        # Проверяем подписку на канал
        if user_plan["plan"] != "admin":
            # Для обычных пользователей проверяем подписку
            # (проверка делается асинхронно, здесь только проверяем наличие данных)
            pass
        
        plan_config = self.subscription_plans[user_plan["plan"]]
        
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
        elif data.startswith("refresh_link_"):
            plan_type = data.replace("refresh_link_", "")
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
        elif data == "admin_settings":
            await self.admin_settings_menu(query)
        elif data == "admin_subscriptions":
            await self.admin_subscriptions_menu(query)
        elif data.startswith("set_subscription_"):
            parts = data.replace("set_subscription_", "").split("_")
            target_user_id = int(parts[0])
            plan_type = parts[1]
            await self.admin_set_subscription(query, target_user_id, plan_type)
        elif data.startswith("edit_plan_"):
            plan_type = data.replace("edit_plan_", "")
            await self.admin_edit_plan_menu(query, plan_type)
        elif data.startswith("save_plan_"):
            plan_type = data.replace("save_plan_", "")
            await self.admin_save_plan(query, plan_type, context)
        elif data == "save_settings":
            self.save_settings()
            await query.answer("✅ Настройки сохранены!")
    
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Админ панель"""
        if not self.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ У вас нет доступа к админ панели")
            return
        
        total_users = len(set(list(self.user_subscriptions.keys()) + 
                            [post.get('user_id') for post in self.scheduled_posts if post.get('user_id')]))
        active_subscriptions = len([sub for sub in self.user_subscriptions.values() if not self.is_subscription_expired(list(self.user_subscriptions.keys())[list(self.user_subscriptions.values()).index(sub)])])
        
        keyboard = [
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("⚙️ Настройка тарифов", callback_data="admin_settings")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("👥 Управление подписками", callback_data="admin_subscriptions")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
        ]
        
        await update.message.reply_text(
            f"👑 Админ Панель\n\n"
            f"📊 Всего пользователей: {total_users}\n"
            f"💳 Активных подписок: {active_subscriptions}\n"
            f"⏰ Запланированных постов: {len([p for p in self.scheduled_posts if p.get('status') != 'sent'])}\n"
            f"📢 Приватных каналов настроено: {sum(1 for plan in self.subscription_plans.values() if plan.get('channel_id'))}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def current_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать текущее время в Москве"""
        current_time = format_moscow_time()
        await update.message.reply_text(
            f"🕐 Текущее время в Москве:\n<b>{current_time}</b>",
            parse_mode="HTML"
        )
    
    async def subscription_plans_menu(self, query):
        """Меню тарифных планов"""
        text = "💳 Выберите тарифный план:\n\n"
        
        for plan_key, plan_config in self.subscription_plans.items():
            text += f"{plan_config['name']}\n"
            text += f"📊 Постов в день: {'∞' if plan_config['posts_per_day'] == -1 else plan_config['posts_per_day']}\n"
            text += f"📢 Каналов: {'∞' if plan_config['channels_limit'] == -1 else plan_config['channels_limit']}\n"
            text += f"💵 Цена: ${plan_config['price']}/месяц\n"
            text += f"⏳ Длительность: {plan_config.get('duration_days', 30)} дней\n"
            
            if plan_config.get('channel_id'):
                text += f"🔒 Доступ к приватному каналу: ✅\n"
            else:
                text += f"🔒 Доступ к приватному каналу: ⚠️ (не настроен)\n"
            
            text += "\n"
        
        keyboard = []
        for plan_key in self.subscription_plans:
            keyboard.append([
                InlineKeyboardButton(
                    self.subscription_plans[plan_key]["name"], 
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
        plan_config = self.subscription_plans[plan_type]
        
        text = f"📋 Детали тарифа:\n\n{plan_config['name']}\n"
        text += f"📊 Постов в день: {'∞' if plan_config['posts_per_day'] == -1 else plan_config['posts_per_day']}\n"
        text += f"📢 Каналов: {'∞' if plan_config['channels_limit'] == -1 else plan_config['channels_limit']}\n"
        text += f"💵 Цена: ${plan_config['price']}/месяц\n"
        text += f"⏳ Длительность: {plan_config.get('duration_days', 30)} дней\n\n"
        
        # Проверяем настроен ли канал
        if not plan_config.get('channel_id'):
            text += "❌ Приватный канал для этого тарифа еще не настроен.\n"
            text += "Обратитесь к администратору для получения доступа."
            
            keyboard = [
                [InlineKeyboardButton("🔙 К тарифам", callback_data="subscription_plans")]
            ]
        else:
            # Создаем ссылку-приглашение
            invite_link = await self.create_invite_link(plan_type, user_id)
            
            if invite_link:
                text += "🔗 Для активации подписки:\n"
                text += "1. Нажмите кнопку '🔗 Вступить в приватный канал'\n"
                text += "2. Нажмите 'Присоединиться' в открывшемся Telegram\n"
                text += "3. Вернитесь в бот и нажмите '✅ Проверить подписку'\n\n"
                text += f"📢 Канал: {plan_config.get('channel_name', 'Приватный канал')}\n"
                text += "⏱ Ссылка действует 24 часа\n\n"
                text += "⚠️ После вступления в канал НЕ выходите из него!"
                
                keyboard = [
                    [InlineKeyboardButton("🔗 Вступить в приватный канал", url=invite_link)],
                    [InlineKeyboardButton("✅ Проверить подписку", callback_data=f"confirm_subscribe_{plan_type}")],
                    [InlineKeyboardButton("🔄 Обновить ссылку", callback_data=f"refresh_link_{plan_type}")],
                    [InlineKeyboardButton("🔙 К тарифам", callback_data="subscription_plans")]
                ]
            else:
                text += "❌ Не удалось создать ссылку для вступления.\n"
                text += "Возможные причины:\n"
                text += "• Бот не является администратором канала\n"
                text += "• У бота нет прав создавать ссылки\n"
                text += "• Канал не существует\n\n"
                text += "Обратитесь к администратору."
                
                keyboard = [
                    [InlineKeyboardButton("🔄 Попробовать снова", callback_data=f"subscribe_{plan_type}")],
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
        
        # Даем время Telegram обновить информацию
        await asyncio.sleep(3)
        
        is_subscribed = await self.check_channel_subscription(user_id, plan_type)
        
        if not is_subscribed:
            plan_config = self.subscription_plans[plan_type]
            channel_id = plan_config.get('channel_id', 'не настроен')
            
            message = "❌ Подписка не обнаружена!\n\n"
            message += "Убедитесь что:\n"
            message += "1. Вы перешли по ссылке выше\n"
            message += "2. Нажали 'Присоединиться' в Telegram\n"
            message += "3. Не вышли из канала\n"
            message += "4. Подождали 10-20 секунд после вступления\n\n"
            message += "Если все сделали правильно, но бот не видит подписку:\n"
            message += "1. Выйдите из канала и зайдите снова\n"
            message += "2. Или попробуйте новую ссылку\n\n"
            message += f"ID канала: {channel_id}"
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Проверить снова", callback_data=f"confirm_subscribe_{plan_type}")],
                    [InlineKeyboardButton("🔗 Новая ссылка", callback_data=f"refresh_link_{plan_type}")],
                    [InlineKeyboardButton("🔙 К тарифам", callback_data="subscription_plans")]
                ])
            )
            return
        
        # Активируем подписку
        plan_config = self.subscription_plans[plan_type]
        expires_at = get_moscow_time() + timedelta(days=plan_config.get('duration_days', 30))
        
        self.user_subscriptions[user_id] = {
            "plan": plan_type,
            "subscribed_at": get_moscow_time().isoformat(),
            "expires_at": expires_at.isoformat(),
            "channel_id": plan_config.get('channel_id')
        }
        
        await query.edit_message_text(
            f"✅ Подписка активирована!\n\n"
            f"Тариф: {plan_config['name']}\n"
            f"📢 Канал: {plan_config.get('channel_name', 'Приватный канал')}\n"
            f"📊 Постов в день: {'∞' if plan_config['posts_per_day'] == -1 else plan_config['posts_per_day']}\n"
            f"⏳ Действует до: {expires_at.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"🎉 Теперь вы можете публиковать посты!",
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
        
        # Проверяем не истекла ли подписка
        if self.is_subscription_expired(user_id):
            await query.edit_message_text(
                "❌ Ваша подписка истекла\n"
                "💳 Продлите подписку для добавления каналов",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                ])
            )
            return
        
        # Проверяем подписку на приватный канал
        is_subscribed = await self.check_channel_subscription(user_id, user_plan["plan"])
        if not is_subscribed:
            await query.edit_message_text(
                "❌ Вы отписались от приватного канала!\n"
                "💳 Обновите подписку для добавления каналов",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Проверить подписку", callback_data="check_subscription")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                ])
            )
            return
        
        # Только для обычных пользователей с подпиской
        plan_config = self.subscription_plans[user_plan["plan"]]
        
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
                plan_config = self.subscription_plans[user_plan["plan"]]
                
                if self.is_subscription_expired(user_id):
                    await query.edit_message_text(
                        "❌ Ваша подписка истекла\n"
                        "💳 Продлите подписку для создания постов",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")],
                            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                        ])
                    )
                    return
                
                # Проверяем подписку на приватный канал
                is_subscribed = await self.check_channel_subscription(user_id, user_plan["plan"])
                if not is_subscribed:
                    await query.edit_message_text(
                        "❌ Вы отписались от приватного канала!\n"
                        "💳 Обновите подписку для создания постов",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("💳 Проверить подписку", callback_data="check_subscription")],
                            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                        ])
                    )
                    return
                
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
            plan_config = self.subscription_plans[user_plan["plan"]]
            welcome_text += f"✅ Ваш тариф: {plan_config['name']}\n"
            
            # Проверяем актуальность подписки
            is_expired = self.is_subscription_expired(user_id)
            if is_expired:
                welcome_text += "❌ Подписка истекла. Продлите для продолжения работы.\n"
            else:
                if "expires_at" in user_plan:
                    expires_at = datetime.fromisoformat(user_plan["expires_at"]).replace(tzinfo=MOSCOW_TZ)
                    days_left = (expires_at - get_moscow_time()).days
                    welcome_text += f"⏳ Дней осталось: {days_left}\n"
                
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
    
    async def admin_panel_from_query(self, query):
        """Админ панель из callback"""
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ У вас нет доступа к админ панели")
            return
        
        total_users = len(set(list(self.user_subscriptions.keys()) + 
                            [post.get('user_id') for post in self.scheduled_posts if post.get('user_id')]))
        active_subscriptions = len([sub for sub in self.user_subscriptions.values() if not self.is_subscription_expired(list(self.user_subscriptions.keys())[list(self.user_subscriptions.values()).index(sub)])])
        
        keyboard = [
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("⚙️ Настройка тарифов", callback_data="admin_settings")],
            [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("👥 Управление подписками", callback_data="admin_subscriptions")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
        ]
        
        await query.edit_message_text(
            f"👑 Админ Панель\n\n"
            f"📊 Всего пользователей: {total_users}\n"
            f"💳 Активных подписок: {active_subscriptions}\n"
            f"⏰ Запланированных постов: {len([p for p in self.scheduled_posts if p.get('status') != 'sent'])}\n"
            f"📢 Приватных каналов настроено: {sum(1 for plan in self.subscription_plans.values() if plan.get('channel_id'))}",
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
        for plan in self.subscription_plans:
            plan_stats[plan] = len([sub for sub in self.user_subscriptions.values() if sub["plan"] == plan])
        
        free_users = total_users - sum(plan_stats.values())
        
        stats_text = "📊 Статистика бота:\n\n"
        stats_text += f"👥 Всего пользователей: {total_users}\n"
        stats_text += f"👤 Без подписки: {free_users}\n\n"
        
        stats_text += "📋 Тарифы:\n"
        for plan, config in self.subscription_plans.items():
            count = plan_stats.get(plan, 0)
            channel_status = "✅" if config.get('channel_id') else "❌"
            stats_text += f"{channel_status} {config['name']}: {count}\n"
        
        stats_text += f"\n⏰ Активных постов: {len([p for p in self.scheduled_posts if p.get('status') != 'sent'])}"
        stats_text += f"\n📢 Всего каналов: {len(self.channels)}"
        
        await query.edit_message_text(
            stats_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 В админ панель", callback_data="admin_panel")]
            ])
        )
    
    async def admin_settings_menu(self, query):
        """Меню настройки тарифов"""
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        text = "⚙️ Настройка тарифных планов:\n\n"
        
        for plan_key, plan_config in self.subscription_plans.items():
            text += f"📋 {plan_config['name']}\n"
            text += f"   💰 Цена: ${plan_config['price']}/месяц\n"
            text += f"   📊 Постов в день: {'∞' if plan_config['posts_per_day'] == -1 else plan_config['posts_per_day']}\n"
            text += f"   📢 Каналов: {'∞' if plan_config['channels_limit'] == -1 else plan_config['channels_limit']}\n"
            text += f"   🔒 Приватный канал: {'✅' if plan_config.get('channel_id') else '❌'}\n"
            if plan_config.get('channel_id'):
                text += f"   🆔 ID канала: {plan_config.get('channel_id')}\n"
                text += f"   📢 Название: {plan_config.get('channel_name', 'Не указано')}\n"
            text += f"   ⏳ Дней подписки: {plan_config.get('duration_days', 30)}\n\n"
        
        keyboard = []
        for plan_key in self.subscription_plans:
            keyboard.append([
                InlineKeyboardButton(f"⚙️ Настроить {self.subscription_plans[plan_key]['name']}", 
                                   callback_data=f"edit_plan_{plan_key}")
            ])
        
        keyboard.append([InlineKeyboardButton("💾 Сохранить настройки", callback_data="save_settings")])
        keyboard.append([InlineKeyboardButton("🔙 В админ панель", callback_data="admin_panel")])
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    async def admin_edit_plan_menu(self, query, plan_type: str):
        """Меню редактирования тарифа"""
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        plan_config = self.subscription_plans[plan_type]
        
        text = f"⚙️ Редактирование тарифа:\n{plan_config['name']}\n\n"
        text += "Отправьте новые настройки в формате:\n"
        text += "<code>цена | постов_в_день | каналов | дней_подписки</code>\n\n"
        text += f"Пример:\n"
        text += f"<code>5 | -1 | -1 | 30</code> (премиум без лимитов на 30 дней)\n\n"
        text += f"Текущие настройки:\n"
        text += f"💰 Цена: ${plan_config.get('price', 1)}/месяц\n"
        text += f"📊 Постов в день: {'∞' if plan_config.get('posts_per_day', 2) == -1 else plan_config.get('posts_per_day', 2)}\n"
        text += f"📢 Каналов: {'∞' if plan_config.get('channels_limit', 1) == -1 else plan_config.get('channels_limit', 1)}\n"
        text += f"⏳ Дней подписки: {plan_config.get('duration_days', 30)}"
        
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Настроить приватный канал", callback_data=f"setup_channel_{plan_type}")],
                [InlineKeyboardButton("🔙 К настройкам", callback_data="admin_settings")]
            ])
        )
        
        # Сохраняем тип тарифа для обработки в message_handler
        self.waiting_for_plan_settings = {
            "user_id": query.from_user.id,
            "plan_type": plan_type,
            "action": "edit_plan"
        }
    
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
        self.waiting_for_broadcast = True
    
    async def admin_subscriptions_menu(self, query):
        """Управление подписками пользователей"""
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        # Получаем список пользователей с подписками
        subscribed_users = []
        for user_id, sub_data in self.user_subscriptions.items():
            try:
                user = await self.application.bot.get_chat(user_id)
                username = f"@{user.username}" if user.username else f"ID: {user_id}"
                plan_name = self.subscription_plans[sub_data["plan"]]["name"]
                
                # Проверяем истекла ли подписка
                is_expired = self.is_subscription_expired(user_id)
                status = "✅ Активна" if not is_expired else "❌ Истекла"
                
                subscribed_users.append((user_id, username, sub_data["plan"], plan_name, status))
            except:
                subscribed_users.append((user_id, f"ID: {user_id}", sub_data["plan"], "Неизвестный тариф", "❌ Ошибка"))
        
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
        
        for user_id, username, plan_type, plan_name, status in subscribed_users[:10]:
            text += f"👤 {username}\n"
            text += f"📦 {plan_name} ({status})\n\n"
            
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
            # Устанавливаем подписку
            expires_at = get_moscow_time() + timedelta(days=self.subscription_plans[plan_type].get('duration_days', 30))
            self.user_subscriptions[user_id] = {
                "plan": plan_type,
                "subscribed_at": get_moscow_time().isoformat(),
                "expires_at": expires_at.isoformat(),
                "channel_id": self.subscription_plans[plan_type].get('channel_id')
            }
            message = f"✅ Установлен тариф: {self.subscription_plans[plan_type]['name']}"
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 К управлению подписками", callback_data="admin_subscriptions")]
            ])
        )
    
    async def admin_save_plan(self, query, plan_type: str, context: ContextTypes.DEFAULT_TYPE):
        """Сохранить настройки тарифа"""
        if not self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        self.save_settings()
        
        await query.edit_message_text(
            "✅ Настройки тарифов сохранены!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 К настройкам", callback_data="admin_settings")]
            ])
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
    
    async def message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик сообщений"""
        message = update.message
        user_id = message.from_user.id
        
        # Обработка настроек тарифов от админа
        if self.waiting_for_plan_settings and self.waiting_for_plan_settings["user_id"] == user_id:
            settings_data = message.text.strip()
            plan_type = self.waiting_for_plan_settings["plan_type"]
            action = self.waiting_for_plan_settings.get("action")
            
            if action == "edit_plan":
                # Разбираем настройки: цена | постов_в_день | каналов | дней_подписки
                parts = settings_data.split('|')
                if len(parts) >= 4:
                    try:
                        price = float(parts[0].strip())
                        posts_per_day = int(parts[1].strip())
                        channels_limit = int(parts[2].strip())
                        duration_days = int(parts[3].strip())
                        
                        # Сохраняем настройки
                        self.subscription_plans[plan_type]["price"] = price
                        self.subscription_plans[plan_type]["posts_per_day"] = posts_per_day
                        self.subscription_plans[plan_type]["channels_limit"] = channels_limit
                        self.subscription_plans[plan_type]["duration_days"] = duration_days
                        
                        self.save_settings()
                        self.waiting_for_plan_settings = None
                        
                        await message.reply_text(
                            f"✅ Настройки для тарифа '{self.subscription_plans[plan_type]['name']}' сохранены!\n\n"
                            f"💰 Цена: ${price}/месяц\n"
                            f"📊 Постов в день: {'∞' if posts_per_day == -1 else posts_per_day}\n"
                            f"📢 Каналов: {'∞' if channels_limit == -1 else channels_limit}\n"
                            f"⏳ Дней подписки: {duration_days}",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("⚙️ К настройкам", callback_data="admin_settings")]
                            ])
                        )
                        return
                        
                    except ValueError as e:
                        await message.reply_text(
                            f"❌ Ошибка в формате чисел: {e}\n"
                            f"Используйте только цифры и '-1' для безлимита"
                        )
                        return
            
            self.waiting_for_plan_settings = None
        
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
            
            # Проверяем не истекла ли подписка
            if not self.is_admin(user_id) and self.is_subscription_expired(user_id):
                await message.reply_text(
                    "❌ Ваша подписка истекла\n"
                    "💳 Продлите подписку для добавления каналов",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")],
                        [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                    ])
                )
                return
            
            # Проверяем подписку на приватный канал
            if not self.is_admin(user_id):
                is_subscribed = await self.check_channel_subscription(user_id, user_plan["plan"])
                if not is_subscribed:
                    await message.reply_text(
                        "❌ Вы отписались от приватного канала!\n"
                        "💳 Обновите подписку для добавления каналов",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("💳 Проверить подписку", callback_data="check_subscription")],
                            [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                        ])
                    )
                    return
            
            # Для обычных пользователей проверяем лимиты
            if not self.is_admin(user_id):
                plan_config = self.subscription_plans[user_plan["plan"]]
                
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
                plan_config = self.subscription_plans[user_plan["plan"]]
                
                if self.is_subscription_expired(user_id):
                    await message.reply_text(
                        "❌ Ваша подписка истекла\n"
                        "💳 Продлите подписку для создания постов",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("💳 Тарифы", callback_data="subscription_plans")],
                            [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                        ])
                    )
                    return
                
                # Проверяем подписку на приватный канал
                is_subscribed = await self.check_channel_subscription(user_id, user_plan["plan"])
                if not is_subscribed:
                    await message.reply_text(
                        "❌ Вы отписались от приватного канала!\n"
                        "💳 Обновите подписку для создания постов",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("💳 Проверить подписку", callback_data="check_subscription")],
                            [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
                        ])
                    )
                    return
                
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
        raise ValueError("BOT_TOKEN не установлен. Установите переменную окружения BOT_TOKEN")
    
    bot = ChannelBot(BOT_TOKEN)
    print("🤖 Бот запущен с полной системой приватных подписок!")
    print(f"👑 ID администратора: {ADMIN_ID}")
    print("🕐 Московское время")
    print("🔒 Поддержка приватных каналов: ✅")
    print("💳 Платные подписки: ✅")
    print("⚙️ Админ-панель: ✅")
    print("\nИспользуйте команды:")
    print("/start - Начать работу")
    print("/setup - Настройка приватного канала (админ)")
    print("/test - Тестирование канала (админ)")
    print("/admin - Админ-панель")
    
    bot.application.run_polling()

if __name__ == "__main__":
    main()
