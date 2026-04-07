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

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
API_KEY = os.getenv("API_KEY") 
STATS_FILE = "stats.json"

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

# --- 3. СКАНЕР (ПОЛНОСТЬЮ ОБНОВЛЁННЫЙ С ОТЛАДКОЙ) ---
async def scanner(bot):
    logging.info("🛠 MONSTER PRO ULTIMATE v2.8 — СКАНЕР ЗАПУЩЕН (отладка v2.8-debug)")
    
    current_key = API_KEY.strip() if API_KEY else "80ec2103f7e47b2294435a50b57ba4eb"
    headers = {"x-apisports-key": current_key}

    logging.info(f"🔑 Используется API ключ: {'свой' if API_KEY else 'fallback'}")

    while True:
        try:
            logging.info("[SYSTEM] Запуск сканирования 100 матчей...")
            sent_signals = 0

            # 1. Получаем 100 ближайших матчей
            res_fix_response = await asyncio.to_thread(
                requests.get, 
                "https://v3.football.api-sports.io/fixtures?next=100", 
                headers=headers, 
                timeout=15
            )
            res_fix = res_fix_response.json()

            if "response" not in res_fix or not res_fix["response"]:
                logging.info("[SYSTEM] Цикл завершен. Проверено 0 матчей. Отправлено 0 сигналов.")
                await asyncio.sleep(1200)
                continue

            matches = res_fix["response"]
            logging.info(f"Найдено {len(matches)} матчей. Начинаю полный анализ...")

            for match in matches:
                f_id = match['fixture']['id']
                home_name = match['teams']['home']['name']
                away_name = match['teams']['away']['name']

                # === Прогнозы (один запрос на матч) ===
                res_pred_response = await asyncio.to_thread(
                    requests.get, 
                    f"https://v3.football.api-sports.io/predictions?fixture={f_id}", 
                    headers=headers
                )
                res_pred = res_pred_response.json()
                if not res_pred.get("response"):
                    logging.info(f"[REJECTED] {home_name} - {away_name} | Причина: Нет данных прогноза")
                    continue

                p_data = res_pred["response"][0]
                comp = p_data.get('comparison', {})
                advice = p_data['predictions'].get('advice', '')

                # Проценты (только home/away)
                try:
                    prob_home = int(p_data['predictions']['percent']['home'].replace('%','')) / 100
                    prob_away = int(p_data['predictions']['percent']['away'].replace('%','')) / 100
                except:
                    logging.info(f"[REJECTED] {home_name} - {away_name} | Причина: Некорректные проценты")
                    continue

                # === Коэффициенты (приоритет Bet365 id=8 → 1xBet id=1) ===
                # Сначала пытаемся с Bet365
                res_odds_response = await asyncio.to_thread(
                    requests.get, 
                    f"https://v3.football.api-sports.io/odds?fixture={f_id}&bookmakers=8", 
                    headers=headers
                )
                res_odds = res_odds_response.json()

                bookie = None
                if res_odds.get("response") and res_odds["response"]:
                    bookmakers_list = res_odds["response"][0].get("bookmakers", [])
                    bookie = next((b for b in bookmakers_list if b.get("id") == 8), None)

                # Fallback на 1xBet
                if not bookie:
                    res_odds_response = await asyncio.to_thread(
                        requests.get, 
                        f"https://v3.football.api-sports.io/odds?fixture={f_id}&bookmakers=1", 
                        headers=headers
                    )
                    res_odds = res_odds_response.json()
                    if res_odds.get("response") and res_odds["response"]:
                        bookmakers_list = res_odds["response"][0].get("bookmakers", [])
                        bookie = next((b for b in bookmakers_list if b.get("id") == 1), None)

                if not bookie:
                    logging.info(f"[REJECTED] {home_name} - {away_name} | Причина: Нет котировок от Bet365/1xBet")
                    continue

                # === СЦЕНАРИЙ А: П1 (Home) ===
                market_1x2 = next((m for m in bookie.get('bets', []) if m.get('id') == 1), None)
                current_home = None
                if market_1x2:
                    current_home = next((float(o['odd']) for o in market_1x2.get('values', []) if o.get('value') == 'Home'), None)

                if current_home and 1.70 <= current_home <= 2.50:
                    form_home = int(comp.get('form', {}).get('home', '0%').replace('%',''))
                    h2h_home = int(comp.get('h2h', {}).get('home', '0%').replace('%',''))

                    stable = True

                    if form_home >= 55 and h2h_home >= 50 and stable:
                        fair_odd = 1 / prob_home if prob_home > 0 else 999
                        edge = (current_home / fair_odd) - 1

                        if edge >= 0.06:
                            # === УСПЕХ П1 ===
                            stats = load_stats()
                            bank = stats['bank']
                            bet_amount = round(bank * 0.03, 2)
                            kf_display = current_home

                            text = (
                                f"💳 **MONSTER PRO: BETBOOM EDITION**\n\n"
                                f"⚽️ {home_name} — {away_name}\n"
                                f"🎯 Ставка: **П1**\n"
                                f"📈 Ориентир КФ: **{kf_display}**\n"
                                f"🟢 Валуйность: **+{int(edge*100)}%**\n"
                                f"⚠️ В BetBoom ставить, если КФ не ниже **{round(kf_display - 0.05, 2)}**"
                            )
                            kb = [[
                                InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{bet_amount}_{kf_display}"),
                                InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet_amount}")
                            ]]

                            await bot.send_message(
                                chat_id=ADMIN_ID, 
                                text=text, 
                                reply_markup=InlineKeyboardMarkup(kb), 
                                parse_mode="Markdown"
                            )
                            await asyncio.sleep(3)
                            sent_signals += 1

                            logging.info(f"[MATCH FOUND] {home_name} - {away_name} | Рынок: П1, КФ: {current_home}, Edge: {int(edge*100)}%. Отправка сигнала...")
                            continue

                    # === REJECTED (статистика или кэф) ===
                    if form_home < 55 or h2h_home < 50:
                        reason = f"Низкая Форма: {form_home}% (нужно 55%)" if form_home < 55 else f"Низкий H2H: {h2h_home}% (нужно 50%)"
                        logging.info(f"[REJECTED] {home_name} - {away_name} | Причина: {reason}")
                    else:
                        logging.info(f"[REJECTED] {home_name} - {away_name} | Статистика ОК, но Edge {int(edge*100)}% ниже порога 6% или КФ вне диапазона")

                # === СЦЕНАРИЙ Б: П2 (Away) ===
                current_away = None
                if market_1x2:
                    current_away = next((float(o['odd']) for o in market_1x2.get('values', []) if o.get('value') == 'Away'), None)

                if current_away and 1.70 <= current_away <= 2.50:
                    form_away = int(comp.get('form', {}).get('away', '0%').replace('%',''))
                    h2h_away = int(comp.get('h2h', {}).get('away', '0%').replace('%',''))

                    stable = True

                    if form_away >= 55 and h2h_away >= 50 and stable:
                        fair_odd = 1 / prob_away if prob_away > 0 else 999
                        edge = (current_away / fair_odd) - 1

                        if edge >= 0.06:
                            # === УСПЕХ П2 ===
                            stats = load_stats()
                            bank = stats['bank']
                            bet_amount = round(bank * 0.03, 2)
                            kf_display = current_away

                            text = (
                                f"💳 **MONSTER PRO: BETBOOM EDITION**\n\n"
                                f"⚽️ {home_name} — {away_name}\n"
                                f"🎯 Ставка: **П2**\n"
                                f"📈 Ориентир КФ: **{kf_display}**\n"
                                f"🟢 Валуйность: **+{int(edge*100)}%**\n"
                                f"⚠️ В BetBoom ставить, если КФ не ниже **{round(kf_display - 0.05, 2)}**"
                            )
                            kb = [[
                                InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{bet_amount}_{kf_display}"),
                                InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet_amount}")
                            ]]

                            await bot.send_message(
                                chat_id=ADMIN_ID, 
                                text=text, 
                                reply_markup=InlineKeyboardMarkup(kb), 
                                parse_mode="Markdown"
                            )
                            await asyncio.sleep(3)
                            sent_signals += 1

                            logging.info(f"[MATCH FOUND] {home_name} - {away_name} | Рынок: П2, КФ: {current_away}, Edge: {int(edge*100)}%. Отправка сигнала...")
                            continue

                    # REJECTED
                    if form_away < 55 or h2h_away < 50:
                        reason = f"Низкая Форма: {form_away}% (нужно 55%)" if form_away < 55 else f"Низкий H2H: {h2h_away}% (нужно 50%)"
                        logging.info(f"[REJECTED] {home_name} - {away_name} | Причина: {reason}")
                    else:
                        logging.info(f"[REJECTED] {home_name} - {away_name} | Статистика ОК, но Edge {int(edge*100)}% ниже порога 6% или КФ вне диапазона")

                # === СЦЕНАРИЙ В: ТБ 2.5 ===
                market_over = next((m for m in bookie.get('bets', []) if m.get('id') == 3 or 'Over/Under' in m.get('name', '')), None)
                current_over = None
                if market_over:
                    current_over = next((float(o['odd']) for o in market_over.get('values', []) if o.get('value') == 'Over 2.5' or 'Over 2.5' in str(o.get('value', ''))), None)

                if "Over 2.5 goals" in advice and current_over and 1.70 <= current_over <= 2.30:
                    stable = True

                    if stable:
                        # === УСПЕХ ТБ 2.5 ===
                        stats = load_stats()
                        bank = stats['bank']
                        bet_amount = round(bank * 0.03, 2)
                        kf_display = current_over

                        text = (
                            f"💳 **MONSTER PRO: BETBOOM EDITION**\n\n"
                            f"⚽️ {home_name} — {away_name}\n"
                            f"🎯 Ставка: **ТБ 2.5**\n"
                            f"📈 Ориентир КФ: **{kf_display}**\n"
                            f"🟢 Валуйность: **+0%** (рекомендация API)\n"
                            f"⚠️ В BetBoom ставить, если КФ не ниже **{round(kf_display - 0.05, 2)}**"
                        )
                        kb = [[
                            InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{bet_amount}_{kf_display}"),
                            InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet_amount}")
                        ]]

                        await bot.send_message(
                            chat_id=ADMIN_ID, 
                            text=text, 
                            reply_markup=InlineKeyboardMarkup(kb), 
                            parse_mode="Markdown"
                        )
                        await asyncio.sleep(3)
                        sent_signals += 1

                        logging.info(f"[MATCH FOUND] {home_name} - {away_name} | Рынок: ТБ 2.5, КФ: {current_over}, Edge: N/A. Отправка сигнала...")
                        continue

                # Если Over не прошёл
                if "Over 2.5 goals" not in advice:
                    logging.info(f"[REJECTED] {home_name} - {away_name} | Причина: Нет рекомендации Over 2.5 в advice")

            # === ИТОГ ЦИКЛА ===
            logging.info(f"[SYSTEM] Цикл завершен. Проверено {len(matches)} матчей. Отправлено {sent_signals} сигналов.")

            logging.info("😴 Жду 20 минут до следующего сканирования...")
            await asyncio.sleep(1200)

        except Exception as e:
            logging.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА В СКАНЕРЕ: {e}", exc_info=True)
            await asyncio.sleep(60)

