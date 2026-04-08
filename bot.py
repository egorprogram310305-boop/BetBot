import os
import asyncio
import threading
import logging
import json
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, ContextTypes
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- НАСТРОЙКИ ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("CHAT_ID")
STATS_FILE = "stats.json"

# --- РОТАЦИЯ 5 КЛЮЧЕЙ ---
FOOTBALL_KEYS = [
    os.getenv("FOOTBALL_API_KEY_1"), os.getenv("FOOTBALL_API_KEY_2"),
    os.getenv("FOOTBALL_API_KEY_3"), os.getenv("FOOTBALL_API_KEY_4"),
    os.getenv("FOOTBALL_API_KEY_5")
]
FOOTBALL_KEYS = [k for k in FOOTBALL_KEYS if k]
current_key_idx = 0

def fetch_data(endpoint, params=None):
    global current_key_idx
    if not FOOTBALL_KEYS: return None
    url = f"https://v3.football.api-sports.io/{endpoint}"
    for _ in range(len(FOOTBALL_KEYS)):
        active_key = FOOTBALL_KEYS[current_key_idx]
        headers = {'x-apisports-key': active_key, 'x-rapidapi-host': 'v3.football.api-sports.io'}
        try:
            response = requests.get(url, headers=headers, params=params, timeout=20)
            res_json = response.json()
            if res_json.get("errors"):
                current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
                continue 
            current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
            return res_json
        except:
            current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
    return None

# --- СЕРВЕР И СТАТИСТИКА ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, *args): return

def run_health_server():
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), HealthHandler).serve_forever()

def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f: return json.load(f)
        except: pass
    return {"bank": 1000, "wins": 0, "losses": 0}

# --- СКАНЕР ULTIMATE v2.8.1 (FINAL) ---
async def scanner(bot):
    logger.info(f"🚀 СТАРТ v2.8.1. Ключей: {len(FOOTBALL_KEYS)}. Пауза: 10 сек.")
    while True:
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            data = await asyncio.to_thread(fetch_data, "fixtures", {"date": today, "timezone": "Europe/Moscow"})
            if not data or not data.get("response"):
                await asyncio.sleep(600); continue

            upcoming = [m for m in data['response'] if m['fixture']['status']['short'] == 'NS']
            
            for item in upcoming[:25]:
                f_id = item['fixture']['id']
                h_name, a_name = item['teams']['home']['name'], item['teams']['away']['name']
                
                # 1. ЗАПРОС КФ
                odds_data = await asyncio.to_thread(fetch_data, "odds", {"fixture": f_id, "bookmaker": 8})
                await asyncio.sleep(10) # Безопасность

                o_p1, o_p2, o_tb = None, None, None
                if odds_data and odds_data.get("response"):
                    try:
                        for bet in odds_data['response'][0]['bookmakers'][0]['bets']:
                            if bet['name'] == "Match Winner":
                                for v in bet['values']:
                                    if v['value'] == 'Home': o_p1 = float(v['odd'])
                                    if v['value'] == 'Away': o_p2 = float(v['odd'])
                            if bet['name'] == "Over/Under":
                                for v in bet['values']:
                                    if v['value'] == 'Over 2.5': o_tb = float(v['odd'])
                    except: pass

                # ФИЛЬТР КФ
                target = None
                if o_p1 and 1.70 <= o_p1 <= 2.50: target = ("П1", o_p1, "home")
                elif o_p2 and 1.70 <= o_p2 <= 2.50: target = ("П2", o_p2, "away")
                elif o_tb and 1.70 <= o_tb <= 2.30: target = ("ТБ 2.5", o_tb, "over")
                
                if not target: continue

                # 2. ЗАПРОС ПРОГНОЗА
                pred_data = await asyncio.to_thread(fetch_data, "predictions", {"fixture": f_id})
                await asyncio.sleep(10)
                if not pred_data or not pred_data.get("response"): continue
                
                res = pred_data['response'][0]
                comp = res['comparison']
                prob = res['predictions']['percent']
                advice = res['predictions']['advice']
                market, odd, side = target

                # Расчет Edge
                try:
                    p_val = float(prob[side].replace('%','')) / 100
                    edge = (odd / (1 / p_val)) - 1
                except: edge = 0

                # СТРОГАЯ ПРОВЕРКА ПО СЦЕНАРИЯМ
                if market == "П1":
                    f_h = float(comp['form']['home'].replace('%',''))
                    h2h_h = float(comp['h2h']['home'].replace('%',''))
                    if f_h >= 55 and h2h_h >= 50 and edge >= 0.06:
                        await process_signal(bot, h_name, a_name, market, odd, edge, f_h, h2h_h, advice)
                    else: logger.info(f"❌ П1 {h_name} мимо фильтра")

                elif market == "П2":
                    f_a = float(comp['form']['away'].replace('%',''))
                    h2h_a = float(comp['h2h']['away'].replace('%',''))
                    if f_a >= 55 and h2h_a >= 50 and edge >= 0.06:
                        await process_signal(bot, h_name, a_name, market, odd, edge, f_a, h2h_a, advice)
                    else: logger.info(f"❌ П2 {a_name} мимо фильтра")

                elif market == "ТБ 2.5":
                    if "over 2.5" in str(advice).lower() and edge >= 0.06:
                        await process_signal(bot, h_name, a_name, market, odd, edge, 0, 0, advice)
                    else: logger.info(f"❌ ТБ {h_name} мимо фильтра")

            # --- ВОТ ТВОЕ ИСПРАВЛЕНИЕ: 5400 секунд (90 минут) ---
            await asyncio.sleep(5400) 
        except Exception as e:
            logger.error(f"Error: {e}"); await asyncio.sleep(600)

