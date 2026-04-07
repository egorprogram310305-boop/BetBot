import os
import asyncio
import threading
import logging
import json
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from http.server import BaseHTTPRequestHandler, HTTPServer

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- НАСТРОЙКИ ПЕРЕМЕННЫХ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
STATS_FILE = "stats.json"

# Список ключей для ротации
FOOTBALL_KEYS = [
    os.getenv("FOOTBALL_API_KEY", "80ec2103f7e47b2294435a50b57ba4eb"),
    os.getenv("FOOTBALL_API_KEY_2")
]
FOOTBALL_KEYS = [key for key in FOOTBALL_KEYS if key]
current_key_index = 0

# --- 1. СЕРВЕР ДЛЯ RENDER ---
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

# --- 2. БАНК ---
def load_stats():
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r") as f:
            try: 
                return json.load(f)
            except: 
                return {"bank": 1000, "wins": 0, "losses": 0}
    return {"bank": 1000, "wins": 0, "losses": 0}

def save_stats(stats):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

# --- УМНАЯ ФУНКЦИЯ ЗАПРОСОВ С РОТАЦИЕЙ КЛЮЧЕЙ ---
def fetch_api_data(url):
    global current_key_index
    if not FOOTBALL_KEYS:
        logging.error("❌ Нет доступных API ключей!")
        return None
        
    for _ in range(len(FOOTBALL_KEYS)):
        active_key = FOOTBALL_KEYS[current_key_index]
        headers = {
            'x-apisports-key': active_key,
            'x-rapidapi-host': 'v3.football.api-sports.io'
        }
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 429 or "requests limit reached" in response.text.lower():
                logging.warning(f"⚠️ Ключ №{current_key_index + 1} исчерпал лимит. Переключаюсь...")
                current_key_index = (current_key_index + 1) % len(FOOTBALL_KEYS)
                continue
            return response.json()
        except Exception as e:
            logging.error(f"❌ Ошибка запроса: {e}")
            return None
    return None

