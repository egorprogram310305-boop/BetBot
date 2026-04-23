import os
import asyncio
import logging
import requests
import time
import json
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiohttp import web
from googletrans import Translator

# --- НАСТРОЙКИ ЛОГИРОВАНИЯ ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()] # Печать в консоль Render
)
logger = logging.getLogger("UltraBetBot")

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID")
API_KEYS = [k.strip() for k in os.getenv("ODDS_API_KEYS", "").split(",") if k.strip()]

translator = Translator()
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- ХРАНИЛИЩЕ ДАННЫХ ---
STATS_FILE = "stats.json"

def load_stats():
    if not os.path.exists(STATS_FILE):
        return {"results": []}
    with open(STATS_FILE, "r") as f:
        return json.load(f)

def save_result(is_win, odds):
    stats = load_stats()
    stats["results"].append({
        "time": time.time(),
        "win": is_win,
        "odds": odds
    })
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

# --- ЛОГИКА АНАЛИЗА ---
def analyze_motivation(event, league_key):
    """Имитация фильтра мотивации на основе статуса матча"""
    score = 0
    # В топ-лигах мотивация выше в конце и начале сезона
    top_leagues = ["soccer_epl", "soccer_uefa_champs_league", "soccer_germany_bundesliga"]
    if league_key in top_leagues:
        score += 1
    return score

def get_best_prediction(event, league_key):
    bb = next((b for b in event['bookmakers'] if b['key'] == 'betboom'), None)
    if not bb: 
        logger.warning(f"⚠️ Betboom не найден для матча {event['home_team']}")
        return None

    picks = []
    h2h = next((m for m in bb['markets'] if m['key'] == 'h2h'), None)
    
    if h2h:
        for outcome in h2h['outcomes']:
            price = outcome['price']
            if 1.60 <= price <= 2.20:
                score = 3 + analyze_motivation(event, league_key)
                picks.append({
                    "pick": f"Победа: {outcome['name']}",
                    "odds": price,
                    "score": score
                })
    
    return sorted(picks, key=lambda x: (x['score'], x['odds']), reverse=True)[0] if picks else None

# --- СКАНЕР ---
async def scanner():
    while True:
        logger.info(f"🚀 Запуск нового цикла сканирования...")
        if not API_KEYS:
            logger.error("❌ Список API ключей пуст!")
            await asyncio.sleep(60)
            continue

        for league_key in ["soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", "soccer_spain_la_liga"]:
            key = API_KEYS[0] # Упростим для примера ротацию
            url = f"https://api.the-odds-api.com/v4/sports/{league_key}/odds/"
            params = {'apiKey': key, 'regions': 'eu', 'markets': 'h2h', 'bookmakers': 'betboom'}
            
            try:
                logger.info(f"📡 Запрос лиги {league_key}...")
                res = requests.get(url, params=params, timeout=15)
                
                if res.status_code != 200:
                    logger.error(f"❌ Ошибка API {res.status_code}: {res.text}")
                    continue

                data = res.json()
                logger.info(f"✅ Получено {len(data)} матчей для {league_key}")

                for event in data:
                    # 1) Фильтр на 4 часа вперед
                    commence_time = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                    now = datetime.now(timezone.utc)
                    time_diff = (commence_time - now).total_seconds() / 3600

                    if 0 < time_diff <= 4:
                        logger.info(f"🔍 Анализ матча: {event['home_team']} (через {round(time_diff, 1)} ч.)")
                        best = get_best_prediction(event, league_key)
                        
                        if best and best['score'] >= 3:
                            # Кнопки результата
                            kb = InlineKeyboardBuilder()
                            kb.button(text="✅ ВИН", callback_data=f"win_{best['odds']}")
                            kb.button(text="❌ ЛОСС", callback_data=f"loss_{best['odds']}")
                            
                            text = (
                                f"🏆 <b>СРОЧНЫЙ ПРОГНОЗ (до начала < 4ч)</b>\n"
                                f"⚽️ {event['home_team']} — {event['away_team']}\n\n"
                                f"✅ Ставка: {best['pick']}\n"
                                f"📈 Кф: {best['odds']}\n"
                                f"🔥 Уверенность: {'🔥' * best['score']}"
                            )
                            await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())
                            await asyncio.sleep(5)
                    else:
                        continue

            except Exception as e:
                logger.error(f"💥 Критическая ошибка в сканере: {e}", exc_info=True)

        logger.info("🛌 Цикл завершен. Сон 30 мин.")
        await asyncio.sleep(1800)

# --- ОБРАБОТКА КНОПОК РЕЗУЛЬТАТА ---
@dp.callback_query(F.data.startswith("win_") | F.data.startswith("loss_"))
async def process_result(callback: types.CallbackQuery):
    data = callback.data.split("_")
    is_win = data[0] == "win"
    odds = float(data[1])
    
    save_result(is_win, odds)
    
    status = "✅ ЗАШЛО" if is_win else "❌ МИМО"
    await callback.message.edit_text(callback.message.text + f"\n\n<b>ИТОГ: {status}</b>", parse_mode=ParseMode.HTML)
    await callback.answer("Результат записан в статистику!")

# --- РАСЧЕТ ROI ---
def calculate_roi(days):
    stats = load_stats()
    cutoff = time.time() - (days * 86400)
    relevant = [r for r in stats["results"] if r["time"] > cutoff]
    
    if not relevant: return "0% (нет данных)"
    
    total_bets = len(relevant)
    profit = 0
    for r in relevant:
        if r["win"]:
            profit += (r["odds"] - 1)
        else:
            profit -= 1
            
    roi = (profit / total_bets) * 100
    return f"{round(roi, 2)}% (Всего ставок: {total_bets})"

@dp.message(F.text == "📊 ROI Статистика")
async def show_roi(message: types.Message):
    text = (
        f"📈 <b>Ваша статистика:</b>\n\n"
        f"📅 За 24 часа: {calculate_roi(1)}\n"
        f"📅 За неделю: {calculate_roi(7)}\n"
        f"📅 За месяц: {calculate_roi(30)}"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)

# --- ИНТЕРФЕЙС И ЗАПУСК ---
@dp.message(Command("start"))
async def start(m: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="📊 ROI Статистика")
    await m.answer("Система готова. Кнопки ROI под постом помогут вести учет.", reply_markup=kb.as_markup(resize_keyboard=True))

async def handle(r): return web.Response(text="OK")

async def main():
    # Создаем пустой файл статов если нет
    if not os.path.exists(STATS_FILE):
        with open(STATS_FILE, "w") as f: json.dump({"results": []}, f)
        
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000)))
    await site.start()
    asyncio.create_task(scanner())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

