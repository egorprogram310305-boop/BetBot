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

# --- НАСТРОЙКИ (Переменные окружения Render) ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")  # Твой Telegram ID для отчетов "Я жив"
STATS_FILE = "stats.json"

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- 1. СЕРВЕР ДЛЯ RENDER (HEALTH CHECK) ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    server = HTTPServer(('0.0.0.0', 10000), HealthHandler)
    server.serve_forever()

# --- 2. ЛОГИКА СТАТИСТИКИ ---
def load_stats():
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    return {"bank": 1000, "wins": 0, "losses": 0, "history": []}

def save_stats(stats):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

# --- 3. СИСТЕМА МОНИТОРИНГА ("Я ЖИВ") ---
async def status_monitor(bot):
    while True:
        try:
            if ADMIN_ID:
                now = datetime.now().strftime("%H:%M")
                await bot.send_message(chat_id=ADMIN_ID, text=f"🔔 Мониторинг: Бот Monster PRO активен [{now}] 🟢")
        except Exception as e:
            logging.error(f"Ошибка мониторинга: {e}")
        await asyncio.sleep(3600)  # Раз в час

# --- 4. СКАНЕР МАТЧЕЙ (ОСНОВНАЯ ЛОГИКА) ---
async def scanner(bot):
    logging.info("🚀 Фоновый сканер матчей запущен!")
    while True:
        try:
            # Имитация запроса к API (замени на свой реальный URL API)
            # response = requests.get("URL_ТВОЕГО_API").json()
            
            # ПРИМЕР ЛОГИКИ (упрощенно):
            # Если нашли матч:
            # 1. Считаем Индекс Келли
            # 2. Формируем сообщение
            # 3. Отправляем в канал или админу
            
            await asyncio.sleep(300)  # Пауза 5 минут между проверками
        except Exception as e:
            logging.error(f"Ошибка в цикле сканера: {e}")
            await asyncio.sleep(60)

# --- 5. ОБРАБОТЧИКИ КОМАНД ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот Monster PRO активен и готов к работе 24/7!\nКоманды: /stats")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_stats()
    msg = (
        f"📊 **Текущая статистика:**\n"
        f"💰 Банк: {data['bank']}₽\n"
        f"✅ Побед: {data['wins']}\n"
        f"❌ Проигрышей: {data['losses']}\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = load_stats()
    action, amount = query.data.split("_")
    amount = float(amount)

    if action == "win":
        data["bank"] += amount
        data["wins"] += 1
        res_text = "✅ Ставка отмечена как ЗАШЛА!"
    else:
        data["bank"] -= amount
        data["losses"] += 1
        res_text = "❌ Ставка отмечена как МИМО."

    save_stats(data)
    await query.edit_message_text(text=f"{query.message.text}\n\n📊 Итог: {res_text}")

# --- 6. ЗАПУСК ПРИЛОЖЕНИЯ ---
async def post_init(application: Application):
    """Этот блок запускает фоновые задачи после старта бота"""
    asyncio.create_task(scanner(application.bot))
    asyncio.create_task(status_monitor(application.bot))

def main():
    # Запуск сервера-пустышки в отдельном потоке
    threading.Thread(target=run_health_server, daemon=True).start()

    if not TOKEN:
        print("❌ ОШИБКА: BOT_TOKEN не найден!")
        return

    # Сборка бота
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    # Регистрация команд
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("🤖 Бот запущен. Ожидание сигналов...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
