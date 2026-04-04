import requests
import asyncio
import os
import urllib.parse
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- НАСТРОЙКИ ИЗ ОБЛАКА ---
TOKEN = os.getenv("BOT_TOKEN")
MY_ID = os.getenv("CHAT_ID")
API_KEYS = os.getenv("API_KEYS", "").split(",")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка связи"""
    await update.message.reply_text("✅ Бот онлайн и работает из облака!")

async def scanner(context: ContextTypes.DEFAULT_TYPE):
    """Фоновый поиск матчей"""
    key_idx = 0
    leagues = ['soccer_epl', 'soccer_spain_la_liga', 'soccer_germany_bundesliga']
    
    while True:
        key = API_KEYS[key_idx].strip()
        key_idx = (key_idx + 1) % len(API_KEYS)
        
        for league in leagues:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?api_key={key}&regions=eu&markets=h2h"
            try:
                res = requests.get(url, timeout=10).json()
                for game in res:
                    for bookie in game.get('bookmakers', []):
                        for market in bookie.get('markets', []):
                            for out in market['outcomes']:
                                # Ищем кэфы в диапазоне 1.8 - 2.5
                                if 1.8 <= out['price'] <= 2.5:
                                    msg = (f"🎯 **CLOUD SIGNAL**\n"
                                           f"🏟 {game['home_team']} - {game['away_team']}\n"
                                           f"📈 Кф: {out['price']}\n"
                                           f"🎯 Ставка: {out['name']}")
                                    await context.bot.send_message(chat_id=MY_ID, text=msg)
                                    await asyncio.sleep(5)
                                    return
            except:
                continue
        await asyncio.sleep(180) # Пауза 3 минуты

def main():
    """Запуск приложения"""
    if not TOKEN:
        return

    # Новый стандарт версии 20.x
    application = Application.builder().token(TOKEN).build()
    
    # Добавляем команду /start для проверки
    application.add_handler(CommandHandler("start", start))
    
    # Запускаем фоновый сканер
    job_queue = application.job_queue
    job_queue.run_once(scanner, when=1)

    print("🚀 Бот запускается...")
    application.run_polling()

if __name__ == "__main__":
    main()
