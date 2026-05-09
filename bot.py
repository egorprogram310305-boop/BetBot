import os
from pathlib import Path
import asyncio
import logging
import requests
import psycopg2
import random
import re
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web
from deep_translator import GoogleTranslator
from aiogram.types import FSInputFile

# --- CONFIG & SYSTEM ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Baron_v7_2_FULL")

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0"))
API_KEYS = [k.strip() for k in os.getenv("ODDS_API_KEYS", "").split(",") if k.strip()]
REQUISITES = os.getenv("PAYMENT_REQUISITES", "Реквизиты не установлены")
DB_URL = os.getenv("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)

# Флаг для тех. отчета (True - включен, False - выключен)
SHOW_FULL_REPORT = False 

T_MGMT = int(os.getenv("TOPIC_ID_MANAGEMENT", "0"))
T_LOGS = int(os.getenv("TOPIC_ID_LOGS", "0")) 
T_CASH = int(os.getenv("TOPIC_ID_PAYMENTS", "0"))
T_USERS = int(os.getenv("TOPIC_ID_USERS", "0"))
T_PRED = int(os.getenv("TOPIC_ID_PREDICTIONS", "0"))

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

TARIFFS = {
    "mini": {"name": "МиниⓂ️ (3 дней)", "days": 3, "price": 149},
    "lite": {"name": "Лайт✨ (7 дней)", "days": 7, "price": 299},
    "classic": {"name": "Классика🎩 (30 дней)", "days": 30, "price": 999},
    "level": {"name": "Уровень💹 (60 дней)", "days": 60, "price": 1666}
}

LEAGUES = [
    # Твои старые лиги
    "soccer_germany_bundesliga", "soccer_epl", "soccer_netherlands_eredivisie",
    "soccer_spain_la_liga", "soccer_belgium_first_div", "soccer_switzerland_superleague",
    "soccer_austria_bundesliga", "soccer_usa_mls",
    "soccer_norway_eliteserien",
    
    # Новые прибыльные лиги
    "soccer_denmark_superliga",   # Дания
    "soccer_sweden_allsvenskan",  # Швеция
    "soccer_turkey_super_lig",    # Турция
    "soccer_poland_ekstraklasa",  # Польша
    "soccer_portugal_primeira_liga", # Португалия
    "soccer_chile_campeonato"     # Чили
]


# --- FSM STATES ---
class AdminStates(StatesGroup):
    wait_min_odds = State()
    wait_mult = State()
    wait_time = State()
    wait_broadcast_msg = State()

class BetStates(StatesGroup):
    wait_amount = State()
    wait_odds = State()

# --- DATABASE LOGIC ---
def init_db():
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("CREATE TABLE IF NOT EXISTS users (uid BIGINT PRIMARY KEY, username TEXT, sub_end TIMESTAMP, trial_used INTEGER DEFAULT 0)")
        curr.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        curr.execute("INSERT INTO settings (key, value) VALUES ('min_odds', '1.50'), ('max_odds', '2.50'), ('multiplier', '0.90'), ('time_depth', '24') ON CONFLICT DO NOTHING")
        conn.commit(); curr.close(); conn.close()
    except Exception as e: logger.error(f"DB Error: {e}")

def get_setting(key, default):
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("SELECT value FROM settings WHERE key = %s", (key,))
        res = curr.fetchone(); curr.close(); conn.close()
        return float(res[0]) if res else default
    except: return default

def set_setting(key, value):
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = %s", (key, str(value), str(value)))
        conn.commit(); curr.close(); conn.close()
    except: pass

def check_and_add_user(uid, username):
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("SELECT trial_used FROM users WHERE uid = %s", (uid,))
        res = curr.fetchone()
        if not res:
            end_trial = datetime.now(timezone.utc) + timedelta(days=3)
            curr.execute("INSERT INTO users (uid, username, sub_end, trial_used) VALUES (%s, %s, %s, 1)", (uid, username, end_trial))
            conn.commit(); curr.close(); conn.close()
            return True, True # is_new, trial_given
        curr.close(); conn.close()
        return False, False
    except: return False, False

def get_sub_status(uid):
    if uid == ADMIN_ID: return True
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("SELECT sub_end FROM users WHERE uid = %s", (uid,))
        res = curr.fetchone(); curr.close(); conn.close()
        if res: return res[0].replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
    except: pass
    return False

# --- ANALYTICS & UTILS ---
def clean_and_translate(name):
    bad = ["U23", "U21", "U19", "FC", "CF", "SSC", "AS", "Utd", "United", "BSC", "AC", "City", "Real", "St", "De", "Club", "FK"]
    cleaned = " ".join([w for w in name.split() if w not in bad]).strip()
    try: return GoogleTranslator(source='auto', target='ru').translate(cleaned)
    except: return cleaned

async def analyze_strict(team_name, is_home):
    await asyncio.sleep(random.uniform(3, 6)) 
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0)'}
        # Уточняем запрос, чтобы Google давал более релевантные результаты
        query = f"{team_name} matches results scores"
        res = requests.get(f"https://www.google.com/search?q={query}", headers=headers, timeout=7)
        content = res.text.lower()
        if "captcha" in content: return "CAPTCHA"
        
        # Улучшенное регулярное выражение: ищет счета типа 2-1, 1:0, 3 - 2
        # Игнорирует даты, так как ограничивает числа (от 0 до 9)
        found_scores = re.findall(r'([0-9])\s*[:\-\u2013]\s*([0-9])', content)
        
        itb_count = 0
        matches_checked = 0
        for s1, s2 in found_scores:
            if matches_checked >= 5: break
            
            # Если команда дома (is_home=True), смотрим на первую цифру (s1)
            # Если в гостях — на вторую (s2)
            goal = int(s1) if is_home else int(s2)
            
            if goal >= 2: # Проверяем ИТБ 1.5 (забито 2 и более)
                itb_count += 1
            matches_checked += 1
            
        return itb_count
    except:
        return 0

