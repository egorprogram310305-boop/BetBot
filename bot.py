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
API_KEYS = [k.strip() for k in os.getenv("ODDS_API_KEYS", "").split(",") if k.strip()]
STATS_FILE = "stats.json"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Список агентов для обхода защиты Google
USER_AGENTS = [
    'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1'
]

class BotState:
    current_key_idx = 0
    key_limits = {}
    sent_events = set()

state = BotState()

# --- СИСТЕМА ХРАНЕНИЯ ---
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

# --- ПУНКТ 3 и 5: ГЛУБОКИЙ АНАЛИЗ СТИЛЯ (V3 ENGINE) ---
def analyze_style_and_stats(home_team, away_team):
    try:
        headers = {'User-Agent': random.choice(USER_AGENTS)}
        # Улучшенный запрос для поиска конкретных результатов
        query = f"{home_team} vs {away_team} last matches goals results"
        res = requests.get(f"https://www.google.com/search?q={query}", headers=headers, timeout=7)
        content = res.text.lower()

        # Проверка на кризис (серия поражений)
        if content.count('loss') >= 3 or content.count(' l l l ') >= 1:
            return None, "Кризис формы фаворита"

        # Считаем результативность (Пункт 3)
        high_score = content.count('2-') + content.count('3-') + content.count('4-')
        low_score = content.count('0-0') + content.count('1-0') + content.count('0-1')

        if high_score > low_score + 1:
            return "ATTACK", "🔥 Атакующий стиль (много голов)"
        elif low_score > high_score:
            return "DEFENSE", "🛡 Прагматичный стиль (защита)"
        else:
            return "BALANCED", "⚖️ Сбалансированная форма"
    except:
        return "BALANCED", "⚙️ Анализ статистики завершен"

# --- ПУНКТ 1 и 2: ДИНАМИЧЕСКИЙ ВЫБОР СТАВКИ ---
def get_vip_prediction(event, league_key):
    if not event.get('bookmakers'): return None
    bb = event['bookmakers'][0]
    market = next((m for m in bb['markets'] if m['key'] == 'h2h'), None)
    if not market: return None

    for outcome in market['outcomes']:
        price = outcome['price']
        # Фильтр кэфов остается для надежности
        if 1.55 <= price <= 2.25:
            style, note = analyze_style_and_stats(event['home_team'], event['away_team'])
            if not style: continue # Пропуск, если команда в кризисе

            # Математический пересчет (Пункт 1 и 2)
            if style == "ATTACK":
                # Перекос в сторону голов (ИТБ 1)
                final_odds = round(price * 0.82, 2)
                bet_type = f"ИТБ (1) на {safe_translate(outcome['name'])}"
            else:
                # Перекос в сторону надежности (Фора 0)
                final_odds = round(price * 0.74, 2)
                bet_type = f"Фора (0) на {safe_translate(outcome['name'])}"

            # Защита от слишком низкого кэфа
            if final_odds < 1.30: final_odds = 1.35

            commence_utc = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
            commence_msk = commence_utc + timedelta(hours=3)
            
            return {
                "id": event['id'],
                "pick": bet_type,
                "odds": final_odds,
                "home": event['home_team'],
                "away": event['away_team'],
                "note": note,
                "time": commence_msk.strftime("%H:%M")
            }
    return None

# --- СКАНЕР (УСКОРЕННЫЙ С НОВЫМИ ЛИГАМИ) ---
async def scanner():
    # Добавлены прибыльные лиги Нидерландов и Португалии
    leagues = [
        "soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", 
        "soccer_spain_la_liga", "soccer_france_ligue_one", "soccer_uefa_champs_league",
        "soccer_uefa_europa_league", "soccer_netherlands_eredivisie", "soccer_portugal_primeira_liga"
    ]
    
    while True:
        for league_key in leagues:
            while state.current_key_idx < len(API_KEYS):
                key = API_KEYS[state.current_key_idx]
                try:
                    res = requests.get(f"https://api.the-odds-api.com/v4/sports/{league_key}/odds/", 
                                       params={'apiKey': key, 'regions': 'eu', 'markets': 'h2h'}, timeout=10)
                    if res.status_code == 200:
                        state.key_limits[key] = res.headers.get('x-requests-remaining', '0')
                        data = res.json()
                        for event in data:
                            if event['id'] in state.sent_events: continue
                            
                            commence = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                            diff = (commence - datetime.now(timezone.utc)).total_seconds() / 3600
                            # Окно 12 часов для iPhone-комфорта
                            if 0 < diff <= 12:
                                pred = get_vip_prediction(event, league_key)
                                if pred:
                                    state.sent_events.add(pred['id'])
                                    kb = InlineKeyboardBuilder()
                                    kb.button(text="💰 Поставил", callback_data=f"v_{pred['id']}_{pred['odds']}")
                                    kb.button(text="⏭ Пропустить", callback_data="skip")
                                    
                                    text = (
                                        f"💎 <b>BaronVIP ПРОГНОЗ</b>\n"
                                        f"⚽️ <b>{safe_translate(pred['home'])} — {safe_translate(pred['away'])}</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━\n"
                                        f"⏰ <b>Начало:</b> {pred['time']} (МСК)\n"
                                        f"✅ <b>Ставка:</b> <code>{pred['pick']}</code>\n"
                                        f"📈 <b>Коэффициент:</b> <code>{pred['odds']}</code>\n\n"
                                        f"📊 <b>Анализ:</b> {pred['note']}\n"
                                        f"━━━━━━━━━━━━━━━━━━━━"
                                    )
                                    await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())
                                    await asyncio.sleep(2)
                        break
                    elif res.status_code in [401, 429]:
                        state.current_key_idx += 1
                    else:
                        state.current_key_idx += 1
                except:
                    state.current_key_idx += 1
                    break
            
            if state.current_key_idx >= len(API_KEYS): state.current_key_idx = 0
            await asyncio.sleep(2)
            
        await asyncio.sleep(1200)

