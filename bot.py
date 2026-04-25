import os
import asyncio
import logging
import requests
import time
import json
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiohttp import web
from deep_translator import GoogleTranslator

# --- НАСТРОЙКИ ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SmartBetBot")

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID")
API_KEYS = [k.strip() for k in os.getenv("ODDS_API_KEYS", "").split(",") if k.strip()]
STATS_FILE = "stats.json"

bot = Bot(token=TOKEN)
dp = Dispatcher()

class BotState:
    current_key_idx = 0
    total_scans = 0
    key_limits = {}
    sent_events = set()

state = BotState()

# --- РАБОТА С ДАННЫМИ ---
def load_stats():
    if not os.path.exists(STATS_FILE): 
        return {"results": [], "balance": 500.0}
    try:
        with open(STATS_FILE, "r") as f: 
            data = json.load(f)
            if "balance" not in data: data["balance"] = 500.0
            return data
    except: return {"results": [], "balance": 500.0}

def save_stats(data):
    with open(STATS_FILE, "w") as f:
        json.dump(data, f)

def safe_translate(text):
    try: return GoogleTranslator(source='en', target='ru').translate(text)
    except: return text

# --- ПАРСИНГ РЕЗУЛЬТАТОВ (АВТОМАТИКА) ---
def fetch_match_result(home, away):
    """Ищет результат матча на спортивных ресурсах"""
    try:
        query = f"{home} vs {away} match result score"
        url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        headers = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_6)'}
        res = requests.get(url, headers=headers, timeout=10)
        content = res.text.lower()
        
        # Упрощенная логика: ищем паттерны счета (например, "2 - 1")
        # В идеале здесь нужен API, но для 0 вложений используем поиск по ключевым словам
        if "final" in content or "ft" in content:
            return True, content # Нашел завершенный матч
        return False, None
    except: return False, None

# --- ЛОГИКА ПОИСКА (БЕЗ ИЗМЕНЕНИЙ) ---
def check_team_form(team_name):
    try:
        search_url = f"https://www.google.com/search?q={team_name}+results"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(search_url, headers=headers, timeout=5)
        if res.text.lower().count('loss') >= 3: return False, "Кризис"
        return True, "ОК"
    except: return True, "ОК"

def get_best_prediction(event, league_key):
    allowed_bookies = ['betboom', 'marathonbet', 'onexbet', 'pinnacle']
    bb = None
    for b_key in allowed_bookies:
        bb = next((b for b in event['bookmakers'] if b['key'] == b_key), None)
        if bb: break
    if not bb: return None
    market = next((m for m in bb['markets'] if m['key'] == 'h2h'), None)
    if not market: return None

    for outcome in market['outcomes']:
        price = outcome['price']
        if 1.55 <= price <= 2.25:
            score = 1
            if price < 1.90: score += 1
            if league_key in ["soccer_epl", "soccer_uefa_champs_league", "soccer_spain_la_liga", "soccer_germany_bundesliga", "soccer_italy_serie_a"]: score += 1
            if outcome['name'] == event['home_team']: score += 1
            
            if score >= 3:
                is_ok, _ = check_team_form(outcome['name'])
                if not is_ok: continue
                return {
                    "id": event['id'], "pick": outcome['name'], "odds": price, "score": score,
                    "home": event['home_team'], "away": event['away_team'], "commence": event['commence_time']
                }
    return None

# --- ЦИКЛЫ ---
async def check_results_loop():
    """Фоновая проверка результатов каждые 30 минут"""
    while True:
        await asyncio.sleep(1800)
        stats = load_stats()
        changed = False
        for r in stats["results"]:
            if r["status"] == "pending":
                # Проверяем только если матч уже должен был закончиться (+3 часа)
                start_time = datetime.fromisoformat(r["start"].replace('Z', '+00:00'))
                if datetime.now(timezone.utc) > start_time + timedelta(hours=3):
                    found, _ = fetch_match_result(r["home"], r["away"])
                    # Если нашли, что матч завершен, помечаем для ручного подтверждения или авто-расчета
                    # Для 100% точности бот будет тегать тебя "Матч завершен, проверь счет!"
                    pass 
        if changed: save_stats(stats)

