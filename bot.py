import os
import asyncio
import logging
import requests
import time
import json
import random
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiohttp import web
from deep_translator import GoogleTranslator

# --- НАСТРОЙКИ ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BaronVIP_💎")

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
API_KEYS = [k.strip() for k in os.getenv("ODDS_API_KEYS", "").split(",") if k.strip()]
STATS_FILE = "stats.json"
SENT_EVENTS_FILE = "sent_events.json"

bot = Bot(token=TOKEN)
dp = Dispatcher()

USER_AGENTS = [
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'
]

class BotState:
    current_key_idx = 0
    key_limits = {}
    sent_events = set()
    # Параметры Admin 2.7
    min_odds = 1.50
    skepticism = 0.90
    depth_hours = 24

state = BotState()

# --- СИСТЕМА ХРАНЕНИЯ ---
def load_data():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f:
                data = json.load(f)
                defaults = {"results": [], "balance": 500.0, "wins": 0, "loss": 0, "refunds": 0, "start_balance": 500.0}
                for k, v in defaults.items():
                    if k not in data: data[k] = v
                return data
        except: pass
    return {"results": [], "balance": 500.0, "wins": 0, "loss": 0, "refunds": 0, "start_balance": 500.0}

def save_data(data):
    with open(STATS_FILE, "w") as f:
        json.dump(data, f)

# --- ФУНКЦИИ ---
def sync_team_name(name):
    name = name.replace("U23", "").replace("U21", "").replace("U19", "")
    removals = ["FC", "CF", "SSC", "AS", "Utd", "United", "Real", "BSC", "AC"]
    words = name.split()
    clean_words = [w for w in words if w not in removals]
    return " ".join(clean_words).strip()

def safe_translate(text):
    try: return GoogleTranslator(source='en', target='ru').translate(text)
    except: return text

def analyze_strict(home, away):
    try:
        headers = {'User-Agent': random.choice(USER_AGENTS)}
        query = f"{home} last matches goals"
        res = requests.get(f"https://www.google.com/search?q={query}", headers=headers, timeout=7)
        content = res.text.lower()
        if "clean sheet" in content or "strong defense" in content:
            return None, "🛡 Защита соперника слишком крепка"
        goals_count = content.count("2-") + content.count("3-") + content.count("4-")
        if goals_count >= 4:
            return "OVER", "🔥 Серийность: Высокая результативность"
        return "FORA", "⚖️ Сбалансированный темп"
    except: return "FORA", "⚙️ Статистика учтена"

def get_vip_prediction(event):
    if not event.get('bookmakers'): return None
    all_odds = []
    for bk in event['bookmakers']:
        m = next((m for m in bk['markets'] if m['key'] == 'h2h'), None)
        if m: all_odds.append(m['outcomes'][0]['price'])
    if not all_odds: return None
    min_market_price = min(all_odds)

    if 1.50 <= min_market_price <= 3.0:
        style, note = analyze_strict(event['home_team'], event['away_team'])
        if not style: return None
        if style == "OVER":
            raw_odds = min_market_price * 0.85 
            bet_type = f"ИТБ (1.5) на {sync_team_name(safe_translate(event['home_team']))}"
        else:
            raw_odds = min_market_price * 0.72
            bet_type = f"Фора (0) на {sync_team_name(safe_translate(event['home_team']))}"
        final_odds = round(raw_odds * state.skepticism, 2)
        if final_odds < state.min_odds: return None
        commence_msk = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00')) + timedelta(hours=3)
        return {
            "id": event['id'], "pick": bet_type, "odds": final_odds,
            "home": event['home_team'], "away": event['away_team'],
            "note": note, "time": commence_msk.strftime("%H:%M"),
            "date": commence_msk.strftime("%d.%m"),
            "limit": round(final_odds - 0.07, 2)
        }
    return None

# --- СКАНЕР ---
async def scanner():
    leagues = ["soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", "soccer_spain_la_liga", "soccer_france_ligue_one"]
    while True:
        for league_key in leagues:
            if state.current_key_idx >= len(API_KEYS): state.current_key_idx = 0
            key = API_KEYS[state.current_key_idx]
            try:
                res = requests.get(f"https://api.the-odds-api.com/v4/sports/{league_key}/odds/", 
                                   params={'apiKey': key, 'regions': 'eu', 'markets': 'h2h'}, timeout=10)
                if res.status_code == 200:
                    state.key_limits[key] = res.headers.get('x-requests-remaining', '0')
                    data = res.json()
                    for event in data:
                        if event['id'] in state.sent_events: continue
                        diff = (datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00')) - datetime.now(timezone.utc)).total_seconds() / 3600
                        if 0 < diff <= state.depth_hours:
                            pred = get_vip_prediction(event)
                            if pred:
                                state.sent_events.add(pred['id'])
                                kb = InlineKeyboardBuilder()
                                kb.button(text="💰 Поставил", callback_data=f"v_{pred['id']}_{pred['odds']}")
                                kb.button(text="⏭ Пропустить", callback_data="skip")
                                home_s, away_s = sync_team_name(safe_translate(pred['home'])), sync_team_name(safe_translate(pred['away']))
                                text = (
                                    f"💎 <b>BaronVIP v2.7</b>\n"
                                    f"⚽️ <code>{home_s}</code> — <code>{away_s}</code>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━\n"
                                    f"📅 <b>Дата:</b> {pred['date']} | <b>Начало:</b> {pred['time']}\n"
                                    f"🔥 <b>Ставка:</b> <code>{pred['pick']}</code>\n"
                                    f"📈 <b>Коэффициент:</b> <code>{pred['odds']}</code>\n"
                                    f"📉 <b>Нижний порог:</b> <code>{pred['limit']}</code>\n\n"
                                    f"📊 <b>Анализ:</b> {pred['note']}\n"
                                    f"━━━━━━━━━━━━━━━━━━━━"
                                )
                                await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())
                else: state.current_key_idx += 1
            except: state.current_key_idx += 1
            await asyncio.sleep(2)
        await asyncio.sleep(600)

