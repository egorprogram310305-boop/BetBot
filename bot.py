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

# --- ДАННЫЕ ИЗ RENDER ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("CHAT_ID")
STATS_FILE = "stats.json"

# Ротация ключей для обхода лимитов (до 200 запросов в сутки суммарно)
FOOTBALL_KEYS = [
    os.getenv("FOOTBALL_API_KEY"),
    os.getenv("FOOTBALL_API_KEY_2")
]
FOOTBALL_KEYS = [k for k in FOOTBALL_KEYS if k]
current_key_idx = 0

# --- 1. HEALTH CHECK СЕРВЕР (Для стабильности на Render) ---
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
    logger.info(f"🌐 Сервер мониторинга порта запущен на {port}")
    server.serve_forever()

# --- 2. СИСТЕМА УПРАВЛЕНИЯ БАНКОМ ---
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
        logger.error("❌ Ключи API не найдены!")
        return None
        
    url = f"https://v3.football.api-sports.io/{endpoint}"
    
    for _ in range(len(FOOTBALL_KEYS)):
        active_key = FOOTBALL_KEYS[current_key_idx]
        headers = {
            'x-apisports-key': active_key,
            'x-rapidapi-host': 'v3.football.api-sports.io'
        }
        try:
            logger.info(f"📡 Запрос: {endpoint} (Ключ #{current_key_idx + 1})")
            response = requests.get(url, headers=headers, params=params, timeout=20)
            res_json = response.json()
            
            # Логируем ошибки плана, если они есть
            if res_json.get("errors"):
                logger.error(f"❌ Ошибка API: {res_json['errors']}")
                current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
                continue
                
            return res_json
        except Exception as e:
            logger.error(f"❌ Сетевая ошибка: {e}")
            current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
            
    return None

# --- 4. СКАНЕР (АДАПТИРОВАН ПОД FREE PLAN + ТЗ) ---
async def scanner(bot):
    logger.info("🚀 СКАНЕР MONSTER PRO ЗАПУЩЕН (FREE MODE)")
    
    while True:
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            logger.info(f"🔎 Шаг 1: Загрузка матчей на дату: {today}")
            
            # Используем разрешенный параметр 'date' вместо 'next'
            data = await asyncio.to_thread(fetch_data, "fixtures", {"date": today, "timezone": "Europe/Moscow"})

            if not data or not data.get("response"):
                logger.warning("⚠️ Данные не получены. Сон 10 минут.")
                await asyncio.sleep(600)
                continue

            # Берем только неначавшиеся матчи (NS)
            upcoming = [m for m in data['response'] if m['fixture']['status']['short'] == 'NS']
            logger.info(f"✅ Найдено матчей сегодня: {len(data['response'])}. Предстоит: {len(upcoming)}")

            # Анализируем первые 15 предстоящих игр (лимит запросов)
            for item in upcoming[:15]:
                f_id = item['fixture']['id']
                h_team = item['teams']['home']['name']
                a_team = item['teams']['away']['name']
                
                logger.info(f"📊 Анализ матча: {h_team} — {a_team}")

                # ШАГ 2: Коэффициенты (Букмекер 8 - 1xBet/Bwin/etc)
                odds_data = await asyncio.to_thread(fetch_data, "odds", {"fixture": f_id, "bookmaker": 8})
                o_p1, o_p2 = None, None
                
                if odds_data and odds_data.get("response") and len(odds_data['response']) > 0:
                    try:
                        for bet in odds_data['response'][0]['bookmakers'][0]['bets']:
                            if bet['name'] == "Match Winner":
                                for val in bet['values']:
                                    if val['value'] == 'Home': o_p1 = float(val['odd'])
                                    if val['value'] == 'Away': o_p2 = float(val['odd'])
                    except: pass

                # Проверка диапазона КФ 1.70 - 2.50
                res_type = None
                if o_p1 and 1.70 <= o_p1 <= 2.50: res_type = ("П1", o_p1)
                elif o_p2 and 1.70 <= o_p2 <= 2.50: res_type = ("П2", o_p2)

                if res_type:
                    logger.info(f"📈 КФ подходит ({res_type[1]}). Проверка статистики...")
                    
                    # ШАГ 3: Прогноз (Форма и H2H)
                    pred_res = await asyncio.to_thread(fetch_data, "predictions", {"fixture": f_id})
                    if not pred_res or not pred_res.get("response"): continue

                    comp = pred_res['response'][0]['comparison']
                    f_h = float(comp['form']['home'].replace('%',''))
                    f_a = float(comp['form']['away'].replace('%',''))
                    h2h_h = float(comp['h2h']['home'].replace('%',''))
                    h2h_a = float(comp['h2h']['away'].replace('%',''))

                    # Проверка по твоему ТЗ (Форма >= 55%, H2H >= 50%)
                    if res_type[0] == "П1" and f_h >= 55 and h2h_h >= 50:
                        await send_signal(bot, h_team, a_team, "П1", o_p1)
                    elif res_type[0] == "П2" and f_a >= 55 and h2h_a >= 50:
                        await send_signal(bot, h_team, a_team, "П2", o_p2)
                    else:
                        logger.info(f"  [ОТКЛОНЕН] Слабая статистика: H(F:{f_h}%, H2H:{h2h_h}%) A(F:{f_a}%, H2H:{h2h_a}%)")
                else:
                    logger.info(f"  [ПРОПУСК] КФ вне диапазона (П1:{o_p1}, П2:{o_p2})")

            logger.info("✅ Цикл сканирования завершен. Сон 1 час.")
            await asyncio.sleep(3600) 

        except Exception as e:
            logger.error(f"❌ Критическая ошибка в сканере: {e}")
            await asyncio.sleep(600)

# --- 5. ФОРМИРОВАНИЕ СИГНАЛА ---
async def send_signal(bot, home, away, market, odd):
    stats = load_stats()
    bet_sum = round(stats['bank'] * 0.03, 2) # ТЗ: 3% от банка
    
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
        logger.info(f"📩 Сигнал отправлен: {home} - {away}")
    except Exception as e:
        logger.error(f"Ошибка отправки сигнала в Telegram: {e}")

# --- 6. КНОПКИ СТАТИСТИКИ ---
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

# --- 7. ЗАПУСК ---
async def post_init(app: Application):
    asyncio.create_task(scanner(app.bot))

def main():
    # Запуск сервера для Render
    threading.Thread(target=run_health_server, daemon=True).start()
    
    if not TOKEN or not ADMIN_ID:
        logger.error("BOT_TOKEN или CHAT_ID не установлены!")
        return

    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    logger.info("🤖 Бот запущен и готов к работе!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
