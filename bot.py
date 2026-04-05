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

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
API_KEY = os.getenv("API_KEY")
STATS_FILE = "stats.json"

# --- 1. СЕРВЕР ДЛЯ RENDER ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    server = HTTPServer(('0.0.0.0', 10000), HealthHandler)
    server.serve_forever()

# --- 2. БАНК И СТАТИСТИКА ---
def load_stats():
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r") as f:
            try: return json.load(f)
            except: return {"bank": 1000, "wins": 0, "losses": 0}
    return {"bank": 1000, "wins": 0, "losses": 0}

def save_stats(stats):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

# --- 3. СКАНЕР (6 СТУПЕНЕЙ АНАЛИЗА) ---
async def scanner(bot):
    logging.info("🚀 Monster PRO: Сканирование запущено...")
    headers = {
        'x-rapidapi-host': "api-football-v1.p.rapidapi.com",
        'x-rapidapi-key': API_KEY
    }

    while True:
        try:
            res_fix = requests.get("https://api-football-v1.p.rapidapi.com/v3/fixtures?next=15", headers=headers, timeout=15).json()
            
            if "response" in res_fix:
                for match in res_fix["response"]:
                    f_id = match['fixture']['id']
                    
                    # СТУПЕНЬ 1: МАТРИЦА (Форма + H2H)
                    res_pred = requests.get(f"https://api-football-v1.p.rapidapi.com/v3/predictions?fixture={f_id}", headers=headers).json()
                    if not res_pred.get("response"): continue
                    p_data = res_pred["response"][0]
                    
                    prob_home = int(p_data['predictions']['percent']['home'].replace('%','')) / 100
                    comp = p_data['comparison']
                    
                    # Фильтр формы и личных встреч
                    if int(comp['form']['home'].replace('%','')) < 60 or int(comp['h2h']['home'].replace('%','')) < 50:
                        continue

                    # СТУПЕНЬ 2-4: ODDS & VALUE
                    res_odds = requests.get(f"https://api-football-v1.p.rapidapi.com/v3/odds?fixture={f_id}", headers=headers).json()
                    if not res_odds.get("response"): continue
                    
                    bookie = res_odds["response"][0]["bookmakers"][0]
                    market = next((m for m in bookie['bets'] if m['id'] == 1), None)
                    if not market: continue
                    
                    current_p1 = next((float(o['odd']) for o in market['values'] if o['value'] == 'Home'), None)
                    
                    if current_p1 and 1.85 <= current_p1 <= 2.80:
                        fair_odd = 1 / prob_home
                        edge = (current_p1 / fair_odd) - 1
                        
                        # ЗЕЛЕНЫЙ СВЕТ (Пункт 4)
                        if edge >= 0.05:
                            # ПУНКТ 6: ДИНАМИЧЕСКИЙ БАНК
                            stats = load_stats()
                            bank = stats['bank']
                            
                            if edge > 0.15 and prob_home > 0.65:
                                confidence = "ВЫСОКАЯ 🔥"
                                percent = 0.07
                            elif edge >= 0.08:
                                confidence = "СРЕДНЯЯ ⚡️"
                                percent = 0.05
                            else:
                                confidence = "НИЗКАЯ ⚠️"
                                percent = 0.02
                            
                            bet_amount = round(bank * percent, 2)
                            
                            text = (
                                f"🔥 **MONSTER PRO: SIGNAL**\n\n"
                                f"⚽️ {match['teams']['home']['name']} — {match['teams']['away']['name']}\n"
                                f"📈 КФ: **{current_p1}**\n"
                                f"📊 Вероятность: {int(prob_home*100)}%\n"
                                f"🟢 Перевес: +{int(edge*100)}%\n"
                                f"🛡 Уверенность: **{confidence}**\n"
                                f"💰 Ставка: **{bet_amount}₽** ({int(percent*100)}%)"
                            )
                            
                            kb = [[InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{bet_amount}"),
                                   InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet_amount}")]]
                            
                            await bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
                            await asyncio.sleep(10)

            await asyncio.sleep(1200)
        except Exception as e:
            logging.error(f"Ошибка сканера: {e}")
            await asyncio.sleep(60)

# --- 4. МОНИТОРИНГ И КОМАНДЫ ---
async def status_monitor(bot):
    while True:
        try:
            if ADMIN_ID:
                # ОШИБКА ИСПРАВЛЕНА ТУТ: Убрана лишняя скобка в f-строке
                time_now = datetime.now().strftime('%H:%M')
                await bot.send_message(chat_id=ADMIN_ID, text=f"🔔 Бот активен 🟢 [{time_now}]")
        except Exception as e:
            logging.error(f"Ошибка монитора: {e}")
        await asyncio.sleep(3600)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот Monster PRO запущен и работает по 6 ступеням анализа!")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = load_stats()
    await update.message.reply_text(f"📊 Банк: {d['bank']}₽\nПобед: {d['wins']} | Поражений: {d['losses']}")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await query.edit_message_text(text=f"{query.message.text}\n\n📊 Итог сохранен в статистику!")

# --- 5. ЗАПУСК ---
async def post_init(app: Application):
    asyncio.create_task(scanner(app.bot))
    asyncio.create_task(status_monitor(app.bot))

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    
    if not TOKEN:
        logging.error("BOT_TOKEN не найден!")
        return

    app = Application.builder().token(TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    logging.info("🤖 Бот запускает polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
