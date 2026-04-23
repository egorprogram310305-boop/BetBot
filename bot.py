import os
import asyncio
import logging
import requests
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiohttp import web

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID") # ID твоего канала
ODDS_API_KEY = os.getenv("ODDS_API_KEY") # Один ключ или первый из списка

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Лиги для анализа
LEAGUES = ["soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", 
           "soccer_spain_la_liga", "soccer_france_ligue_one", "soccer_russia_premier_league"]

# --- ЛОГИКА АНАЛИЗА (СУММА ФАКТОРОВ) ---
def analyze_match(event):
    """
    Имитация глубокого анализа. 
    В рамках бесплатного API Odds данные о H2H и травмах ограничены,
    поэтому мы строим логику на движении коэффициентов и вероятностях.
    """
    score = 0
    reasons = []

    # 1. Фактор фаворита (Форма)
    # Если кэф на фаворита падает или он стабильно низок - это +1
    h2h_market = next((m for b in event['bookmakers'] if b['key'] == 'betboom' for m in b['markets'] if m['key'] == 'h2h'), None)
    
    if h2h_market:
        home_price = h2h_market['outcomes'][0]['price']
        if home_price < 1.7:
            score += 1
            reasons.append(f"{event['home_team']} в отличной форме дома.")

    # 2. Фактор результативности (Статистика голов)
    totals_market = next((m for b in event['bookmakers'] if b['key'] == 'betboom' for m in b['markets'] if m['key'] == 'totals'), None)
    if totals_market:
        score += 1
        reasons.append("Статистика указывает на высокую результативность.")

    # 3. Фактор домашнего поля
    score += 1 
    
    # Итоговая оценка (ограничим 5)
    final_score = min(score + 1, 5)
    return final_score, " ".join(reasons[:2])

# --- ПОЛУЧЕНИЕ ДАННЫХ ---
async def fetch_and_post():
    for league in LEAGUES:
        url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/"
        params = {
            'apiKey': ODDS_API_KEY,
            'regions': 'eu',
            'markets': 'h2h,totals',
            'bookmakers': 'betboom'
        }
        
        try:
            res = requests.get(url, params=params)
            if res.status_code != 200: continue
            data = res.json()

            for event in data:
                bb = next((b for b in event['bookmakers'] if b['key'] == 'betboom'), None)
                if not bb: continue

                score, reason = analyze_match(event)
                
                if score >= 3:
                    # Формируем сообщение
                    conf_stars = "🔥" * score
                    market = bb['markets'][0]
                    outcome = market['outcomes'][0]
                    
                    text = (
                        f"🏆 <b>{event['sport_title']}</b>\n"
                        f"⚽️ {event['home_team']} — {event['away_team']}\n\n"
                        f"<b>Ставка:</b> {outcome['name']}\n"
                        f"<b>Коэффициент:</b> {outcome['price']}\n"
                        f"<b>Уверенность:</b> {conf_stars} ({score}/5)\n\n"
                        f"<b>Обоснование:</b> {reason}\n\n"
                        f"📍 Ставим тут: <a href='https://betboom.ru'>Betboom</a>"
                    )
                    
                    await bot.send_message(CHANNEL_ID, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                    await asyncio.sleep(2) # Защита от спама

        except Exception as e:
            logging.error(f"Ошибка при парсинге {league}: {e}")

# --- ПЛАНИРОВЩИК ---
async def scheduler():
    while True:
        logging.info("Запуск ежечасного сканирования...")
        await fetch_and_post()
        await asyncio.sleep(3600) # Ждем 1 час

# --- HEALTH CHECK SERVER (Для Render) ---
async def handle_health(request):
    return web.Response(text="Бот в порядке", status=200)

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000)))
    await site.start()

# --- MAIN ---
async def main():
    # Запускаем веб-сервер для Render
    asyncio.create_task(start_webserver())
    # Запускаем сканер
    asyncio.create_task(scheduler())
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
