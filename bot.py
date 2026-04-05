import os
import asyncio
import threading
import logging
import json
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
API_KEY = os.getenv("API_KEY")
STATS_FILE = "stats.json"

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- 1. СЕРВЕР ДЛЯ RENDER (HEALTH CHECK) ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    server = HTTPServer(('0.0.0.0', 10000), HealthHandler)
    server.serve_forever()

# --- 2. УПРАВЛЕНИЕ БАНКОМ ---
def load_stats():
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r") as f:
            try: return json.load(f)
            except: return {"bank": 1000, "wins": 0, "losses": 0}
    return {"bank": 1000, "wins": 0, "losses": 0}

def save_stats(stats):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

# --- 3. СКАНЕР (ЛОГИКА API-FOOTBALL) ---
async def scanner(bot):
    logging.info("🚀 Сканер API-Football запущен...")
    # API-Football использует эти заголовки на RapidAPI
    headers = {
        'x-rapidapi-host': "api-football-v1.p.rapidapi.com",
        'x-rapidapi-key': API_KEY
    }

    while True:
        try:
            # Шаг 1: Берем ближайшие матчи (Fixtures)
            url_fix = "https://api-football-v1.p.rapidapi.com/v3/fixtures?next=15"
            res_fix = requests.get(url_fix, headers=headers, timeout=15).json()
            
            if "response" in res_fix:
                for match in res_fix["response"]:
                    f_id = match['fixture']['id']
                    
                    # --- СТУПЕНЬ 1: АНАЛИЗ (PREDICTIONS) ---
                    url_pred = f"https://api-football-v1.p.rapidapi.com/v3/predictions?fixture={f_id}"
                    res_pred = requests.get(url_pred, headers=headers).json()
                    
                    if not res_pred.get("response"): continue
                    p_data = res_pred["response"][0]
                    
                    # Извлекаем вероятность и сравнение
                    prob_home = int(p_data['predictions']['percent']['home'].replace('%','')) / 100
                    comp = p_data['comparison']
                    
                    # Фильтр 1: Форма > 60% и H2H > 50%
                    h_form = int(comp['form']['home'].replace('%',''))
                    h_h2h = int(comp['h2h']['home'].replace('%',''))
                    
                    if h_form < 60 or h_h2h < 50: continue

                    # --- СТУПЕНЬ 2, 3, 4: ODDS & VALUE ---
                    url_odds = f"https://api-football-v1.p.rapidapi.com/v3/odds?fixture={f_id}"
                    res_odds = requests.get(url_odds, headers=headers).json()
                    
                    if not res_odds.get("response"): continue
                    
                    # Ищем маркет "Match Winner" у основного букмекера
                    bookie = res_odds["response"][0]["bookmakers"][0]
                    market = next((m for m in bookie['bets'] if m['id'] == 1), None) # ID 1 обычно Match Winner
                    if not market: continue
                    
                    current_p1 = next((float(o['odd']) for o in market['values'] if o['value'] == 'Home'), None)
                    
                    if current_p1 and 1.85 <= current_p1 <= 2.80:
                        fair_odd = 1 / prob_home
                        edge = (current_p1 / fair_odd) - 1
                        
                        # ЗЕЛЕНЫЙ СВЕТ: Если перевес > 5%
                        if edge >= 0.05:
                            stats = load_stats()
                            bet_amount = round(stats['bank'] * 0.05, 2)
                            
                            text = (
                                f"🔥 **MONSTER PRO: API-FOOTBALL**\n\n"
                                f"⚽️ {match['teams']['home']['name']} — {match['teams']['away']['name']}\n"
                                f"📈 КФ: **{current_p1}** (Edge: +{int(edge*100)}%)\n"
                                f"📊 Вероятность: {int(prob_home*100)}%\n"
                                f"🟢 Статус: **Зеленый свет**\n"
                                f"💰 Ставка: **{bet_amount}₽**"
                            )
                            
                            kb = [[InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{bet_amount}"),
                                   InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet_amount}")]]
                            
                            await bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
                            await asyncio.sleep(5) 

            await asyncio.sleep(1200) # Цикл 20 минут
        except Exception as e:
            logging.error(f"Scanner Error: {e}")
            await asyncio.sleep(60)

# --- 4. ОСТАЛЬНОЙ ФУНКЦИОНАЛ ---
async def status_monitor(bot):
    while True:
        try:
            if ADMIN_ID:
                await bot.send_message(chat_id=ADMIN_ID, text=f"🔔 Бот в строю 🟢 [{datetime.now().strftime('%H:%M')]")
        except: pass
        await asyncio.sleep(3600)

async def start_cmd(update, context):
    await update.message.reply_text("✅ Бот активен! Жду валуйные сигналы по стратегии Monster PRO.")

async def stats_cmd(update, context):
    d = load_stats()
    await update.message.reply_text(f"📊 Статистика:\n💰 Банк: {d['bank']}₽\nПобед: {d['wins']} | Поражений: {d['losses']}")

async def handle_callback(update, context):
    query = update.callback_query
    await query.answer()
    data = load_stats()
    action, amt = query.data.split("_")
    amt = float(amt)
    if action == "win":
        data["bank"] += amt * 0.9
        data["wins"] += 1
    else:
        data["bank"] -= amt
        data["losses"] += 1
    save_stats(data)
    await query.edit_message_text(text=f"{query.message.text}\n\n📊 Данные обновлены!")

async def post_init(app: Application):
    asyncio.create_task(scanner(app.bot))
    asyncio.create_task(status_monitor(app.bot))

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    if not TOKEN: return
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
