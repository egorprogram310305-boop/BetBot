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

# --- НАСТРОЙКИ ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("CHAT_ID")
STATS_FILE = "stats.json"

# Ротация ключей
FOOTBALL_KEYS = [os.getenv("FOOTBALL_API_KEY"), os.getenv("FOOTBALL_API_KEY_2")]
FOOTBALL_KEYS = [k for k in FOOTBALL_KEYS if k]
current_key_idx = 0

# --- 1. СЕРВЕР ДЛЯ RENDER ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args): return

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    server.serve_forever()

# --- 2. БАНК И СТАТИСТИКА ---
def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f: return json.load(f)
        except: pass
    return {"bank": 1000, "wins": 0, "losses": 0}

def save_stats(stats):
    with open(STATS_FILE, "w") as f: json.dump(stats, f)

# --- 3. ЗАПРОСЫ К API С ДИАГНОСТИКОЙ ---
def fetch_data(endpoint, params=None):
    global current_key_idx
    if not FOOTBALL_KEYS:
        logger.error("❌ Ключи API не настроены в Render!")
        return None
        
    url = f"https://v3.football.api-sports.io/{endpoint}"
    
    for _ in range(len(FOOTBALL_KEYS)):
        active_key = FOOTBALL_KEYS[current_key_idx]
        headers = {'x-apisports-key': active_key, 'x-rapidapi-host': 'v3.football.api-sports.io'}
        try:
            logger.info(f"📡 Запрос {endpoint} (Ключ #{current_key_idx+1})")
            response = requests.get(url, headers=headers, params=params, timeout=15)
            data = response.json()
            
            # Если API вернуло ошибки внутри JSON
            if data.get("errors"):
                logger.error(f"❌ Ошибка API: {data['errors']}")
                current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
                continue

            if response.status_code == 429:
                logger.warning(f"⚠️ Лимит ключа #{current_key_idx+1} исчерпан.")
                current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
                continue
                
            return data
        except Exception as e:
            logger.error(f"❌ Сетевая ошибка: {e}")
            current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
            
    return None

# --- 4. СКАНЕР (УТРЕННЕЕ ТЗ + ПРАВКИ) ---
async def scanner(bot):
    logger.info("🚀 СКАНЕР MONSTER PRO ЗАПУЩЕН")
    
    while True:
        try:
            logger.info("🔎 Шаг 1: Получение 20 ближайших матчей (Таймзона: Moscow)...")
            # Добавили таймзону, чтобы API понимало контекст времени
            data = await asyncio.to_thread(fetch_data, "fixtures", {"next": 20, "timezone": "Europe/Moscow"})

            if not data or not data.get("response") or len(data['response']) == 0:
                logger.warning("⚠️ Матчи не получены. Повтор через 5 минут.")
                await asyncio.sleep(300)
                continue

            logger.info(f"✅ Найдено {len(data['response'])} матчей. Начинаю фильтрацию по КФ...")

            for item in data['response']:
                f_id = item['fixture']['id']
                h_name = item['teams']['home']['name']
                a_name = item['teams']['away']['name']
                
                # ШАГ 2: Коэффициенты
                odds_res = await asyncio.to_thread(fetch_data, "odds", {"fixture": f_id, "bookmaker": 8})
                o_p1, o_p2 = None, None
                
                if odds_res and odds_res.get("response") and len(odds_res['response']) > 0:
                    try:
                        for bet in odds_res['response'][0]['bookmakers'][0]['bets']:
                            if bet['name'] == "Match Winner":
                                for val in bet['values']:
                                    if val['value'] == 'Home': o_p1 = float(val['odd'])
                                    if val['value'] == 'Away': o_p2 = float(val['odd'])
                    except: pass

                # Проверка диапазона КФ (1.70 - 2.50)
                target = None
                if o_p1 and 1.70 <= o_p1 <= 2.50: target = ("П1", o_p1)
                elif o_p2 and 1.70 <= o_p2 <= 2.50: target = ("П2", o_p2)

                if target:
                    logger.info(f"📈 КФ подходит для {h_name}-{a_name} ({target[0]}: {target[1]}). Проверка статистики...")
                    
                    # ШАГ 3: Прогноз и сравнение
                    pred_res = await asyncio.to_thread(fetch_data, "predictions", {"fixture": f_id})
                    if not pred_res or not pred_res.get("response"): continue
                    
                    comp = pred_res['response'][0]['comparison']
                    # Утреннее ТЗ: Форма команды
                    f_h = float(comp['form']['home'].replace('%',''))
                    f_a = float(comp['form']['away'].replace('%',''))
                    # Утреннее ТЗ: H2H (очные встречи)
                    h2h_h = float(comp['h2h']['home'].replace('%',''))
                    h2h_a = float(comp['h2h']['away'].replace('%',''))

                    # Условие для П1
                    if target[0] == "П1" and f_h >= 55 and h2h_h >= 50:
                        await send_signal(bot, h_name, a_name, "П1", o_p1)
                    # Условие для П2
                    elif target[0] == "П2" and f_a >= 55 and h2h_a >= 50:
                        await send_signal(bot, h_name, a_name, "П2", o_p2)
                    else:
                        logger.info(f"  [REJECTED] Статистика слабая (Форма: {f_h}%/{f_a}%, H2H: {h2h_h}%/{h2h_a}%)")
                else:
                    logger.info(f"  [SKIP] {h_name}-{a_name}: КФ не в диапазоне")

            logger.info("✅ Цикл завершен. Сон 1 час...")
            await asyncio.sleep(3600)

        except Exception as e:
            logger.error(f"❌ Ошибка сканера: {e}")
            await asyncio.sleep(300)

# --- 5. ОТПРАВКА СИГНАЛА ---
async def send_signal(bot, home, away, market, odd):
    stats = load_stats()
    bet_sum = round(stats['bank'] * 0.03, 2) # Утреннее ТЗ: 3% от банка
    
    text = (
        f"💳 <b>MONSTER PRO SIGNAL</b>\n\n"
        f"⚽️ {home} — {away}\n"
        f"🎯 Ставка: <b>{market}</b>\n"
        f"📈 КФ: <b>{odd}</b>\n"
        f"💰 Сумма: <b>{bet_sum}₽</b> (3%)\n\n"
        f"📊 Текущий банк: {round(stats['bank'], 2)}₽"
    )
    kb = [[
        InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{bet_sum}_{odd}"),
        InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet_sum}")
    ]]
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    except Exception as e: logger.error(f"TG Error: {e}")

# --- 6. ОБРАБОТКА КНОПОК ---
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    stats = load_stats()
    data = query.data.split("_")
    
    if data[0] == "win":
        profit = float(data[1]) * (float(data[2]) - 1)
        stats["bank"] += profit
        stats["wins"] += 1
        res = f"✅ ЗАШЛО (+{round(profit, 2)}₽)"
    else:
        stats["bank"] -= float(data[1])
        stats["losses"] += 1
        res = "❌ МИМО"
    
    save_stats(stats)
    await query.edit_message_text(text=f"{query.message.text_html}\n\n<b>{res}</b>", parse_mode="HTML")

async def post_init(app: Application):
    asyncio.create_task(scanner(app.bot))

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CallbackQueryHandler(handle_callback))
    logger.info("🤖 Бот запущен. Ожидание сигналов...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

