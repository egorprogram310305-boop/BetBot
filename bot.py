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

# --- НАСТРОЙКИ ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ProBetBot")

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID")
API_KEYS = [k.strip() for k in os.getenv("ODDS_API_KEYS", "").split(",") if k.strip()]

# Словари для перевода (можно дополнять)
TRANSLATIONS = {
    "soccer_epl": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Английская Премьер-лига",
    "soccer_germany_bundesliga": "🇩🇪 Немецкая Бундеслига",
    "soccer_italy_serie_a": "🇮🇹 Итальянская Серия А",
    "soccer_spain_la_liga": "🇪🇸 Испанская Ла Лига",
    "soccer_france_ligue_one": "🇫🇷 Французская Лига 1",
    "soccer_uefa_champs_league": "🇪🇺 Лига Чемпионов УЕФА",
    "soccer_russia_premier_league": "🇷🇺 РПЛ"
}

bot = Bot(token=TOKEN)
dp = Dispatcher()

class BotState:
    start_time = time.time()
    total_scans = 0
    key_limits = {key: "???" for key in API_KEYS}
    current_key_idx = 0

state = BotState()

# --- ЛОГИКА ВЫБОРА ЛУЧШЕЙ СТАВКИ ---
def get_best_prediction(event):
    bb = next((b for b in event['bookmakers'] if b['key'] == 'betboom'), None)
    if not bb: return None

    potential_picks = []
    h2h = next((m for m in bb['markets'] if m['key'] == 'h2h'), None)
    totals = next((m for m in bb['markets'] if m['key'] == 'totals'), None)

    # 1. Оценка исходов (П1/П2)
    if h2h:
        for outcome in h2h['outcomes']:
            name = outcome['name']
            price = outcome['price']
            # Ищем фаворита с кэфом в рабочем диапазоне
            if 1.45 <= price <= 2.20:
                score = 3
                if price < 1.70: score += 1
                potential_picks.append({
                    "pick": f"Победа: {name}",
                    "odds": price,
                    "score": score,
                    "reason": f"Команда {name} имеет значительное игровое преимущество и стабильный состав на текущий тур."
                })

    # 2. Оценка тоталов (ТБ 2.5)
    if totals:
        t_over = next((o for o in totals['outcomes'] if o['name'] == 'Over' and o['point'] == 2.5), None)
        if t_over and 1.60 <= t_over['price'] <= 2.10:
            potential_picks.append({
                "pick": "Тотал больше (2.5)",
                "odds": t_over['price'],
                "score": 3,
                "reason": "Оба коллектива проповедуют атакующий стиль игры, что часто приводит к высокой результативности."
            })

    if not potential_picks: return None

    # Сортируем: сначала по баллу уверенности, потом по самому выгодному кэфу
    best = sorted(potential_picks, key=lambda x: (x['score'], x['odds']), reverse=True)[0]
    return best

# --- ЗАПРОСЫ ---
async def fetch_data(league):
    key = API_KEYS[state.current_key_idx]
    url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/"
    params = {'apiKey': key, 'regions': 'eu', 'markets': 'h2h,totals', 'bookmakers': 'betboom'}
    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code == 200:
            state.key_limits[key] = res.headers.get('x-requests-remaining', '0')
            return res.json()
        if res.status_code in [401, 429]:
            state.current_key_idx = (state.current_key_idx + 1) % len(API_KEYS)
    except: pass
    return None

# --- СКАНЕР ---
async def scanner():
    while True:
        for league_key in TRANSLATIONS.keys():
            data = await fetch_data(league_key)
            if not data: continue

            for event in data:
                best_bet = get_best_prediction(event)
                if best_bet and best_bet['score'] >= 3:
                    league_name = TRANSLATIONS.get(league_key, event['sport_title'])
                    
                    text = (
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏆 <b>{league_name}</b>\n"
                        f"⚽️ <b>{event['home_team']} — {event['away_team']}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"✅ <b>Прогноз:</b> <code>{best_bet['pick']}</code>\n"
                        f"📈 <b>Коэффициент:</b> <code>{best_bet['odds']}</code>\n"
                        f"🔥 <b>Уверенность:</b> {'🔥' * best_bet['score']}\n\n"
                        f"📝 <b>Аналитика:</b> {best_bet['reason']}\n\n"
                        f"💎 <b>Ставим тут:</b> <a href='https://betboom.ru'>Betboom</a>\n"
                        f"━━━━━━━━━━━━━━━━━━━━"
                    )
                    
                    try:
                        await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                        await asyncio.sleep(15)
                    except: pass
        
        state.total_scans += 1
        await asyncio.sleep(3600)

# --- ИНТЕРФЕЙС ---
@dp.message(Command("start"))
async def start(m: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="🔑 Мои Ключи"); kb.button(text="📊 Состояние системы")
    await m.answer("🦾 <b>Спортивный аналитик приветствует вас!</b>\nСистема мониторинга запущена.", 
                   parse_mode=ParseMode.HTML, reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text == "🔑 Мои Ключи")
async def keys_info(m: types.Message):
    msg = "<b>Доступные API ключи:</b>\n"
    for i, k in enumerate(API_KEYS):
        status = "🟢" if i == state.current_key_idx else "⚪️"
        msg += f"{status} <code>{k[:4]}...</code> | Остаток: {state.key_limits.get(k, '0')}\n"
    await m.answer(msg, parse_mode=ParseMode.HTML)

@dp.message(F.text == "📊 Состояние системы")
async def status_info(m: types.Message):
    uptime = str(datetime.now() - datetime.fromtimestamp(state.start_time)).split('.')[0]
    await m.answer(f"📈 <b>Статистика:</b>\n\n"
                   f"⏱ Время в сети: <code>{uptime}</code>\n"
                   f"🔄 Проверок лиг: <code>{state.total_scans}</code>\n"
                   f"📡 Активный узел: <code>Ключ №{state.current_key_idx + 1}</code>", 
                   parse_mode=ParseMode.HTML)

async def handle(r): return web.Response(text="Бот онлайн")

async def main():
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

