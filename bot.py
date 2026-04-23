import os
import asyncio
import logging
import requests
import time
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiohttp import web

# --- НАСТРОЙКИ ЛОГИРОВАНИЯ ---
# Бот будет писать абсолютно все действия в консоль (логи Render)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("SportBot")

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID")
RAW_KEYS = os.getenv("ODDS_API_KEYS", "")
API_KEYS = [k.strip() for k in RAW_KEYS.split(",") if k.strip()]

LEAGUES = ["soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", "soccer_spain_la_liga", "soccer_france_ligue_one"]

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- СОСТОЯНИЕ БОТА ---
class BotState:
    start_time = time.time()
    total_scans = 0
    found_matches = 0
    key_limits = {key: "Неизвестно" for key in API_KEYS}
    current_key_idx = 0

state = BotState()

# --- КЛАВИАТУРА ---
def main_kb():
    builder = ReplyKeyboardBuilder()
    builder.button(text="🔑 Ключи и лимиты")
    builder.button(text="📊 Статус работы")
    return builder.as_markup(resize_keyboard=True)

# --- ОБРАБОТЧИКИ КОМАНД ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    logger.info(f"Пользователь {message.from_user.id} нажал /start")
    await message.answer(
        "🚀 Бот-аналитик запущен и работает в фоновом режиме.\n"
        "Используйте кнопки ниже для контроля.",
        reply_markup=main_kb()
    )

@dp.message(F.text == "🔑 Ключи и лимиты")
async def show_keys(message: types.Message):
    logger.info("Запрос информации о ключах")
    text = "📜 <b>Состояние ключей:</b>\n\n"
    for i, key in enumerate(API_KEYS):
        status = "✅ Активен" if i == state.current_key_idx else "💤 Ожидание"
        limit = state.key_limits.get(key, "Нет данных")
        text += f"Ключ №{i+1}: <code>{key[:5]}***</code>\nСтатус: {status}\nОстаток лимита: {limit}\n\n"
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(F.text == "📊 Статус работы")
async def show_status(message: types.Message):
    uptime_sec = int(time.time() - state.start_time)
    uptime = str(asyncio.tasks.helpers.timedelta(seconds=uptime_sec))
    logger.info("Запрос статуса работы")
    text = (
        f"✅ <b>Бот работает стабильно</b>\n\n"
        f"⏱ Uptime: <code>{uptime}</code>\n"
        f"🔄 Сканирований: <code>{state.total_scans}</code>\n"
        f"🎯 Найдено матчей: <code>{state.found_matches}</code>\n"
        f"📡 Последняя проверка: {datetime.now().strftime('%H:%M:%S')}"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)

# --- ЛОГИКА API ---
async def fetch_odds(league):
    for _ in range(len(API_KEYS)):
        current_key = API_KEYS[state.current_key_idx]
        url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/"
        params = {'apiKey': current_key, 'regions': 'eu', 'markets': 'h2h,totals', 'bookmakers': 'betboom'}

        try:
            logger.info(f"📡 Запрос API для {league} (Ключ №{state.current_key_idx + 1})")
            res = requests.get(url, params=params, timeout=10)
            
            # Сохраняем лимиты из заголовков
            remaining = res.headers.get('x-requests-remaining')
            if remaining:
                state.key_limits[current_key] = remaining

            if res.status_code == 200:
                return res.json()
            elif res.status_code in [401, 429]:
                logger.warning(f"⚠️ Ключ №{state.current_key_idx + 1} исчерпан или невалиден. Переключаюсь...")
                state.current_key_idx = (state.current_key_idx + 1) % len(API_KEYS)
                continue
            else:
                logger.error(f"❌ Ошибка API {res.status_code}: {res.text}")
                return None
        except Exception as e:
            logger.error(f"💥 Критическая ошибка запроса: {e}")
            state.current_key_idx = (state.current_key_idx + 1) % len(API_KEYS)
    return None

# --- СКАНЕР ---
async def scanner():
    while True:
        state.total_scans += 1
        logger.info(f"--- Начало цикла сканирования №{state.total_scans} ---")
        
        for league in LEAGUES:
            data = await fetch_odds(league)
            if not data: continue

            for event in data:
                # Упрощенная логика анализа (score 3-5)
                score = 3 # В реальности тут ваша функция analyze_match
                if score >= 3:
                    state.found_matches += 1
                    logger.info(f"➕ Нашел подходящий матч: {event['home_team']} - {event['away_team']}")
                    
                    # Отправка в канал
                    msg = f"🏆 <b>{league}</b>\n⚽️ {event['home_team']} — {event['away_team']}\nУверенность: 🔥🔥🔥\n\nСтавим тут: Betboom"
                    try:
                        await bot.send_message(CHANNEL_ID, msg, parse_mode=ParseMode.HTML)
                        await asyncio.sleep(2)
                    except Exception as e:
                        logger.error(f"Ошибка отправки в ТГ: {e}")

        logger.info(f"Цикл завершен. Сон 1 час.")
        await asyncio.sleep(3600)

# --- WEB SERVER (Health Check) ---
async def handle(request):
    return web.Response(text="Бот запущен", status=200)

async def main():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000)))
    
    logger.info("Запуск веб-сервера и бота...")
    await site.start()
    
    # Запускаем сканер в фоне
    asyncio.create_task(scanner())
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
