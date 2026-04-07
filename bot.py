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

# Список ключей для ротации
FOOTBALL_KEYS = [
    os.getenv("FOOTBALL_API_KEY", "80ec2103f7e47b2294435a50b57ba4eb"),
    os.getenv("FOOTBALL_API_KEY_2")
]
FOOTBALL_KEYS = [key for key in FOOTBALL_KEYS if key]
current_key_index = 0

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
            try: 
                return json.load(f)
            except: 
                return {"bank": 1000, "wins": 0, "losses": 0}
    return {"bank": 1000, "wins": 0, "losses": 0}

def save_stats(stats):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

# --- 3. РОТАЦИЯ API КЛЮЧЕЙ ---
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
                logging.warning(f"⚠️ Ключ №{current_key_index + 1} исчерпан. Переключаюсь...")
                current_key_index = (current_key_index + 1) % len(FOOTBALL_KEYS)
                continue
            return response.json()
        except Exception as e:
            logging.error(f"❌ Ошибка запроса: {e}")
            return None
    return None

# --- 4. УЛУЧШЕННЫЙ СКАНЕР (ОБЪЕДИНЕННАЯ ВЕРСИЯ) ---
async def scanner(bot):
    logging.info("🛠 MONSTER PRO ULTIMATE v2.8 — СИСТЕМА ЗАПУЩЕНА")
    
    while True:
        try:
            # Получаем текущую дату в формате YYYY-MM-DD
            today = datetime.datetime.now().strftime('%Y-%m-%d')
            logging.info(f"[SYSTEM] Запрос матчей на сегодня: {today}")
            
            url = f"https://v3.football.api-sports.io/fixtures?date={today}"
            res_fix = await asyncio.to_thread(fetch_api_data, url)

            if not res_fix or "response" not in res_fix or not res_fix["response"]:
                logging.warning("[SYSTEM] API прислал пустой список или ошибка доступа. Жду 10 мин...")
                await asyncio.sleep(600)
                continue

            all_matches = res_fix["response"]
            # Фильтруем только те, что еще не начались (статус NS)
            upcoming = [m for m in all_matches if m['fixture']['status']['short'] == 'NS']
            
            logging.info(f"[DEBUG] Найдено матчей сегодня: {len(all_matches)}. Ожидают начала: {len(upcoming)}")
            
            sent_signals = 0

            # Ограничиваем выборку первыми 40 предстоящими матчами для экономии лимитов
            for match in upcoming[:40]:
                f_id = match['fixture']['id']
                home_n = match['teams']['home']['name']
                away_n = match['teams']['away']['name']
                match_label = f"{home_n} — {away_n}"

                # ШАГ 1: Проверка коэффициентов
                res_odds = await asyncio.to_thread(fetch_api_data, f"https://v3.football.api-sports.io/odds?fixture={f_id}&bookmakers=8")
                bookie = None
                if res_odds and res_odds.get("response") and len(res_odds["response"]) > 0:
                    bookmakers_list = res_odds["response"][0].get("bookmakers", [])
                    # Ищем Bet365 (8) или 1xBet (1)
                    bookie = next((b for b in bookmakers_list if b.get("id") in [1, 8]), None)
                
                if not bookie:
                    continue 

                # Извлекаем маркеты 1X2 и ТБ
                market_1x2 = next((m for m in bookie.get('bets', []) if m.get('id') == 1), None)
                market_over = next((m for m in bookie.get('bets', []) if m.get('id') == 3 or 'Over/Under' in m.get('name', '')), None)

                curr_h = next((float(o['odd']) for o in market_1x2.get('values', []) if o.get('value') == 'Home'), 0) if market_1x2 else 0
                curr_a = next((float(o['odd']) for o in market_1x2.get('values', []) if o.get('value') == 'Away'), 0) if market_1x2 else 0
                curr_ov = next((float(o['odd']) for o in market_over.get('values', []) if 'Over 2.5' in str(o.get('value', ''))), 0) if market_over else 0

                # Если КФ не подходят под стратегию — пропускаем
                if not (1.70 <= curr_h <= 2.50 or 1.70 <= curr_a <= 2.50 or 1.70 <= curr_ov <= 2.30):
                    continue

                # ШАГ 2: Глубокий анализ (прогнозы)
                logging.info(f"🔎 Анализ параметров для: {match_label}")
                res_pred = await asyncio.to_thread(fetch_api_data, f"https://v3.football.api-sports.io/predictions?fixture={f_id}")
                if not res_pred or not res_pred.get("response"): continue
                
                p_data = res_pred["response"][0]
                comp = p_data.get('comparison', {})
                advice = p_data['predictions'].get('advice', '')
                
                try:
                    prob_h = int(p_data['predictions']['percent']['home'].replace('%','')) / 100
                    prob_a = int(p_data['predictions']['percent']['away'].replace('%','')) / 100
                    form_h = int(comp.get('form', {}).get('home', '0%').replace('%',''))
                    form_a = int(comp.get('form', {}).get('away', '0%').replace('%',''))
                    h2h_h = int(comp.get('h2h', {}).get('home', '0%').replace('%',''))
                    h2h_a = int(comp.get('h2h', {}).get('away', '0%').replace('%',''))
                except: continue

                # Логика П1
                if 1.70 <= curr_h <= 2.50 and form_h >= 55 and h2h_h >= 50:
                    edge = (curr_h / (1/prob_h if prob_h > 0 else 99)) - 1
                    if edge >= 0.06:
                        await send_signal(bot, home_n, away_n, "П1", curr_h, edge)
                        sent_signals += 1
                        continue

                # Логика П2
                if 1.70 <= curr_a <= 2.50 and form_a >= 55 and h2h_a >= 50:
                    edge = (curr_a / (1/prob_a if prob_a > 0 else 99)) - 1
                    if edge >= 0.06:
                        await send_signal(bot, home_n, away_n, "П2", curr_a, edge)
                        sent_signals += 1
                        continue

                # Логика ТБ 2.5
                if "Over 2.5 goals" in advice and 1.70 <= curr_ov <= 2.30:
                    await send_signal(bot, home_n, away_n, "ТБ 2.5", curr_ov, 0)
                    sent_signals += 1

            logging.info(f"[SYSTEM] Цикл окончен. Сигналов найдено: {sent_signals}. Сон 15 мин.")
            await asyncio.sleep(900)

        except Exception as e:
            logging.error(f"❌ Критическая ошибка в сканере: {e}", exc_info=True)
            await asyncio.sleep(60)

# --- 5. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def send_signal(bot, home, away, market, kf, edge):
    stats = load_stats()
    bet_amount = round(stats['bank'] * 0.03, 2)
    text = (
        f"💳 **MONSTER PRO: SIGNAL**\n\n"
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
        logging.error(f"Ошибка отправки: {e}")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Сканер Monster PRO запущен и анализирует матчи на сегодня!")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = load_stats()
    await update.message.reply_text(f"📊 **Статистика:**\n💰 Банк: {round(d['bank'], 2)}₽\n✅ Побед: {d['wins']}\n❌ Поражений: {d['losses']}")

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
    await query.edit_message_text(text=f"{query.message.text}\n\n{res}\n📊 Банк обновлен!")

async def post_init(app: Application):
    asyncio.create_task(scanner(app.bot))

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    if not TOKEN: 
        logging.error("BOT_TOKEN не найден!")
        return
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