# --- 3. ОБНОВЛЕННЫЙ СКАНЕР (NEXT=100) ---
async def scanner(bot):
    logging.info("🛠 MONSTER PRO ULTIMATE v2.8 — СКАНЕР ЗАПУЩЕН")
    logging.info(f"🔑 Доступно ключей для ротации: {len(FOOTBALL_KEYS)}")

    while True:
        try:
            logging.info("[SYSTEM] Запуск сканирования 100 предстоящих матчей...")
            sent_signals = 0

            # ✅ ИСПОЛЬЗУЕМ NEXT=100 ДЛЯ БУДУЩИХ ИГР
            res_fix = await asyncio.to_thread(
                fetch_api_data, 
                "https://v3.football.api-sports.io/fixtures?next=100"
            )

            if not res_fix or "response" not in res_fix or not res_fix["response"]:
                logging.info("[SYSTEM] Цикл завершен. Будущих матчей не найдено.")
                await asyncio.sleep(1200)
                continue

            matches = res_fix["response"]
            logging.info(f"Найдено {len(matches)} предстоящих матчей. Начинаю анализ...")

            for match in matches:
                f_id = match['fixture']['id']
                home_name = match['teams']['home']['name']
                away_name = match['teams']['away']['name']

                # Прогнозы
                res_pred = await asyncio.to_thread(fetch_api_data, f"https://v3.football.api-sports.io/predictions?fixture={f_id}")
                if not res_pred or not res_pred.get("response"):
                    continue

                p_data = res_pred["response"][0]
                comp = p_data.get('comparison', {})
                advice = p_data['predictions'].get('advice', '')

                try:
                    prob_home = int(p_data['predictions']['percent']['home'].replace('%','')) / 100
                    prob_away = int(p_data['predictions']['percent']['away'].replace('%','')) / 100
                except: continue

                # Коэффициенты
                res_odds = await asyncio.to_thread(fetch_api_data, f"https://v3.football.api-sports.io/odds?fixture={f_id}&bookmakers=8")
                bookie = None
                if res_odds and res_odds.get("response"):
                    bookmakers_list = res_odds["response"][0].get("bookmakers", [])
                    bookie = next((b for b in bookmakers_list if b.get("id") == 8), None)

                if not bookie:
                    res_odds = await asyncio.to_thread(fetch_api_data, f"https://v3.football.api-sports.io/odds?fixture={f_id}&bookmakers=1")
                    if res_odds and res_odds.get("response"):
                        bookmakers_list = res_odds["response"][0].get("bookmakers", [])
                        bookie = next((b for b in bookmakers_list if b.get("id") == 1), None)

                if not bookie: continue

                market_1x2 = next((m for m in bookie.get('bets', []) if m.get('id') == 1), None)
                
                # --- ЛОГИКА П1 ---
                current_home = next((float(o['odd']) for o in market_1x2.get('values', []) if o.get('value') == 'Home'), None) if market_1x2 else None
                if current_home and 1.70 <= current_home <= 2.50:
                    form_home = int(comp.get('form', {}).get('home', '0%').replace('%',''))
                    h2h_home = int(comp.get('h2h', {}).get('home', '0%').replace('%',''))
                    if form_home >= 55 and h2h_home >= 50:
                        fair_odd = 1 / prob_home if prob_home > 0 else 999
                        edge = (current_home / fair_odd) - 1
                        if edge >= 0.06:
                            await send_signal(bot, home_name, away_name, "П1", current_home, edge)
                            sent_signals += 1
                            continue

                # --- ЛОГИКА П2 ---
                current_away = next((float(o['odd']) for o in market_1x2.get('values', []) if o.get('value') == 'Away'), None) if market_1x2 else None
                if current_away and 1.70 <= current_away <= 2.50:
                    form_away = int(comp.get('form', {}).get('away', '0%').replace('%',''))
                    h2h_away = int(comp.get('h2h', {}).get('away', '0%').replace('%',''))
                    if form_away >= 55 and h2h_away >= 50:
                        fair_odd = 1 / prob_away if prob_away > 0 else 999
                        edge = (current_away / fair_odd) - 1
                        if edge >= 0.06:
                            await send_signal(bot, home_name, away_name, "П2", current_away, edge)
                            sent_signals += 1
                            continue

                # --- ЛОГИКА ТБ 2.5 ---
                market_over = next((m for m in bookie.get('bets', []) if m.get('id') == 3 or 'Over/Under' in m.get('name', '')), None)
                current_over = next((float(o['odd']) for o in market_over.get('values', []) if o.get('value') == 'Over 2.5' or 'Over 2.5' in str(o.get('value', ''))), None) if market_over else None
                if "Over 2.5 goals" in advice and current_over and 1.70 <= current_over <= 2.30:
                    await send_signal(bot, home_name, away_name, "ТБ 2.5", current_over, 0)
                    sent_signals += 1

            logging.info(f"[SYSTEM] Цикл завершен. Отправлено {sent_signals} сигналов. Сон 20 мин.")
            await asyncio.sleep(1200)

        except Exception as e:
            logging.error(f"❌ Ошибка в сканере: {e}", exc_info=True)
            await asyncio.sleep(60)

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ОТПРАВКИ ---
async def send_signal(bot, home, away, market, kf, edge):
    stats = load_stats()
    bet_amount = round(stats['bank'] * 0.03, 2)
    text = (
        f"💳 **MONSTER PRO: BETBOOM EDITION**\n\n"
        f"⚽️ {home} — {away}\n"
        f"🎯 Ставка: **{market}**\n"
        f"📈 Ориентир КФ: **{kf}**\n"
        f"🟢 Валуйность: **+{int(edge*100)}%**\n"
        f"⚠️ В BetBoom ставить, если КФ не ниже **{round(kf - 0.05, 2)}**"
    )
    kb = [[
        InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{bet_amount}_{kf}"),
        InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet_amount}")
    ]]
    await bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    await asyncio.sleep(3)

# --- ОСТАЛЬНЫЕ КОМАНДЫ ---
async def status_monitor(bot):
    while True:
        try:
            if ADMIN_ID: await bot.send_message(chat_id=ADMIN_ID, text=f"🔔 Monster PRO v2.8 в поиске... 🟢")
        except: pass
        await asyncio.sleep(3600)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот запущен! Ищем будущие матчи...")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = load_stats()
    await update.message.reply_text(f"📊 Банк: {d['bank']}₽\nПобед: {d['wins']} | Поражений: {d['losses']}")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    stats = load_stats()
    data = query.data.split("_")
    action, amt = data[0], float(data[1])
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
    await query.edit_message_text(text=f"{query.message.text}\n\n{res}\n📊 Статистика обновлена!")

async def post_init(app: Application):
    asyncio.create_task(scanner(app.bot))
    asyncio.create_task(status_monitor(app.bot))

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    if not TOKEN: return
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
