import os
import asyncio
import logging
import requests
import psycopg2
import random
import re
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

# --- CONFIG ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Baron_v7_1")

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0"))
API_KEYS = [k.strip() for k in os.getenv("ODDS_API_KEYS", "").split(",") if k.strip()]
REQUISITES = os.getenv("PAYMENT_REQUISITES", "Реквизиты не установлены")
DB_URL = os.getenv("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)

# Topics
T_USERS = int(os.getenv("TOPIC_ID_USERS", "0"))
T_CASH = int(os.getenv("TOPIC_ID_PAYMENTS", "0"))
T_MGMT = int(os.getenv("TOPIC_ID_MANAGEMENT", "0"))

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

TARIFFS = {
    "mini": {"name": "МиниⓂ️", "days": 3, "price": 149},
    "lite": {"name": "Лайт✨", "days": 7, "price": 299},
    "classic": {"name": "Классика🎩", "days": 30, "price": 999},
    "level": {"name": "Уровень💹", "days": 60, "price": 1666}
}

# --- DB ---
def init_db():
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("CREATE TABLE IF NOT EXISTS users (uid BIGINT PRIMARY KEY, username TEXT, sub_end TIMESTAMP, trial_used INTEGER)")
        curr.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        curr.execute("INSERT INTO settings (key, value) VALUES ('min_odds', '1.55'), ('multiplier', '0.92') ON CONFLICT DO NOTHING")
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

def get_sub_status(uid):
    if uid == ADMIN_ID: return True
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("SELECT sub_end FROM users WHERE uid = %s", (uid,))
        res = curr.fetchone(); curr.close(); conn.close()
        if res: return res[0].replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
    except: pass
    return False

# --- UTILS ---
def clean_name(name):
    bad = ["U23", "U21", "U19", "FC", "CF", "SSC", "AS", "Utd", "United", "BSC", "AC", "City", "Real", "St", "De", "Club", "FK"]
    return " ".join([w for w in name.split() if w not in bad]).strip()

async def deep_analyze_itb15(team_name):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        query = f"{team_name} last 5 matches results"
        res = requests.get(f"https://www.google.com/search?q={query}", headers=headers, timeout=7)
        scores = re.findall(r'(\d)-\d', res.text.lower())
        return sum(1 for s in scores[:5] if int(s) >= 2), 0
    except: return -1, 0

# --- SCANNER v7.1 ---
async def scanner():
    sent = set()
    idx = 0
    # Расширенный список лиг для ежедневной работы
    leagues = [
        "soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", 
        "soccer_spain_la_liga", "soccer_france_ligue_one", 
        "soccer_brazil_campeonato", "soccer_usa_mls", "soccer_uefa_champs_league"
    ]
    
    while True:
        if not API_KEYS: await asyncio.sleep(60); continue
        
        m_odds = get_setting("min_odds", 1.55)
        m_mult = get_setting("multiplier", 0.92)
        found_any_match = False
        
        for league in leagues:
            try:
                r = requests.get(f"https://api.the-odds-api.com/v4/sports/{league}/odds/", 
                                 params={'apiKey': API_KEYS[idx], 'regions': 'eu', 'markets': 'h2h'})
                if r.status_code == 200:
                    events = r.json()
                    if events: found_any_match = True
                    for ev in events:
                        if ev['id'] in sent: continue
                        start = datetime.fromisoformat(ev['commence_time'].replace('Z', '+00:00'))
                        now = datetime.now(timezone.utc)
                        
                        if 1.0 < (start - now).total_seconds() / 3600 <= 24:
                            price = ev['bookmakers'][0]['markets'][0]['outcomes'][0]['price']
                            final_odds = round(price * m_mult, 2)
                            
                            if final_odds < m_odds:
                                await bot.send_message(ADMIN_GROUP_ID, f"⏩ Низкий кэф: {ev['home_team']} ({final_odds})", message_thread_id=T_MGMT)
                                continue
                            
                            itb, _ = await deep_analyze_itb15(ev['home_team'])
                            if itb >= 4:
                                sent.add(ev['id'])
                                msg = (f"💎 <b>Baron’s Verdict</b>\n⚽️ <code>{clean_name(ev['home_team'])}</code> — <code>{clean_name(ev['away_team'])}</code>\n"
                                       f"━━━━━━━━━━━━━━━━━━━━\n"
                                       f"📊 Анализ: ИТБ 1.5 в {itb}/5 последних\n"
                                       f"🔥 Ставка: ИТБ (1.5)\n"
                                       f"📈 Кэф: <code>{final_odds}</code>")
                                await bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                            else:
                                await bot.send_message(ADMIN_GROUP_ID, f"⏩ Слабая стата: {ev['home_team']} ({itb}/5)", message_thread_id=T_MGMT)
                elif r.status_code == 429:
                    idx = (idx + 1) % len(API_KEYS)
            except: pass
            await asyncio.sleep(2)
        
        if not found_any_match:
            await bot.send_message(ADMIN_GROUP_ID, "🔍 Сканер: матчей в API пока нет. Жду...", message_thread_id=T_MGMT)
            
        await asyncio.sleep(1800)

# --- BOT COMMANDS ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    init_db()
    status = "✅ VIP" if get_sub_status(m.from_user.id) else "❌ Нет доступа"
    kb = InlineKeyboardBuilder().button(text="👤 Профиль", callback_data="profile").button(text="💳 Купить", callback_data="show_tariffs")
    await m.answer(f"💎 <b>Baron’s Verdict</b>\nСтатус: {status}", reply_markup=kb.adjust(1).as_markup(), parse_mode=ParseMode.HTML)

@dp.message(Command("status"))
async def status(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    await m.answer(f"⚙️ <b>Статус:</b>\nКэф: {get_setting('min_odds', 0)}\nМножитель: {get_setting('multiplier', 0)}\nКлючей API: {len(API_KEYS)}", parse_mode=ParseMode.HTML)

@dp.message(Command("set_odds"))
async def set_odds(m: types.Message, command: CommandObject):
    if m.from_user.id == ADMIN_ID and command.args:
        set_setting("min_odds", float(command.args))
        await m.answer(f"✅ Мин. кэф: {command.args}")

@dp.callback_query(F.data == "profile")
async def profile(c: types.CallbackQuery):
    await c.message.answer(f"👤 ID: <code>{c.from_user.id}</code>\nДоступ: {'Активен' if get_sub_status(c.from_user.id) else 'Истек'}", parse_mode=ParseMode.HTML)

# --- WEB SERVER ---
async def main():
    init_db()
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Baron v7.1 Alive"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    asyncio.create_task(scanner())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