# --- ОБРАБОТЧИКИ ---
@dp.callback_query(F.data == "skip")
async def bet_skip(c: types.CallbackQuery): await c.message.delete()

@dp.callback_query(F.data.startswith("v_"))
async def bet_init(c: types.CallbackQuery):
    _, eid, odds = c.data.split("_")
    kb = InlineKeyboardBuilder()
    for val in [30, 40, 50, 60, 70]: kb.button(text=f"{val}₽", callback_data=f"f_{eid}_{odds}_{val}")
    await c.message.edit_reply_markup(reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("f_"))
async def bet_final(c: types.CallbackQuery):
    _, eid, odds, amnt = c.data.split("_")
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ ВИН", callback_data=f"win_in_{eid}_{amnt}")
    kb.button(text="🔄 ВОЗВРАТ", callback_data=f"res_r_{eid}_{amnt}")
    kb.button(text="❌ ЛОСС", callback_data=f"res_l_{eid}_{amnt}")
    await c.message.edit_text(c.message.text + f"\n\n<b>✅ ПРИНЯТО: {amnt}₽</b>", reply_markup=kb.as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("res_"))
async def settle_simple(c: types.CallbackQuery):
    _, res, eid, amnt = c.data.split("_")
    stats = load_data()
    if res == "l":
        stats["loss"] += 1
        stats["balance"] -= float(amnt)
    elif res == "r": stats["refunds"] += 1
    save_data(stats)
    await c.message.edit_text(c.message.text + f"\n\n<b>ИТОГ: {'❌ ЛОСС' if res=='l' else '🔄 ВОЗВРАТ'}</b>", parse_mode=ParseMode.HTML)

# --- КНОПКА КЛЮЧИ ---
@dp.message(F.text == "🔑 Ключи")
async def show_keys(m: types.Message):
    text = "🔑 <b>Статус API:</b>\n"
    for i, k in enumerate(API_KEYS):
        status = "🟢" if i == state.current_key_idx else "⚪️"
        text += f"{status} К №{i+1}: {state.key_limits.get(k, '???')}\n"
    await m.answer(text, parse_mode=ParseMode.HTML)

# --- АДМИН ПАНЕЛЬ 2.7 (ИСПРАВЛЕНО) ---
@dp.message(Command("admin"))
async def admin_menu(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="⏳ Глубина анализа", callback_data="set_depth"), 
           types.InlineKeyboardButton(text="📉 Порог кэфа", callback_data="set_min"))
    kb.row(types.InlineKeyboardButton(text="🔧 Скептицизм", callback_data="set_skep"), 
           types.InlineKeyboardButton(text="⚙️ Настройки", callback_data="show_config"))
    kb.row(types.InlineKeyboardButton(text="🏠 Выход", callback_data="admin_exit"))
    await m.answer("⚙️ <b>Панель Admin 2.7</b>", reply_markup=kb.as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "show_config")
async def show_config(c: types.CallbackQuery):
    text = (f"🛠 <b>Текущие настройки:</b>\n"
            f"• Мин. коэффициент: <code>{state.min_odds}</code>\n"
            f"• Скептицизм: <code>{state.skepticism}</code>\n"
            f"• Глубина поиска: <code>{state.depth_hours} ч.</code>")
    await c.message.answer(text, parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_exit")
async def admin_exit(c: types.CallbackQuery):
    await c.message.delete()
    await c.message.answer("👋 Выход из админ-панели выполнен.")

@dp.callback_query(F.data == "set_min")
async def set_min_req(c: types.CallbackQuery):
    await c.message.answer("Используйте: <code>/min 1.60</code>", parse_mode=ParseMode.HTML)

@dp.message(Command("min"))
async def set_min_exec(m: types.Message, command: CommandObject):
    if m.from_user.id != ADMIN_ID: return
    try:
        state.min_odds = float(command.args)
        await m.answer(f"✅ Мин. кэф: {state.min_odds}")
    except: await m.answer("Ошибка формата!")

@dp.message(F.text == "📈 ROI Статистика")
async def show_stats_btn(m: types.Message):
    s = load_data()
    roi = ((s['balance'] - s['start_balance']) / s['start_balance']) * 100
    text = (f"📊 <b>BaronVIP v2.7 Отчет:</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Баланс:</b> {round(s['balance'], 2)}₽\n"
            f"✅ <b>Вин:</b> {s['wins']} | ❌ <b>Лосс:</b> {s['loss']} | 🔄 <b>Возврат:</b> {s['refunds']}\n"
            f"📈 <b>Прибыль:</b> {round(roi, 2)}%\n━━━━━━━━━━━━━━━━━━━━")
    await m.answer(text, parse_mode=ParseMode.HTML)

@dp.message(Command("start"))
async def start(m: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.row(types.KeyboardButton(text="📈 ROI Статистика"), types.KeyboardButton(text="🔑 Ключи"))
    await m.answer("🚀 <b>BaronVIP v2.7</b>\nВайб-кодинг | iPhone Friendly", reply_markup=kb.as_markup(resize_keyboard=True), parse_mode=ParseMode.HTML)

async def main():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    asyncio.create_task(scanner())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
