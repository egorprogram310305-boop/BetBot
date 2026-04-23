import os
import asyncio
import logging
import requests
import time
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiohttp import web
from googletrans import Translator

# --- НАСТРОЙКИ ЛОГИРОВАНИЯ ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ProBetBot")

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID")
RAW_KEYS = os.getenv("ODDS_API_KEYS", "")
API_KEYS = [k.strip() for k in RAW_KEYS.split(",") if k.strip()]

# Инициализация
translator = Translator()
bot = Bot(token=TOKEN)
dp = Dispatcher()

TRANSLATIONS_LEAGUES = {
    "soccer_epl": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Английская Премьер-лига",
    "soccer_germany_bundesliga": "🇩🇪 Немецкая Бундеслига",
    "soccer_italy_serie_a": "🇮🇹 Итальянская Серия А",
    "soccer_spain_la_liga": "🇪🇸 Испанская Ла Лига",
    "soccer_france_ligue_one": "🇫🇷 Французская Лига 1",
    "soccer_uefa_champs_league": "🇪🇺 Лига Чемпионов",
    "soccer_russia_premier_league": "🇷🇺 РПЛ"
}

class BotState:
    start_time = time.time()
    total_scans = 0
    key_limits = {key: "???" for key in API_KEYS}
    current_key_idx = 0

state = BotState()

# --- ФУНКЦИИ ПЕРЕВОДА И АНАЛИЗА ---
def safe_translate(text):
    try:
        return translator.translate(text, dest='ru').text
    except:
        return text

def get_best_prediction(event):
    bb = next((b for b in event['bookmakers'] if b['key'] == 'betboom'), None)
    if not bb: return None

    picks = []
    h2h = next((m for m in bb['markets'] if m['key'] == 'h2h'), None)
    totals = next((m for m in bb['markets'] if m['key'] == 'totals'), None)

    if h2h:
        for outcome in h2h['outcomes']:
            if 1.50 <= outcome['price'] <= 2.50:
                picks.append({
                    "pick": f"Победа: {safe_translate(outcome['name'])}",
                    "odds": outcome['price'],
                    "score": 4 if outcome['price'] < 1.8 else 3,
                    "reason": "Команда демонстрирует стабильный футбол и имеет преимущество по ключевым показателям."
                })

    if totals:
        t_over = next((o for o in totals['outcomes'] if o['name'] == 'Over' and o['point'] == 2.5), None)
        if t_over and 1.65 <= t_over['price'] <= 2.30:
            picks.append({
                "pick": "Тотал больше (2.5)",
                "odds": t_over['price'],
                "score": 3,
                "reason": "Ожидается открытая игра с большим количеством голевых моментов у обоих ворот."
            })

    return sorted(picks, key=lambda x: (x['score'], x['odds']), reverse=True)[0] if picks else None

# --- СКАНЕР ---
async def scanner():
    while True:
        logger.info(f"🔄 Запуск цикла сканирования №{state.total_scans + 1}")
        for league_key in TRANSLATIONS_LEAGUES.keys():
            if not API_KEYS: break
            key = API_KEYS[state.current_key_idx]
            url = f"https://api.the-odds-api.com/v4/sports/{league_key}/odds/"
            params = {'apiKey': key, 'regions': 'eu', 'markets': 'h2h,totals', 'bookmakers': 'betboom'}
            
            try:
                res = requests.get(url, params=params, timeout=15)
                if res.status_code == 200:
                    data = res.json()
                    state.key_limits[key] = res.headers.get('x-requests-remaining', '0')
                    for event in data:
                        best = get_best_prediction(event)
                        if best:
                            home_ru = safe_translate(event['home_team'])
                            away_ru = safe_translate(event['away_team'])
                            text = (
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"🏆 <b>{TRANSLATIONS_LEAGUES[league_key]}</b>\n"
                                f"⚽️ <b>{home_ru} — {away_ru}</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                                f"✅ <b>Прогноз:</b> <code>{best['pick']}</code>\n"
                                f"📈 <b>Коэффициент:</b> <code>{best['odds']}</code>\n"
                                f"🔥 <b>Уверенность:</b> {'🔥' * best['score']}\n\n"
                                f"📝 <b>Аналитика:</b> {best['reason']}\n\n"
                                f"💎 <b>Ставим тут:</b> <a href='https://betboom.ru'>Betboom</a>\n"
                                f"━━━━━━━━━━━━━━━━━━━━"
                            )
                            await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                            await asyncio.sleep(10)
                elif res.status_code in [401, 429]:
                    state.current_key_idx = (state.current_key_idx + 1) % len(API_KEYS)
            except Exception as e:
                logger.error(f"Ошибка API: {e}")
        
        state.total_scans += 1
        await asyncio.sleep(1800)

# --- ИНТЕРФЕЙС БОТА ---
@dp.message(Command("start"))
async def start_cmd(m: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="🔑 Мои Ключи"); kb.button(text="📊 Состояние системы")
    await m.answer("🦾 <b>Система анализа запущена!</b>", 
                   parse_mode=ParseMode.HTML, reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text == "🔑 Мои Ключи")
async def keys_info(m: types.Message):
    msg = "<b>Лимиты ключей:</b>\n"
    for i, k in enumerate(API_KEYS):
        status = "🟢" if i == state.current_key_idx else "⚪️"
        msg += f"{status} Ключ {i+1}: {state.key_limits.get(k, '0')}\n"
    await m.answer(msg, parse_mode=ParseMode.HTML)

@dp.message(F.text == "📊 Состояние системы")
async def status_info(m: types.Message):
    uptime = str(datetime.now() - datetime.fromtimestamp(state.start_time)).split('.')[0]
    await m.answer(f"📈 <b>Статус:</b>\n\n⏱ Uptime: {uptime}\n🔄 Циклов: {state.total_scans}", parse_mode=ParseMode.HTML)

# --- WEB SERVER ---
async def handle(r): return web.Response(text="OK")

async def main():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    
    asyncio.create_task(scanner())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
