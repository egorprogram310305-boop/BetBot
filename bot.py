import requests
import asyncio
import os
import urllib.parse
import random
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

# --- БЕЗОПАСНЫЙ СБОР СЕКРЕТОВ ---
TOKEN = os.getenv("BOT_TOKEN")
MY_ID = os.getenv("CHAT_ID")
API_KEYS_STR = os.getenv("API_KEYS", "")
API_KEYS = [k.strip() for k in API_KEYS_STR.split(",") if k.strip()]

# Банк в памяти (сбросится при перезагрузке сервера)
CURRENT_BANK = 1000.0 

# Команды и переводы
TEAM_MAP = {"Real Sociedad": "Реал Сосьедад", "Levante": "Леванте"}
LEAGUES = ['soccer_epl', 'soccer_spain_la_liga', 'soccer_germany_bundesliga', 'basketball_nba']

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # Логика кнопок (упрощена для стабильности)
    await query.edit_message_text(text=f"{query.message.text}\n\n✅ Ставка учтена!")

async def scanner(app):
    key_idx = 0
    # Отправляем проверочное сообщение при старте
    try:
        await app.bot.send_message(chat_id=MY_ID, text="🚀 **MONSTER CLOUD ЗАПУЩЕН!**\nНачинаю поиск матчей...")
    except: pass

    while True:
        if not API_KEYS: break
        key = API_KEYS[key_idx]
        key_idx = (key_idx + 1) % len(API_KEYS)
        
        for league in LEAGUES:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?api_key={key}&regions=eu&markets=h2h,totals"
            try:
                r = requests.get(url, timeout=10)
                data = r.json()
                for game in data:
                    # Логика поиска кэфов 1.75 - 2.90
                    for bookie in game.get('bookmakers', []):
                        for market in bookie.get('markets', []):
                            for out in market['outcomes']:
                                if 1.75 <= out['price'] <= 2.90:
                                    search = urllib.parse.quote(f"{game['home_team']} {game['away_team']}")
                                    msg = (f"🎯 **НОВЫЙ СИГНАЛ**\n🏟 {game['home_team']} - {game['away_team']}\n"
                                           f"📈 Кф: {out['price']}\n🔗 [BetBoom](https://betboom.ru/sport#search={search})")
                                    await app.bot.send_message(chat_id=MY_ID, text=msg, parse_mode='Markdown')
                                    await asyncio.sleep(10)
                                    return # Ждем след. цикла
            except: continue
        await asyncio.sleep(180) # Пауза 3 минуты

async def main():
    if not TOKEN:
        print("Ошибка: Токен не найден в Environment Variables!")
        return
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # Запуск бота и сканера одновременно
    async with app:
        await app.initialize()
        await app.start_polling()
        await scanner(app)
        await app.stop_polling()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
