import asyncio
import logging
import re
import os
import shutil
from datetime import datetime

import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from telethon import TelegramClient, events
from telethon.tl.types import InputPeerChannel

from config import api_id, api_hash, bot_token, my_id, proxy_url  # Переконайтесь, що ці параметри правильно налаштовані

# Визначення станів для додавання каналів та інших операцій
class ChannelAdding(StatesGroup):
    waiting_for_channel_id = State()

class DestinationChannelSetting(StatesGroup):
    waiting_for_destination_channel_id = State()

class KeywordsManagement(StatesGroup):
    waiting_for_action = State()
    waiting_for_new_keyword = State()
    waiting_for_keyword_to_remove = State()

# Налаштування логування
logging.basicConfig(
    level=logging.DEBUG,  # Встановлено рівень DEBUG для максимально детального логування
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_debug.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Ініціалізація бота та клієнта Telethon
bot = Bot(token=bot_token)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())

client = TelegramClient('myGrab', api_id, api_hash)

# Функція для створення бази даних та таблиць
async def init_db():
    try:
        async with aiosqlite.connect('channels.db') as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY,
                    access_hash INTEGER NOT NULL,
                    title TEXT NOT NULL
                )
            ''')
            await db.execute('''
                CREATE TABLE IF NOT EXISTS destination (
                    id INTEGER PRIMARY KEY,
                    access_hash INTEGER NOT NULL
                )
            ''')
            await db.execute('''
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT NOT NULL
                )
            ''')
            await db.commit()
        logger.info("База даних ініціалізована та таблиці створені (якщо їх не було).")
    except Exception as e:
        logger.exception(f"Помилка при ініціалізації бази даних: {e}")

# Функція для створення резервної копії бази даних
async def backup_db():
    try:
        backup_dir = 'backups'
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = os.path.join(backup_dir, f'channels_backup_{timestamp}.db')
        await asyncio.to_thread(shutil.copy, 'channels.db', backup_file)
        logger.info(f"Створено резервну копію бази даних: {backup_file}")
        return backup_file
    except Exception as e:
        logger.exception(f"Помилка при створенні резервної копії бази даних: {e}")
        return None

# Функції для роботи з базою даних
async def save_channel(channel_id, access_hash, channel_title):
    try:
        async with aiosqlite.connect('channels.db') as db:
            await db.execute(
                '''
                INSERT INTO channels (id, access_hash, title)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET access_hash=excluded.access_hash, title=excluded.title
                ''',
                (channel_id, access_hash, channel_title)
            )
            await db.commit()
        logger.debug(f"Збережено канал: ID={channel_id}, access_hash={access_hash}, title='{channel_title}'")
    except Exception as e:
        logger.exception(f"Помилка при збереженні каналу {channel_title} (ID: {channel_id}): {e}")

async def delete_channel(channel_id):
    try:
        async with aiosqlite.connect('channels.db') as db:
            await db.execute('DELETE FROM channels WHERE id = ?', (channel_id,))
            await db.commit()
        logger.debug(f"Видалено канал з ID={channel_id}")
    except Exception as e:
        logger.exception(f"Помилка при видаленні каналу з ID={channel_id}: {e}")

async def set_destination_channel(channel_id, access_hash):
    try:
        async with aiosqlite.connect('channels.db') as db:
            await db.execute('DELETE FROM destination')  # Видалення попереднього каналу-приймача
            if channel_id and access_hash:
                await db.execute('INSERT INTO destination (id, access_hash) VALUES (?, ?)',
                                 (channel_id, access_hash))
            await db.commit()
        if channel_id:
            logger.debug(f"Встановлено канал-приймач: ID={channel_id}, access_hash={access_hash}")
        else:
            logger.debug("Видалено канал-приймач")
    except Exception as e:
        logger.exception(f"Помилка при встановленні каналу-приймача: {e}")

async def get_destination_channel():
    try:
        async with aiosqlite.connect('channels.db') as db:
            cursor = await db.execute('SELECT id, access_hash FROM destination LIMIT 1')
            row = await cursor.fetchone()
            if row:
                logger.debug(f"Отримано канал-приймач: ID={row[0]}, access_hash={row[1]}")
                return {'id': row[0], 'access_hash': row[1]}
            logger.debug("Канал-приймач не встановлено")
            return None
    except Exception as e:
        logger.exception(f"Помилка при отриманні каналу-приймача: {e}")
        return None

async def get_channels():
    try:
        async with aiosqlite.connect('channels.db') as db:
            cursor = await db.execute('SELECT id, access_hash, title FROM channels')
            rows = await cursor.fetchall()
            channels = [{'id': row[0], 'access_hash': row[1], 'title': row[2]} for row in rows]
            logger.debug(f"Отримано канали: {channels}")
            return channels
    except Exception as e:
        logger.exception(f"Помилка при отриманні каналів: {e}")
        return []

async def get_keywords():
    try:
        async with aiosqlite.connect('channels.db') as db:
            cursor = await db.execute('SELECT keyword FROM keywords')
            rows = await cursor.fetchall()
            keywords = [row[0] for row in rows]
            logger.debug(f"Отримано заборонені слова: {keywords}")
            return keywords
    except Exception as e:
        logger.exception(f"Помилка при отриманні заборонених слів: {e}")
        return []

async def add_keyword(keyword):
    try:
        async with aiosqlite.connect('channels.db') as db:
            await db.execute('INSERT INTO keywords (keyword) VALUES (?)', (keyword.lower(),))
            await db.commit()
        logger.debug(f"Додано заборонене слово: '{keyword}'")
    except Exception as e:
        logger.exception(f"Помилка при додаванні слова '{keyword}': {e}")

async def remove_keyword(keyword):
    try:
        async with aiosqlite.connect('channels.db') as db:
            await db.execute('DELETE FROM keywords WHERE keyword = ?', (keyword.lower(),))
            await db.commit()
        logger.debug(f"Видалено заборонене слово: '{keyword}'")
    except Exception as e:
        logger.exception(f"Помилка при видаленні слова '{keyword}': {e}")

async def remove_all_keywords():
    try:
        async with aiosqlite.connect('channels.db') as db:
            await db.execute('DELETE FROM keywords')
            await db.commit()
        logger.debug("Видалено всі заборонені слова")
    except Exception as e:
        logger.exception(f"Помилка при видаленні всіх заборонених слів: {e}")

# Функція для створення меню клавіатури
def create_menu_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    buttons = [
        types.KeyboardButton("Додати канал"),
        types.KeyboardButton("Видалити канал"),
        types.KeyboardButton("Показати список каналів"),
        types.KeyboardButton("Встановити канал-приймач"),
        types.KeyboardButton("Видалити канал-приймач"),
        types.KeyboardButton("Показати канал-приймач"),
        types.KeyboardButton("Управління забороненими словами"),
        types.KeyboardButton("Статистика"),
        types.KeyboardButton("Допомога")
    ]
    keyboard.add(*buttons)
    logger.debug("Створено головне меню клавіатури")
    return keyboard

# Обробник команди /start
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    if message.from_user.id != my_id:
        logger.warning(f"Невідомий користувач {message.from_user.id} спробував запустити бота")
        return

    logger.info(f"Користувач {message.from_user.id} запустив бота")
    start_message = "Привіт! Я бот для роботи з каналами в Telegram.\n\nНатисніть кнопку на клавіатурі для вибору дії."
    keyboard = create_menu_keyboard()
    await message.reply(start_message, reply_markup=keyboard)
    logger.debug("Відправлено стартове повідомлення з меню")

# Обробник повідомлень з кнопками
@dp.message_handler()
async def handle_message(message: types.Message):
    if message.from_user.id != my_id:
        logger.warning(f"Невідомий користувач {message.from_user.id} надіслав повідомлення")
        return

    logger.info(f"Отримано повідомлення від користувача {message.from_user.id}: {message.text}")

    if message.text == "Додати канал":
        await ChannelAdding.waiting_for_channel_id.set()
        await message.reply('Введіть username каналу, починаючи з "@", або його ID, який ви хочете додати:')
        logger.debug("Перейшли до стану додавання каналу")

    elif message.text == "Видалити канал":
        channels = await get_channels()
        if channels:
            buttons = [
                types.InlineKeyboardButton(text=ch['title'], callback_data=f'delete_channel_{ch["id"]}')
                for ch in channels
            ]
            keyboard = types.InlineKeyboardMarkup(row_width=2)
            keyboard.add(*buttons)
            await message.reply("Виберіть канал, який хочете видалити:", reply_markup=keyboard)
            logger.debug("Відправлено список каналів для видалення")
        else:
            await message.reply("Список каналів порожній.")
            logger.debug("Список каналів порожній при спробі видалення")

    elif message.text == "Показати список каналів":
        channels = await get_channels()
        if channels:
            channel_list = '\n'.join(f"{ch['title']} ({ch['id']})" for ch in channels)
            await message.reply("Список каналів:\n" + channel_list)
            logger.debug("Відправлено список каналів")
        else:
            await message.reply("Список каналів порожній.")
            logger.debug("Список каналів порожній при запиті")

    elif message.text == "Встановити канал-приймач":
        await DestinationChannelSetting.waiting_for_destination_channel_id.set()
        await message.reply('Введіть username каналу-приймача, починаючи з "@", або його ID, який ви хочете встановити як основний:')
        logger.debug("Перейшли до стану встановлення каналу-приймача")

    elif message.text == "Видалити канал-приймач":
        destination_channel = await get_destination_channel()
        if destination_channel:
            await set_destination_channel(None, None)
            backup_file = await backup_db()
            if backup_file:
                await message.reply("Канал-приймач видалено. Резервна копія створена.")
                logger.debug("Канал-приймач видалено та створено резервну копію")
            else:
                await message.reply("Канал-приймач видалено, але резервна копія не вдалася.")
                logger.error("Канал-приймач видалено, але резервна копія не вдалася")
        else:
            await message.reply("Канал-приймач не встановлено.")
            logger.debug("Спроба видалити канал-приймач, коли він не встановлено")

    elif message.text == "Показати канал-приймач":
        destination_channel = await get_destination_channel()
        if destination_channel:
            try:
                chat = await client.get_entity(destination_channel['id'])
                await message.reply(f"Поточний канал-приймач: {chat.title} (ID: {destination_channel['id']})")
                logger.debug(f"Відправлено інформацію про канал-приймача: {chat.title} (ID: {destination_channel['id']})")
            except Exception as e:
                await message.reply(f"Поточний канал-приймач ID: {destination_channel['id']}")
                logger.error(f"Помилка при отриманні інформації про канал-приймача: {e}")
        else:
            await message.reply("Канал-приймач не встановлено.")
            logger.debug("Спроба показати канал-приймач, коли він не встановлено")

    elif message.text == "Управління забороненими словами":
        await manage_keywords(message)  # Обробник нижче
        logger.debug("Перейшли до управління забороненими словами")

    elif message.text == "Статистика":
        await stats(message)  # Обробник нижче
        logger.debug("Перейшли до перегляду статистики")

    elif message.text == "Допомога":
        await help_message(message)
        logger.debug("Відправлено повідомлення допомоги")

    else:
        await message.reply("Невідома команда. Натисніть кнопку на клавіатурі для вибору дії.")
        logger.warning(f"Невідома команда від користувача {message.from_user.id}: {message.text}")

# Обробник стану додавання каналу
@dp.message_handler(state=ChannelAdding.waiting_for_channel_id)
async def add_channel_handler(message: types.Message, state: FSMContext):
    logger.info(f"Користувач {message.from_user.id} вводить дані для додавання каналу: {message.text}")
    try:
        channel_input = message.text.strip()
        chat = None

        if channel_input.startswith('@'):
            username = channel_input[1:]
            chat = await client.get_entity(username)
            logger.debug(f"Отримано сутність каналу за username: {username}")
        elif channel_input.startswith("-100"):
            channel_id = int(channel_input)
            chat = await client.get_entity(channel_id)
            logger.debug(f"Отримано сутність каналу за ID: {channel_id}")
        else:
            channel_id = int(channel_input)
            chat = await client.get_entity(channel_id)
            logger.debug(f"Отримано сутність каналу за ID: {channel_id}")

        if chat:
            channel_id = chat.id
            access_hash = chat.access_hash
            title = chat.title
            await save_channel(channel_id, access_hash, title)
            backup_file = await backup_db()
            if backup_file:
                await message.reply(f"Канал '{title}' (ID: {channel_id}) додано.\nРезервна копія створена.")
                logger.info(f"Канал '{title}' (ID: {channel_id}) успішно додано до бази даних")
                logger.debug(f"Резервна копія створена: {backup_file}")
            else:
                await message.reply(f"Канал '{title}' (ID: {channel_id}) додано.\nНе вдалося створити резервну копію.")
                logger.error("Канал додано, але резервна копія не вдалася")
        else:
            await message.reply("Канал не знайдено. Будь ласка, вкажіть коректний ID каналу або його username (починається з '@').")
            logger.error("Помилка при додаванні каналу: канал не знайдено")
    except ValueError:
        await message.reply("Некоректний формат ID. Будь ласка, введіть числовий ID або правильний username (починається з '@').")
        logger.error("Некоректний формат ID каналу")
    except Exception as e:
        await message.reply(f"Сталася помилка при додаванні каналу: {e}")
        logger.exception(f"Помилка при додаванні каналу: {e}")
    finally:
        await state.finish()
        keyboard = create_menu_keyboard()
        await message.reply("Повернення в основне меню.", reply_markup=keyboard)
        logger.debug("Завершено стан додавання каналу та повернення до меню")

# Обробник кнопок для видалення каналу
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('delete_channel_'))
async def delete_channel_callback(callback_query: types.CallbackQuery):
    channel_id = int(callback_query.data[len('delete_channel_'):])
    logger.info(f"Користувач {callback_query.from_user.id} запитує видалення каналу з ID={channel_id}")
    try:
        async with aiosqlite.connect('channels.db') as db:
            cursor = await db.execute('SELECT title FROM channels WHERE id = ?', (channel_id,))
            row = await cursor.fetchone()
            if row:
                channel_title = row[0]
                await delete_channel(channel_id)
                backup_file = await backup_db()
                if backup_file:
                    await callback_query.message.reply(f"Канал '{channel_title}' (ID: {channel_id}) видалено.\nРезервна копія створена.")
                    logger.info(f"Канал '{channel_title}' (ID: {channel_id}) успішно видалено з бази даних")
                    logger.debug(f"Резервна копія створена: {backup_file}")
                else:
                    await callback_query.message.reply(f"Канал '{channel_title}' (ID: {channel_id}) видалено.\nНе вдалося створити резервну копію.")
                    logger.error("Канал видалено, але резервна копія не вдалася")
            else:
                await callback_query.message.reply("Канал не знайдено.")
                logger.warning(f"Спроба видалити неіснуючий канал з ID={channel_id}")
    except Exception as e:
        await callback_query.message.reply(f"Сталася помилка при видаленні каналу: {e}")
        logger.exception(f"Помилка при видаленні каналу з ID={channel_id}: {e}")
    finally:
        await callback_query.answer()
        keyboard = create_menu_keyboard()
        await callback_query.message.reply("Повернення в основне меню.", reply_markup=keyboard)
        logger.debug("Завершено видалення каналу та повернення до меню")

# Обробник стану встановлення каналу-приймача
@dp.message_handler(state=DestinationChannelSetting.waiting_for_destination_channel_id)
async def set_destination_channel_handler(message: types.Message, state: FSMContext):
    logger.info(f"Користувач {message.from_user.id} вводить дані для встановлення каналу-приймача: {message.text}")
    try:
        channel_input = message.text.strip()
        chat = None

        if channel_input.startswith('@'):
            username = channel_input[1:]
            chat = await client.get_entity(username)
            logger.debug(f"Отримано сутність каналу-приймача за username: {username}")
        elif channel_input.startswith("-100"):
            channel_id = int(channel_input)
            chat = await client.get_entity(channel_id)
            logger.debug(f"Отримано сутність каналу-приймача за ID: {channel_id}")
        else:
            channel_id = int(channel_input)
            chat = await client.get_entity(channel_id)
            logger.debug(f"Отримано сутність каналу-приймача за ID: {channel_id}")

        if chat:
            channel_id = chat.id
            access_hash = chat.access_hash
            title = chat.title
            await set_destination_channel(channel_id, access_hash)
            backup_file = await backup_db()
            if backup_file:
                await message.reply(f"Канал-приймач '{title}' (ID: {channel_id}) встановлено.\nРезервна копія створена.")
                logger.info(f"Канал-приймач '{title}' (ID: {channel_id}) успішно встановлено")
                logger.debug(f"Резервна копія створена: {backup_file}")
            else:
                await message.reply(f"Канал-приймач '{title}' (ID: {channel_id}) встановлено.\nНе вдалося створити резервну копію.")
                logger.error("Канал-приймач встановлено, але резервна копія не вдалася")
        else:
            await message.reply("Канал-приймач не знайдено. Будь ласка, вкажіть коректний ID каналу-приймача або його username (починається з '@').")
            logger.error("Помилка при встановленні каналу-приймача: канал не знайдено")
    except ValueError:
        await message.reply("Некоректний формат ID. Будь ласка, введіть числовий ID або правильний username (починається з '@').")
        logger.error("Некоректний формат ID каналу-приймача")
    except Exception as e:
        await message.reply(f"Сталася помилка при встановленні каналу-приймача: {e}")
        logger.exception(f"Помилка при встановленні каналу-приймача: {e}")
    finally:
        await state.finish()
        keyboard = create_menu_keyboard()
        await message.reply("Повернення в основне меню.", reply_markup=keyboard)
        logger.debug("Завершено стан встановлення каналу-приймача та повернення до меню")

# Обробник допомоги
async def help_message(message: types.Message):
    if message.from_user.id != my_id:
        logger.warning(f"Невідомий користувач {message.from_user.id} запитує допомогу")
        return

    help_message_text = (
        "📋 **Список доступних команд та кнопок:**\n"
        "Натисніть кнопку на клавіатурі для вибору дії.\n\n"
        "🔹 **Додати канал**: Додати канал для моніторингу\n"
        "🔹 **Видалити канал**: Видалити канал зі списку\n"
        "🔹 **Показати список каналів**: Переглянути додані канали\n"
        "🔹 **Встановити канал-приймач**: Встановити основний канал-приймач\n"
        "🔹 **Видалити канал-приймач**: Видалити канал-приймач\n"
        "🔹 **Показати канал-приймач**: Переглянути встановлений канал-приймач\n"
        "🔹 **Управління забороненими словами**: Переглянути, додати або видалити заборонені слова\n"
        "🔹 **Статистика**: Переглянути статистику бота\n"
        "🔹 **Допомога**: Отримати цю інформацію\n"
    )
    await message.reply(help_message_text, parse_mode='Markdown')
    logger.debug("Відправлено повідомлення допомоги")

# Обробник для кнопки "Управління забороненими словами"
@dp.message_handler(lambda message: message.text == "Управління забороненими словами")
async def manage_keywords(message: types.Message):
    logger.info(f"Користувач {message.from_user.id} перейшов до управління забороненими словами")
    await message.reply(
        "Виберіть дію:",
        reply_markup=types.ReplyKeyboardMarkup(
            resize_keyboard=True,
            one_time_keyboard=True
        ).add("Переглянути список", "Додати слово", "Видалити слово", "Назад")
    )
    await KeywordsManagement.waiting_for_action.set()
    logger.debug("Перейшли до стану управління забороненими словами")

# Обробник дій в управлінні ключовими словами
@dp.message_handler(state=KeywordsManagement.waiting_for_action)
async def keywords_action_handler(message: types.Message, state: FSMContext):
    action = message.text.lower()
    logger.info(f"Користувач {message.from_user.id} вибрав дію: {action}")

    if action == "переглянути список":
        keywords = await get_keywords()
        if keywords:
            keyword_list = ', '.join(keywords)
            await message.reply(f"Заборонені слова:\n{keyword_list}")
            logger.debug("Відправлено список заборонених слів")
        else:
            await message.reply("Список заборонених слів порожній.")
            logger.debug("Список заборонених слів порожній")
        await state.finish()
        keyboard = create_menu_keyboard()
        await message.reply("Повернення в основне меню.", reply_markup=keyboard)
        logger.debug("Повернення до головного меню після перегляду списку")

    elif action == "додати слово":
        await message.reply("Введіть слово, яке потрібно додати до заборонених:")
        await KeywordsManagement.waiting_for_new_keyword.set()
        logger.debug("Перейшли до стану додавання нового забороненого слова")

    elif action == "видалити слово":
        keywords = await get_keywords()
        if keywords:
            buttons = [
                types.InlineKeyboardButton(text=kw, callback_data=f"remove_kw_{kw}") for kw in keywords
            ]
            keyboard = types.InlineKeyboardMarkup(row_width=2)
            keyboard.add(*buttons)
            await message.reply("Виберіть слово для видалення:", reply_markup=keyboard)
            logger.debug("Відправлено список заборонених слів для видалення")
        else:
            await message.reply("Список заборонених слів порожній.")
            logger.debug("Спроба видалити слово, коли список порожній")
        await state.finish()
        keyboard = create_menu_keyboard()
        await message.reply("Повернення в основне меню.", reply_markup=keyboard)
        logger.debug("Повернення до головного меню після спроби видалення слова")

    elif action == "назад":
        keyboard = create_menu_keyboard()
        await message.reply("Повернення в основне меню.", reply_markup=keyboard)
        await state.finish()
        logger.debug("Повернення до головного меню")

    else:
        await message.reply("Невідома дія. Спробуйте ще раз.")
        logger.warning(f"Невідома дія: {action}")

# Обробник додавання нового забороненого слова
@dp.message_handler(state=KeywordsManagement.waiting_for_new_keyword)
async def add_new_keyword_handler(message: types.Message, state: FSMContext):
    keyword = message.text.strip().lower()
    logger.info(f"Користувач {message.from_user.id} додає заборонене слово: '{keyword}'")
    if keyword:
        await add_keyword(keyword)
        backup_file = await backup_db()
        if backup_file:
            await message.reply(f"Слово '{keyword}' додано до заборонених.\nРезервна копія створена.")
            logger.debug(f"Слово '{keyword}' додано до бази даних")
            logger.debug(f"Резервна копія створена: {backup_file}")
        else:
            await message.reply(f"Слово '{keyword}' додано до заборонених.\nНе вдалося створити резервну копію.")
            logger.error("Слово додано, але резервна копія не вдалася")
    else:
        await message.reply("Некоректне слово. Спробуйте ще раз.")
        logger.warning("Спроба додати некоректне слово")
    await state.finish()
    keyboard = create_menu_keyboard()
    await message.reply("Повернення в основне меню.", reply_markup=keyboard)
    logger.debug("Повернення до головного меню після додавання слова")

# Обробник видалення забороненого слова через callback
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('remove_kw_'))
async def remove_keyword_callback(callback_query: types.CallbackQuery):
    keyword = callback_query.data[len('remove_kw_'):].lower()
    logger.info(f"Користувач {callback_query.from_user.id} видаляє заборонене слово: '{keyword}'")
    try:
        await remove_keyword(keyword)
        backup_file = await backup_db()
        if backup_file:
            await callback_query.message.reply(f"Слово '{keyword}' видалено з заборонених.\nРезервна копія створена.")
            logger.debug(f"Слово '{keyword}' видалено з бази даних")
            logger.debug(f"Резервна копія створена: {backup_file}")
        else:
            await callback_query.message.reply(f"Слово '{keyword}' видалено з заборонених.\nНе вдалося створити резервну копію.")
            logger.error("Слово видалено, але резервна копія не вдалася")
    except Exception as e:
        await callback_query.message.reply(f"Сталася помилка при видаленні слова: {e}")
        logger.exception(f"Помилка при видаленні слова '{keyword}': {e}")
    finally:
        await callback_query.answer()
        keyboard = create_menu_keyboard()
        await callback_query.message.reply("Повернення в основне меню.", reply_markup=keyboard)
        logger.debug("Повернення до головного меню після видалення слова")

# Обробник команди /stats
@dp.message_handler(commands=['stats'])
async def stats(message: types.Message):
    if message.from_user.id != my_id:
        logger.warning(f"Невідомий користувач {message.from_user.id} спробував переглянути статистику")
        return
    channels = await get_channels()
    keywords = await get_keywords()
    stats_message = (
        f"📊 **Статистика бота**\n\n"
        f"**Додані канали:** {len(channels)}\n"
        f"**Заборонені слова:** {len(keywords)}"
    )
    await message.reply(stats_message, parse_mode='Markdown')
    logger.debug(f"Відправлено статистику: канали={len(channels)}, слова={len(keywords)}")

# Обробник натискання кнопки "Статистика"
@dp.message_handler(lambda message: message.text == "Статистика")
async def handle_stats_button(message: types.Message):
    await stats(message)
    logger.debug("Оброблено кнопку 'Статистика'")

# Обробник нових повідомлень з каналів
@client.on(events.NewMessage())
async def my_event_handler(event):
    logger.debug(f"Отримано нове повідомлення з чату ID={event.chat_id}")
    try:
        channels = await get_channels()
        channels_list = [ch['id'] for ch in channels]
        logger.debug(f"Список каналів з бази даних: {channels_list}")

        # Нормалізація chat_id: видалення префіксу -100, якщо він є
        normalized_chat_id = event.chat_id
        if isinstance(normalized_chat_id, int) and normalized_chat_id < 0:
            chat_id_str = str(normalized_chat_id)
            if chat_id_str.startswith("-100"):
                normalized_chat_id = int(chat_id_str[4:])
                logger.debug(f"Нормалізований chat_id: {normalized_chat_id}")

        if normalized_chat_id not in channels_list:
            logger.debug(f"Чат ID {event.chat_id} не знаходиться в списку відстежуваних каналів")
            return

        destination = await get_destination_channel()
        if not destination:
            logger.error("Канал-приймач не встановлено")
            return

        try:
            destination_input_peer = InputPeerChannel(destination['id'], destination['access_hash'])
            logger.debug(f"Створено InputPeerChannel для каналу-приймача: ID={destination['id']}")
        except Exception as e:
            logger.exception(f"Не вдалося створити InputPeerChannel для каналу-приймача: {e}")
            return

        # Фільтрація опитувань
        if event.message.poll:
            logger.debug("Повідомлення є опитуванням. Пропускаємо.")
            return

        # Отримання тексту повідомлення
        message_text = event.message.message or ""
        logger.debug(f"Текст повідомлення: {message_text}")

        # Отримання заборонених слів
        forbidden_keywords = await get_keywords()
        logger.debug(f"Заборонені слова: {forbidden_keywords}")

        # Фільтр по забороненим словам
        if forbidden_keywords:
            pattern = r'\b(' + '|'.join(re.escape(kw) for kw in forbidden_keywords) + r')\b'
            if re.search(pattern, message_text.lower()):
                logger.debug("Повідомлення відфільтровано за забороненими словами")
                return

        # Пересилання повідомлення
        try:
            await event.message.forward_to(destination_input_peer)
            logger.info(f"Повідомлення переслано до каналу-приймача ID={destination['id']}")
        except Exception as e:
            logger.exception(f"Помилка при пересиланні повідомлення: {e}")
    except Exception as e:
        logger.exception(f"Помилка в обробці повідомлення: {e}")

# Основна функція
if __name__ == "__main__":
    async def main():
        logger.info("Початок ініціалізації бота")
        await init_db()
        try:
            await client.start()
            await client.connect()
            logger.info("Клієнт Telethon запущено та підключено")

            # Реєстрація обробників команд
            dp.register_message_handler(start, commands=['start'], commands_prefix='/')
            dp.register_message_handler(help_message, commands=['help'], commands_prefix='/')
            dp.register_message_handler(stats, commands=['stats'], commands_prefix='/')

            logger.info("Запуск бота та клієнта паралельно")
            # Запускаємо клієнт і бота паралельно
            await asyncio.gather(
                client.run_until_disconnected(),
                dp.start_polling()
            )
        except Exception as e:
            logger.exception(f"Сталася критична помилка: {e}")
        finally:
            await client.disconnect()
            logger.info("Клієнт Telethon відключено")

    asyncio.run(main())
