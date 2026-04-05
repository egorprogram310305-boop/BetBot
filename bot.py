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

# Настройка логирования для отслеживания работы
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Получаем переменные окружения
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
API_KEY = os.getenv("API_KEY")
STATS_FILE = "stats.json"

# --- 1. СЕРВЕР ДЛЯ RENDER (Чтобы бот не засыпал на хостинге) ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    server = HTTPServer(('0.0.0.0', 10000), HealthHandler)
    server.serve_forever()

# --- 2. БАНК И СТАТИСТИКА (Хранение данных) ---
def load_stats():
    """Загружает статистику из файла или создает новую, если файла нет."""
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r") as f:
            try: 
                return json.load(f)
            except: 
                return {"bank": 1000, "wins": 0, "losses": 0}
    return {"bank": 1000, "wins": 0, "losses": 0}

def save_stats(stats):
    """Сохраняет текущий прогресс в файл."""
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

# --- 3. СКАНЕР (Аналитика: Safe Mode) ---
async def scanner(bot):
    logging.info("🚀 Monster PRO: Безопасное сканирование запущено...")
    headers = {
        'x-rapidapi-host': "api-football-v1.p.rapidapi.com",
        'x-rapidapi-key': API_KEY
    }

    while True:
        try:
            # Делаем запрос в фоновом потоке, чтобы не блокировать Telegram-бота
            res_fix_response = await asyncio.to_thread(
                requests.get, 
                "https://api-football-v1.p.rapidapi.com/v3/fixtures?next=15", 
                headers=headers, 
                timeout=15
            )
            res_fix = res_fix_response.json()
            
            if "response" in res_fix:
                for match in res_fix["response"]:
                    f_id = match['fixture']['id']
                    
                    # СТУПЕНЬ 1: МАТРИЦА (Форма + H2H)
                    res_pred_response = await asyncio.to_thread(
                        requests.get, 
                        f"https://api-football-v1.p.rapidapi.com/v3/predictions?fixture={f_id}", 
                        headers=headers
                    )
                    res_pred = res_pred_response.json()
                    
                    if not res_pred.get("response"): continue
                    p_data = res_pred["response"][0]
                    
                    prob_home = int(p_data['predictions']['percent']['home'].replace('%','')) / 100
                    comp = p_data['comparison']
                    
                    # ПРАВИЛО SAFE MODE: Форма строго от 75%, H2H от 50%
                    if int(comp['form']['home'].replace('%','')) < 75 or int(comp['h2h']['home'].replace('%','')) < 50:
                        continue

                    # СТУПЕНЬ 2-4: ODDS & VALUE
                    res_odds_response = await asyncio.to_thread(
                        requests.get, 
                        f"https://api-football-v1.p.rapidapi.com/v3/odds?fixture={f_id}", 
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
                        
                        # ПРАВИЛО SAFE MODE: Перевес от 10%
                        if edge >= 0.10:
                            stats = load_stats()
                            bank = stats['bank']
                            
                            # ПРАВИЛО SAFE MODE: Ставки от 3% до 5%
                            if edge > 0.15 and prob_home > 0.65:
                                confidence = "ВЫСОКАЯ 🔥"
                                percent = 0.05
                            elif edge >= 0.12:
                                confidence = "СРЕДНЯЯ ⚡️"
                                percent = 0.04
                            else:
                                confidence = "НИЗКАЯ ⚠️"
                                percent = 0.03
                            
                            bet_amount = round(bank * percent, 2)
                            
                            text = (
                                f"🔥 **MONSTER PRO: SAFE SIGNAL**\n\n"
                                f"⚽️ {match['teams']['home']['name']} — {match['teams']['away']['name']}\n"
                                f"📈 КФ: **{current_p1}**\n"
                                f"📊 Вероятность: {int(prob_home*100)}%\n"
                                f"🟢 Перевес: +{int(edge*100)}%\n"
                                f"🛡 Уверенность: **{confidence}**\n"
                                f"💰 Реком. ставка: **{bet_amount}₽** ({int(percent*100)}%)"
                            )
                            
                            kb = [[InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{bet_amount}"),
                                   InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet_amount}")]]
                            
                            await bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
                            await asyncio.sleep(10) # Пауза, чтобы не спамить

            await asyncio.sleep(1200) # Ожидание 20 минут до следующего сканирования
        except Exception as e:
            logging.error(f"Ошибка сканера: {e}")
            await asyncio.sleep(60)

# --- 4. МОНИТОРИНГ И КОМАНДЫ (Общение с Telegram) ---
async def status_monitor(bot):
    """Каждый час отправляет сообщение о том, что бот работает."""
    while True:
        try:
            if ADMIN_ID:
                time_now = datetime.now().strftime('%H:%M')
                await bot.send_message(chat_id=ADMIN_ID, text=f"🔔 Бот активен 🟢 [{time_now}]")
        except Exception as e:
            logging.error(f"Ошибка монитора: {e}")
        await asyncio.sleep(3600)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отвечает на команду /start"""
    await update.message.reply_text("✅ Бот Monster PRO запущен и работает в безопасном режиме (Safe Mode)!")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отвечает на команду /stats и показывает баланс"""
    d = load_stats()
    await update.message.reply_text(f"📊 Банк: {d['bank']}₽\n✅ Побед: {d['wins']} | ❌ Поражений: {d['losses']}")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на кнопки под сигналами"""
    query = update.callback_query
    await query.answer()
    data = load_stats()
    
    # Разбираем данные с кнопки (например, "win_50.0")
    action, amt = query.data.split("_")
    amt = float(amt)
    
    if action == "win":
        data["bank"] += amt * 0.9 # Учитываем маржу и прибыль
        data["wins"] += 1
    else:
        data["bank"] -= amt       # Отнимаем проигранную сумму
        data["losses"] += 1
        
    save_stats(data)
    
    # Обновляем сообщение, чтобы кнопка исчезла
    await query.edit_message_text(text=f"{query.message.text}\n\n📊 Итог сохранен в статистику! Текущий банк: {round(data['bank'], 2)}₽")

# --- 5. ЗАПУСК БОТА ---
async def post_init(app: Application):
    """Запускает фоновые задачи после старта бота"""
    asyncio.create_task(scanner(app.bot))
    asyncio.create_task(status_monitor(app.bot))

def main():
    # Запуск сервера для хостинга
    threading.Thread(target=run_health_server, daemon=True).start()
    
    if not TOKEN:
        logging.error("КРИТИЧЕСКАЯ ОШИБКА: BOT_TOKEN не найден в переменных окружения!")
        return

    # Создание приложения бота
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    
    # Регистрация команд
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    logging.info("🤖 Бот успешно запущен и перешел в режим ожидания (polling)...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
