import os
import asyncio
import logging
import requests
import psycopg2  # Библиотека остается той же в коде, меняется только в requirements.txt
import random
import json
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0"))
API_KEYS = [k.strip() for k in os.getenv("ODDS_API_KEYS", "").split(",") if k.strip()]
REQUISITES = os.getenv("PAYMENT_REQUISITES", "Реквизиты не установлены")
DB_URL = os.getenv("DATABASE_URL")

# TOPIC IDS
T_USERS = int(os.getenv("TOPIC_ID_USERS", "0"))
T_CASH = int(os.getenv("TOPIC_ID_PAYMENTS", "0"))
T_MGMT = int(os.getenv("TOPIC_ID_MANAGEMENT", "0"))

LEAGUES = [
    "soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", 
    "soccer_spain_la_liga", "soccer_france_ligue_one", "soccer_uefa_champs_league",
    "soccer_uefa_europa_league", "soccer_netherlands_eredivisie", "soccer_portugal_primeira_liga",
    "soccer_belgium_first_division", "soccer_austria_bundesliga", "soccer_turkey_super_lig",
    "soccer_england_league_one", "soccer_england_championship"
]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

TARIFFS = {
    "mini": {"name": "МиниⓂ️", "days": 3, "price": 149},
    "lite": {"name": "Лайт✨", "days": 7, "price": 299},
    "classic": {"name": "Классика🎩", "days": 30, "price": 999},
    "level": {"name": "Уровень💹", "days": 60, "price": 1666}
}

TEAM_TRANSLATIONS = {
    "Arsenal": "Арсенал", "Chelsea": "Челси", "Liverpool": "Ливерпуль", 
    "Manchester City": "Манчестер Сити", "Real Madrid": "Реал Мадрид",
    "Bayern Munich": "Бавария", "AC Milan": "Милан", "Inter Milan": "Интер",
    "Barcelona": "Барселона", "Borussia Dortmund": "Боруссия Д", "Juventus": "Ювентус",
    "Tottenham Hotspur": "Тоттенхэм", "Manchester United": "Манчестер Юнайтед",
    "Paris Saint Germain": "ПСЖ", "Atletico Madrid": "Атлетико", "RB Leipzig": "РБ Лейпциг"
}

# --- FUNCTIONS ---
def sync_team_name(name):
    name = name.replace("U23", "").replace("U21", "").replace("U19", "").replace("FC ", "").replace(" CF", "")
    removals = ["SSC", "AS", "Utd", "United", "BSC", "AC", "City"]
    words = name.split()
    clean_words = [w for w in words if w not in removals]
    return " ".join(clean_words).strip()

def safe_translate(name):
    if name in TEAM_TRANSLATIONS:
        return TEAM_TRANSLATIONS[name]
    clean = sync_team_name(name)
    return TEAM_TRANSLATIONS.get(clean, clean)

# --- DATABASE ---
def init_db():
    conn = psycopg2.connect(DB_URL)
    curr = conn.cursor()
    curr.execute('''CREATE TABLE IF NOT EXISTS users 
                 (uid BIGINT PRIMARY KEY, username TEXT, sub_end TIMESTAMP, trial_used INTEGER)''')
    conn.commit()
    curr.close(); conn.close()

def has_active_sub(uid):
    try:
        conn = psycopg2.connect(DB_URL)
        curr = conn.cursor()
        curr.execute("SELECT sub_end FROM users WHERE uid = %s", (uid,))
        user = curr.fetchone()
        curr.close(); conn.close()
        if user:
            return user[0].replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
    except: pass
    return False

async def send_to_office(topic_id, text, kb=None):
    try:
        await bot.send_message(ADMIN_GROUP_ID, text, message_thread_id=topic_id, 
                               reply_markup=kb, parse_mode=ParseMode.HTML)
    except: pass

# --- SCANNER ---
current_key_idx = 0

async def scanner():
    global current_key_idx
    sent_events = set()
    while True:
        if not API_KEYS: 
            await asyncio.sleep(60)
            continue
            
        key = API_KEYS[current_key_idx]
        for league in LEAGUES:
            try:
                res = requests.get(f"https://api.the-odds-api.com/v4/sports/{league}/odds/", 
                                   params={'apiKey': key, 'regions': 'eu', 'markets': 'h2h'}, timeout=10)
                
                if res.status_code == 200:
                    events = res.json()
                    for ev in events:
                        if ev['id'] in sent_events: continue
                        commence = datetime.fromisoformat(ev['commence_time'].replace('Z', '+00:00'))
                        diff = (commence - datetime.now(timezone.utc)).total_seconds() / 3600
                        
                        if 1.5 < diff < 24:
                            raw_price = ev['bookmakers'][0]['markets'][0]['outcomes'][0]['price']
                            final_odds = round(raw_price * 0.96, 2)
                            
                            if final_odds < 1.60: continue
                            
                            sent_events.add(ev['id'])
                            h_name = safe_translate(ev['home_team'])
                            a_name = safe_translate(ev['away_team'])

                            msg = (f"💎 <b>Baron’s Verdict</b>\n⚽️ <code>{h_name}</code> — <code>{a_name}</code>\n"
                                   f"━━━━━━━━━━━━━━━━━━━━\n🔥 Ставка: <b>ИТБ (1.5)</b>\n📈 Кэф: <code>{final_odds}</code>\n"
                                   f"📊 Анализ: Высокая вероятность голов\n━━━━━━━━━━━━━━━━━━━━")
                            await bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                            await send_to_office(T_MGMT, f"🕹 <b>Прогноз отправлен:</b>\n{h_name} vs {a_name}")

                elif res.status_code == 429:
                    current_key_idx = (current_key_idx + 1) % len(API_KEYS)
                    break 

            except Exception as e:
                logging.error(f"Scanner error: {e}")
            await asyncio.sleep(2)
        await asyncio.sleep(1200)

