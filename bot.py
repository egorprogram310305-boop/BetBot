import os
import asyncio
import threading
import logging
import json
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- ЛОГИРОВАНИЕ ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("CHAT_ID")
STATS_FILE = "stats.json"

# --- РОТАЦИЯ 5 КЛЮЧЕЙ ---
FOOTBALL_KEYS = [os.getenv(f"FOOTBALL_API_KEY_{i}") for i in range(1, 6) if os.getenv(f"FOOTBALL_API_KEY_{i}")]
current_key_idx = 0

def fetch_data(endpoint, params=None):
    global current_key_idx
    if not FOOTBALL_KEYS: return None
    url = f"https://v3.football.api-sports.io/{endpoint}"
    for _ in range(len(FOOTBALL_KEYS)):
        headers = {'x-apisports-key': FOOTBALL_KEYS[current_key_idx], 'x-rapidapi-host': 'v3.football.api-sports.io'}
        try:
            response = requests.get(url, headers=headers, params=params, timeout=20)
            res_json = response.json()
            current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
            if res_json.get("errors"): continue
            return res_json
        except:
            current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
    return None

# --- СТАТИСТИКА И СЕРВЕР ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, *args): return

def run_health_server():
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), HealthHandler).serve_forever()

def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f: return json.load(f)
        except: pass
    return {"bank": 1000.0, "wins": 0, "losses": 0}

def save_stats(s):
    with open(STATS_FILE, "w") as f: json.dump(s, f)

# --- КОМАНДЫ ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) == str(ADMIN_ID):
        await update.message.reply_text("🚀 <b>Monster Pro v2.8.3 запущен!</b>\nСканирую линию. Жди сигналов.\n\n/stats — проверить банк", parse_mode="HTML")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) == str(ADMIN_ID):
        s = load_stats()
        roi = round(((s['bank'] - 1000)/10), 1)
        text = (f"📊 <b>СТАТИСТИКА:</b>\n\n💰 Банк: <b>{round(s['bank'], 2)}₽</b>\n"
                f"✅ Вин: {s['wins']} | ❌ Луз: {s['losses']}\n📈 Профит: {roi}%")
        await update.message.reply_text(text, parse_mode="HTML")

# --- СКАНЕР ---
async def scanner(bot):
    logger.info(f"✅ Сканер ожил. Ключей в работе: {len(FOOTBALL_KEYS)}")
    while True:
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            data = await asyncio.to_thread(fetch_data, "fixtures", {"date": today, "timezone": "Europe/Moscow"})
            
            if not data or not data.get("response"):
                await asyncio.sleep(600); continue

            upcoming = [m for m in data['response'] if m['fixture']['status']['short'] == 'NS']
            logger.info(f"🔎 Нашел {len(upcoming)} матчей. Начинаю проверку...")

            for item in upcoming[:25]:
                f_id = item['fixture']['id']
                h_n, a_n = item['teams']['home']['name'], item['teams']['away']['name']
                
                # 1. Запрос коэффициентов
                odds_data = await asyncio.to_thread(fetch_data, "odds", {"fixture": f_id, "bookmaker": 8})
                await asyncio.sleep(10)

                o_p1, o_p2, o_tb = None, None, None
                if odds_data and odds_data.get("response"):
                    try:
                        for b in odds_data['response'][0]['bookmakers'][0]['bets']:
                            if b['name'] == "Match Winner":
                                for v in b['values']:
                                    if v['value'] == 'Home': o_p1 = float(v['odd'])
                                    if v['value'] == 'Away': o_p2 = float(v['odd'])
                            if b['name'] == "Over/Under":
                                for v in b['values']:
                                    if v['value'] == 'Over 2.5': o_tb = float(v['odd'])
                    except: pass

                target = None
                if o_p1 and 1.70 <= o_p1 <= 2.50: target = ("П1", o_p1, "home")
                elif o_p2 and 1.70 <= o_p2 <= 2.50: target = ("П2", o_p2, "away")
                elif o_tb and 1.70 <= o_tb <= 2.30: target = ("ТБ 2.5", o_tb, "over")
                
                if not target:
                    logger.info(f" ⏩ {h_n}: КФ не в диапазоне.")
                    continue

                # 2. Запрос прогноза
                pred_data = await asyncio.to_thread(fetch_data, "predictions", {"fixture": f_id})
                await asyncio.sleep(10)
                if not pred_data or not pred_data.get("response"): continue
                
                res = pred_data['response'][0]
                prob = res['predictions']['percent']
                market, odd, side = target
                
                try:
                    p_val = float(prob[side].replace('%','')) / 100
                    edge = (odd / (1 / p_val)) - 1
                except: edge = 0

                # Фильтр валуя (Edge >= 6% для всех)
                if edge < 0.06:
                    logger.info(f" ❌ {h_n}: Низкий Edge ({round(edge*100,1)}%)")
                    continue

                # Фильтры сценариев
                advice = res['predictions']['advice']
                comp = res['comparison']
                
                if market in ["П1", "П2"]:
                    f_val = float(comp['form'][side].replace('%',''))
                    h2h_val = float(comp['h2h'][side].replace('%',''))
                    if f_val >= 55 and h2h_val >= 50:
                        await process_signal(bot, h_n, a_n, market, odd, edge, f_val, h2h_val, advice)
                    else: logger.info(f" ❌ {h_n}: Слабая форма/H2H")
                
                elif market == "ТБ 2.5":
                    if "over 2.5" in str(advice).lower():
                        await process_signal(bot, h_n, a_n, market, odd, edge, 0, 0, advice)
                    else: logger.info(f" ❌ {h_n}: API не советует ТБ")

            logger.info("📡 Цикл завершен. Сон 90 минут.")
            await asyncio.sleep(5400)
        except Exception as e:
            logger.error(f"Scanner Error: {e}"); await asyncio.sleep(600)

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
    
    text = (f"💳 <b>MONSTER PRO: BETBOOM</b>\n\n⚽️ {h} — {a}\n🎯 Ставка: <b>{market}</b>\n"
            f"📊 Уверенность: [{stars}]\n📈 КФ: <b>{odd}</b> | Валуй: <b>+{round(edge*100, 1)}%</b>\n"
            f"💰 Сумма: <b>{percent}% ({rub}₽)</b>\n\n⚠️ Не ниже {round(odd - 0.05, 2)}")
    
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
    save_stats(s)
    await query.edit_message_text(f"{query.message.text_html}\n\n<b>{'✅ ЗАШЛО' if d[0]=='w' else '❌ МИМО'}</b>", parse_mode="HTML")

async def post_init(app: Application):
    asyncio.create_task(scanner(app.bot))

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()

