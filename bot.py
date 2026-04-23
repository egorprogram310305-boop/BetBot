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

# --- НАСТРОЙКИ ЛОГИРОВАНИЯ ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("BetBot_Pro")

# --- ПЕРЕМЕННЫЕ ---
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID")
API_KEYS = [k.strip() for k in os.getenv("ODDS_API_KEYS", "").split(",") if k.strip()]
STATS_FILE = "stats.json"

bot = Bot(token=TOKEN)
dp = Dispatcher()

class BotState:
    start_time = time.time()
    total_scans = 0
    current_key_idx = 0
    key_limits = {}

state = BotState()

# --- ФУНКЦИИ ПЕРЕВОДА И СТАТИСТИКИ ---
def safe_translate(text):
    """Исправленная функция перевода через deep-translator"""
    try:
        if not text: return text
        return GoogleTranslator(source='en', target='ru').translate(text)
    except Exception as e:
        logger.error(f"❌ Ошибка перевода '{text}': {e}")
        return text

def load_stats():
    if not os.path.exists(STATS_FILE):
        return {"results": []}
    try:
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    except:
        return {"results": []}

def save_result(is_win, odds):
    stats = load_stats()
    stats["results"].append({
        "time": time.time(),
        "win": is_win,
        "odds": odds
    })
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

# --- АНАЛИЗ МАТЧЕЙ ---
def get_best_prediction(event, league_name):
    bb = next((b for b in event['bookmakers'] if b['key'] == 'betboom'), None)
    if not bb:
        logger.info(f"跳 Пропуск: {event['home_team']} - нет кэфов Betboom")
        return None

    picks = []
    h2h = next((m for m in bb['markets'] if m['key'] == 'h2h'), None)
    
    if h2h:
        for outcome in h2h['outcomes']:
            price = outcome['price']
            # Фильтр ROI+: кэфы от 1.60 до 2.30
            if 1.60 <= price <= 2.30:
                # Базовый балл 3 + бонус за "сильную" лигу (мотивация)
                score = 3
                if any(x in league_name.lower() for x in ["англия", "германия", "лига чемпионов"]):
                    score += 1
                
                picks.append({
                    "pick": f"Победа: {safe_translate(outcome['name'])}",
                    "odds": price,
                    "score": score
                })
    
    return sorted(picks, key=lambda x: (x['score'], x['odds']), reverse=True)[0] if picks else None

