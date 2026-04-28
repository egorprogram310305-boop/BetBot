import os
import asyncio
import logging
import requests
import time
import json
import random
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiohttp import web
from deep_translator import GoogleTranslator

# --- НАСТРОЙКИ ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Baron_V3_Dynamic")

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID")
API_KEYS = [k.strip() for k in os.getenv("ODDS_API_KEYS", "").split(",") if k.strip()]
STATS_FILE = "stats.json"

bot = Bot(token=TOKEN)
dp = Dispatcher()

USER_AGENTS = [
    'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'
]

class BotState:
    current_key_idx = 0
    total_scans = 0
    key_limits = {}
    sent_events = set()

state = BotState()

# --- УМНЫЙ МОДУЛЬ АНАЛИЗА (ИДЕЯ №3) ---
def analyze_style_and_stats(home_team, away_team):
    """
    Определяет стиль команды и выбирает лучший рынок для ставки.
    """
    try:
        headers = {'User-Agent': random.choice(USER_AGENTS)}
        # Один запрос для получения общей картины
        query = f"{home_team} vs {away_team} last matches goals results"
        res = requests.get(f"https://www.google.com/search?q={query}", headers=headers, timeout=7)
        content = res.text.lower()

        # 1. Проверка на кризис
        if content.count('loss') >= 3 or content.count(' l l l ') >= 1:
            return None, "Кризис формы"

        # 2. Оценка результативности (упрощенный скоринг)
        # Считаем количество упоминаний результативных матчей (2-1, 2-2, 3-1 и т.д.)
        high_score_matches = content.count('2-') + content.count('3-') + content.count('4-')
        low_score_matches = content.count('0-0') + content.count('1-0') + content.count('0-1')

        if high_score_matches > low_score_matches + 2:
            return "ATTACK", "Атакующий стиль: Фаворит много забивает"
        elif low_score_matches > high_score_matches:
            return "DEFENSE", "Прагматичный стиль: Упор на оборону"
        else:
            return "BALANCED", "Сбалансированный стиль"
            
    except:
        return "BALANCED", "Анализ стиля временно недоступен"

def safe_translate(text):
    try: return GoogleTranslator(source='en', target='ru').translate(text)
    except: return text

def load_stats():
    if not os.path.exists(STATS_FILE): return {"results": [], "balance": 0}
    try:
        with open(STATS_FILE, "r") as f: return json.load(f)
    except: return {"results": [], "balance": 0}

# --- ДИНАМИЧЕСКАЯ ЛОГИКА ОТБОРА ---
def get_dynamic_prediction(event, league_key):
    bookies = event.get('bookmakers', [])
    if not bookies: return None
    
    market = next((m for m in bookies[0]['markets'] if m['key'] == 'h2h'), None)
    if not market: return None

    home_team = event['home_team']
    away_team = event['away_team']

    for outcome in market['outcomes']:
        price = outcome['price']
        if 1.55 <= price <= 2.25:
            # Базовые проверки пройдены, идем в глубокий анализ
            style, style_desc = analyze_style_and_stats(home_team, away_team)
            
            if not style: continue # Пропускаем если кризис

            # Динамический выбор рынка:
            if style == "ATTACK":
                # Если команда атакующая, берем ИТБ 1 (коэффициент обычно на 15-20% ниже П1)
                final_odds = round(price * 0.82, 2)
                bet_type = f"ИТБ (1) на {safe_translate(outcome['name'])}"
                note = "🔥 Высокая результативность. Страховка на голы."
            else:
                # В остальных случаях берем Фору (0)
                final_odds = round(price * 0.72, 2)
                bet_type = f"Фора (0) на {safe_translate(outcome['name'])}"
                note = "🛡 Прагматичный футбол. Страховка от ничьей."

            if final_odds < 1.30: final_odds = 1.38

            return {
                "pick": bet_type,
                "odds": final_odds,
                "style_note": note,
                "home": safe_translate(home_team),
                "away": safe_translate(away_team),
                "id": event['id']
            }
    return None