async def analyze_h2h(home_team, away_team):
    await asyncio.sleep(random.uniform(3, 6))
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0)'}
        query = f"{home_team} vs {away_team} last matches results"
        res = requests.get(f"https://www.google.com/search?q={query}", headers=headers, timeout=7)
        content = res.text.lower()
        if "captcha" in content: return "CAPTCHA"

        found_scores = re.findall(r'([0-9])\s*[:\-\u2013]\s*([0-9])', content)
        if not found_scores: return 0 
        
        # Считаем, сколько раз в личках было забито 2+ гола (общий тотал для проверки тренда)
        itb_h2h = sum(1 for s1, s2 in found_scores[:5] if (int(s1) + int(s2)) >= 2)
        return itb_h2h
    except:
        return 0



# --- UI HELPER ---
def main_menu_kb():
    builder = ReplyKeyboardBuilder()
    builder.button(text="🎯 Поиск Прогнозов")
    builder.button(text="👤 Профиль")
    builder.button(text="💳 Подписка")
    builder.button(text="🏛 Инструкция")  # Новая кнопка
    builder.button(text="🚀 Начать")      # Новая кнопка (аналог /start)
    
    # adjust(1, 2, 2) значит: 1 кнопка в первом ряду, по 2 в остальных
    return builder.adjust(1, 2, 2).as_markup(resize_keyboard=True)


# --- BOT INTERFACE ---
@dp.message(Command("ping"))
async def cmd_ping_logic(m: types.Message, command: CommandObject):
    global SHOW_FULL_REPORT
    if m.from_user.id != ADMIN_ID: return
    
    arg = command.args
    if arg == "999":
        SHOW_FULL_REPORT = True
        await m.answer("✅ <b>Режим детальной проверки включен.</b> Отчеты будут приходить в Тех. Лог.", parse_mode=ParseMode.HTML)
    elif arg == "1":
        SHOW_FULL_REPORT = False
        await m.answer("🛑 <b>Режим детальной проверки выключен.</b>", parse_mode=ParseMode.HTML)
    else:
        await m.answer("Используйте: <code>/ping 999</code> для ВКЛ или <code>/ping 1</code> для ВЫКЛ.", parse_mode=ParseMode.HTML)

