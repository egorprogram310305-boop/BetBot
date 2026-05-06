

import os
import asyncio
import logging
import requests
import psycopg2
import random
import re
import aiohttp
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

# --- CONFIG ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0"))
API_KEYS = [k.strip() for k in os.getenv("ODDS_API_KEYS", "").split(",") if k.strip()]
REQUISITES = os.getenv("PAYMENT_REQUISITES", "Реквизиты не установлены")
DB_URL = os.getenv("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)

T_MGMT = int(os.getenv("TOPIC_ID_MANAGEMENT", "0"))
T_LOGS = int(os.getenv("TOPIC_ID_LOGS", "0")) 
T_CASH = int(os.getenv("TOPIC_ID_PAYMENTS", "0"))
T_USERS = int(os.getenv("TOPIC_ID_USERS", "0"))
T_PRED = int(os.getenv("TOPIC_ID_PREDICTIONS", "0"))

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

TARIFFS = {
    "mini": {"name": "Мини (3 дня)", "days": 3, "price": 149},
    "lite": {"name": "Лайт (7 дней)", "days": 7, "price": 299},
    "classic": {"name": "Классика (30 дней)", "days": 30, "price": 999},
    "level": {"name": "Уровень (60 дней)", "days": 60, "price": 1666}
}

LEAGUES = ["soccer_germany_bundesliga", "soccer_epl", "soccer_netherlands_eredivisie", "soccer_spain_la_liga", "soccer_belgium_first_div", "soccer_austria_bundesliga", "soccer_usa_mls"]

class AdminStates(StatesGroup):
    wait_min_odds = State()
    wait_mult = State()
    wait_broadcast_msg = State()

class BetStates(StatesGroup):
    wait_amount = State()
    wait_odds = State()

# --- DATABASE ---
def init_db():
    conn = psycopg2.connect(DB_URL); curr = conn.cursor()
    curr.execute("CREATE TABLE IF NOT EXISTS users (uid BIGINT PRIMARY KEY, username TEXT, sub_end TIMESTAMP, trial_used INTEGER DEFAULT 0)")
    curr.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    curr.execute("INSERT INTO settings (key, value) VALUES ('min_odds', '1.50'), ('multiplier', '0.90'), ('ping_mode', '0') ON CONFLICT DO NOTHING")
    conn.commit(); curr.close(); conn.close()

def get_setting(key, default):
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("SELECT value FROM settings WHERE key = %s", (key,))
        res = curr.fetchone(); curr.close(); conn.close()
        return float(res[0]) if res else default
    except: return default

def set_setting(key, value):
    conn = psycopg2.connect(DB_URL); curr = conn.cursor()
    curr.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = %s", (key, str(value), str(value)))
    conn.commit(); curr.close(); conn.close()

# --- UTILS (С ПРАВКОЙ ОШИБОК) ---
async def fetch_limit(session, i, key):
    try:
        async with session.get(f"https://api.the-odds-api.com/v4/sports/?apiKey={key}", timeout=10) as r:
            rem = r.headers.get('x-requests-remaining', '?')
            if r.status == 200:
                return f"Ключ №{i+1}: ✅ {rem} ост."
            else:
                # Если ошибка, пытаемся получить текст ошибки из JSON API
                try:
                    err_data = await r.json()
                    err_msg = err_data.get('message', 'Без описания')
                except:
                    err_msg = f"HTTP {r.status}"
                return f"Ключ №{i+1}: ⚠️ Ошибка: {err_msg}"
    except Exception as e:
        return f"Ключ №{i+1}: ❌ Ошибка сети: {str(e)[:50]}"

async def analyze_strict(team_name, is_home):
    try:
        res = requests.get(f"https://www.google.com/search?q={team_name} goals last 5 matches", headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        content = res.text.lower()
        if "captcha" in content: return "CAPTCHA", 0
        scores = re.findall(r'(\d)-\d' if is_home else r'\d-(\d)', content)
        return sum(1 for s in scores[:5] if int(s) >= 2), 0
    except: return -1, 0

# --- HANDLERS ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    conn = psycopg2.connect(DB_URL); curr = conn.cursor()
    curr.execute("SELECT uid FROM users WHERE uid = %s", (m.from_user.id,))
    if not curr.fetchone():
        end = datetime.now(timezone.utc) + timedelta(days=3)
        curr.execute("INSERT INTO users (uid, username, sub_end, trial_used) VALUES (%s, %s, %s, 1)", (m.from_user.id, m.from_user.username, end))
        conn.commit()
    curr.close(); conn.close()
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎯 Поиск Прогнозов"); kb.button(text="👤 Профиль"); kb.button(text="💳 Подписка")
    await m.answer("🎩 <b>Baron’s Verdict</b>", reply_markup=kb.adjust(2).as_markup(resize_keyboard=True), parse_mode=ParseMode.HTML)

@dp.message(Command("limits"))
async def cmd_limits(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_limit(session, i, key) for i, key in enumerate(API_KEYS)]
        results = await asyncio.gather(*tasks)
    for i in range(0, len(results), 15):
        await m.answer("\n".join(results[i:i+15]), message_thread_id=T_MGMT if m.chat.id == ADMIN_GROUP_ID else None)

@dp.message(F.text == "💳 Подписка")
async def btn_sub(m: types.Message):
    kb = InlineKeyboardBuilder()
    for k, v in TARIFFS.items(): kb.button(text=f"{v['name']} - {v['price']}₽", callback_data=f"buy_{k}")
    await m.answer("💳 Выберите тариф:", reply_markup=kb.adjust(1).as_markup())

@dp.callback_query(F.data.startswith("buy_"))
async def buy_process(c: types.CallbackQuery):
    tid = c.data.split("_")[1]
    pid = random.randint(1000, 9999)
    kb = InlineKeyboardBuilder().button(text="✅ Я оплатил", callback_data=f"done_{tid}_{pid}")
    await c.message.edit_text(f"Оплата {TARIFFS[tid]['price']}₽\nРеквизиты: <code>{REQUISITES}</code>\nКод: <code>#{pid}</code>", reply_markup=kb.as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("done_"))
async def done_process(c: types.CallbackQuery):
    _, tid, pid = c.data.split("_")
    kb = InlineKeyboardBuilder().button(text="✅ Одобрить", callback_data=f"adm_ok_{tid}_{c.from_user.id}").button(text="❌ Отклонить", callback_data=f"adm_no_{c.from_user.id}")
    await bot.send_message(ADMIN_GROUP_ID, f"💰 Чек: @{c.from_user.username}\nID: {c.from_user.id}\nТариф: {tid}\nКод: #{pid}", reply_markup=kb.as_markup(), message_thread_id=T_CASH)
    await c.message.edit_text("⏳ Ожидайте подтверждения.")

@dp.callback_query(F.data.startswith("adm_ok_"))
async def adm_approve(c: types.CallbackQuery):
    _, _, tid, uid = c.data.split("_")
    end = datetime.now(timezone.utc) + timedelta(days=TARIFFS[tid]['days'])
    conn = psycopg2.connect(DB_URL); curr = conn.cursor()
    curr.execute("UPDATE users SET sub_end = %s WHERE uid = %s", (end, int(uid))); conn.commit(); curr.close(); conn.close()
    await bot.send_message(uid, "✅ Подписка активирована!"); await c.message.edit_text("✅ Одобрено")

# --- СТАТИСТИКА СТАВОК ---
@dp.callback_query(F.data == "pred_place")
async def pred_place(c: types.CallbackQuery, state: FSMContext):
    await state.update_data(orig_msg_id=c.message.message_id)
    await c.message.answer("Сумма ставки:", message_thread_id=T_PRED)
    await state.set_state(BetStates.wait_amount)

@dp.message(StateFilter(BetStates.wait_amount))
async def bet_amt(m: types.Message, state: FSMContext):
    await state.update_data(amt=m.text)
    await m.answer("Коэффициент:", message_thread_id=T_PRED)
    await state.set_state(BetStates.wait_odds)

@dp.message(StateFilter(BetStates.wait_odds))
async def bet_odds(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ ВИН", callback_data=f"res_win_{data['orig_msg_id']}_{data['amt']}_{m.text}")
    kb.button(text="❌ ЛОСС", callback_data=f"res_loss_{data['orig_msg_id']}_{data['amt']}_{m.text}")
    await bot.edit_message_reply_markup(chat_id=m.chat.id, message_id=data['orig_msg_id'], reply_markup=kb.as_markup())
    await state.clear()

@dp.callback_query(F.data.startswith("res_"))
async def res_save(c: types.CallbackQuery):
    _, r, _, amt, odds = c.data.split("_")
    prof = round(float(amt)*float(odds)-float(amt), 2) if r=="win" else -float(amt)
    await c.message.edit_reply_markup(reply_markup=InlineKeyboardBuilder().button(text=f"Итог: {prof}₽", callback_data="noop").as_markup())

# --- SCANNER ---
async def scanner():
    sent = set(); idx = 0
    while True:
        try:
            r = requests.get(f"https://api.the-odds-api.com/v4/sports/{LEAGUES[0]}/odds/", params={'apiKey': API_KEYS[idx], 'regions': 'eu', 'markets': 'totals'}, timeout=10)
            if r.status_code == 200:
                for ev in r.json():
                    if ev['id'] not in sent:
                        itb, _ = await analyze_strict(ev['home_team'], True)
                        if isinstance(itb, int) and itb >= 3:
                            sent.add(ev['id'])
                            msg = f"💎 <b>Прогноз</b>\n⚽️ {ev['home_team']}\n🔥 ИТБ 1 (1.5)"
                            kb = InlineKeyboardBuilder().button(text="💰 Поставил", callback_data="pred_place")
                            await bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                            await bot.send_message(ADMIN_GROUP_ID, msg, reply_markup=kb.as_markup(), message_thread_id=T_PRED, parse_mode=ParseMode.HTML)
            elif r.status_code == 429: idx = (idx + 1) % len(API_KEYS)
        except: pass
        await asyncio.sleep(600)

async def main():
    init_db()
    asyncio.create_task(scanner())
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Baron Alive"))
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