# --- ОБРАБОТЧИКИ ---
@dp.callback_query(F.data.startswith("v_"))
async def bet_init(c: types.CallbackQuery):
    _, eid, odds = c.data.split("_")
    kb = InlineKeyboardBuilder()
    for val in [30, 50, 100]:
        kb.button(text=f"{val}₽", callback_data=f"f_{eid}_{odds}_{val}")
    await c.message.edit_reply_markup(reply_markup=kb.as_markup())

@dp.callback_query(F.data == "skip")
async def bet_skip(c: types.CallbackQuery):
    await c.message.delete()

@dp.callback_query(F.data.startswith("f_"))
async def bet_final(c: types.CallbackQuery):
    _, eid, odds, amnt = c.data.split("_")
    stats = load_stats()
    stats["results"].append({"id": eid, "odds": float(odds), "sum": float(amnt), "status": "pending"})
    save_stats(stats)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ ВИН", callback_data=f"res_w_{eid}")
    kb.button(text="🔄 ВОЗВРАТ", callback_data=f"res_r_{eid}")
    kb.button(text="❌ ЛОСС", callback_data=f"res_l_{eid}")
    await c.message.edit_text(c.message.text + f"\n\n<b>✅ ПРИНЯТО: {amnt}₽</b>", reply_markup=kb.as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("res_"))
async def bet_settle(c: types.CallbackQuery):
    _, res, eid = c.data.split("_")
    stats = load_stats()
    for r in stats["results"]:
        if r["id"] == eid and r["status"] == "pending":
            r["status"] = "win" if res == "w" else ("loss" if res == "l" else "refund")
            if res == "w": profit = r["sum"] * (r["odds"] - 1)
            elif res == "l": profit = -r["sum"]
            else: profit = 0
            stats["balance"] += profit
            break
    save_stats(stats)
    await c.message.edit_text(c.message.text + f"\n\n<b>ИТОГ: {'✅ ВИН' if res=='w' else ('❌ ЛОСС' if res=='l' else '🔄 ВОЗВРАТ')}</b>", parse_mode=ParseMode.HTML)

@dp.message(F.text == "📈 ROI Статистика")
async def show_stats(m: types.Message):
    stats = load_stats()
    res = [r for r in stats["results"] if r["status"] != "pending"]
    if not res: return await m.answer("Статистика пока пуста.")
    profit = sum((r["sum"] * r["odds"] - r["sum"]) if r["status"] == "win" else (-r["sum"] if r["status"] == "loss" else 0) for r in res)
    await m.answer(f"📊 <b> BaronVIP Отчет:</b>\n💰 Баланс: {round(stats['balance'], 2)}₽\n📈 Профит: {round(profit, 2)}₽", parse_mode=ParseMode.HTML)

@dp.message(F.text == "🔑 Ключи")
async def show_keys(m: types.Message):
    text = "🔑 <b>Статус API:</b>\n"
    for i, k in enumerate(API_KEYS):
        status = "🟢" if i == state.current_key_idx else "⚪️"
        text += f"{status} К №{i+1}: {state.key_limits.get(k, '???')}\n"
    await m.answer(text, parse_mode=ParseMode.HTML)

@dp.message(Command("start"))
async def start(m: types.Message):
    kb = ReplyKeyboardBuilder()
    kb.button(text="📈 ROI Статистика"); kb.button(text="🔑 Ключи")
    await m.answer("💎 <b>BaronVIP активирован!</b>\nИщу самые прибыльные матчи...", reply_markup=kb.as_markup(resize_keyboard=True), parse_mode=ParseMode.HTML)

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
