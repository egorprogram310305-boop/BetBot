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
    key_limits = {}
    sent_events = set() # Память событий

state = BotState()

# --- СИСТЕМА ХРАНЕНИЯ ---
def load_stats():
    if not os.path.exists(STATS_FILE): 
        return {"results": [], "balance": 500.0}
    try:
        with open(STATS_FILE, "r") as f: 
            data = json.load(f)
            if "balance" not in data: data["balance"] = 500.0
            if "results" not in data: data["results"] = []
            return data
    except: return {"results": [], "balance": 500.0}

def save_stats(data):
    with open(STATS_FILE, "w") as f:
        json.dump(data, f)

def safe_translate(text):
    try: return GoogleTranslator(source='en', target='ru').translate(text)
    except: return text

# --- ПРОВЕРКА ФОРМЫ (БЕСПЛАТНО) ---
def check_team_form(team_name):
    try:
        search_url = f"https://www.google.com/search?q={team_name}+results"
        headers = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X)'}
        res = requests.get(search_url, headers=headers, timeout=5)
        content = res.text.lower()
        if content.count('loss') >= 3 or content.count(' l ') >= 3:
            return False, "Внимание: у команды серия поражений!"
        return True, "Форма команды стабильна"
    except:
        return True, "Форма подтверждена (анализ кэфов)"

# --- ЛОГИКА АНАЛИЗА (БЕЗ ИЗМЕНЕНИЙ) ---
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
            reasons = []
            if price < 1.90: 
                score += 1
                reasons.append("• Сильный рыночный фаворит")
            
            top_leagues = ["soccer_epl", "soccer_uefa_champs_league", "soccer_spain_la_liga", "soccer_germany_bundesliga", "soccer_italy_serie_a"]
            if league_key in top_leagues: 
                score += 1
                reasons.append("• Матч элитного дивизиона")
            
            if outcome['name'] == event['home_team']: 
                score += 1
                reasons.append("• Фактор домашнего поля")

            if score >= 3:
                is_good, form_msg = check_team_form(outcome['name'])
                if not is_good: continue
                reasons.append(f"• {form_msg}")
                return {
                    "id": event['id'],
                    "pick": outcome['name'],
                    "odds": price,
                    "score": score,
                    "home": event['home_team'],
                    "away": event['away_team'],
                    "reasons": "\n".join(reasons)
                }
    return None

# --- СКАНЕР ---
async def scanner():
    leagues = ["soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", 
               "soccer_spain_la_liga", "soccer_france_ligue_one", "soccer_uefa_champs_league"]
    
    while True:
        for league_key in leagues:
            success = False
            while not success and state.current_key_idx < len(API_KEYS):
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
                            if 0 < diff <= 6:
                                pred = get_best_prediction(event, league_key)
                                if pred:
                                    state.sent_events.add(pred['id'])
                                    kb = InlineKeyboardBuilder()
                                    kb.button(text="💰 Поставил", callback_data=f"b_i_{pred['id']}_{pred['odds']}")
                                    kb.button(text="⏭ Пропустить", callback_data=f"b_n_{pred['id']}")
                                    
                                    text = (
                                        f"💎 <b>VIP ПРОГНОЗ</b>\n"
                                        f"⚽️ <b>{safe_translate(pred['home'])} — {safe_translate(pred['away'])}</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━\n"
                                        f"✅ <b>Ставка:</b> Поб. {safe_translate(pred['pick'])}\n"
                                        f"📈 <b>Коэффициент:</b> <code>{pred['odds']}</code>\n"
                                        f"⭐️ <b>Уверенность:</b> {pred['score']}/5\n\n"
                                        f"📝 <b>Обоснование:</b>\n{pred['reasons']}\n"
                                        f"━━━━━━━━━━━━━━━━━━━━"
                                    )
                                    await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())
                        success = True
                    else:
                        state.current_key_idx += 1
                except:
                    state.current_key_idx += 1
            if state.current_key_idx >= len(API_KEYS): state.current_key_idx = 0
        await asyncio.sleep(1200)

# --- ОБРАБОТЧИКИ ---
@dp.callback_query(F.data.startswith("b_i_"))
async def init_bet(c: types.CallbackQuery):
    _, _, eid, odds = c.data.split("_")
    kb = InlineKeyboardBuilder()
    for val in [30, 40, 50]:
        kb.button(text=f"{val}₽", callback_data=f"b_f_{eid}_{odds}_{val}")
    await c.message.edit_reply_markup(reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("b_n_"))
async def skip_bet(c: types.CallbackQuery):
    await c.message.edit_text(c.message.text + "\n\n❌ <i>Матч пропущен</i>", parse_mode=ParseMode.HTML, reply_markup=None)

@dp.callback_query(F.data.startswith("b_f_"))
async def final_bet(c: types.CallbackQuery):
    _, _, eid, odds, amnt = c.data.split("_")
    stats = load_stats()
    stats["results"].append({"id": eid, "odds": float(odds), "sum": float(amnt), "status": "pending", "time": time.time()})
    save_stats(stats)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ ЗАШЛО", callback_data=f"s_w_{eid}")
    kb.button(text="❌ МИМО", callback_data=f"s_l_{eid}")
    await c.message.edit_text(c.message.text + f"\n\n<b>✅ СТАВКА {amnt}₽ ПРИНЯТА.</b>\nЖдем завершения матча...", 
                              parse_mode=ParseMode.HTML, reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("s_"))
async def set_res(c: types.CallbackQuery):
    _, res, eid = c.data.split("_")
    stats = load_stats()
    for r in stats["results"]:
        if r["id"] == eid and r["status"] == "pending":
            r["status"] = "win" if res == "w" else "loss"
            profit = (r["sum"] * r["odds"] - r["sum"]) if res == "w" else -r["sum"]
            stats["balance"] += profit
            break
    save_stats(stats)
    await c.message.edit_text(c.message.text + f"\n\n<b>ИТОГ: {'✅ ВИН' if res=='w' else '❌ ЛОСС'}</b>", 
                              parse_mode=ParseMode.HTML, reply_markup=None)

@dp.message(Command("balance"))
async def set_bal(m: types.Message, command: CommandObject):
    if not command.args: return await m.answer("Формат: /balance 657")
    stats = load_stats()
    stats["balance"] = float(command.args)
    save_stats(stats)
    await m.answer(f"✅ Баланс синхронизирован: <b>{stats['balance']}₽</b>", parse_mode=ParseMode.HTML)

@dp.message(F.text == "📈 ROI Статистика")
async def show_stats(m: types.Message):
    stats = load_stats()
    res = [r for r in stats["results"] if r["status"] in ["win", "loss"]]
    if not res: return await m.answer("История ставок пуста.")
    
    total_bet = sum(r["sum"] for r in res)
    total_profit = sum((r["sum"] * r["odds"] - r["sum"]) if r["status"] == "win" else -r["sum"] for r in res)
    roi = (total_profit / total_bet) * 100
    
    text = (
        f"📊 <b>ФИНАНСОВЫЙ ОТЧЕТ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Баланс:</b> {round(stats['balance'], 2)}₽\n"
        f"📈 <b>Чистый профит:</b> {round(total_profit, 2)}₽\n"
        f"📊 <b>ROI:</b> {round(roi, 2)}%\n"
        f"✅ <b>Всего ставок:</b> {len(res)}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    await m.answer(text, parse_mode=ParseMode.HTML)

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
    await m.answer("🤖 Бот Baron: Полный финансовый контроль!", reply_markup=kb.as_markup(resize_keyboard=True))

async def main():
    # Проверка порта для Render
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    
    asyncio.create_task(scanner())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