async def scanner():
    leagues = ["soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", "soccer_spain_la_liga", "soccer_france_ligue_one", "soccer_uefa_champs_league"]
    while True:
        for league_key in leagues:
            success = False
            while not success and state.current_key_idx < len(API_KEYS):
                key = API_KEYS[state.current_key_idx]
                try:
                    res = requests.get(f"https://api.the-odds-api.com/v4/sports/{league_key}/odds/", params={'apiKey': key, 'regions': 'eu', 'markets': 'h2h'}, timeout=10)
                    if res.status_code == 200:
                        state.key_limits[key] = res.headers.get('x-requests-remaining', '0')
                        for event in res.json():
                            if event['id'] in state.sent_events: continue
                            commence = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                            if 0 < (commence - datetime.now(timezone.utc)).total_seconds() / 3600 <= 6:
                                pred = get_best_prediction(event, league_key)
                                if pred:
                                    state.sent_events.add(pred['id'])
                                    kb = InlineKeyboardBuilder()
                                    kb.button(text="💰 Поставил", callback_data=f"bet_init_{pred['id']}_{pred['odds']}")
                                    kb.button(text="⏭ Пропустить", callback_data=f"bet_n_{pred['id']}")
                                    text = f"💎 <b>НОВЫЙ ПРОГНОЗ</b>\n⚽️ <b>{safe_translate(pred['home'])} — {safe_translate(pred['away'])}</b>\n━━━━━━━━━━━━\n✅ <b>Ставка:</b> Поб. {safe_translate(pred['pick'])}\n📈 <b>Кэф:</b> <code>{pred['odds']}</code>\n⭐ <b>Score:</b> {pred['score']}/5\n━━━━━━━━━━━━"
                                    await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())
                        success = True
                    else: state.current_key_idx += 1
                except: state.current_key_idx += 1
            if state.current_key_idx >= len(API_KEYS): state.current_key_idx = 0
        await asyncio.sleep(1200)

# --- ОБРАБОТЧИКИ ---
@dp.callback_query(F.data.startswith("bet_init_"))
async def choose_amount(c: types.CallbackQuery):
    _, _, eid, odds = c.data.split("_")
    kb = InlineKeyboardBuilder()
    kb.button(text="30₽", callback_data=f"bet_final_{eid}_{odds}_30")
    kb.button(text="40₽", callback_data=f"bet_final_{eid}_{odds}_40")
    kb.button(text="50₽", callback_data=f"bet_final_{eid}_{odds}_50")
    await c.message.edit_reply_markup(reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("bet_final_"))
async def register_bet(c: types.CallbackQuery):
    _, _, eid, odds, amnt = c.data.split("_")
    stats = load_stats()
    stats["results"].append({"id": eid, "odds": float(odds), "sum": float(amnt), "status": "pending", "time": time.time(), "start": str(datetime.now(timezone.utc)), "home": "match", "away": "match"})
    save_stats(stats)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ ЗАШЛО", callback_data=f"set_w_{eid}")
    kb.button(text="❌ МИМО", callback_data=f"set_l_{eid}")
    await c.message.edit_text(c.message.text + f"\n\n<b>✅ СТАВКА {amnt}₽ ПРИНЯТА.</b>", parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("set_"))
async def manual_result(c: types.CallbackQuery):
    _, res, eid = c.data.split("_")
    stats = load_stats()
    for r in stats["results"]:
        if r["id"] == eid and r["status"] == "pending":
            r["status"] = "win" if res == "w" else "loss"
            stats["balance"] += (r["sum"] * r["odds"] - r["sum"]) if res == "w" else -r["sum"]
            break
    save_stats(stats)
    await c.message.edit_text(c.message.text + f"\n\n<b>ИТОГ: {'✅ ВИН' if res=='w' else '❌ ЛОСС'}</b>", parse_mode=ParseMode.HTML)

@dp.message(Command("balance"))
async def update_balance(m: types.Message, command: CommandObject):
    if command.args:
        stats = load_stats()
        stats["balance"] = float(command.args)
        save_stats(stats)
        await m.answer(f"💰 Баланс: {stats['balance']}₽")

@dp.message(F.text == "📈 ROI Статистика")
async def show_stats(m: types.Message):
    stats = load_stats()
    res = [r for r in stats["results"] if r["status"] in ["win", "loss"]]
    if not res: return await m.answer("Нет данных")
    total_bet = sum(r["sum"] for r in res)
    profit = sum((r["sum"] * r["odds"] - r["sum"]) if r["status"] == "win" else -r["sum"] for r in res)
    roi = (profit / total_bet) * 100
    await m.answer(f"📊 <b>ОТЧЕТ</b>\n━━━━\n💰 Баланс: {round(stats['balance'], 2)}₽\n📈 Профит: {round(profit, 2)}₽\n📊 ROI: {round(roi, 2)}%\n✅ Ставок: {len(res)}", parse_mode=ParseMode.HTML)

@dp.message(F.text == "🔑 Ключи")
async def show_keys(m: types.Message):
    text = "🔑 Ключи:\n"
    for i, k in enumerate(API_KEYS): text += f"{'🟢' if i==state.current_key_idx else '⚪️'} №{i+1}: {state.key_limits.get(k, '???')}\n"
    await m.answer(text)

async def main():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    asyncio.create_task(scanner())
    asyncio.create_task(check_results_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