@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    is_new, trial_given = check_and_add_user(m.from_user.id, m.from_user.username)
    
    if is_new and T_USERS != 0:
        await bot.send_message(ADMIN_GROUP_ID, f"🆕 <b>Новый пользователь!</b>\n👤 @{m.from_user.username} (<code>{m.from_user.id}</code>)", message_thread_id=T_USERS, parse_mode=ParseMode.HTML)
    
    welcome_text = (
        "🎩 <b>Baron’s Verdict: Твой вердикт — прибыли!</b>\n\n"
        """Добро пожаловать в закрытый аналитический клуб! Здесь решения принимаются на основе данных, а не эмоций. Наши алгоритмы ежедневно фильтруют сотни событий, оставляя только самые перспективные индивидуальные тоталы.
🎁 Тебе открыт VIP-доступ на 3 дня!
Используй это время, чтобы бесплатно протестировать точность наших прогнозов и ощутить мощь алгоритмического беттинга.
📍 Твой результат сегодня — это наша аналитика вчера.\n\n"""
        """<i>Выбирай действие:
1.	🎯 Получить прогноз — поиск актуальных исходов на ближайшие матчи.
2.	👤 Профиль — статус твоей подписки и настройки.</i>"""
    )
    if trial_given:
        welcome_text += "\n\n🎁 Вам автоматически начислен <b>пробный доступ на 3 дня!</b> Вы уже можете получать прогнозы."

    await m.answer(welcome_text, reply_markup=main_menu_kb(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "👤 Профиль")
async def btn_profile(m: types.Message):
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("SELECT sub_end FROM users WHERE uid = %s", (m.from_user.id,))
        res = curr.fetchone(); curr.close(); conn.close()
        date_str = res[0].strftime('%d.%m.%Y %H:%M') if res and get_sub_status(m.from_user.id) else "Нет доступа или истекла"
        await m.answer(f"👤 <b>Ваш профиль</b>\nID: <code>{m.from_user.id}</code>\nПодписка до: <b>{date_str}</b>", parse_mode=ParseMode.HTML)
    except: await m.answer("Ошибка БД")

@dp.message(F.text == "🏛 Инструкция")
async def btn_instruction(m: types.Message):
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    try:
        # 1. Сначала пытаемся отправить фото
        photo = FSInputFile("IMG_7179.png")
        await m.answer_photo(photo=photo)
        
        # 2. Затем формируем и отправляем текст
        text = (
            "🏛 <b>ИНСТРУКЦИЯ: BARON’S VERDICT</b>\n\n"
            "Добро пожаловать в эпицентр футбольной аналитики! <b>Baron’s Verdict</b> — это не просто бот, это твой персональный аналитический отдел, который работает 24/7 без эмоций и ошибок.\n\n"
            "🛡 <b>Что ты получаешь?</b>\n"
            "Наш секрет — в уникальном алгоритме динамической корреляции. Мы не просто ищем матчи, мы фильтруем их через «сито» жестких условий:\n"
            "• 🔥 <b>Форма команд:</b> Анализируем последние 5 матчей. Находим тех, кто «разносит» соперников прямо сейчас.\n"
            "• 🤝 <b>H2H (Личные встречи):</b> Проверяем историю противостояний. Если команды исторически играют результативно — это наш вариант.\n"
            "• 📊 <b>Математический перевес:</b> Бот автоматически корректирует коэффициенты, выдавая только те прогнозы, где риск минимален, а потенциал прибыли высок.\n\n"
            "🚀 <b>Как пользоваться ботом?</b>\n"
            "1. <b>Следи за уведомлениями:</b> Как только алгоритм находит «золотой» матч — ты получаешь сигнал в чат.\n"
            "2. <b>Делай ставку:</b> Мы рекомендуем <b>ИТБ 1.5</b> на конкретную команду (это значит, что команда должна забить минимум 2 гола).\n"
            "3. <b>Управляй банком:</b> Для стабильного пассивного дохода ставь фиксированный процент (флэт 3-5%).\n\n"
            "💳 <b>Как оформить подписку?</b>\n"
            "Доступ к VIP-аналитике открывается в пару кликов:\n"
            "1. Нажми кнопку «💳 Подписка» в главном меню.\n"
            "2. Выбери подходящий тариф.\n"
            "3. Переведи сумму по реквизитам. <b>Обязательно</b> укажи проверочный код в комментарии к платежу!\n"
            "4. Нажми «✅ Я оплатил» и дождись подтверждения.\n\n"
            "🤝 <b>Поддержка и связь</b>\n"
            "Если возникли вопросы — наш администратор всегда на связи.\n"
            "📍 <b>Контакт для связи:</b> @poprivetstvui"
        )
        await m.answer(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        # Это тот самый блок 'except', которого не хватало
        logger.error(f"Ошибка в инструкции: {e}")
        await m.answer("⚠️ Не удалось загрузить инструкцию. Попробуйте позже.")

# === КОНЕЦ ВСТАВКИ ===

@dp.message(F.text == "💳 Подписка")
async def btn_sub(m: types.Message):
    kb = InlineKeyboardBuilder()
    for tid, d in TARIFFS.items(): 
        kb.button(text=f"{d['name']} - {d['price']}₽", callback_data=f"buy_{tid}")
    
    # Твоя ссылка на оферту
    offer_link = "https://telegra.ph/Publichnaya-oferta-i-Politika-konfidencialnosti--Barons-Verdict-05-07"
    
    # В этом тексте ОБЯЗАТЕЛЬНО должна быть ссылка
    text = (
        "📊 <b>Выберите тарифный план:</b>\n"
        "<i>Инвестируйте в качественную аналитику.</i>\n\n"
        f"Оплачивая доступ, вы принимаете условия <a href='{offer_link}'>Публичной оферты</a>."
    )
    
    # parse_mode=ParseMode.HTML важен, чтобы ссылка стала кликабельной
    await m.answer(text, reply_markup=kb.adjust(1).as_markup(), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@dp.message(F.text == "🎯 Поиск Прогнозов")
async def btn_analytics(m: types.Message):
    if get_sub_status(m.from_user.id):
        # Сообщение, которое ты просил
        await m.answer("Анализ матчей начинается🔎 Как только будет найден прогноз, вы получите уведомление 🔔")
    else:
        await m.answer("У вас нет активной подписки. Перейдите в раздел 💳 Подписка.")

@dp.callback_query(F.data.startswith("buy_"))
async def buy(c: types.CallbackQuery):
    tid = c.data.split("_")[1]
    pid = random.randint(1000, 9999)
    text = (f"💳 <b>Оплата тарифа {TARIFFS[tid]['name']}</b>\n\n"
            f"Переведите <b>{TARIFFS[tid]['price']}₽</b> по реквизитам:\n"
            f"💳 Карта: <code>{REQUISITES}</code>\n\n"
            f"⚠️ Важно: В комментарии к платежу укажите код: <code>#{pid}</code>\n\n"
            f"<i>После перевода нажмите кнопку ниже.</i>")
    kb = InlineKeyboardBuilder().button(text="✅ Я оплатил", callback_data=f"done_{tid}_{pid}")
    await c.message.edit_text(text, reply_markup=kb.adjust(1).as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("done_"))
async def done(c: types.CallbackQuery):
    _, tid, pid = c.data.split("_")
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Одобрить", callback_data=f"adm_ok_{tid}_{c.from_user.id}")
    kb.button(text="❌ Отклонить", callback_data=f"adm_no_{c.from_user.id}")
    await bot.send_message(ADMIN_GROUP_ID, f"💰 <b>Заявка на оплату!</b>\nОт: @{c.from_user.username}\nID: <code>{c.from_user.id}</code>\nТариф: {TARIFFS[tid]['name']}\nСумма: {TARIFFS[tid]['price']}₽\nКод: #{pid}", 
                           reply_markup=kb.adjust(2).as_markup(), message_thread_id=T_CASH, parse_mode=ParseMode.HTML)
    await c.message.edit_text("⏳ <b>Заявка отправлена!</b>\nОжидайте проверки администратором.", parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("adm_ok_"))
async def adm_ok(c: types.CallbackQuery):
    _, _, tid, uid = c.data.split("_")
    end = datetime.now(timezone.utc) + timedelta(days=TARIFFS[tid]['days'])
    conn = psycopg2.connect(DB_URL); curr = conn.cursor()
    curr.execute("UPDATE users SET sub_end = %s WHERE uid = %s", (end, int(uid))); conn.commit(); curr.close(); conn.close()
    
    try:
        # Создаем кнопку, которая при нажатии отправит тот же текст
        kb = InlineKeyboardBuilder().button(text="🚀 Начать анализировать матчи", callback_data="start_analysis_notice")
        await bot.send_message(int(uid), "🎉 <b>Оплата успешно подтверждена!</b> Доступ открыт.", reply_markup=kb.as_markup(), parse_mode=ParseMode.HTML)
    except: pass
    await c.message.edit_text(f"{c.message.text}\n\n✅ <b>ОДОБРЕНО</b>")


@dp.callback_query(F.data.startswith("adm_no_"))
async def adm_no(c: types.CallbackQuery):
    uid = c.data.split("_")[2]
    try: await bot.send_message(int(uid), "❌ <b>Оплата не найдена.</b> Заявка отклонена. Если это ошибка, обратитесь к администратору в описании бота.", parse_mode=ParseMode.HTML)
    except: pass
    await c.message.edit_text(f"{c.message.text}\n\n❌ <b>ОТКЛОНЕНО</b>")

async def fetch_limit(session, i, key):
    """Вспомогательная функция для ОДНОГО запроса"""
    url = f"https://api.the-odds-api.com/v4/sports/?apiKey={key}"
    try:
        async with session.get(url, timeout=10) as r:
            rem = r.headers.get('x-requests-remaining', '?')
            if r.status == 200:
                return f"Ключ №{i+1}: ✅ {rem} ост."
            elif r.status == 401:
                return f"Ключ №{i+1}: ❌ Ошибка 401 (Неверный ключ)"
            elif r.status == 429:
                return f"Ключ №{i+1}: ⚠️ Ошибка 429 (Лимит исчерпан)"
            else:
                return f"Ключ №{i+1}: ❓ Ошибка {r.status}"
    except Exception as e:
        return f"Ключ №{i+1}: 🚫 Ошибка сети"

@dp.callback_query(F.data == "check_api_limits")
async def check_api_limits_handler(c: types.CallbackQuery):
    """Обработчик нажатия на кнопку"""
    if c.from_user.id != ADMIN_ID:
        return await c.answer("Нет прав")

    await c.answer("Начинаю проверку всех ключей...")
    
    # Чтобы не спамить в один пост, если ключей много, разобьем на части по 15 штук
    import aiohttp
    
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_limit(session, i, key) for i, key in enumerate(API_KEYS)]
        results = await asyncio.gather(*tasks)
    
    # Разбиваем список результатов на части, чтобы влезло в сообщение Телеграм
    chunk_size = 15
    for i in range(0, len(results), chunk_size):
        chunk = results[i:i + chunk_size]
        msg_text = "📊 <b>Статус лимитов API:</b>\n\n" + "\n".join(chunk)
        await bot.send_message(
            chat_id=c.message.chat.id,
            text=msg_text,
            message_thread_id=T_MGMT,
            parse_mode=ParseMode.HTML
        )
        
@dp.callback_query(F.data == "start_analysis_notice")
async def process_analysis_notice(c: types.CallbackQuery):
    # Убираем кнопку под сообщением и выводим нужный текст
    await c.message.edit_reply_markup(reply_markup=None)
    await c.message.answer("Анализ матчей начинается🔎 Как только будет найден прогноз, вы получите уведомление 🔔")
    await c.answer()

@dp.message(F.text == "🚀 Начать")
async def btn_restart_proxy(m: types.Message):
    await cmd_start(m)


# --- ADMIN PANEL ---

@dp.message(Command("admin"))
async def admin_panel(m: types.Message):
    # Проверка прав: только админ и только в админ-группе (или личке админа)
    if m.from_user.id != ADMIN_ID:
        return
    
    # Визуальный эффект набора текста
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    
    # Получаем текущие настройки из БД
    mo = get_setting("min_odds", 1.5)
    ml = get_setting("multiplier", 0.9)
    td = get_setting("time_depth", 24)
    
    text = (f"🛠 <b>Панель управления</b>\n\n"
            f"Ключей API: <b>{len(API_KEYS)}</b>\n"
            f"Мин. Кэф: <b>{mo}</b>\n"
            f"Баз. корреляция: <b>{ml}</b>\n"
            f"Анализ (часы): <b>{td}ч</b>")
    
    kb = InlineKeyboardBuilder()
    kb.button(text="⚙️ Мин. Кэф", callback_data="set_adm_min_odds")
    kb.button(text="📉 Множитель", callback_data="set_adm_mult")
    kb.button(text="⏳ Глубина", callback_data="set_adm_time")
    kb.button(text="📊 Лимиты API", callback_data="check_api_limits") # Добавь эту строку
    kb.button(text="📢 Рассылка", callback_data="start_broadcast")
    
    # ИСПОЛЬЗУЕМ bot.send_message для стабильности в топиках
    await bot.send_message(
        chat_id=m.chat.id,
        text=text,
        reply_markup=kb.adjust(2).as_markup(),
        parse_mode=ParseMode.HTML,
        message_thread_id=T_MGMT # Отправляем строго в топик Управление
    )

@dp.callback_query(F.data.startswith("set_adm_"))
async def set_adm_params(c: types.CallbackQuery, state: FSMContext):
    action = c.data.replace("set_adm_", "")
    prompts = {
        "min_odds": "минимальный кэф", 
        "mult": "множитель", 
        "time": "глубину анализа (в часах)"
    }
    
    await bot.send_message(
        chat_id=c.message.chat.id,
        text=f"🔢 Введите новое значение для: <b>{prompts.get(action, 'параметра')}</b>",
        message_thread_id=T_MGMT,
        parse_mode=ParseMode.HTML
    )
    
    if action == "min_odds": await state.set_state(AdminStates.wait_min_odds)
    elif action == "mult": await state.set_state(AdminStates.wait_mult)
    elif action == "time": await state.set_state(AdminStates.wait_time)
    await c.answer()

@dp.message(StateFilter(AdminStates.wait_min_odds, AdminStates.wait_mult, AdminStates.wait_time))
async def save_adm_params(m: types.Message, state: FSMContext):
    if m.chat.id != ADMIN_GROUP_ID: return
    
    cur_state = await state.get_state()
    try:
        val = float(m.text.replace(",", "."))
        
        if "min_odds" in str(cur_state): set_setting("min_odds", val)
        elif "mult" in str(cur_state): set_setting("multiplier", val)
        elif "time" in str(cur_state): set_setting("time_depth", val)
        
        await bot.send_message(
            chat_id=m.chat.id,
            text=f"✅ Настройка <b>{val}</b> успешно сохранена!",
            message_thread_id=T_MGMT,
            parse_mode=ParseMode.HTML
        )
    except ValueError:
        await bot.send_message(
            chat_id=m.chat.id,
            text="❌ Ошибка! Введите число (например: 1.5 или 0.9)",
            message_thread_id=T_MGMT
        )
    await state.clear()

@dp.message(Command("give_sub"))
async def give_sub_cmd(m: types.Message, command: CommandObject):
    if m.from_user.id != ADMIN_ID: return
    try:
        uid, days = map(int, command.args.split())
        end = datetime.now(timezone.utc) + timedelta(days=days)
        
        conn = psycopg2.connect(DB_URL)
        curr = conn.cursor()
        curr.execute("UPDATE users SET sub_end = %s WHERE uid = %s", (end, uid))
        conn.commit()
        curr.close()
        conn.close()
        
        await bot.send_message(
            chat_id=m.chat.id,
            text=f"✅ Доступ выдан ID {uid} на {days} дней.",
            message_thread_id=T_MGMT
        )
        # Уведомляем пользователя в личку
        try:
            await bot.send_message(uid, f"🎁 Администратор выдал вам доступ на {days} дней!")
        except:
            pass
    except:
        await bot.send_message(
            chat_id=m.chat.id,
            text="⚠️ Формат: <code>/give_sub ID ДНИ</code>",
            message_thread_id=T_MGMT,
            parse_mode=ParseMode.HTML
        )

@dp.callback_query(F.data == "start_broadcast")
async def start_broad(c: types.CallbackQuery, state: FSMContext):
    await bot.send_message(
        chat_id=c.message.chat.id,
        text="📝 Отправьте сообщение для рассылки (можно с фото/видео):",
        message_thread_id=T_MGMT
    )
    await state.set_state(AdminStates.wait_broadcast_msg)
    await c.answer()

@dp.message(StateFilter(AdminStates.wait_broadcast_msg))
async def process_broad(m: types.Message, state: FSMContext):
    if m.chat.id != ADMIN_GROUP_ID: return
    
    conn = psycopg2.connect(DB_URL)
    curr = conn.cursor()
    curr.execute("SELECT uid FROM users")
    users = curr.fetchall()
    curr.close()
    conn.close()
    
    count = 0
    status_msg = await bot.send_message(m.chat.id, "⏳ Рассылка начата...", message_thread_id=T_MGMT)
    
    for u in users:
        try:
            # copy_to позволяет переслать сообщение без пометки "переслано"
            await m.copy_to(chat_id=u[0])
            count += 1
            await asyncio.sleep(0.05) # Небольшая пауза, чтобы не спамить API Telegram
        except:
            continue
            
    await bot.send_message(
        chat_id=m.chat.id,
        text=f"📢 Рассылка завершена.\nДоставлено: <b>{count}</b> пользователям.",
        message_thread_id=T_MGMT,
        parse_mode=ParseMode.HTML
    )
    await state.clear()


# --- PREDICTIONS INTERACTION ---
@dp.callback_query(F.data == "pred_skip")
async def pred_skip(c: types.CallbackQuery):
    await c.message.edit_reply_markup(reply_markup=None)
    await c.message.reply("⏩ Матч пропущен.", message_thread_id=T_PRED)

@dp.callback_query(F.data == "pred_place")
async def pred_place(c: types.CallbackQuery, state: FSMContext):
    msg = await c.message.reply("Введите сумму ставки (₽):", message_thread_id=T_PRED)
    await state.update_data(orig_msg_id=c.message.message_id, prompt1=msg.message_id)
    await state.set_state(BetStates.wait_amount)

@dp.message(StateFilter(BetStates.wait_amount))
async def wait_amount(m: types.Message, state: FSMContext):
    await state.update_data(amount=m.text, prompt2=m.message_id)
    msg = await m.answer("Введите реальный коэффициент в БК:", message_thread_id=T_PRED)
    await state.update_data(prompt3=msg.message_id)
    await state.set_state(BetStates.wait_odds)

@dp.message(StateFilter(BetStates.wait_odds))
async def wait_real_odds(m: types.Message, state: FSMContext):
    data = await state.get_data()
    orig_msg_id = data.get("orig_msg_id")
    amt = data.get("amount")
    odds = m.text
    
    for msg_id in [data.get("prompt1"), data.get("prompt2"), data.get("prompt3"), m.message_id]:
        try: await bot.delete_message(m.chat.id, msg_id)
        except: pass

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ ВИН", callback_data=f"res_win_{orig_msg_id}_{amt}_{odds}")
    kb.button(text="🔄 ВОЗВРАТ", callback_data=f"res_ret_{orig_msg_id}_{amt}_{odds}")
    kb.button(text="❌ ЛОСС", callback_data=f"res_loss_{orig_msg_id}_{amt}_{odds}")
    
    try:
        orig_msg = await bot.edit_message_reply_markup(chat_id=m.chat.id, message_id=orig_msg_id, reply_markup=kb.as_markup())
        await bot.send_message(m.chat.id, f"📝 Ставка зафиксирована: {amt}₽ под {odds}", reply_to_message_id=orig_msg_id, message_thread_id=T_PRED)
    except: pass
    await state.clear()

@dp.callback_query(F.data.startswith("res_"))
async def bet_result(c: types.CallbackQuery):
    _, res_type, msg_id, amt, odds = c.data.split("_")
    res_text = "✅ ПОБЕДА" if res_type == "win" else "🔄 ВОЗВРАТ" if res_type == "ret" else "❌ ПРОИГРЫШ"
    profit = round(float(amt) * float(odds) - float(amt), 2) if res_type == "win" else 0
    profit_str = f"+{profit}₽" if res_type == "win" else f"0₽" if res_type == "ret" else f"-{amt}₽"
    
    final_kb = InlineKeyboardBuilder().button(text=f"{res_text} ({profit_str})", callback_data="noop")
    await c.message.edit_reply_markup(reply_markup=final_kb.as_markup())

# --- FULL SCANNER ---
async def scanner():
    sent = set()
    idx = 0
    
    while True:
        if not API_KEYS: await asyncio.sleep(60); continue
        
        # Переменные для отчета
        league_cnt = 0
        sig_cnt = 0
        err_cnt = 0
        filtered_cnt = 0 # Счетчик матчей, не прошедших фильтры

        
        m_odds = get_setting("min_odds", 1.50)
        max_o = get_setting("max_odds", 2.50)
        m_mult = get_setting("multiplier", 0.90)
        t_depth = get_setting("time_depth", 24)
        
        for league in LEAGUES:
            try:
                r = requests.get(f"https://api.the-odds-api.com/v4/sports/{league}/odds/", 
                                 params={'apiKey': API_KEYS[idx], 'regions': 'eu', 'markets': 'h2h'}, timeout=10)
                league_cnt += 1
                
                if r.status_code == 200:
                    for ev in r.json():
                        if ev['id'] in sent: continue
                        start = datetime.fromisoformat(ev['commence_time'].replace('Z', '+00:00'))
                        now = datetime.now(timezone.utc)
                        hours_left = (start - now).total_seconds() / 3600
                        
                        if 1.0 < hours_left <= t_depth:
                            price = ev['bookmakers'][0]['markets'][0]['outcomes'][0]['price']
                            
                            # Динамический множитель
                                                        # Динамический множитель (более мягкая коррекция)
                            if price < 1.45:
                                dynamic_mult = m_mult * 1.10 # Было 1.30
                            elif 1.45 <= price < 1.85:
                                dynamic_mult = m_mult * 1.05 # Было 1.15
                            elif 1.85 <= price < 2.30:
                                dynamic_mult = m_mult * 1.02 # Было 1.05
                            else:
                                dynamic_mult = m_mult * 0.95 # Было 0.90

                            final_odds = round(price * dynamic_mult, 2)
                            
                                                                                   # Анализируем обе команды
                            itb_home = await analyze_strict(ev['home_team'], is_home=True)
                            itb_away = await analyze_strict(ev['away_team'], is_home=False)
                            itb_h2h = await analyze_h2h(ev['home_team'], ev['away_team'])

                            if itb_home == "CAPTCHA" or itb_away == "CAPTCHA":
                                continue

                            # Решаем, на кого ставить
                            target_team, stat_val = None, 0
                            if isinstance(itb_home, int) and itb_home >= 3 and itb_h2h >= 1:
                                target_team, stat_val = ev['home_team'], itb_home
                            elif isinstance(itb_away, int) and itb_away >= 3 and itb_h2h >= 1:
                                target_team, stat_val = ev['away_team'], itb_away
                            else:
                                # Если условия выше не сработали, значит матч не прошел критерии
                                filtered_cnt += 1 


                            if target_team:
                                sig_cnt += 1
                                sent.add(ev['id'])
                                h_name, a_name = clean_and_translate(ev['home_team']), clean_and_translate(ev['away_team'])
                                target_name = clean_and_translate(target_team)
                                msk_time = start + timedelta(hours=3)
                                
                                msg_vip = (
                                    f"🎩 <b>Baron’s Verdict</b>\n"
                                    f"⚽️ <code>{h_name} — {a_name}</code>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━\n"
                                    f"📅 <b>Дата:</b> {msk_time.strftime('%d.%m')} | <b>Начало:</b> {msk_time.strftime('%H:%M')}\n"
                                    f"🔥 <b>Ставка:</b> ИТБ 1.5 на <b>{target_name}</b>\n"
                                    f"📈 <b>Коэффициент:</b> {final_odds}\n"
                                    f"📉 <b>Нижний порог:</b> {m_odds}\n"
                                    f"━━━━━━━━━━━━━━━━━━━━\n"
                                    f"📊 <b>Анализ:</b> 🔥 Форма: {stat_val}/5 | 🤝 H2H: {itb_h2h}/5"
                                )
                                await bot.send_message(CHANNEL_ID, msg_vip, parse_mode=ParseMode.HTML)
                                kb = InlineKeyboardBuilder()
                                kb.button(text="💰 Поставил", callback_data="pred_place")
                                kb.button(text="⏩ Пропустил", callback_data="pred_skip")
                                await bot.send_message(ADMIN_GROUP_ID, msg_vip, reply_markup=kb.as_markup(), message_thread_id=T_PRED, parse_mode=ParseMode.HTML)

                
                elif r.status_code in [401, 429]:
                    err_cnt += 1
                    idx = (idx + 1) % len(API_KEYS)
            except: 
                err_cnt += 1
            await asyncio.sleep(5) # Пауза между лигами
        
                # ОТПРАВКА ОТЧЕТА (Команда /ping 999)
                # ОТПРАВКА ОТЧЕТА
        if SHOW_FULL_REPORT:
            msk_report_time = datetime.now(timezone.utc) + timedelta(hours=3)
            report = (
                "⚙️ <b>Отчет круга анализа</b>\n"
                f"✅ Лиг: {league_cnt}\n"
                f"🎯 Сигналов: {sig_cnt}\n"
                f"🗑 Отсеяно: {filtered_cnt}\n" # <--- НОВАЯ СТРОКА
                f"⚠️ Ошибки API: {err_cnt}\n"
                f"⏰ Время: {msk_report_time.strftime('%H:%M')}"
            )

            try:
                await bot.send_message(ADMIN_GROUP_ID, report, message_thread_id=T_LOGS, parse_mode=ParseMode.HTML)
            except: pass

        await asyncio.sleep(2400) # Ждем 30 минут до следующего круга


# --- WEB SERVER (For Render Keep-Alive) ---
async def main():
    init_db()
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Baron v7.2 CRM Alive"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    asyncio.create_task(scanner())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())




