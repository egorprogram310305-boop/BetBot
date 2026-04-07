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

# --- НАСТРОЙКИ ЛОГИРОВАНИЯ ---
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("CHAT_ID")
STATS_FILE = "stats.json"

# Ротация ключей API-Football
FOOTBALL_KEYS = [
    os.getenv("FOOTBALL_API_KEY"),
    os.getenv("FOOTBALL_API_KEY_2")
]
FOOTBALL_KEYS = [k for k in FOOTBALL_KEYS if k]
current_key_idx = 0

# --- 1. СЕРВЕР ДЛЯ RENDER (ЧТОБЫ НЕ ВЫЛЕТАЛО) ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args): return

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"🌐 Health-сервер запущен на порту {port}")
    server.serve_forever()

# --- 2. УПРАВЛЕНИЕ СТАТИСТИКОЙ ---
def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f:
                return json.load(f)
        except: pass
    return {"bank": 1000, "wins": 0, "losses": 0}

def save_stats(stats):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

# --- 3. УМНЫЙ ЗАПРОС К API ---
def fetch_data(endpoint, params=None):
    global current_key_idx
    if not FOOTBALL_KEYS:
        logger.error("❌ Ключи API-Football не найдены в переменных!")
        return None
        
    url = f"https://v3.football.api-sports.io/{endpoint}"
    
    for attempt in range(len(FOOTBALL_KEYS)):
        active_key = FOOTBALL_KEYS[current_key_idx]
        headers = {
            'x-apisports-key': active_key,
            'x-rapidapi-host': 'v3.football.api-sports.io'
        }
        try:
            logger.info(f"📡 Запрос: {endpoint} (Ключ #{current_key_idx + 1})")
            response = requests.get(url, headers=headers, params=params, timeout=15)
            res_json = response.json()
            
            # Проверка лимитов
            if response.status_code == 429 or (res_json.get("errors") and "limit" in str(res_json["errors"])):
                logger.warning(f"⚠️ Лимит ключа #{current_key_idx + 1} исчерпан. Ротация...")
                current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
                continue
                
            return res_json
        except Exception as e:
            logger.error(f"❌ Ошибка сети: {e}")
            current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
            
    return None

# --- 4. СКАНЕР (ОПТИМИЗИРОВАННЫЙ) ---
async def scanner(bot):
    logger.info("🚀 МОНИТОРИНГ ЗАПУЩЕН (ЭКОНОМ-РЕЖИМ)")
    
    while True:
        try:
            logger.info("🔎 Шаг 1: Получение 15 ближайших матчей...")
            data = await asyncio.to_thread(fetch_data, "fixtures", {"next": 15})

            if not data or not data.get("response"):
                logger.error("⛔ Не удалось получить матчи. Сон 15 мин.")
                await asyncio.sleep(900)
                continue

            for item in data['response']:
                f_id = item['fixture']['id']
                h_team = item['teams']['home']['name']
                a_team = item['teams']['away']['name']
                
                logger.info(f"--- Анализ матча: {h_team} vs {a_team} ---")

                # Сначала берем коэффициенты (экономим запросы на статистику)
                odds_data = await asyncio.to_thread(fetch_data, "odds", {"fixture": f_id, "bookmaker": 8})
                
                o_p1, o_p2 = None, None
                if odds_data and odds_data.get("response"):
                    try:
                        for bet in odds_data['response'][0]['bookmakers'][0]['bets']:
                            if bet['name'] == "Match Winner":
                                for val in bet['values']:
                                    if val['value'] == 'Home': o_p1 = float(val['odd'])
                                    if val['value'] == 'Away': o_p2 = float(val['odd'])
                    except: pass

                # Проверка: подходят ли кэфы?
                if (o_p1 and 1.70 <= o_p1 <= 2.50) or (o_p2 and 1.70 <= o_p2 <= 2.50):
                    logger.info(f"  [OK] Кэфы подходят ({o_p1 or '-'}/{o_p2 or '-'}). Запрашиваю статистику...")
                    
                    pred_res = await asyncio.to_thread(fetch_data, "predictions", {"fixture": f_id})
                    if not pred_res or not pred_res.get("response"):
                        continue

                    comp = pred_res['response'][0]['comparison']
                    f_h = float(comp['form']['home'].replace('%',''))
                    f_a = float(comp['form']['away'].replace('%',''))
                    h2h_h = float(comp['h2h']['home'].replace('%',''))
                    h2h_a = float(comp['h2h']['away'].replace('%',''))

                    # Проверка стратегии (Форма >= 55%, H2H >= 50%)
                    if o_p1 and 1.70 <= o_p1 <= 2.50 and f_h >= 55 and h2h_h >= 50:
                        logger.info(f"  🎯 СИГНАЛ: П1 на {h_team}")
                        await send_signal(bot, h_team, a_team, "П1", o_p1)
                    elif o_p2 and 1.70 <= o_p2 <= 2.50 and f_a >= 55 and h2h_a >= 50:
                        logger.info(f"  🎯 СИГНАЛ: П2 на {a_team}")
                        await send_signal(bot, h_team, a_team, "П2", o_p2)
                    else:
                        logger.info(f"  [REJECTED] Низкие показатели (Форма: H:{f_h}% A:{f_a}%)")
                else:
                    logger.info(f"  [SKIP] Кэфы вне диапазона")

            logger.info("✅ Цикл завершен. Сон 1 час для экономии лимитов.")
            await asyncio.sleep(3600) 

        except Exception as e:
            logger.error(f"❌ Критическая ошибка в сканере: {e}")
            await asyncio.sleep(600)

# --- 5. ОТПРАВКА СИГНАЛА ---
async def send_signal(bot, home, away, market, odd):
    stats = load_stats()
    bet_sum = round(stats['bank'] * 0.03, 2)
    
    text = (
        f"💳 <b>MONSTER PRO SIGNAL</b>\n\n"
        f"⚽️ {home} — {away}\n"
        f"🎯 Ставка: <b>{market}</b>\n"
        f"📈 КФ: <b>{odd}</b>\n"
        f"💰 Сумма: <b>{bet_sum}₽</b> (3%)\n\n"
        f"📊 Банк: {round(stats['bank'], 2)}₽"
    )
    
    kb = [[
        InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{bet_sum}_{odd}"),
        InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet_sum}")
    ]]
    
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка отправки в TG: {e}")

# --- 6. ОБРАБОТКА КОМАНД И КНОПОК ---
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    stats = load_stats()
    data = query.data.split("_")
    action, amt = data[0], float(data[1])
    
    if action == "win":
        kf = float(data[2])
        profit = amt * (kf - 1)
        stats["bank"] += profit
        stats["wins"] += 1
        res_text = f"✅ ЗАШЛО (+{round(profit, 2)}₽)"
    else:
        stats["bank"] -= amt
        stats["losses"] += 1
        res_text = "❌ МИМО"
        
    save_stats(stats)
    await query.edit_message_text(
        text=f"{query.message.text_html}\n\n<b>{res_text}</b>\n📊 Банк обновлен!",
        parse_mode="HTML"
    )

async def post_init(app: Application):
    asyncio.create_task(scanner(app.bot))

def main():
    # Запуск Health Check сервера
    threading.Thread(target=run_health_server, daemon=True).start()
    
    if not TOKEN:
        logger.error("BOT_TOKEN не найден!")
        return

    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    logger.info("🤖 Бот запущен. Ожидаю первый цикл сканирования...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
