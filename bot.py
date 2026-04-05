import requests
import asyncio
import os
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Данные берутся из настроек Render (Environment Variables)
TOKEN = os.getenv("BOT_TOKEN")
MY_ID = os.getenv("CHAT_ID")
# Ключи API пиши в Render через запятую без пробелов
API_KEYS = os.getenv("API_KEYS", "").split(",")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот запущен в облаке Render!")

async def scanner(context: ContextTypes.DEFAULT_TYPE):
    key_idx = 0
    leagues = ['soccer_epl', 'soccer_spain_la_liga', 'soccer_germany_bundesliga']
    while True:
        if not API_KEYS or not API_KEYS[0]:
            await asyncio.sleep(60)
            continue
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
                                if 1.8 <= out['price'] <= 2.5:
                                    msg = f"🎯 СИГНАЛ\n{game['home_team']} - {game['away_team']}\nКф: {out['price']}\nСтавка: {out['name']}"
                                    await context.bot.send_message(chat_id=MY_ID, text=msg)
                                    await asyncio.sleep(2)
            except:
                continue
        await asyncio.sleep(180)

def main():
    if not TOKEN: return
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.job_queue.run_once(scanner, when=1)
    application.run_polling()

if __name__ == "__main__":
    main()