async def process_signal(bot, h, a, market, odd, edge, form, h2h, advice):
    score = 0
    if form >= 75 and h2h >= 70: score += 1
    adv_low = str(advice).lower()
    is_adv = (market=="П1" and "home" in adv_low) or (market=="П2" and "away" in adv_low) or (market=="ТБ 2.5" and "over 2.5" in adv_low)
    if edge >= 0.12 or is_adv: score += 1
    percent = 3 + score
    stars = "⭐" * (score + 1)
    stats = load_stats()
    rub = round(stats['bank'] * (percent/100), 2)
    text = (
        f"💳 <b>MONSTER PRO: BETBOOM EDITION</b>\n\n"
        f"⚽️ {h} — {a}\n"
        f"🎯 Ставка: <b>{market}</b>\n"
        f"📊 Уверенность: [{stars}]\n"
        f"📈 Ориентир КФ: <b>{odd}</b>\n"
        f"🟢 Валуйность: <b>+{round(edge*100, 1)}%</b>\n"
        f"💰 Сумма ставки: <b>{percent}% ({rub}₽)</b>\n\n"
        f"⚠️ Инструкция: В BetBoom ставить, если КФ не ниже {round(odd - 0.05, 2)}"
    )
    kb = [[InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"w_{rub}_{odd}"),
           InlineKeyboardButton("❌ МИМО", callback_data=f"l_{rub}")]]
    await bot.send_message(ADMIN_ID, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    s = load_stats()
    d = query.data.split("_")
    if d[0] == "w":
        s["bank"] += float(d[1]) * (float(d[2]) - 1); s["wins"] += 1
    else:
        s["bank"] -= float(d[1]); s["losses"] += 1
    with open(STATS_FILE, "w") as f: json.dump(s, f)
    await query.edit_message_text(f"{query.message.text_html}\n\n<b>{'✅ ЗАШЛО' if d[0]=='w' else '❌ МИМО'}</b>", parse_mode="HTML")

async def post_init(app: Application):
    asyncio.create_task(scanner(app.bot))

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()


