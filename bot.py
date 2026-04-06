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

# Переменные окружения
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
API_KEY = os.getenv("API_KEY")
STATS_FILE = "stats.json"

# --- 1. СЕРВЕР ДЛЯ RENDER (Исправлен для ошибки 501) ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"OK") # Робот увидит этот ответ и успокоится
        except Exception as e:
            logging.error(f"Ошибка сервера Health-Check: {e}")

    def log_message(self, format, *args):
        return # Отключаем лишний лог запросов

def run_health_server():
    # Порт берем из системы или 10000
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logging.info(f"🌍 Health-Check сервер запущен на порту {port}")
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

# --- 3. СКАНЕР (Аналитика: Smart-Safe Mode) ---
async def scanner(bot):
    logging.info("🚀 Monster PRO: Smart-Safe сканирование запущено...")
    headers = {
        'x-rapidapi-host': "api-football-v1.p.rapidapi.com",
        'x-rapidapi-key': API_KEY
    }

    while True:
        try:
            # 1. Поиск ближайших матчей
            res_fix_response = await asyncio.to_thread(
                requests.get, "https://api-football-v1.p.rapidapi.com/v3/fixtures?next=15", 
                headers=headers, timeout=15
            )
            res_fix = res_fix_response.json()
            
            if "response" in res_fix:
                for match in res_fix["response"]:
                    f_id = match['fixture']['id']
                    
                    # 2. Прогнозы (Predictions)
                    res_pred_response = await asyncio.to_thread(
                        requests.get, f"https://api-football-v1.p.rapidapi.com/v3/predictions?fixture={f_id}", 
                        headers=headers
                    )
                    res_pred = res_pred_response.json()
                    if not res_pred.get("response"): continue
                    
                    p_data = res_pred["response"][0]
                    prob_home = int(p_data['predictions']['percent']['home'].replace('%','')) / 100
                    comp = p_data['comparison']
                    
                    # ФИЛЬТР: Форма 65%, H2H 50%
                    if int(comp['form']['home'].replace('%','')) < 65 or int(comp['h2h']['home'].replace('%','')) < 50:
                        continue

                    # 3. Коэффициенты (Odds)
                    res_odds_response = await asyncio.to_thread(
                        requests.get, f"https://api-football-v1.p.rapidapi.com/v3/odds?fixture={f_id}", 
                        headers=headers
                    )
                    res_odds = res_odds_response.json()
                    if not res_odds.get("response"): continue
                    
                    bookie = res_odds["response"][0]["bookmakers"][0]
                    market = next((m for m in bookie['bets'] if m['id'] == 1), None)
                    if not market: continue
                    
                    current_p1 = next((float(o['odd']) for o in market['values'] if o['value'] == 'Home'), None)
                    
                    if current_p1 and 1.85 <= current_p1 <= 2.80:
                        fair_odd = 1 / prob_home
                        edge = (current_p1 / fair_odd) - 1
                        
                        # ФИЛЬТР: Валуй 7%
                        if edge >= 0.07:
                            stats = load_stats()
                            bank = stats['bank']
                            
                            # Адаптивный стейкинг (Пункт 6)
                            if edge > 0.12 and prob_home > 0.60:
                                conf, perc = "ВЫСОКАЯ 🔥", 0.05
                            elif edge >= 0.09:
                                conf, perc = "СРЕДНЯЯ ⚡️", 0.04
                            else:
                                conf, perc = "УМЕРЕННАЯ 📈", 0.03
                            
                            bet_amount = round(bank * perc, 2)
                            text = (
                                f"🔥 **MONSTER PRO: SMART SIGNAL**\n\n"
                                f"⚽️ {match['teams']['home']['name']} — {match['teams']['away']['name']}\n"
                                f"📈 КФ: **{current_p1}**\n"
                                f"📊 Вероятность: {int(prob_home*100)}%\n"
                                f"🟢 Перевес: +{int(edge*100)}%\n"
                                f"🛡 Уверенность: **{conf}**\n"
                                f"💰 Ставка: **{bet_amount}₽** ({int(perc*100)}%)"
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
                time_now = datetime.now().strftime('%H:%M')
                await bot.send_message(chat_id=ADMIN_ID, text=f"🔔 Бот активен 🟢 [{time_now}]")
        except: pass
        await asyncio.sleep(3600)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот Monster PRO активен и ищет сигналы!")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = load_stats()
    await update.message.reply_text(f"📊 Банк: {d['bank']}₽\n✅ П: {d['wins']} | ❌ Л: {d['losses']}")

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
    await query.edit_message_text(text=f"{query.message.text}\n\n📊 Итог сохранен!")

async def post_init(app: Application):
    asyncio.create_task(scanner(app.bot))
    asyncio.create_task(status_monitor(app.bot))

def main():
    # Запускаем сервер в отдельном потоке
    server_thread = threading.Thread(target=run_health_server, daemon=True)
    server_thread.start()
    
    if not TOKEN:
        logging.error("BOT_TOKEN не найден!")
        return

    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    logging.info("🤖 Бот запущен и готов к работе.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