# --- СКАНЕР ---
async def scanner():
    leagues = ["soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", 
               "soccer_spain_la_liga", "soccer_france_ligue_one", "soccer_uefa_champs_league"]
    
    while True:
        for league_key in leagues:
            if state.current_key_idx >= len(API_KEYS): break
            key = API_KEYS[state.current_key_idx]
            url = f"https://api.the-odds-api.com/v4/sports/{league_key}/odds/"
            try:
                res = requests.get(url, params={'apiKey': key, 'regions': 'eu', 'markets': 'h2h'}, timeout=10)
                if res.status_code == 200:
                    events = res.json()
                    for event in events:
                        if event['id'] in state.sent_events: continue
                        
                        commence = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                        diff = (commence - datetime.now(timezone.utc)).total_seconds() / 3600
                        
                        if 0 < diff <= 6:
                            pred = get_dynamic_prediction(event, league_key)
                            if pred:
                                state.sent_events.add(event['id'])
                                kb = InlineKeyboardBuilder()
                                kb.button(text="💰 30₽", callback_data=f"st_30_{pred['odds']}")
                                kb.button(text="💰 50₽", callback_data=f"st_50_{pred['odds']}")
                                kb.button(text="⏭ Пропустить", callback_data="skip")
                                
                                text = (
                                    f"🧬 <b>ДИНАМИЧЕСКИЙ АНАЛИЗ V3</b>\n"
                                    f"⚽️ <b>{pred['home']} — {pred['away']}</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━\n"
                                    f"🎯 <b>Ставка:</b> <code>{pred['pick']}</code>\n"
                                    f"📈 <b>Коэффициент:</b> <code>{pred['odds']}</code>\n\n"
                                    f"📊 <b>Вердикт:</b>\n{pred['style_note']}\n"
                                    f"━━━━━━━━━━━━━━━━━━━━"
                                )
                                await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())
                                await asyncio.sleep(3)
                elif res.status_code in [401, 429]:
                    state.current_key_idx = (state.current_key_idx + 1) % len(API_KEYS)
            except: pass
            await asyncio.sleep(5)
            
        state.total_scans += 1
        await asyncio.sleep(1200)

# --- ИНТЕРФЕЙС И БАЛАНС ---
@dp.callback_query(F.data.startswith("st_"))
async def place_bet(c: types.CallbackQuery):
    _, amount, odds = c.data.split("_")
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ ВИН", callback_data=f"res_w_{amount}_{odds}")
    kb.button(text="🔄 ВОЗВРАТ", callback_data=f"res_r_{amount}_{odds}")
    kb.button(text="❌ ЛОСС", callback_data=f"res_l_{amount}_{odds}")
    await c.message.edit_reply_markup(reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("res_"))
async def settle_bet(c: types.CallbackQuery):
    _, result, amount, odds = c.data.split("_")
    amount, odds = float(amount), float(odds)
    stats = load_stats()
    profit = (amount * (odds - 1)) if result == "w" else (-amount if result == "l" else 0)
    stats["results"].append({"win": result, "profit": profit, "time": time.time()})
    stats["balance"] = stats.get("balance", 0) + profit
    with open(STATS_FILE, "w") as f: json.dump(stats, f)
    await c.message.edit_text(c.message.text + f"\n\n📊 Итог: {'✅ ПЛЮС' if profit > 0 else ('❌ МИНУС' if profit < 0 else '🔄 ВОЗВРАТ')}")

@dp.callback_query(F.data == "skip")
async def skip_match(c: types.CallbackQuery):
    await c.message.delete()

@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="📈 Статистика профита")
    await m.answer("🤖 Baron V3: Динамический выбор стратегии активирован!", reply_markup=kb.as_markup(resize_keyboard=True))

@dp.message(F.text == "📈 Статистика профита")
async def show_stats(m: types.Message):
    stats = load_stats()
    await m.answer(f"💰 <b>Ваш профит:</b> {round(stats.get('balance', 0), 2)}₽\nВсего прогнозов: {len(stats['results'])}", parse_mode=ParseMode.HTML)

async def main():
    if not os.path.exists(STATS_FILE):
        with open(STATS_FILE, "w") as f: json.dump({"results": [], "balance": 0}, f)
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    asyncio.create_task(scanner())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
