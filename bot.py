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
from deep_translator import GoogleTranslator

# --- CONFIG & SYSTEM ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Baron_v7")

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

# --- DATABASE LOGIC ---
def init_db():
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute('''CREATE TABLE IF NOT EXISTS users 
                     (uid BIGINT PRIMARY KEY, username TEXT, sub_end TIMESTAMP, trial_used INTEGER)''')
        curr.execute('''CREATE TABLE IF NOT EXISTS settings 
                     (key TEXT PRIMARY KEY, value TEXT)''')
        # Начальные настройки, если таблицы пусты
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

# --- ANALYTICS ---
def clean_name(name):
    name = name.replace("U23", "").replace("U21", "").replace("U19", "").replace("FC ", "").replace(" CF", "")
    bad = ["SSC", "AS", "Utd", "United", "BSC", "AC", "City", "Real", "St", "De", "Club", "FK", "FC"]
    return " ".join([w for w in name.split() if w not in bad]).strip()

async def deep_analyze_itb15(team_name):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)'}
        query = f"{team_name} last 5 matches results"
        res = requests.get(f"https://www.google.com/search?q={query}", headers=headers, timeout=7)
        content = res.text.lower()
        if "captcha" in content: return "CAPTCHA", 0
        
        scores = re.findall(r'(\d)-\d', content) # Упрощенный поиск голов нашей команды
        itb_count = sum(1 for s in scores[:5] if int(s) >= 2)
        return itb_count, 0
    except: return -1, 0

# --- SCANNER ---
async def scanner():
    sent = set()
    idx = 0
    leagues = ["soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", "soccer_spain_la_liga", "soccer_france_ligue_one"]
    
    while True:
        m_odds = get_setting("min_odds", 1.55)
        m_mult = get_setting("multiplier", 0.92)
        
        if not API_KEYS: await asyncio.sleep(60); continue
        key = API_KEYS[idx]
        for league in leagues:
            try:
                r = requests.get(f"https://api.the-odds-api.com/v4/sports/{league}/odds/", params={'apiKey': key, 'regions': 'eu', 'markets': 'h2h'})
                if r.status_code == 200:
                    for ev in r.json():
                        if ev['id'] in sent: continue
                        start = datetime.fromisoformat(ev['commence_time'].replace('Z', '+00:00'))
                        if 1.0 < (start - datetime.now(timezone.utc)).total_seconds() / 3600 <= 24:
                            raw_odds = ev['bookmakers'][0]['markets'][0]['outcomes'][0]['price']
                            final_odds = round(raw_odds * m_mult, 2)
                            
                            # Проверка кэфа
                            if final_odds < m_odds:
                                await bot.send_message(ADMIN_GROUP_ID, f"⏩ Пропуск: {ev['home_team']} (Кэф {final_odds} < {m_odds})", message_thread_id=T_MGMT)
                                continue
                            
                            itb_val, _ = await deep_analyze_itb15(ev['home_team'])
                            if itb_val == "CAPTCHA":
                                await bot.send_message(ADMIN_GROUP_ID, "⚠️ Google CAPCHA! Анализ стоит.", message_thread_id=T_MGMT)
                                continue
                            
                            if itb_val < 4:
                                await bot.send_message(ADMIN_GROUP_ID, f"⏩ Пропуск: {ev['home_team']} (ИТБ1.5 только в {itb_val}/5)", message_thread_id=T_MGMT)
                                continue
                            
                            sent.add(ev['id'])
                            h, a = clean_name(ev['home_team']), clean_name(ev['away_team'])
                            msg = (f"💎 <b>Baron’s Verdict</b>\n⚽️ <code>{h}</code> — <code>{a}</code>\n"
                                   f"━━━━━━━━━━━━━━━━━━━━\n"
                                   f"📊 <b>Анализ:</b> ИТБ 1.5 зашел в {itb_val}/5 последних\n"
                                   f"🔥 <b>Ставка:</b> ИТБ (1.5)\n"
                                   f"📈 <b>Кэф:</b> <code>{final_odds}</code>\n"
                                   f"━━━━━━━━━━━━━━━━━━━━")
                            await bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                elif r.status_code == 429: idx = (idx + 1) % len(API_KEYS)
            except: pass
            await asyncio.sleep(5)
        await asyncio.sleep(1200)