# --- СКАНЕР ---
async def scanner():
    leagues = ["soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", "soccer_spain_la_liga", "soccer_france_ligue_one", "soccer_uefa_champs_league"]
    
    while True:
        logger.info(f"--- 🔄 ЦИКЛ СКАНИРОВАНИЯ №{state.total_scans + 1} ---")
        
        for league_key in leagues:
            if not API_KEYS:
                logger.error("❌ Нет API ключей в ODDS_API_KEYS!")
                break
                
            current_key = API_KEYS[state.current_key_idx]
            url = f"https://api.the-odds-api.com/v4/sports/{league_key}/odds/"
            params = {'apiKey': current_key, 'regions': 'eu', 'markets': 'h2h', 'bookmakers': 'betboom'}

            try:
                logger.info(f"📡 Запрос {league_key} (Ключ №{state.current_key_idx + 1})")
                res = requests.get(url, params=params, timeout=20)
                
                if res.status_code == 200:
                    state.key_limits[current_key] = res.headers.get('x-requests-remaining', '0')
                    data = res.json()
                    
                    for event in data:
                        # 1. Фильтр по времени (ближайшие 4 часа)
                        commence_time = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                        time_diff = (commence_time - datetime.now(timezone.utc)).total_seconds() / 3600

                        if 0 < time_diff <= 4:
                            best = get_best_prediction(event, league_key)
                            if best:
                                home_ru = safe_translate(event['home_team'])
                                away_ru = safe_translate(event['away_team'])
                                
                                # Кнопки учета
                                kb = InlineKeyboardBuilder()
                                kb.button(text="✅ ВИН", callback_data=f"res_w_{best['odds']}")
                                kb.button(text="❌ ЛОСС", callback_data=f"res_l_{best['odds']}")
                                
                                text = (
                                    f"━━━━━━━━━━━━━━━━━━━━\n"
                                    f"🏆 <b>ГОРЯЧИЙ ПРОГНОЗ</b>\n"
                                    f"⚽️ <b>{home_ru} — {away_ru}</b>\n"
                                    f"⏰ Начало через: {round(time_diff, 1)} ч.\n"
                                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                                    f"✅ <b>Ставка:</b> <code>{best['pick']}</code>\n"
                                    f"📈 <b>Коэффициент:</b> <code>{best['odds']}</code>\n"
                                    f"🔥 <b>Уверенность:</b> {'🔥' * best['score']}\n\n"
                                    f"📍 <b>БК:</b> Betboom\n"
                                    f"━━━━━━━━━━━━━━━━━━━━"
                                )
                                await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())
                                logger.info(f"✅ Отправлен прогноз на {home_ru}")
                                await asyncio.sleep(10) # Защита от спама
                        
                elif res.status_code in [401, 429]:
                    logger.warning(f"⚠️ Ключ №{state.current_key_idx + 1} исчерпан. Переключаюсь...")
                    state.current_key_idx = (state.current_key_idx + 1) % len(API_KEYS)
                else:
                    logger.error(f"❌ Ошибка API {res.status_code}: {res.text}")

            except Exception as e:
                logger.error(f"💥 Ошибка в процессе: {e}")

        state.total_scans += 1
        logger.info(f"😴 Цикл завершен. Сон 30 минут.")
        await asyncio.sleep(1800)

# --- ОБРАБОТЧИКИ ---
@dp.callback_query(F.data.startswith("res_"))
async def handle_result(callback: types.CallbackQuery):
    _, result, odds = callback.data.split("_")
    is_win = (result == "w")
    save_result(is_win, float(odds))
    
    await callback.message.edit_reply_markup(reply_markup=None) # Убираем кнопки
    status_text = "💰 ВЫИГРЫШ!" if is_win else "📉 ПРОИГРЫШ"
    await callback.message.reply(f"<b>Результат записан: {status_text}</b>", parse_mode=ParseMode.HTML)
    await callback.answer()

@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="📈 Статистика ROI"); kb.button(text="🔑 Ключи")
    await m.answer("🤖 Бот запущен. ROI считается автоматически по вашим отметкам.", 
                   reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text == "📈 Статистика ROI")
async def show_roi(m: types.Message):
    stats = load_stats()
    def calc(days):
        cutoff = time.time() - (days * 86400)
        hits = [r for r in stats["results"] if r["time"] > cutoff]
        if not hits: return "Данных нет"
        profit = sum((r["odds"] - 1) if r["win"] else -1 for r in hits)
        roi = (profit / len(hits)) * 100
        return f"{round(roi, 1)}% (Ставок: {len(hits)})"

    await m.answer(f"📊 <b>Ваш ROI:</b>\n\nДень: {calc(1)}\nНеделя: {calc(7)}\nМесяц: {calc(30)}", parse_mode=ParseMode.HTML)

@dp.message(F.text == "🔑 Ключи")
async def show_keys(m: types.Message):
    text = "🔑 <b>Состояние ключей:</b>\n"
    for i, k in enumerate(API_KEYS):
        lim = state.key_limits.get(k, '???')
        text += f"{i+1}. <code>{k[:5]}...</code> | Остаток: {lim}\n"
    await m.answer(text, parse_mode=ParseMode.HTML)

# --- ЗАПУСК ---
async def handle_health(request): return web.Response(text="Бот в сети", status=200)

async def main():
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000)))
    await site.start()
    
    asyncio.create_task(scanner())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
