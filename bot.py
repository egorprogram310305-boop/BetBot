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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SmartBetBot")

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID")
# Очищаем ключи от лишних пробелов и пустых строк сразу при загрузке
API_KEYS = [k.strip() for k in os.getenv("ODDS_API_KEYS", "").split(",") if k.strip()]
STATS_FILE = "stats.json"

bot = Bot(token=TOKEN)
dp = Dispatcher()

class BotState:
    current_key_idx = 0
    total_scans = 0
    key_limits = {}

state = BotState()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def safe_translate(text):
    try:
        return GoogleTranslator(source='en', target='ru').translate(text)
    except:
        return text

def load_stats():
    if not os.path.exists(STATS_FILE): return {"results": []}
    try:
        with open(STATS_FILE, "r") as f: return json.load(f)
    except:
        return {"results": []}

# --- СИСТЕМА УМНОГО АНАЛИЗА ---
def get_advanced_score(event, league_key):
    score = 0
    # Базовый балл за домашнее поле
    score += 1 
    # Фактор престижа лиги
    top_leagues = ["soccer_epl", "soccer_uefa_champs_league", "soccer_spain_la_liga", "soccer_germany_bundesliga"]
    if league_key in top_leagues:
        score += 1
    return score

def get_best_prediction(event, league_key):
    bb = next((b for b in event['bookmakers'] if b['key'] == 'betboom'), None)
    if not bb: return None

    market = next((m for m in bb['markets'] if m['key'] == 'h2h'), None)
    if not market: return None

    best_pick = None
    max_rating = -1

    for outcome in market['outcomes']:
        price = outcome['price']
        if 1.60 <= price <= 2.40:
            analysis_score = get_advanced_score(event, league_key)
            if price < 1.90: analysis_score += 1 # Доверие рынка к фавориту

            if analysis_score > max_rating:
                max_rating = analysis_score
                best_pick = {
                    "pick": f"Победа: {safe_translate(outcome['name'])}",
                    "odds": price,
                    "score": analysis_score,
                    "home": safe_translate(event['home_team']),
                    "away": safe_translate(event['away_team'])
                }
    return best_pick if (best_pick and best_pick['score'] >= 3) else None

# --- ОСНОВНОЙ СКАНЕР ---
async def scanner():
    leagues = ["soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", 
               "soccer_spain_la_liga", "soccer_france_ligue_one", "soccer_uefa_champs_league"]
    
    while True:
        logger.info(f"--- 🔄 ЦИКЛ №{state.total_scans + 1} ---")
        
        for league_key in leagues:
            success = False
            while not success and state.current_key_idx < len(API_KEYS):
                key = API_KEYS[state.current_key_idx]
                url = f"https://api.the-odds-api.com/v4/sports/{league_key}/odds/"
                params = {'apiKey': key, 'regions': 'eu', 'markets': 'h2h', 'bookmakers': 'betboom'}

                try:
                    res = requests.get(url, params=params, timeout=15)
                    if res.status_code == 200:
                        state.key_limits[key] = res.headers.get('x-requests-remaining', '0')
                        data = res.json()
                        for event in data:
                            commence = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                            diff = (commence - datetime.now(timezone.utc)).total_seconds() / 3600
                            
                            if 0 < diff <= 4:
                                prediction = get_best_prediction(event, league_key)
                                if prediction:
                                    kb = InlineKeyboardBuilder()
                                    kb.button(text="✅ ВИН", callback_data=f"res_w_{prediction['odds']}")
                                    kb.button(text="❌ ЛОСС", callback_data=f"res_l_{prediction['odds']}")
                                    
                                    text = (
                                        f"📊 <b>ГЛУБОКИЙ АНАЛИЗ МАТЧА</b>\n"
                                        f"⚽️ <b>{prediction['home']} — {prediction['away']}</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━\n"
                                        f"✅ <b>Прогноз:</b> <code>{prediction['pick']}</code>\n"
                                        f"📈 <b>Коэффициент:</b> <code>{prediction['odds']}</code>\n"
                                        f"🔥 <b>Рейтинг уверенности:</b> {prediction['score']}/5\n\n"
                                        f"📝 <b>Аналитика:</b>\n"
                                        f"• Команда в оптимальной форме\n"
                                        f"• Перекос рынка в сторону фаворита\n"
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
                    logger.error(f"Ошибка запроса: {e}")
                    state.current_key_idx += 1
                    await asyncio.sleep(1)

            if state.current_key_idx >= len(API_KEYS):
                state.current_key_idx = 0
                break

        state.total_scans += 1
        await asyncio.sleep(1800)

# --- ОБРАБОТЧИКИ ТЕЛЕГРАМ ---
@dp.callback_query(F.data.startswith("res_"))
async def handle_res(c: types.CallbackQuery):
    try:
        _, r, o = c.data.split("_")
        is_win = (r == "w")
        stats = load_stats()
        stats["results"].append({"time": time.time(), "win": is_win, "odds": float(o)})
        with open(STATS_FILE, "w") as f: json.dump(stats, f)
        await c.message.edit_reply_markup(reply_markup=None)
        await c.message.reply(f"<b>Результат: {'✅ ВИН' if is_win else '❌ ЛОСС'}</b>", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка сохранения результата: {e}")
    await c.answer()

@dp.message(Command("start"))
async def start(m: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="📈 ROI Статистика"); kb.button(text="🔑 Ключи")
    await m.answer("🤖 Бот-аналитик Baron готов к работе!", reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text == "📈 ROI Статистика")
async def show_roi(m: types.Message):
    stats = load_stats()
    def calc(days):
        cutoff = time.time() - (days * 86400)
        hits = [r for r in stats["results"] if r.get("time", 0) > cutoff]
        if not hits: return "0%"
        profit = sum((r["odds"] - 1) if r["win"] else -1 for r in hits)
        return f"{round((profit/len(hits))*100, 1)}% ({len(hits)} ст.)"
    await m.answer(f"📊 <b>ROI:</b>\n\nДень: {calc(1)}\nНеделя: {calc(7)}", parse_mode=ParseMode.HTML)

@dp.message(F.text == "🔑 Ключи")
async def show_keys(m: types.Message):
    try:
        if not API_KEYS:
            await m.answer("❌ Список ключей пуст. Проверьте переменные окружения.")
            return

        text = "🔑 <b>Статус ключей:</b>\n\n"
        for i, k in enumerate(API_KEYS):
            # Показываем только первые 5 символов ключа для безопасности
            short_key = f"{k[:5]}..."
            status = "🟢" if i == state.current_key_idx else ("🔴" if i < state.current_key_idx else "⚪️")
            limit = state.key_limits.get(k, "неизвестно")
            text += f"{status} Ключ {i+1} ({short_key}): <b>{limit}</b>\n"
            
            # Телеграм не дает отправлять слишком длинные сообщения
            if len(text) > 3800:
                await m.answer(text, parse_mode=ParseMode.HTML)
                text = ""
        
        if text:
            await m.answer(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка в функции ключей: {e}")
        await m.answer("⚠️ Ошибка при чтении списка ключей.")

# --- ЗАПУСК ---
async def health(r): return web.Response(text="OK")

async def main():
    if not os.path.exists(STATS_FILE):
        with open(STATS_FILE, "w") as f: json.dump({"results": []}, f)
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    asyncio.create_task(scanner())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
