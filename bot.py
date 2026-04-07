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
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("CHAT_ID") # Используем CHAT_ID из твоих настроек
STATS_FILE = "stats.json"

# Ротация ключей от api-football.com
FOOTBALL_KEYS = [
    os.getenv("FOOTBALL_API_KEY"),
    os.getenv("FOOTBALL_API_KEY_2")
]
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

# --- 2. УПРАВЛЕНИЕ БАНКОМ ---
def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f: return json.load(f)
        except: pass
    return {"bank": 1000, "wins": 0, "losses": 0}

def save_stats(stats):
    with open(STATS_FILE, "w") as f: json.dump(stats, f)

# --- 3. УМНЫЙ ЗАПРОС (API-FOOTBALL) ---
def fetch_data(endpoint, params=None):
    global current_key_idx
    url = f"https://v3.football.api-sports.io/{endpoint}"
    
    for _ in range(len(FOOTBALL_KEYS)):
        active_key = FOOTBALL_KEYS[current_key_idx]
        headers = {'x-apisports-key': active_key, 'x-rapidapi-host': 'v3.football.api-sports.io'}
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            res_json = response.json()
            
            # Проверка лимитов (429 или ошибка в JSON)
            if response.status_code == 429 or (res_json.get("errors") and "limit" in str(res_json["errors"])):
                logging.warning(f"⚠️ Ключ №{current_key_idx + 1} исчерпан. Ротация...")
                current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
                continue
            return res_json
        except Exception as e:
            logging.error(f"❌ Ошибка запроса: {e}")
    return None

# --- 4. СКАНЕР MONSTER PRO v2.9 ---
async def scanner(bot):
    logging.info("🛠 СИСТЕМА MONSTER PRO (API-FOOTBALL) ЗАПУЩЕНА")
    
    while True:
        try:
            logging.info("[SYSTEM] Запрос 100 матчей...")
            data = await asyncio.to_thread(fetch_data, "fixtures", {"next": 100})

            if not data or not data.get("response"):
                logging.warning("⚠️ Нет ответа от API. Сон 15 мин.")
                await asyncio.sleep(900)
                continue

            signals_count = 0
            for item in data['response']:
                f_id = item['fixture']['id']
                h_team = item['teams']['home']['name']
                a_team = item['teams']['away']['name']
                
                logging.info(f"--- Проверка: {h_team} vs {a_team} ---")

                # Получаем статистику и кэфы
                pred_res = await asyncio.to_thread(fetch_data, "predictions", {"fixture": f_id})
                odds_res = await asyncio.to_thread(fetch_data, "odds", {"fixture": f_id, "bookmaker": 8}) # Bet365

                if not pred_res or not pred_res.get("response") or not odds_res or not odds_res.get("response"):
                    logging.info(f"  [REJECTED] Нет данных по кэфам или статистике")
                    continue

                # Извлекаем форму и H2H
                comp = pred_res['response'][0]['comparison']
                f_home = float(comp['form']['home'].replace('%',''))
                f_away = float(comp['form']['away'].replace('%',''))
                h2h_home = float(comp['h2h']['home'].replace('%',''))
                h2h_away = float(comp['h2h']['away'].replace('%',''))

                # Извлекаем коэффициенты
                o_p1, o_p2 = None, None
                for bet in odds_res['response'][0]['bookmakers'][0]['bets']:
                    if bet['name'] == "Match Winner":
                        for val in bet['values']:
                            if val['value'] == 'Home': o_p1 = float(val['odd'])
                            if val['value'] == 'Away': o_p2 = float(val['odd'])

                # Сценарий П1
                if o_p1 and 1.70 <= o_p1 <= 2.50:
                    if f_home >= 55 and h2h_home >= 50:
                        logging.info(f"  [SUCCESS] П1 подходит! КФ: {o_p1}")
                        await send_signal(bot, h_team, a_team, "П1", o_p1)
                        signals_count += 1
                    else:
                        logging.info(f"  [REJECTED] Форма {f_home}% или H2H {h2h_home}% < нормы")

                # Сценарий П2
                elif o_p2 and 1.70 <= o_p2 <= 2.50:
                    if f_away >= 55 and h2h_away >= 50:
                        logging.info(f"  [SUCCESS] П2 подходит! КФ: {o_p2}")
                        await send_signal(bot, h_team, a_team, "П2", o_p2)
                        signals_count += 1
                    else:
                        logging.info(f"  [REJECTED] Форма {f_away}% или H2H {h2h_away}% < нормы")
                else:
                    logging.info(f"  [REJECTED] Кэфы вне диапазона 1.70 - 2.50")

            logging.info(f"[SYSTEM] Сканирование окончено. Найдено: {signals_count}. Сон 20 мин.")
            await asyncio.sleep(1200)

        except Exception as e:
            logging.error(f"❌ Ошибка сканера: {e}")
            await asyncio.sleep(60)

# --- 5. ОТПРАВКА СИГНАЛА ---
async def send_signal(bot, home, away, market, odd):
    stats = load_stats()
    bet = round(stats['bank'] * 0.03, 2)
    text = (
        f"💳 <b>MONSTER PRO: BETBOOM EDITION</b>\n\n"
        f"⚽️ {home} — {away}\n"
        f"🎯 Ставка: <b>{market}</b>\n"
        f"📈 КФ: <b>{odd}</b>\n"
        f"💰 Сумма: <b>{bet}₽</b> (3%)\n\n"
        f"⚠️ В BetBoom ставить, если КФ не ниже {round(odd - 0.05, 2)}"
    )
    kb = [[
        InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{bet}_{odd}"),
        InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet}")
    ]]
    await bot.send_message(chat_id=ADMIN_ID, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

# --- ОБРАБОТКА КНОПОК И КОМАНД ---
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
    logging.info("🤖 Бот запущен. Ожидание сигналов...")
    app.run_polling()

if __name__ == "__main__":
    main()