# --- USER HANDLERS ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    conn = psycopg2.connect(DB_URL); c = conn.cursor()
    c.execute("SELECT * FROM users WHERE uid = %s", (m.from_user.id,))
    user = c.fetchone()
    if not user:
        end_trial = datetime.now(timezone.utc) + timedelta(days=3)
        c.execute("INSERT INTO users (uid, username, sub_end, trial_used) VALUES (%s, %s, %s, %s)", 
                  (m.from_user.id, m.from_user.username, end_trial, 1))
        conn.commit()
        await send_to_office(T_USERS, f"📩 <b>Новый юзер:</b> @{m.from_user.username}\nID: <code>{m.from_user.id}</code>")
    c.close(); conn.close()

    active = has_active_sub(m.from_user.id)
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Тарифы", callback_data="show_tariffs")
    
    status = "✅ Активна" if active else "❌ Неактивна"
    await m.answer(f"💎 <b>Baron’s Verdict</b>\nСтатус подписки: {status}", reply_markup=kb.as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "show_tariffs")
async def show_tariffs(c: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    for tid, data in TARIFFS.items():
        kb.button(text=f"{data['name']} - {data['price']}₽", callback_data=f"buy_{tid}")
    kb.adjust(1)
    await c.message.edit_text("📊 <b>Выберите тариф:</b>", reply_markup=kb.as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(c: types.CallbackQuery):
    tid = c.data.split("_")[1]
    pay_id = random.randint(1000, 9999)
    text = (f"💳 <b>Оплата {TARIFFS[tid]['name']}</b>\n"
            f"🏦 Карта: <code>{REQUISITES}</code>\n"
            f"💰 Сумма: <b>{TARIFFS[tid]['price']}₽</b>\n"
            f"🆔 Коммент: <code>#{pay_id}</code>")
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Оплатил", callback_data=f"paydone_{tid}_{pay_id}")
    await c.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("paydone_"))
async def pay_done(c: types.CallbackQuery):
    _, tid, pid = c.data.split("_")
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Одобрить", callback_data=f"adm_ok_{tid}_{c.from_user.id}")
    kb.button(text="❌ Отклонить", callback_data=f"adm_no_{c.from_user.id}")
    await send_to_office(T_CASH, f"💰 <b>Оплата!</b>\nЮзер: @{c.from_user.username}\nКод: #{pid}", kb.as_markup())
    await c.answer("Заявка отправлена!")

# --- ADMIN FUNCTIONS ---
@dp.callback_query(F.data.startswith("adm_ok_"))
async def adm_approve(c: types.CallbackQuery):
    _, _, tid, uid = c.data.split("_")
    new_end = datetime.now(timezone.utc) + timedelta(days=TARIFFS[tid]['days'])
    conn = psycopg2.connect(DB_URL); curr = conn.cursor()
    curr.execute("UPDATE users SET sub_end = %s WHERE uid = %s", (new_end, uid))
    conn.commit(); curr.close(); conn.close()
    await bot.send_message(uid, "✅ <b>Подписка активирована!</b>")
    await c.message.edit_text(c.message.text + "\n\n✅ <b>ОДОБРЕНО</b>")

@dp.callback_query(F.data.startswith("adm_no_"))
async def adm_decline(c: types.CallbackQuery):
    uid = c.data.split("_")[2]
    await bot.send_message(uid, "❌ <b>Оплата не найдена.</b>")
    await c.message.edit_text(c.message.text + "\n\n❌ <b>ОТКЛОНЕНО</b>")

@dp.message(Command("get_ids"))
async def get_ids(m: types.Message):
    if m.from_user.id == ADMIN_ID:
        await m.answer(f"📍 Группа: <code>{m.chat.id}</code>\n🧵 Тема: <code>{m.message_thread_id}</code>", parse_mode=ParseMode.HTML)

async def main():
    init_db()
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    asyncio.create_task(scanner())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
