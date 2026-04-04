import requests
import asyncio
import os
import urllib.parse
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

# --- ЗАГРУЗКА НАСТРОЕК ---
TOKEN = os.getenv("BOT_TOKEN")
MY_ID = os.getenv("CHAT_ID")
# Чистим ключи от пробелов и пустых строк
raw_keys = os.getenv("API_KEYS", "").split(",")
API_KEYS = [k.strip() for k in raw_keys if len(k.strip()) > 10]

LEAGUES = ['soccer_epl', 'soccer_spain_la_liga', 'soccer_germany_bundesliga', 'soccer_italy_serie_a', 'basketball_nba']

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text=f"{query.message.text}\n\n✅ Ставка зафиксирована!")

async def scanner(app):
    print("Сканер запущен...")
    key_idx = 0
    
    while True:
        if not API_KEYS:
            print("ОШИБКА: Ключи API не найдены!")
            await asyncio.sleep(60)
            continue
            
        current_key = API_KEYS[key_idx]
        key_idx = (key_idx + 1) % len(API_KEYS)
        
        for league in LEAGUES:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?api_key={current_key}&regions=eu&markets=h2h,totals"
            try:
                response = requests.get(url, timeout=15)
                data = response.json()
                
                if not isinstance(data, list): continue

                for game in data:
                    home = game.get('home_team')
                    away = game.get('away_team')
                    
                    for bookie in game.get('bookmakers', []):
                        if bookie['key'] == 'betboom': # Или любой другой, если нужен конкретный
                            pass 

                        for market in bookie.get('markets', []):
                            for out in market.get('outcomes', []):
                                price = out['price']
                                # Наш рабочий диапазон кэфов
                                if 1.80 <= price <= 2.60:
                                    search_url = f"https://betboom.ru/sport#search={urllib.parse.quote(home + ' ' + away)}"
                                    
                                    text = (f"🚀 **MONSTER SIGNAL**\n\n"
                                            f"🏟 `{home} — {away}`\n"
                                            f"🎯 Ставка: **{out['name']}**\n"
                                            f"📈 Коэффициент: `{price}`\n\n"
                                            f"🔗 [ОТКРЫТЬ В BETBOOM]({search_url})")
                                    
                                    await app.bot.send_message(chat_id=MY_ID, text=text, parse_mode='Markdown', disable_web_page_preview=True)
                                    await asyncio.sleep(5) # Чтобы не спамить
                                    return # Выходим из циклов после одного сигнала
            except Exception as e:
                print(f"Ошибка в сканере: {e}")
                continue
        
        await asyncio.sleep(120) # Пауза между кругами сканирования

async def run_bot():
    if not TOKEN or not MY_ID:
        print("КРИТИЧЕСКАЯ ОШИБКА: Токен или ID не заданы!")
        return

    print("Инициализация бота...")
    application = Application.builder().token(TOKEN).build()
    
    # Добавляем обработку кнопок
    application.add_handler(CallbackQueryHandler(handle_callback))

    async with application:
        await application.initialize()
        await application.start_polling()
        print("Бот вышел в онлайн!")
        await scanner(application)
        await application.stop_polling()

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