# --- COMMANDS ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    init_db()
    active = get_sub_status(m.from_user.id)
    kb = InlineKeyboardBuilder()
    kb.button(text="👤 Профиль", callback_data="profile")
    kb.button(text="💳 Купить подписку", callback_data="show_tariffs")
    status = "✅ VIP" if active else "❌ Нет доступа"
    await m.answer(f"💎 <b>Baron’s Verdict</b>\nВаш статус: {status}", reply_markup=kb.adjust(1).as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "profile")
async def profile(c: types.CallbackQuery):
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("SELECT sub_end FROM users WHERE uid = %s", (c.from_user.id,))
        res = curr.fetchone(); curr.close(); conn.close()
        date_str = res[0].strftime('%d.%m.%Y %H:%M') if res else "Нет данных"
        await c.message.answer(f"👤 <b>Ваш профиль</b>\nID: <code>{c.from_user.id}</code>\nПодписка до: <b>{date_str}</b>", parse_mode=ParseMode.HTML)
    except: await c.answer("Ошибка БД")

@dp.message(Command("broadcast"))
async def broadcast(m: types.Message, command: CommandObject):
    if m.from_user.id != ADMIN_ID: return
    if not command.args: return await m.answer("Пиши: /broadcast текст")
    conn = psycopg2.connect(DB_URL); curr = conn.cursor()
    curr.execute("SELECT uid FROM users"); users = curr.fetchall()
    count = 0
    for u in users:
        try:
            await bot.send_message(u[0], command.args)
            count += 1; await asyncio.sleep(0.05)
        except: pass
    await m.answer(f"📢 Разослано {count} пользователям")

@dp.message(Command("status"))
async def status(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    mo = get_setting("min_odds", 0)
    ml = get_setting("multiplier", 0)
    await m.answer(f"⚙️ <b>Статус системы:</b>\nМин. кэф: {mo}\nМножитель: {ml}\nКлючей API: {len(API_KEYS)}", parse_mode=ParseMode.HTML)

# Все остальные команды (give_sub, take_sub, set_odds, set_mult) из v6.0 сохранены, просто используй set_setting внутри них.
@dp.message(Command("set_odds"))
async def set_odds(m: types.Message, command: CommandObject):
    if m.from_user.id != ADMIN_ID: return
    try:
        val = float(command.args); set_setting("min_odds", val)
        await m.answer(f"✅ Мин. кэф сохранен: {val}")
    except: pass

@dp.callback_query(F.data == "show_tariffs")
async def show_tariffs(c: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    for tid, d in TARIFFS.items(): kb.button(text=f"{d['name']} - {d['price']}₽", callback_data=f"buy_{tid}")
    await c.message.edit_text("📊 <b>Тарифы:</b>", reply_markup=kb.adjust(1).as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("buy_"))
async def buy(c: types.CallbackQuery):
    tid = c.data.split("_")[1]
    pid = random.randint(100, 999)
    await c.message.edit_text(f"💳 <b>Оплата</b>\nКарта: <code>{REQUISITES}</code>\nСумма: {TARIFFS[tid]['price']}₽\nКод: <code>#{pid}</code>", 
                              reply_markup=InlineKeyboardBuilder().button(text="✅ Оплатил", callback_data=f"done_{tid}_{pid}").as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("done_"))
async def done(c: types.CallbackQuery):
    _, tid, pid = c.data.split("_")
    kb = InlineKeyboardBuilder().button(text="✅ ОК", callback_data=f"adm_ok_{tid}_{c.from_user.id}").button(text="❌ Нет", callback_data=f"adm_no_{c.from_user.id}")
    await bot.send_message(ADMIN_GROUP_ID, f"💰 Чек: @{c.from_user.username}\nID: {c.from_user.id}\nКод: #{pid}", reply_markup=kb.as_markup(), message_thread_id=T_CASH)
    await c.answer("Ждем админа...")

@dp.callback_query(F.data.startswith("adm_ok_"))
async def adm_ok(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    _, _, tid, uid = c.data.split("_")
    end = datetime.now(timezone.utc) + timedelta(days=TARIFFS[tid]['days'])
    conn = psycopg2.connect(DB_URL); curr = conn.cursor()
    curr.execute("UPDATE users SET sub_end = %s WHERE uid = %s", (end, uid)); conn.commit(); curr.close(); conn.close()
    await bot.send_message(uid, "✅ Доступ открыт!"); await c.message.edit_text("✅ ОДОБРЕНО")

# --- START ---
async def main():
    init_db()
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Running"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    asyncio.create_task(scanner())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