# --- 4. МОНИТОРИНГ И КОМАНДЫ ---
async def status_monitor(bot):
    while True:
        try:
            if ADMIN_ID:
                await bot.send_message(chat_id=ADMIN_ID, text=f"🔔 Monster PRO v2.8 в поиске сигналов... 🟢")
        except: pass
        await asyncio.sleep(3600)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот Monster PRO v2.8 запущен по ТЗ ULTIMATE!\nОжидайте сигналы с тотальным логированием.")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = load_stats()
    await update.message.reply_text(f"📊 Банк: {d['bank']}₽\nПобед: {d['wins']} | Поражений: {d['losses']}")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    stats = load_stats()
    data = query.data.split("_")
    action = data[0]
    amt = float(data[1])

    if len(data) > 2:
        kf = float(data[2])
    else:
        kf = 1.0

    if action == "win":
        profit = amt * (kf - 1)
        stats["bank"] += profit
        stats["wins"] += 1
        result_text = f"✅ ЗАШЛО (+{round(profit, 2)}₽)"
    else:
        stats["bank"] -= amt
        stats["losses"] += 1
        result_text = "❌ МИМО"

    save_stats(stats)
    await query.edit_message_text(text=f"{query.message.text}\n\n{result_text}\n📊 Статистика обновлена!")

# --- 5. POST_INIT С ОТЛАДКОЙ (ТОЛЬКО ЭТО ИЗМЕНИЛ) ---
async def post_init(app: Application):
    logging.info("🚀 POST_INIT ЗАПУЩЕН — создаём background tasks...")
    try:
        scanner_task = asyncio.create_task(scanner(app.bot))
        monitor_task = asyncio.create_task(status_monitor(app.bot))
        
        logging.info("✅ Scanner task создан успешно")
        logging.info("✅ Status monitor task создан успешно")
        
        app.bot_data["scanner_task"] = scanner_task
        app.bot_data["monitor_task"] = monitor_task
    except Exception as e:
        logging.error(f"❌ ОШИБКА ПРИ СОЗДАНИИ TASKS: {e}", exc_info=True)

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    if not TOKEN:
        logging.error("❌ BOT_TOKEN не задан!")
        return
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
