import os
import asyncio
import threading
import logging
import json
import requests
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from http.server import BaseHTTPRequestHandler, HTTPServer

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- НАСТРОЙКИ ПЕРЕМЕННЫХ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
STATS_FILE = "stats.json"

# Список ключей Football-Data.org для ротации
FD_KEYS = [
    "ТВОЙ_КЛЮЧ_1",
    "ТВОЙ_КЛЮЧ_2",
    "ТВОЙ_КЛЮЧ_3",
    "ТВОЙ_КЛЮЧ_4"
]
# Очищаем пустые значения
FD_KEYS = [k for k in FD_KEYS if k and k != "ТВОЙ_КЛЮЧ_1"]
current_key_idx = 0

# --- 1. СЕРВЕР ДЛЯ RENDER (Health Check) ---
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
    server.serve_forever()

# --- 2. УПРАВЛЕНИЕ СТАТИСТИКОЙ ---
def load_stats():
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r") as f:
            try: return json.load(f)
            except: return {"bank": 1000, "wins": 0, "losses": 0}
    return {"bank": 1000, "wins": 0, "losses": 0}

def save_stats(stats):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

# --- 3. ФУНКЦИЯ ЗАПРОСА (FOOTBALL-DATA.ORG) ---
def fetch_fd_data(endpoint):
    global current_key_idx
    if not FD_KEYS:
        logging.error("❌ Ключи API не настроены!")
        return None
        
    url = f"https://api.football-data.org/v4/{endpoint}"
    
    for _ in range(len(FD_KEYS)):
        active_key = FD_KEYS[current_key_idx]
        headers = {'X-Auth-Token': active_key}
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 429:
                logging.warning(f"⚠️ Ключ №{current_key_idx + 1} исчерпан. Ротация...")
                current_key_idx = (current_key_idx + 1) % len(FD_KEYS)
                continue
            return response.json()
        except Exception as e:
            logging.error(f"❌ Ошибка запроса: {e}")
            return None
    return None

# --- 4. УЛУЧШЕННЫЙ СКАНЕР v3.0 ---
async def scanner(bot):
    logging.info("🛠 MONSTER PRO FD-EDITION — СИСТЕМА ЗАПУЩЕНА")
    
    while True:
        try:
            logging.info("[SYSTEM] Запрос актуальных матчей...")
            # Получаем все матчи (Football-Data отдает список на ближайшее время)
            data = await asyncio.to_thread(fetch_fd_data, "matches")

            if not data or "matches" not in data:
                logging.warning("[SYSTEM] API недоступно или пустой ответ. Сплю 10 мин.")
                await asyncio.sleep(600)
                continue

            sent_signals = 0
            for match in data['matches']:
                # Фильтруем: только те, что еще не начались
                if match['status'] != 'TIMED':
                    continue

                home_n = match['homeTeam']['name']
                away_n = match['awayTeam']['name']
                
                # Извлекаем коэффициенты (если API их отдает для этой лиги)
                odds = match.get('odds', {})
                curr_h = odds.get('homeWin')
                curr_a = odds.get('awayWin')
                curr_d = odds.get('draw')

                if not curr_h or not curr_a:
                    continue

                # Стратегия П1 (Валуйность рассчитываем на основе базового алгоритма)
                # В FD-org нет детальных predictions в бесплатке, поэтому используем мат. ожидание
                if 1.70 <= curr_h <= 2.50:
                    # Условный расчет вероятности (можно заменить на свою формулу)
                    # Если КФ на П1 значительно ниже чем на П2, считаем валуйность
                    if curr_a / curr_h > 1.5: 
                        edge = round((1 / curr_h) * 0.1, 2) # Пример упрощенного Edge
                        await send_signal(bot, home_n, away_n, "П1", curr_h, edge)
                        sent_signals += 1

                # Стратегия П2
                elif 1.70 <= curr_a <= 2.50:
                    if curr_h / curr_a > 1.5:
                        edge = round((1 / curr_a) * 0.1, 2)
                        await send_signal(bot, home_n, away_n, "П2", curr_a, edge)
                        sent_signals += 1

            logging.info(f"[SYSTEM] Сканирование окончено. Найдено: {sent_signals}. Сон 30 мин.")
            await asyncio.sleep(1800) # 30 минут, чтобы не спамить лимиты

        except Exception as e:
            logging.error(f"❌ Ошибка в сканере: {e}", exc_info=True)
            await asyncio.sleep(60)

# --- 5. ОБРАБОТКА СИГНАЛОВ И КОМАНД ---
async def send_signal(bot, home, away, market, kf, edge):
    stats = load_stats()
    bet_amount = round(stats['bank'] * 0.03, 2)
    text = (
        f"💳 **MONSTER PRO: SIGNAL (FD)**\n\n"
        f"⚽️ {home} — {away}\n"
        f"🎯 Ставка: **{market}**\n"
        f"📈 КФ: **{kf}**\n"
        f"🟢 Валуйность: **+{int(edge*100)}%**\n"
        f"💰 Сумма: **{bet_amount}₽**"
    )
    kb = [[
        InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{bet_amount}_{kf}"),
        InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet_amount}")
    ]]
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Ошибка отправки сообщения: {e}")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Сканер Monster PRO (v3.0) на базе Football-Data запущен!")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = load_stats()
    await update.message.reply_text(f"📊 **Статистика:**\n💰 Банк: {round(d['bank'], 2)}₽\n✅ Побед: {d['wins']}\n❌ Поражений: {d['losses']}")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    stats = load_stats()
    
    data = query.data.split("_")
    action = data[0]
    amt = float(data[1])
    kf = float(data[2]) if len(data) > 2 else 1.0

    if action == "win":
        profit = amt * (kf - 1)
        stats["bank"] += profit
        stats["wins"] += 1
        res = f"✅ ЗАШЛО (+{round(profit, 2)}₽)"
    else:
        stats["bank"] -= amt
        stats["losses"] += 1
        res = "❌ МИМО"

    save_stats(stats)
    await query.edit_message_text(text=f"{query.message.text}\n\n{res}\n📊 Банк обновлен!")

async def post_init(app: Application):
    asyncio.create_task(scanner(app.bot))

def main():
    # Запуск сервера для Render
    threading.Thread(target=run_health_server, daemon=True).start()
    
    if not TOKEN:
        logging.error("BOT_TOKEN не найден в переменных окружения!")
        return

    app = Application.builder().token(TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    logging.info("🤖 Бот запущен. Ожидание сигналов...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
