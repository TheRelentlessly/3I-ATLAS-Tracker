import os 
os.system("pip install bs4")
import asyncio
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram import F
import aiosqlite

# Твой токен от @BotFather
BOT_TOKEN = '8151788716:AAHFKmcCao_u_e_NDsbLPtjUmgy5Xh6NDF0'  # Замени на свой!

# ID админа (замени на свой Telegram ID)
ADMIN_ID = 7459617421  # Пример, замени на реальный ID

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальные переменные для кэша
cached_distance = None
last_update = None
UPDATE_INTERVAL = 90  # Секунд

# Словарь для предотвращения множественных callback от одного пользователя
processing_callbacks = {}  # user_id: True/False

# Константы для конвертации
AU_TO_KM = 149597870.7  # 1 AU в километрах
# Скорость света в км/с
LIGHT_SPEED_KM_S = 299792.458
# Количество секунд в году (365.25 дней для учёта високосных)
SECONDS_PER_YEAR = 60 * 60 * 24 * 365.25
# 1 световой год в километрах
LIGHT_YEAR_KM = LIGHT_SPEED_KM_S * SECONDS_PER_YEAR

# Функция инициализации БД
async def init_db():
    async with aiosqlite.connect('bot_db.db') as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                language TEXT DEFAULT 'ru',
                unit TEXT DEFAULT 'km',
                notifications_enabled INTEGER DEFAULT 1  -- 1 = включены, 0 = выключены
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS votes (
                user_id INTEGER,
                news_id INTEGER,
                vote INTEGER,  -- 1 = like, -1 = dislike
                PRIMARY KEY (user_id, news_id)
            )
        ''')
        await db.commit()

# Функция получения языка пользователя
async def get_user_language(user_id):
    async with aiosqlite.connect('bot_db.db') as db:
        cursor = await db.execute('SELECT language FROM users WHERE user_id = ?', (user_id,))
        row = await cursor.fetchone()
        lang = row[0] if row else None
        if lang in ['ru', 'en']:
            return lang
        return None

# Функция получения единиц измерения пользователя
async def get_user_unit(user_id):
    async with aiosqlite.connect('bot_db.db') as db:
        cursor = await db.execute('SELECT unit FROM users WHERE user_id = ?', (user_id,))
        row = await cursor.fetchone()
        unit = row[0] if row else 'km'  # Дефолтная единица - км
        if unit in ['km', 'au', 'light_years', 'm']:
            return unit
        return 'km'  # Если вдруг в БД записано что-то другое

# Функция получения статуса уведомлений пользователя
async def get_user_notifications(user_id):
    async with aiosqlite.connect('bot_db.db') as db:
        cursor = await db.execute('SELECT notifications_enabled FROM users WHERE user_id = ?', (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else 1  # По умолчанию включены

# Функция установки языка пользователя
async def set_user_language(user_id, language):
    async with aiosqlite.connect('bot_db.db') as db:
        await db.execute('''
            INSERT OR REPLACE INTO users (user_id, language, unit, notifications_enabled)
            VALUES (?, ?, COALESCE((SELECT unit FROM users WHERE user_id = ?), 'km'), COALESCE((SELECT notifications_enabled FROM users WHERE user_id = ?), 1))
        ''', (user_id, language, user_id, user_id))
        await db.commit()

# Функция установки единиц измерения пользователя
async def set_user_unit(user_id, unit):
    async with aiosqlite.connect('bot_db.db') as db:
        await db.execute('''
            INSERT OR REPLACE INTO users (user_id, language, unit, notifications_enabled)
            VALUES (?, COALESCE((SELECT language FROM users WHERE user_id = ?), 'ru'), ?, COALESCE((SELECT notifications_enabled FROM users WHERE user_id = ?), 1))
        ''', (user_id, user_id, unit, user_id))
        await db.commit()

# Функция установки статуса уведомлений пользователя
async def set_user_notifications(user_id, enabled):
    async with aiosqlite.connect('bot_db.db') as db:
        await db.execute('''
            INSERT OR REPLACE INTO users (user_id, language, unit, notifications_enabled)
            VALUES (?, COALESCE((SELECT language FROM users WHERE user_id = ?), 'ru'), COALESCE((SELECT unit FROM users WHERE user_id = ?), 'km'), ?)
        ''', (user_id, user_id, user_id, enabled))
        await db.commit()

# Функция получения всех новостей
async def get_all_news():
    async with aiosqlite.connect('bot_db.db') as db:
        cursor = await db.execute('SELECT id, title, content, created_at FROM news ORDER BY created_at DESC')
        rows = await cursor.fetchall()
        return rows

# Функция добавления новости
async def add_news(title, content):
    async with aiosqlite.connect('bot_db.db') as db:
        await db.execute('INSERT INTO news (title, content) VALUES (?, ?)', (title, content))
        await db.commit()

# Функция редактирования новости
async def edit_news(news_id, title=None, content=None):
    async with aiosqlite.connect('bot_db.db') as db:
        if title is not None:
            await db.execute('UPDATE news SET title = ? WHERE id = ?', (title, news_id))
        if content is not None:
            await db.execute('UPDATE news SET content = ? WHERE id = ?', (content, news_id))
        await db.commit()

# Функция удаления новости
async def delete_news(news_id):
    async with aiosqlite.connect('bot_db.db') as db:
        await db.execute('DELETE FROM news WHERE id = ?', (news_id,))
        await db.commit()

# Функция получения новости по ID
async def get_news_by_id(news_id):
    async with aiosqlite.connect('bot_db.db') as db:
        cursor = await db.execute('SELECT id, title, content, created_at FROM news WHERE id = ?', (news_id,))
        row = await cursor.fetchone()
        return row

# Функция добавления или обновления голоса
async def add_or_update_vote(user_id, news_id, vote):
    async with aiosqlite.connect('bot_db.db') as db:
        await db.execute('''
            INSERT OR REPLACE INTO votes (user_id, news_id, vote)
            VALUES (?, ?, ?)
        ''', (user_id, news_id, vote))
        await db.commit()

# Функция получения количества лайков и дизлайков для новости
async def get_votes(news_id):
    async with aiosqlite.connect('bot_db.db') as db:
        cursor = await db.execute('SELECT vote, COUNT(*) FROM votes WHERE news_id = ? GROUP BY vote', (news_id,))
        rows = await cursor.fetchall()
        likes = 0
        dislikes = 0
        for vote, count in rows:
            if vote == 1:
                likes = count
            elif vote == -1:
                dislikes = count
        return likes, dislikes

# Функция получения голоса пользователя за новость
async def get_user_vote(user_id, news_id):
    async with aiosqlite.connect('bot_db.db') as db:
        cursor = await db.execute('SELECT vote FROM votes WHERE user_id = ? AND news_id = ?', (user_id, news_id))
        row = await cursor.fetchone()
        return row[0] if row else 0

# Функция отправки уведомления о новости всем пользователям с включенными уведомлениями
async def send_news_notification(news_id, title):
    async with aiosqlite.connect('bot_db.db') as db:
        cursor = await db.execute('SELECT user_id FROM users WHERE notifications_enabled = 1')
        user_ids = await cursor.fetchall()
    
    for (user_id,) in user_ids:
        try:
            lang = await get_user_language(user_id) or 'ru'
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=texts[lang]['view_news_button'], callback_data=f'view_news_{news_id}')],
                [InlineKeyboardButton(text=texts[lang]['disable_notifications_button'], callback_data='disable_notifications')]
            ])
            await bot.send_message(user_id, f"ПРИШЛА новая новость: {title}", reply_markup=keyboard)
        except Exception as e:
            print(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

# Тексты на разных языках
texts = {
    'ru': {
        'start_select': "Добрый день. Пожалуйста, выберите язык:",
        'start_main': "Добрый день. Пожалуйста, выберите действие:",
        'button_distance': "Узнать расстояние кометы",
        'button_info': "Информация о комете",
        'button_settings': "Настройки",
        'button_news': "Новости",
        'button_admin': "Админ панель",
        'distance_text': "Текущее расстояние до кометы (данные обновлены {time}):\n{distance}\n\nСледующее обновление через {left} секунд.",
        'distance_text_no_cache': "Текущее расстояние до кометы:\n{distance}",
        'error_distance': "Ошибка получения данных о комете. Повторите запрос позже.",
        'info': (
            "Информация о комете 3I/ATLAS:\n"
            "- Комета открыта в 2025 году.\n"
            "- Ожидается приближение к Солнцу с перигелием в ближайшие месяцы.\n"
            "- Яркость и видимость зависят от текущего расстояния.\n"
            "- Источник данных: The Sky Live.\n\n"
            "Для получения актуального расстояния выберите 'Узнать расстояние кометы'."
        ),
        'settings': "Настройки:\nВыберите опцию:",
        'settings_language': "Язык",
        'settings_unit': "Единицы измерения",
        'settings_notifications': "Уведомления о новостях",
        'language_ru': "Русский",
        'language_en': "English",
        'language_set': "Язык установлен на {lang}.",
        'unit_select': "Выберите единицу измерения расстояния:",
        'unit_km': "км",
        'unit_au': "AU",
        'unit_light': "св. лет",
        'unit_m': "м",
        'unit_km_selected': "км ✅",
        'unit_au_selected': "AU ✅",
        'unit_light_selected': "св. лет ✅",
        'unit_m_selected': "м ✅",
        'unit_set': "Единица измерения установлена на {unit}.",
        'notifications_enabled': "Уведомления включены",
        'notifications_disabled': "Уведомления выключены",
        'toggle_notifications_enable': "Включить уведомления",
        'toggle_notifications_disable': "Выключить уведомления",
        'fallback_no_lang': "Введите /start для начала работы с ботом.",
        'news_list': "Выберите новость для чтения:",
        'no_news': "Новостей пока нет.",
        'news_detail': "Новость от {date} {time}:\n\n{title}\n\n{content}\n\n👍 {likes} 👎 {dislikes}",
        'admin_panel': "Админ панель:",
        'admin_news': "Новости",
        'admin_add_news': "Добавить новость",
        'admin_edit_news': "Редактировать новости",
        'admin_delete_news': "Удалить новости",
        'enter_news_title': "Введите заголовок новости:",
        'enter_news_content': "Введите содержание новости:",
        'news_added': "Новость добавлена!",
        'news_deleted': "Новость удалена!",
        'news_not_found': "Новость не найдена.",
        'select_news_to_edit': "Выберите новость для редактирования:",
        'select_news_to_delete': "Выберите новость для удаления:",
        'edit_title_or_content': "Что редактировать?",
        'edit_title': "Заголовок",
        'edit_content': "Содержание",
        'enter_new_title': "Введите новый заголовок:",
        'enter_new_content': "Введите новое содержание:",
        'news_updated': "Новость обновлена!",
        'back_button': "◀ Назад",
        'view_news_button': "Посмотреть новость",
        'disable_notifications_button': "Выключить уведомления",
        'previous_page': "◀ Предыдущая",
        'next_page': "Следующая ▶",
        'like_button': "👍",
        'dislike_button': "👎",
        'already_voted': "Вы уже проголосовали за эту новость!",
        'vote_updated': "Ваш голос обновлён!",
        'vote_canceled': "Ваш голос отменён!",
    },
    'en': {
        'start_select': "Hello. Please select language:",
        'start_main': "Hello. Please choose an action:",
        'button_distance': "Get comet distance",
        'button_info': "Comet information",
        'button_settings': "Settings",
        'button_news': "News",
        'button_admin': "Admin panel",
        'distance_text': "Current distance to the comet (data updated at {time}):\n{distance}\n\nNext update in {left} seconds.",
        'distance_text_no_cache': "Current distance to the comet:\n{distance}",
        'error_distance': "Error retrieving comet data. Please try again later.",
        'info': (
            "Information about comet 3I/ATLAS:\n"
            "- Discovered in 2025.\n"
            "- Expected to approach the Sun with perihelion in the coming months.\n"
            "- Brightness and visibility depend on the current distance.\n"
            "- Data source: The Sky Live.\n\n"
            "To get the current distance, select 'Get comet distance'."
        ),
        'settings': "Settings:\nChoose an option:",
        'settings_language': "Language",
        'settings_unit': "Distance units",
        'settings_notifications': "News notifications",
        'language_ru': "Russian",
        'language_en': "English",
        'language_set': "Language set to {lang}.",
        'unit_select': "Select distance unit:",
        'unit_km': "km",
        'unit_au': "AU",
        'unit_light': "light years",
        'unit_m': "m",
        'unit_km_selected': "km ✅",
        'unit_au_selected': "AU ✅",
        'unit_light_selected': "light years ✅",
        'unit_m_selected': "m ✅",
        'unit_set': "Distance unit set to {unit}.",
        'notifications_enabled': "Notifications enabled",
        'notifications_disabled': "Notifications disabled",
        'toggle_notifications_enable': "Enable notifications",
        'toggle_notifications_disable': "Disable notifications",
        'fallback_no_lang': "Enter /start to start using the bot.",
        'news_list': "Choose news to read:",
        'no_news': "No news yet.",
        'news_detail': "News from {date} {time}:\n\n{title}\n\n{content}\n\n👍 {likes} 👎 {dislikes}",
        'admin_panel': "Admin panel:",
        'admin_news': "News",
        'admin_add_news': "Add news",
        'admin_edit_news': "Edit news",
        'admin_delete_news': "Delete news",
        'enter_news_title': "Enter news title:",
        'enter_news_content': "Enter news content:",
        'news_added': "News added!",
        'news_deleted': "News deleted!",
        'news_not_found': "News not found.",
        'select_news_to_edit': "Select news to edit:",
        'select_news_to_delete': "Select news to delete:",
        'edit_title_or_content': "What to edit?",
        'edit_title': "Title",
        'edit_content': "Content",
        'enter_new_title': "Enter new title:",
        'enter_new_content': "Enter new content:",
        'news_updated': "News updated!",
        'back_button': "◀ Back",
        'view_news_button': "View news",
        'disable_notifications_button': "Disable notifications",
        'previous_page': "◀ Previous",
        'next_page': "Next ▶",
        'like_button': "👍",
        'dislike_button': "👎",
        'already_voted': "You have already voted for this news!",
        'vote_updated': "Your vote has been updated!",
        'vote_canceled': "Your vote has been canceled!",
    }
}

# Функция парсинга дистанции (без изменений)
async def parse_distance():
    try:
        url = 'https://theskylive.com/c2025n1-info'
        response = requests.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # Найдём заголовок "Distance from Earth"
        distance_header = soup.find('h2', string=re.compile(r'Distance from Earth', re.IGNORECASE))
        if not distance_header:
            print("Заголовок 'Distance from Earth' не найден.")
            return None

        # Найдём следующий элемент после заголовка
        next_element = distance_header.find_next_sibling()
        text_to_search = ""
        while next_element and next_element.name != 'h2':
            if next_element.string:
                text_to_search += next_element.string.strip() + " "
            else:
                text_to_search += next_element.get_text().strip() + " "
            next_element = next_element.find_next_sibling()

        print(f"Текст для поиска расстояния: {text_to_search[:200]}...")  # Для отладки

        # Ищем значение в астрономических единицах (AU)
        au_pattern = r'(\d+\.?\d*)\s*(?:Astronomical Units?|AU)'
        au_match = re.search(au_pattern, text_to_search, re.IGNORECASE)

        if au_match:
            au_distance = float(au_match.group(1))
            km_distance = au_distance * AU_TO_KM
            print(f"Найдено расстояние: {au_distance} AU = {km_distance} km")
            return km_distance
        else:
            print("Значение AU не найдено в тексте.")
            return None

    except requests.RequestException as e:
        print(f"Ошибка запроса: {e}")
        return None
    except Exception as e:
        print(f"Ошибка парсинга: {e}")
        return None

# Функция получения данных (с кэшем)
async def get_distance_data():
    global cached_distance, last_update
    now = datetime.now()
    if last_update is None or (now - last_update) > timedelta(seconds=UPDATE_INTERVAL):
        print("Обновление кэша расстояния...")
        cached_distance = await parse_distance()
        last_update = now
    return cached_distance, last_update

# Функция форматирования расстояния в разных единицах
def format_distance(distance_km, unit, lang):
    if distance_km is None:
        return texts[lang]['error_distance']

    if unit == 'km':
        formatted = f"{distance_km:,.0f} {texts[lang]['unit_km']}"
    elif unit == 'au':
        au = distance_km / AU_TO_KM
        formatted = f"{au:.3f} {texts[lang]['unit_au']}"
    elif unit == 'light_years':
        ly = distance_km / LIGHT_YEAR_KM
        formatted = f"{ly:.6f} {texts[lang]['unit_light']}"
    elif unit == 'm':
        meters = distance_km * 1000
        formatted = f"{meters:,.0f} {texts[lang]['unit_m']}"
    else:
        formatted = f"{distance_km:,.0f} {texts[lang]['unit_km']}"  # Fallback

    return formatted

# Создание клавиатуры выбора языка
def get_language_keyboard(lang=None):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts['ru']['language_ru'], callback_data='lang_ru')],
        [InlineKeyboardButton(text=texts['en']['language_en'], callback_data='lang_en')]
    ])
    return keyboard

# Создание главного меню
def get_main_keyboard(lang, user_id):
    buttons = [
        [KeyboardButton(text=texts[lang]['button_distance'])],
        [KeyboardButton(text=texts[lang]['button_info'])],
        [KeyboardButton(text=texts[lang]['button_news'])],
        [KeyboardButton(text=texts[lang]['button_settings'])]
    ]
    if user_id == ADMIN_ID:
        buttons.append([KeyboardButton(text=texts[lang]['button_admin'])])
    keyboard = ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    return keyboard

# Создание клавиатуры настроек
def get_settings_keyboard(lang):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts[lang]['settings_language'], callback_data='settings_lang')],
        [InlineKeyboardButton(text=texts[lang]['settings_unit'], callback_data='settings_unit')],
        [InlineKeyboardButton(text=texts[lang]['settings_notifications'], callback_data='settings_notifications')]
    ])
    return keyboard

# Создание клавиатуры выбора единиц
def get_unit_keyboard(current_unit, lang):
    units = {
        'km': texts[lang]['unit_km_selected'] if current_unit == 'km' else texts[lang]['unit_km'],
        'au': texts[lang]['unit_au_selected'] if current_unit == 'au' else texts[lang]['unit_au'],
        'light_years': texts[lang]['unit_light_selected'] if current_unit == 'light_years' else texts[lang]['unit_light'],
        'm': texts[lang]['unit_m_selected'] if current_unit == 'm' else texts[lang]['unit_m']
    }
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=units['km'], callback_data='unit_km'),
            InlineKeyboardButton(text=units['au'], callback_data='unit_au')
        ],
        [
            InlineKeyboardButton(text=units['light_years'], callback_data='unit_light_years'),
            InlineKeyboardButton(text=units['m'], callback_data='unit_m')
        ],
        [InlineKeyboardButton(text=texts[lang]['back_button'], callback_data='back_to_settings')]
    ])
    return keyboard

# Создание клавиатуры уведомлений
def get_notifications_keyboard(enabled, lang):
    button_text = texts[lang]['toggle_notifications_disable'] if enabled else texts[lang]['toggle_notifications_enable']
    callback_data = 'toggle_notifications_disable' if enabled else 'toggle_notifications_enable'
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=button_text, callback_data=callback_data)],
        [InlineKeyboardButton(text=texts[lang]['back_button'], callback_data='back_to_settings')]
    ])
    return keyboard

# Создание клавиатуры админ панели
def get_admin_keyboard(lang):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts[lang]['admin_news'], callback_data='admin_news')]])
    return keyboard

# Создание клавиатуры управления новостями админа
def get_admin_news_keyboard(lang):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts[lang]['admin_add_news'], callback_data='admin_add_news')],
        [InlineKeyboardButton(text=texts[lang]['admin_edit_news'], callback_data='admin_edit_news')],
        [InlineKeyboardButton(text=texts[lang]['admin_delete_news'], callback_data='admin_delete_news')],
        [InlineKeyboardButton(text=texts[lang]['back_button'], callback_data='back_to_admin')]
    ])
    return keyboard

# Создание клавиатуры выбора новости для редактирования/удаления
def get_select_news_keyboard(news_list, action, lang):
    buttons = []
    for row in news_list:
        news_id, title, _, created_at_str = row
        created_at = datetime.fromisoformat(created_at_str)
        date_str = created_at.strftime("%d.%m.%Y" if lang == 'ru' else "%m/%d/%Y")
        short_title = title[:25] + "..." if len(title) > 25 else title
        button_text = f"{date_str}: {short_title}"
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=f'{action}_news_{news_id}')])
    buttons.append([InlineKeyboardButton(text=texts[lang]['back_button'], callback_data='back_to_admin_news')])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard

# Создание клавиатуры выбора что редактировать
def get_edit_what_keyboard(lang):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts[lang]['edit_title'], callback_data='edit_title')],
        [InlineKeyboardButton(text=texts[lang]['edit_content'], callback_data='edit_content')],
        [InlineKeyboardButton(text=texts[lang]['back_button'], callback_data='back_to_select_edit')]
    ])
    return keyboard

# Создание клавиатуры новостей для пользователей (с пагинацией)
def get_news_keyboard(news_list, lang, page=0, per_page=5):
    start = page * per_page
    end = start + per_page
    current_news = news_list[start:end]
    buttons = []
    for row in current_news:
        news_id, title, _, created_at_str = row
        created_at = datetime.fromisoformat(created_at_str)
        date_str = created_at.strftime("%d.%m.%Y" if lang == 'ru' else "%m/%d/%Y")
        short_title = title[:25] + "..." if len(title) > 25 else title
        button_text = f"{date_str}: {short_title}"
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=f'view_news_{news_id}')])
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text=texts[lang]['previous_page'], callback_data=f'news_page_{page-1}'))
    if end < len(news_list):
        nav_buttons.append(InlineKeyboardButton(text=texts[lang]['next_page'], callback_data=f'news_page_{page+1}'))
    if nav_buttons:
        buttons.append(nav_buttons)
    if not buttons:
        return None
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard

# Обработчик /start
@dp.message(Command('start'))
async def start_handler(message: types.Message):
    user_id = message.from_user.id
    lang = await get_user_language(user_id)
    if lang:
        await message.answer(texts[lang]['start_main'], reply_markup=get_main_keyboard(lang, user_id))
    else:
        await message.answer(texts['ru']['start_select'], reply_markup=get_language_keyboard())

# Обработчик callback для выбора языка
@dp.callback_query(F.data.startswith('lang_'))
async def lang_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    lang_code = callback.data.split('_')[1]
    lang = 'ru' if lang_code == 'ru' else 'en'
    await set_user_language(user_id, lang)
    await callback.message.edit_text(
        texts[lang]['language_set'].format(lang=texts[lang]['language_' + lang_code]),
        reply_markup=None
    )
    await callback.answer()
    # Показываем главное меню
    await callback.message.answer(texts[lang]['start_main'], reply_markup=get_main_keyboard(lang, user_id))

# Обработчик кнопок главного меню
@dp.message(F.text.in_(['Узнать расстояние кометы', 'Get comet distance', 'Информация о комете', 'Comet information', 'Настройки', 'Settings', 'Новости', 'News']))
async def main_menu_handler(message: types.Message):
    user_id = message.from_user.id
    lang = await get_user_language(user_id) or 'ru'
    text = message.text.lower()

    if 'distance' in text or 'расстояние' in text:
        distance, update_time = await get_distance_data()
        now = datetime.now()
        unit = await get_user_unit(user_id)
        formatted_distance = format_distance(distance, unit, lang)

        if distance is not None:
            if last_update:
                time_str = update_time.strftime("%H:%M:%S")
                left_seconds = max(0, UPDATE_INTERVAL - int((now - update_time).total_seconds()))
                full_text = texts[lang]['distance_text'].format(
                    time=time_str, distance=formatted_distance, left=left_seconds
                )
            else:
                full_text = texts[lang]['distance_text_no_cache'].format(distance=formatted_distance)
        else:
            full_text = texts[lang]['error_distance']

        await message.answer(full_text)

    elif 'info' in text or 'информация' in text:
        await message.answer(texts[lang]['info'])

    elif 'settings' in text or 'настройки' in text:
        await message.answer(texts[lang]['settings'], reply_markup=get_settings_keyboard(lang))

    elif 'news' in text or 'новости' in text:
        news_list = await get_all_news()
        if news_list:
            keyboard = get_news_keyboard(news_list, lang, page=0)
            await message.answer(texts[lang]['news_list'], reply_markup=keyboard)
        else:
            await message.answer(texts[lang]['no_news'])

# Отдельный обработчик для админ панели
@dp.message(F.text.in_(['Админ панель', 'Admin panel']))
async def admin_menu_handler(message: types.Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.answer("Доступ запрещен!")
        return
    lang = await get_user_language(user_id) or 'ru'
    await message.answer(texts[lang]['admin_panel'], reply_markup=get_admin_keyboard(lang))

# Обработчик настроек
@dp.callback_query(F.data == 'settings_lang')
async def settings_lang_callback(callback: types.CallbackQuery):
    lang = await get_user_language(callback.from_user.id) or 'ru'
    await callback.message.edit_text(
        texts[lang]['settings_language'] + ":\n" + texts[lang].get('select_language', ''),
        reply_markup=get_language_keyboard(lang)
    )
    await callback.answer()

@dp.callback_query(F.data == 'settings_unit')
async def settings_unit_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    lang = await get_user_language(user_id) or 'ru'
    current_unit = await get_user_unit(user_id)
    await callback.message.edit_text(
        texts[lang]['unit_select'],
        reply_markup=get_unit_keyboard(current_unit, lang)
    )
    await callback.answer()

@dp.callback_query(F.data == 'settings_notifications')
async def settings_notifications_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    lang = await get_user_language(user_id) or 'ru'
    enabled = await get_user_notifications(user_id)
    status_text = texts[lang]['notifications_enabled'] if enabled else texts[lang]['notifications_disabled']
    await callback.message.edit_text(
        f"{texts[lang]['settings_notifications']}:\n{status_text}",
        reply_markup=get_notifications_keyboard(enabled, lang)
    )
    await callback.answer()

# Обработчик toggle уведомлений
@dp.callback_query(F.data.in_(['toggle_notifications_enable', 'toggle_notifications_disable']))
async def toggle_notifications_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    lang = await get_user_language(user_id) or 'ru'
    enabled = 1 if callback.data == 'toggle_notifications_enable' else 0
    await set_user_notifications(user_id, enabled)
    status_text = texts[lang]['notifications_enabled'] if enabled else texts[lang]['notifications_disabled']
    await callback.message.edit_text(
        f"{texts[lang]['settings_notifications']}:\n{status_text}",
        reply_markup=get_notifications_keyboard(enabled, lang)
    )
    await callback.answer()

# Обработчик выбора единицы
@dp.callback_query(F.data.startswith('unit_'))
async def unit_callback(callback: types.CallbackQuery):
    if callback.from_user.id in processing_callbacks:
        await callback.answer("Обработка в процессе...")
        return

    processing_callbacks[callback.from_user.id] = True
    try:
        user_id = callback.from_user.id
        new_unit = callback.data.split('_')[1]  # unit_km -> km, unit_light_years -> light_years
        if new_unit == 'light_years':
            new_unit = 'light_years'
        current_unit = await get_user_unit(user_id)

        if new_unit == current_unit:
            await callback.answer("Единица уже выбрана! ✅")
            return

        await set_user_unit(user_id, new_unit)

        lang = await get_user_language(user_id) or 'ru'
        current_text = callback.message.text or texts[lang]['unit_select']
        new_text = current_text + f"\n\n{texts[lang]['unit_set'].format(unit=texts[lang][f'unit_{new_unit}'])}"

        keyboard = get_unit_keyboard(new_unit, lang)

        if callback.message.text != new_text or callback.message.reply_markup != keyboard:
            await callback.message.edit_text(new_text, reply_markup=keyboard)

        await callback.answer()
    finally:
        processing_callbacks.pop(callback.from_user.id, None)

# Обработчик возврата в настройки
@dp.callback_query(F.data == 'back_to_settings')
async def back_to_settings_callback(callback: types.CallbackQuery):
    lang = await get_user_language(callback.from_user.id) or 'ru'
    await callback.message.edit_text(
        texts[lang]['settings'],
        reply_markup=get_settings_keyboard(lang)
    )
    await callback.answer()

# Обработчики админ панели
@dp.callback_query(F.data == 'back_to_admin')
async def back_to_admin_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен!")
        return
    lang = await get_user_language(callback.from_user.id) or 'ru'
    await callback.message.edit_text(
        texts[lang]['admin_panel'],
        reply_markup=get_admin_keyboard(lang)
    )
    await callback.answer()

@dp.callback_query(F.data == 'back_to_admin_news')
async def back_to_admin_news_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен!")
        return
    lang = await get_user_language(callback.from_user.id) or 'ru'
    await callback.message.edit_text(
        texts[lang]['admin_news'],
        reply_markup=get_admin_news_keyboard(lang)
    )
    await callback.answer()

@dp.callback_query(F.data == 'admin_news')
async def admin_news_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен!")
        return
    lang = await get_user_language(callback.from_user.id) or 'ru'
    await callback.message.edit_text(
        texts[lang]['admin_news'],
        reply_markup=get_admin_news_keyboard(lang)
    )
    await callback.answer()

# Состояния для админа
admin_states = {}  # user_id: {'state': '...', 'data': {...}}

@dp.callback_query(F.data == 'admin_add_news')
async def admin_add_news_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен!")
        return
    admin_states[callback.from_user.id] = {'state': 'add_title', 'data': {}}
    lang = await get_user_language(callback.from_user.id) or 'ru'
    await callback.message.edit_text(texts[lang]['enter_news_title'])
    await callback.answer()

@dp.callback_query(F.data == 'admin_edit_news')
async def admin_edit_news_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен!")
        return
    news_list = await get_all_news()
    if not news_list:
        lang = await get_user_language(callback.from_user.id) or 'ru'
        await callback.message.edit_text(texts[lang]['no_news'])
        await callback.answer()
        return
    lang = await get_user_language(callback.from_user.id) or 'ru'
    admin_states[callback.from_user.id] = {'state': 'select_edit_news', 'data': {}}
    await callback.message.edit_text(texts[lang]['select_news_to_edit'], reply_markup=get_select_news_keyboard(news_list, 'edit', lang))
    await callback.answer()

@dp.callback_query(F.data == 'admin_delete_news')
async def admin_delete_news_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен!")
        return
    news_list = await get_all_news()
    if not news_list:
        lang = await get_user_language(callback.from_user.id) or 'ru'
        await callback.message.edit_text(texts[lang]['no_news'])
        await callback.answer()
        return
    lang = await get_user_language(callback.from_user.id) or 'ru'
    admin_states[callback.from_user.id] = {'state': 'select_delete_news', 'data': {}}
    await callback.message.edit_text(texts[lang]['select_news_to_delete'], reply_markup=get_select_news_keyboard(news_list, 'delete', lang))
    await callback.answer()

@dp.callback_query(F.data.startswith('edit_news_'))
async def edit_news_select_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен!")
        return
    news_id = int(callback.data.split('_')[2])
    admin_states[callback.from_user.id] = {'state': 'edit_what', 'data': {'news_id': news_id}}
    lang = await get_user_language(callback.from_user.id) or 'ru'
    await callback.message.edit_text(texts[lang]['edit_title_or_content'], reply_markup=get_edit_what_keyboard(lang))
    await callback.answer()

@dp.callback_query(F.data == 'edit_title')
async def edit_title_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен!")
        return
    admin_states[callback.from_user.id]['state'] = 'enter_new_title'
    lang = await get_user_language(callback.from_user.id) or 'ru'
    await callback.message.edit_text(texts[lang]['enter_new_title'])
    await callback.answer()

@dp.callback_query(F.data == 'edit_content')
async def edit_content_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен!")
        return
    admin_states[callback.from_user.id]['state'] = 'enter_new_content'
    lang = await get_user_language(callback.from_user.id) or 'ru'
    await callback.message.edit_text(texts[lang]['enter_new_content'])
    await callback.answer()

@dp.callback_query(F.data == 'back_to_select_edit')
async def back_to_select_edit_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен!")
        return
    news_list = await get_all_news()
    lang = await get_user_language(callback.from_user.id) or 'ru'
    await callback.message.edit_text(texts[lang]['select_news_to_edit'], reply_markup=get_select_news_keyboard(news_list, 'edit', lang))
    await callback.answer()

@dp.callback_query(F.data.startswith('delete_news_'))
async def delete_news_select_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен!")
        return
    news_id = int(callback.data.split('_')[2])
    await delete_news(news_id)
    lang = await get_user_language(callback.from_user.id) or 'ru'
    await callback.message.edit_text(texts[lang]['news_deleted'], reply_markup=get_admin_news_keyboard(lang))
    await callback.answer()

@dp.message(F.text)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    if user_id not in admin_states:
        return  # Не админ в состоянии

    state = admin_states[user_id]['state']
    lang = await get_user_language(user_id) or 'ru'

    if state == 'add_title':
        admin_states[user_id]['data']['title'] = message.text
        admin_states[user_id]['state'] = 'add_content'
        await message.answer(texts[lang]['enter_news_content'])
    elif state == 'add_content':
        title = admin_states[user_id]['data']['title']
        content = message.text
        await add_news(title, content)
        # Получаем ID последней добавленной новости
        news_list = await get_all_news()
        news_id = news_list[0][0] if news_list else None
        if news_id:
            await send_news_notification(news_id, title)
        await message.answer(texts[lang]['news_added'], reply_markup=get_admin_news_keyboard(lang))
        del admin_states[user_id]
    elif state == 'enter_new_title':
        news_id = admin_states[user_id]['data']['news_id']
        await edit_news(news_id, title=message.text)
        await message.answer(texts[lang]['news_updated'], reply_markup=get_admin_news_keyboard(lang))
        del admin_states[user_id]
    elif state == 'enter_new_content':
        news_id = admin_states[user_id]['data']['news_id']
        await edit_news(news_id, content=message.text)
        await message.answer(texts[lang]['news_updated'], reply_markup=get_admin_news_keyboard(lang))
        del admin_states[user_id]

# Обработчик просмотра новости (с кнопками лайк/дизлайк и счетчиками)
@dp.callback_query(F.data.startswith('view_news_'))
async def view_news_callback(callback: types.CallbackQuery):
    news_id = int(callback.data.split('_')[2])
    news = await get_news_by_id(news_id)
    if news:
        lang = await get_user_language(callback.from_user.id) or 'ru'
        created_at = datetime.fromisoformat(news[3])
        date = created_at.strftime("%d.%m.%Y" if lang == 'ru' else "%m/%d/%Y")
        time = created_at.strftime("%H:%M")
        likes, dislikes = await get_votes(news_id)
        text = texts[lang]['news_detail'].format(date=date, time=time, title=news[1], content=news[2], likes=likes, dislikes=dislikes)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts[lang]['like_button'], callback_data=f'like_{news_id}'),
             InlineKeyboardButton(text=texts[lang]['dislike_button'], callback_data=f'dislike_{news_id}')],
            [InlineKeyboardButton(text=texts[lang]['back_button'], callback_data='back_to_news_list')]
        ])
        await callback.message.edit_text(text, reply_markup=keyboard)
    else:
        lang = await get_user_language(callback.from_user.id) or 'ru'
        await callback.message.edit_text(texts[lang]['news_not_found'])
    await callback.answer()

# Обработчик пагинации новостей
@dp.callback_query(F.data.startswith('news_page_'))
async def news_page_callback(callback: types.CallbackQuery):
    page = int(callback.data.split('_')[2])
    lang = await get_user_language(callback.from_user.id) or 'ru'
    news_list = await get_all_news()
    if news_list:
        keyboard = get_news_keyboard(news_list, lang, page=page)
        if keyboard:
            await callback.message.edit_reply_markup(reply_markup=keyboard)
        else:
            await callback.message.edit_text(texts[lang]['no_news'])
    await callback.answer()

# Обработчик возврата к списку новостей
@dp.callback_query(F.data == 'back_to_news_list')
async def back_to_news_list_callback(callback: types.CallbackQuery):
    lang = await get_user_language(callback.from_user.id) or 'ru'
    news_list = await get_all_news()
    if news_list:
        keyboard = get_news_keyboard(news_list, lang, page=0)
        await callback.message.edit_text(texts[lang]['news_list'], reply_markup=keyboard)
    else:
        await callback.message.edit_text(texts[lang]['no_news'])
    await callback.answer()

# Обработчик лайка
@dp.callback_query(F.data.startswith('like_'))
async def like_callback(callback: types.CallbackQuery):
    if callback.from_user.id in processing_callbacks:
        await callback.answer("Обработка в процессе...")
        return
    processing_callbacks[callback.from_user.id] = True
    try:
        news_id = int(callback.data.split('_')[1])
        user_id = callback.from_user.id
        current_vote = await get_user_vote(user_id, news_id)
        lang = await get_user_language(user_id) or 'ru'

        if current_vote == 1:
            await callback.answer(texts[lang]['already_voted'])
            return
        elif current_vote == -1:
            # Отменяем дизлайк и ставим лайк
            await add_or_update_vote(user_id, news_id, 1)
            await callback.answer(texts[lang]['vote_updated'])
        else:
            # Ставим лайк
            await add_or_update_vote(user_id, news_id, 1)
            await callback.answer("👍 Спасибо за лайк!")

        # Обновляем сообщение с новыми счётчиками
        news = await get_news_by_id(news_id)
        if news:
            created_at = datetime.fromisoformat(news[3])
            date = created_at.strftime("%d.%m.%Y" if lang == 'ru' else "%m/%d/%Y")
            time = created_at.strftime("%H:%M")
            likes, dislikes = await get_votes(news_id)
            text = texts[lang]['news_detail'].format(date=date, time=time, title=news[1], content=news[2], likes=likes, dislikes=dislikes)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=texts[lang]['like_button'], callback_data=f'like_{news_id}'),
                 InlineKeyboardButton(text=texts[lang]['dislike_button'], callback_data=f'dislike_{news_id}')],
                [InlineKeyboardButton(text=texts[lang]['back_button'], callback_data='back_to_news_list')]
            ])
            await callback.message.edit_text(text, reply_markup=keyboard)
    finally:
        processing_callbacks.pop(callback.from_user.id, None)

# Обработчик дизлайка
@dp.callback_query(F.data.startswith('dislike_'))
async def dislike_callback(callback: types.CallbackQuery):
    if callback.from_user.id in processing_callbacks:
        await callback.answer("Обработка в процессе...")
        return
    processing_callbacks[callback.from_user.id] = True
    try:
        news_id = int(callback.data.split('_')[1])
        user_id = callback.from_user.id
        current_vote = await get_user_vote(user_id, news_id)
        lang = await get_user_language(user_id) or 'ru'

        if current_vote == -1:
            await callback.answer(texts[lang]['already_voted'])
            return
        elif current_vote == 1:
            # Отменяем лайк и ставим дизлайк
            await add_or_update_vote(user_id, news_id, -1)
            await callback.answer(texts[lang]['vote_updated'])
        else:
            # Ставим дизлайк
            await add_or_update_vote(user_id, news_id, -1)
            await callback.answer("👎 Спасибо за отзыв!")

        # Обновляем сообщение с новыми счётчиками
        news = await get_news_by_id(news_id)
        if news:
            created_at = datetime.fromisoformat(news[3])
            date = created_at.strftime("%d.%m.%Y" if lang == 'ru' else "%m/%d/%Y")
            time = created_at.strftime("%H:%M")
            likes, dislikes = await get_votes(news_id)
            text = texts[lang]['news_detail'].format(date=date, time=time, title=news[1], content=news[2], likes=likes, dislikes=dislikes)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=texts[lang]['like_button'], callback_data=f'like_{news_id}'),
                 InlineKeyboardButton(text=texts[lang]['dislike_button'], callback_data=f'dislike_{news_id}')],
                [InlineKeyboardButton(text=texts[lang]['back_button'], callback_data='back_to_news_list')]
            ])
            await callback.message.edit_text(text, reply_markup=keyboard)
    finally:
        processing_callbacks.pop(callback.from_user.id, None)

# Обработчик отключения уведомлений из уведомления
@dp.callback_query(F.data == 'disable_notifications')
async def disable_notifications_from_notification(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await set_user_notifications(user_id, 0)
    lang = await get_user_language(user_id) or 'ru'
    await callback.message.edit_text(texts[lang]['notifications_disabled'])
    await callback.answer()

# Запуск бота
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
