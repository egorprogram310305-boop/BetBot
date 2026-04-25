import os
import asyncio
import logging
import requests
import time
import json
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiohttp import web
from deep_translator import GoogleTranslator

# --- ЛОГИРОВАНИЕ ---
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

state = BotState()

def safe_translate(text):
    try: return GoogleTranslator(source='en', target='ru').translate(text)
    except: return text

def load_stats():
    if not os.path.exists(STATS_FILE): return {"results": []}
    try:
        with open(STATS_FILE, "r") as f: return json.load(f)
    except: return {"results": []}

# --- УЛУЧШЕННЫЙ АНАЛИЗ ---
def get_best_prediction(event, league_key):
    # Ищем любую из популярных БК, если Betboom недоступен
    allowed_bookies = ['betboom', 'marathonbet', 'onexbet', 'pinnacle']
    bb = None
    for b_key in allowed_bookies:
        bb = next((b for b in event['bookmakers'] if b['key'] == b_key), None)
        if bb: break
    
    if not bb: return None

    market = next((m for m in bb['markets'] if m['key'] == 'h2h'), None)
    if not market: return None

    best_pick = None
    max_rating = -1

    for outcome in market['outcomes']:
        price = outcome['price']
        # Расширяем диапазон кэфов чуть-чуть
        if 1.50 <= price <= 2.60:
            score = 1 # Базовый
            if price < 1.90: score += 1 # Фаворит
            
            top_leagues = ["soccer_epl", "soccer_uefa_champs_league", "soccer_spain_la_liga", "soccer_germany_bundesliga", "soccer_italy_serie_a"]
            if league_key in top_leagues: score += 1 # Топ лига
            
            if outcome['name'] == event['home_team']: score += 1 # Домашняя команда

            if score > max_rating:
                max_rating = score
                best_pick = {
                    "pick": f"Победа: {safe_translate(outcome['name'])}",
                    "odds": price,
                    "score": score,
                    "home": safe_translate(event['home_team']),
                    "away": safe_translate(event['away_team']),
                    "bookmaker": bb['title']
                }
    
    # Снижаем порог до 2 баллов, чтобы видеть больше прогнозов
    return best_pick if (best_pick and best_pick['score'] >= 2) else None

# --- СКАНЕР ---
async def scanner():
    leagues = ["soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", 
               "soccer_spain_la_liga", "soccer_france_ligue_one", "soccer_uefa_champs_league"]
    
    while True:
        logger.info(f"--- 🔄 ЦИКЛ №{state.total_scans + 1} ---")
        found_any_match = False
        
        for league_key in leagues:
            success = False
            while not success and state.current_key_idx < len(API_KEYS):
                key = API_KEYS[state.current_key_idx]
                url = f"https://api.the-odds-api.com/v4/sports/{league_key}/odds/"
                params = {'apiKey': key, 'regions': 'eu', 'markets': 'h2h'} # Убрали фильтр БК из запроса

                try:
                    res = requests.get(url, params=params, timeout=10)
                    if res.status_code == 200:
                        state.key_limits[key] = res.headers.get('x-requests-remaining', '0')
                        data = res.json()
                        for event in data:
                            commence = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                            diff = (commence - datetime.now(timezone.utc)).total_seconds() / 3600
                            
                            # Увеличим окно до 6 часов
                            if 0 < diff <= 6:
                                pred = get_best_prediction(event, league_key)
                                if pred:
                                    found_any_match = True
                                    kb = InlineKeyboardBuilder()
                                    kb.button(text="✅ ВИН", callback_data=f"res_w_{pred['odds']}")
                                    kb.button(text="❌ ЛОСС", callback_data=f"res_l_{pred['odds']}")
                                    
                                    text = (
                                        f"📊 <b>ПРОГНОЗ: {pred['home']} — {pred['away']}</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━\n"
                                        f"✅ <b>Ставка:</b> <code>{pred['pick']}</code>\n"
                                        f"📈 <b>Кэф:</b> <code>{pred['odds']}</code>\n"
                                        f"🔥 <b>Уверенность:</b> {pred['score']}/5\n"
                                        f"📍 <b>БК:</b> {pred['bookmaker']}\n"
                                        f"━━━━━━━━━━━━━━━━━━━━"
                                    )
                                    await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())
                                    await asyncio.sleep(5)
                        success = True
                    elif res.status_code in [401, 429]:
                        state.current_key_idx += 1
                    else:
                        state.current_key_idx += 1
                except Exception as e:
                    logger.error(f"Ошибка: {e}")
                    state.current_key_idx += 1
                    await asyncio.sleep(1)

            if state.current_key_idx >= len(API_KEYS):
                state.current_key_idx = 0
                break

        state.total_scans += 1
        # Уменьшим время сна до 20 минут, чтобы чаще ловить матчи
        await asyncio.sleep(1200)

# --- ОСТАЛЬНОЙ КОД БЕЗ ИЗМЕНЕНИЙ ---
@dp.callback_query(F.data.startswith("res_"))
async def handle_res(c: types.CallbackQuery):
    _, r, o = c.data.split("_")
    is_win = (r == "w")
    stats = load_stats()
    stats["results"].append({"time": time.time(), "win": is_win, "odds": float(o)})
    with open(STATS_FILE, "w") as f: json.dump(stats, f)
    await c.message.edit_reply_markup(reply_markup=None)
    await c.answer("Сохранено!")

@dp.message(Command("start"))
async def start(m: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="📈 ROI Статистика"); kb.button(text="🔑 Ключи")
    await m.answer("🤖 Бот Baron обновлен!", reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text == "📈 ROI Статистика")
async def show_roi(m: types.Message):
    stats = load_stats()
    def calc(days):
        cutoff = time.time() - (days * 86400)
        hits = [r for r in stats["results"] if r.get("time", 0) > cutoff]
        if not hits: return "0%"
        profit = sum((r["odds"] - 1) if r["win"] else -1 for r in hits)
        return f"{round((profit/len(hits))*100, 1)}% ({len(hits)} ст.)"
    await m.answer(f"📊 <b>ROI:</b>\nДень: {calc(1)}\nНеделя: {calc(7)}", parse_mode=ParseMode.HTML)

@dp.message(F.text == "🔑 Ключи")
async def show_keys(m: types.Message):
    if not API_KEYS: return await m.answer("Ключей нет")
    text = "🔑 <b>Статус ключей:</b>\n"
    for i, k in enumerate(API_KEYS):
        status = "🟢" if i == state.current_key_idx else ("🔴" if i < state.current_key_idx else "⚪️")
        lim = state.key_limits.get(k, "???")
        text += f"{status} К №{i+1}: {lim}\n"
        if len(text) > 3500:
            await m.answer(text, parse_mode=ParseMode.HTML); text = ""
    if text: await m.answer(text, parse_mode=ParseMode.HTML)

async def main():
    if not os.path.exists(STATS_FILE):
        with open(STATS_FILE, "w") as f: json.dump({"results": []}, f)
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    asyncio.create_task(scanner())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
