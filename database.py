import sqlite3
from datetime import datetime, timedelta
import logging

def init_db():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # Таблица тарифов
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tariffs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        channel_link TEXT NOT NULL,  # Приватная ссылка на канал
        channel_id TEXT,  # ID канала для проверки
        message_limit INTEGER NOT NULL,
        duration_days INTEGER NOT NULL,
        is_active BOOLEAN DEFAULT 1
    )
    ''')
    
    # Таблица пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        tariff_id INTEGER,
        messages_left INTEGER DEFAULT 0,
        subscription_end DATE,
        joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tariff_id) REFERENCES tariffs (id)
    )
    ''')
    
    # Таблица каналов для мониторинга
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS monitored_channels (
        channel_id TEXT PRIMARY KEY,
        tariff_id INTEGER NOT NULL,
        channel_username TEXT,
        added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tariff_id) REFERENCES tariffs (id)
    )
    ''')
    
    conn.commit()
    conn.close()

def add_tariff(name, channel_link, channel_id, message_limit, duration_days):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('''
    INSERT INTO tariffs (name, channel_link, channel_id, message_limit, duration_days)
    VALUES (?, ?, ?, ?, ?)
    ''', (name, channel_link, channel_id, message_limit, duration_days))
    conn.commit()
    conn.close()

def get_tariffs():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM tariffs WHERE is_active = 1')
    tariffs = cursor.fetchall()
    conn.close()
    return tariffs

def get_tariff_by_id(tariff_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM tariffs WHERE id = ?', (tariff_id,))
    tariff = cursor.fetchone()
    conn.close()
    return tariff

def get_tariff_by_channel_id(channel_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM tariffs WHERE channel_id = ?', (channel_id,))
    tariff = cursor.fetchone()
    conn.close()
    return tariff

def update_tariff(tariff_id, field, value):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    if field in ['name', 'channel_link', 'channel_id']:
        cursor.execute(f'UPDATE tariffs SET {field} = ? WHERE id = ?', (value, tariff_id))
    elif field in ['message_limit', 'duration_days']:
        cursor.execute(f'UPDATE tariffs SET {field} = ? WHERE id = ?', (int(value), tariff_id))
    
    conn.commit()
    conn.close()

def delete_tariff(tariff_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE tariffs SET is_active = 0 WHERE id = ?', (tariff_id,))
    conn.commit()
    conn.close()

# Функции для пользователей
def add_user(user_id, username, first_name):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # Проверяем, существует ли пользователь
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    if cursor.fetchone() is None:
        cursor.execute('''
        INSERT INTO users (user_id, username, first_name)
        VALUES (?, ?, ?)
        ''', (user_id, username, first_name))
    
    conn.commit()
    conn.close()

def update_user_tariff(user_id, tariff_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # Получаем информацию о тарифе
    cursor.execute('SELECT message_limit, duration_days FROM tariffs WHERE id = ?', (tariff_id,))
    tariff = cursor.fetchone()
    
    if tariff:
        message_limit, duration_days = tariff
        subscription_end = (datetime.now() + timedelta(days=duration_days)).strftime('%Y-%m-%d')
        
        cursor.execute('''
        UPDATE users 
        SET tariff_id = ?, messages_left = ?, subscription_end = ?
        WHERE user_id = ?
        ''', (tariff_id, message_limit, subscription_end, user_id))
    
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def decrement_user_messages(user_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('''
    UPDATE users 
    SET messages_left = messages_left - 1 
    WHERE user_id = ? AND messages_left > 0
    ''', (user_id,))
    conn.commit()
    conn.close()

def check_subscription_expiry():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # Находим пользователей, у которых истекла подписка
    cursor.execute('''
    SELECT user_id, tariff_id 
    FROM users 
    WHERE subscription_end < DATE('now') 
    AND tariff_id IS NOT NULL
    ''')
    expired_users = cursor.fetchall()
    
    # Сбрасываем тариф у этих пользователей
    cursor.execute('''
    UPDATE users 
    SET tariff_id = NULL, messages_left = 0, subscription_end = NULL
    WHERE subscription_end < DATE('now')
    ''')
    
    conn.commit()
    conn.close()
    return expired_users

# Функции для каналов
def add_monitored_channel(channel_id, tariff_id, channel_username):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    INSERT OR REPLACE INTO monitored_channels (channel_id, tariff_id, channel_username)
    VALUES (?, ?, ?)
    ''', (channel_id, tariff_id, channel_username))
    
    conn.commit()
    conn.close()

def get_all_monitored_channels():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('''
    SELECT mc.channel_id, mc.channel_username, t.name 
    FROM monitored_channels mc
    JOIN tariffs t ON mc.tariff_id = t.id
    ''')
    channels = cursor.fetchall()
    conn.close()
    return channels

def get_monitored_channel_by_id(channel_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM monitored_channels WHERE channel_id = ?', (channel_id,))
    channel = cursor.fetchone()
    conn.close()
    return channel
