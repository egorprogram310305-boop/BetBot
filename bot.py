import os
import asyncio
import threading
import logging
import json
import requests
import traceback
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- НАСТРОЙКИ ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("MonsterV4.0_Pro")

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("CHAT_ID")
TIME_OFFSET = 3  # МОСКВА (UTC+3)

# Загрузка ключей с проверкой
ODDS_KEYS = [os.getenv(f"ODDS_API_KEY_{i}") for i in range(1, 26) if os.getenv(f"ODDS_API_KEY_{i}")]
current_key_idx = 0
key_remaining = {}

# Очищенные списки лиг (без 404 ошибок)
TIER_1_LEAGUES = ["soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", "soccer_spain_la_liga", "soccer_uefa_champs_league"]
TIER_2_LEAGUES = ["soccer_russia_premier_league"]

last_odds_cache = {}

def load_stats():
    if os.path.exists("stats.json"):
        try:
            with open("stats.json", "r") as f: return json.load(f)
        except: pass
    return {"bank": 1000.0, "wins": 0, "losses": 0}

def save_stats(s):
    with open("stats.json", "w") as f: json.dump(s, f)

def get_fair_odds(bookies_data, market_key):
    sharps = ['pinnacle', 'betfair_ex_eu', 'betonline_ag']
    all_fair_probs = []
    for b_key in sharps:
        bookie = next((b for b in bookies_data if b['key'] == b_key), None)
        if not bookie: continue
        market = next((m for m in bookie['markets'] if m['key'] == market_key), None)
        if not market: continue
        odds = [o['price'] for o in market['outcomes']]
        inv_sum = sum(1/o for o in odds)
        all_fair_probs.append([(1/o) / inv_sum for o in odds])
    if not all_fair_probs: return None
    avg_probs = [sum(p) / len(p) for p in zip(*all_fair_probs)]
    return [1/p for p in avg_probs]

def fetch_odds(league):
    global current_key_idx
    if not ODDS_KEYS:
        logger.error("❌ КЛЮЧИ ODDS_API НЕ НАЙДЕНЫ В ENV!")
        return "ERROR_NO_KEYS"
    
    for _ in range(len(ODDS_KEYS)):
        api_key = ODDS_KEYS[current_key_idx]
        url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/"
        params = {'apiKey': api_key, 'regions': 'eu', 'markets': 'h2h,totals,spreads', 'oddsFormat': 'decimal'}
        try:
            res = requests.get(url, params=params, timeout=15)
            remaining = res.headers.get('x-requests-remaining')
            if remaining: key_remaining[current_key_idx + 1] = remaining
            
            if res.status_code == 200:
                return res.json()
            elif res.status_code == 429:
                logger.warning(f"⚠️ Ключ #{current_key_idx+1} исчерпан (429). Переключаюсь...")
                current_key_idx = (current_key_idx + 1) % len(ODDS_KEYS)
                continue
            else:
                logger.error(f"❌ Ошибка API: {res.status_code} для {league}")
                return None
        except Exception as e:
            logger.error(f"❌ Ошибка запроса: {e}")
            current_key_idx = (current_key_idx + 1) % len(ODDS_KEYS)
    return None

# --- КОМАНДЫ ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) == str(ADMIN_ID):
        await update.message.reply_text(f"🚀 <b>Monster v4.0 PRO АКТИВЕН</b>\nКлючей в обойме: {len(ODDS_KEYS)}", parse_mode="HTML")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) == str(ADMIN_ID):
        s = load_stats(); text = f"📊 <b>БАНК: {round(s['bank'], 2)}₽</b>\n✅ {s['wins']} | ❌ {s['losses']}"
        await update.message.reply_text(text, parse_mode="HTML")

async def set_bank_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) == str(ADMIN_ID):
        try:
            new_bank = float(context.args[0])
            s = load_stats(); s['bank'] = new_bank; save_stats(s)
            await update.message.reply_text(f"💰 Банк: <b>{new_bank}₽</b>", parse_mode="HTML")
        except: await update.message.reply_text("Формат: /setbank 5000")

async def keys_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) == str(ADMIN_ID):
        text = "🔑 <b>Лимиты:</b>\n"
        for k, v in key_remaining.items(): text += f"Ключ #{k}: {v}\n"
        await update.message.reply_text(text if key_remaining else "Круг еще не пройден.", parse_mode="HTML")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); s = load_stats(); d = query.data.split("_")
    try:
        if d[0] == "w": s["bank"] += float(d[1]) * (float(d[2]) - 1); s["wins"] += 1
        else: s["bank"] -= float(d[1]); s["losses"] += 1
        save_stats(s); await query.edit_message_text(f"{query.message.text_html}\n\n<b>✅ ОБРАБОТАНО</b>", parse_mode="HTML")
    except: pass

# --- СКАНЕР ---
async def scanner(bot):
    logger.info(f"🚀 СИСТЕМА МОНИТОРИНГА ЗАПУЩЕНА (UTC+{TIME_OFFSET})")
    while True:
        try:
            # --- НОВЫЙ БЛОК: НОЧНОЙ РЕЖИМ ---
            current_hour = (datetime.now(timezone.utc) + timedelta(hours=TIME_OFFSET)).hour
            if 1 <= current_hour <= 9:
                sleep_time = 1200 # 20 минут пауза ночью
                logger.info(f"🌙 Ночной режим. Спим 20 минут (Сейчас {current_hour}:00 МСК)")
            else:
                sleep_time = 240  # 4 минуты пауза днем
            # --------------------------------

            for league in (TIER_1_LEAGUES + TIER_2_LEAGUES):
                data = await asyncio.to_thread(fetch_odds, league)
                
                if data == "ERROR_NO_KEYS":
                    logger.error("🛑 СКАНЕР ОСТАНОВЛЕН: Нет ключей в настройках!")
                    return
                if data is None or not isinstance(data, list):
                    logger.info(f"📡 {league}: Данные не получены или пусты.")
                    continue
                
                total_matches = len(data)
                suitable_count = 0
                edge_threshold = 0.04 if league in TIER_1_LEAGUES else 0.08

                for event in data:
                    st = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                    # Фильтр по времени: от 15 минут до 20 часов до старта
                    if not (0.25 < (st - datetime.now(timezone.utc)).total_seconds() / 3600 < 20): continue
                    
                    # Проверяем исходы, тоталы и форы
                    for m_type in ['h2h', 'totals', 'spreads']:
                        fair_odds = get_fair_odds(event['bookmakers'], m_type)
                        if not fair_odds: continue
                        
                        bb = next((b for b in event['bookmakers'] if b['key'] == 'betboom'), None)
                        if not bb: continue
                        market = next((m for m in bb['markets'] if m['key'] == m_type), None)
                        if not market: continue
                        
                        for i, outcome in enumerate(market['outcomes']):
                            s_odd, f_odd = outcome['price'], fair_odds[i]
                            edge = (s_odd / f_odd) - 1
                            
                            # Проверка перевеса и диапазона кэфов
                            if 1.65 <= s_odd <= 3.0 and edge >= edge_threshold:
                                suitable_count += 1
                                p = 1/f_odd; b = s_odd - 1
                                # Расчет Келли (дробный 0.25)
                                kelly = ((p * s_odd - 1) / b) * 0.25
                                kelly_pct = max(0.01, min(0.05, kelly)) # от 1% до 5%
                                await send_signal(bot, event, outcome.get('name', 'N/A'), outcome.get('point', ''), s_odd, edge, kelly_pct, m_type)
                
                logger.info(f"📡 {league.replace('soccer_', '')}: Матчей: {total_matches} | Найдено: {suitable_count}")
                await asyncio.sleep(2) # 2 секунды между лигами
            
            logger.info(f"🛌 Круг завершен. Пауза {sleep_time // 60} мин.")
            await asyncio.sleep(sleep_time)
            
        except Exception:
            logger.error(f"❌ Ошибка цикла: {traceback.format_exc()}")
            await asyncio.sleep(60)

async def send_signal(bot, ev, side, point, odd, edge, k_pct, m_type):
    s = load_stats(); rub = round(s['bank'] * k_pct, 2)
    local_time = (datetime.fromisoformat(ev['commence_time'].replace('Z', '+00:00')) + timedelta(hours=TIME_OFFSET)).strftime("%H:%M")
    market_name = "ФОРА" if m_type == 'spreads' else "ТОТАЛ" if m_type == 'totals' else "ИСХОД"
    point_str = f"({point})" if point != '' else ""
    text = (f"<b>🔥 BETBOOM: {market_name}</b>\n\n"
            f"⚽️ {ev['home_team']} — {ev['away_team']}\n"
            f"⏰ Начало: <b>{local_time}</b> (МСК)\n"
            f"🎯 Ставка: <b>{side} {point_str}</b>\n"
            f"📈 КФ: <b>{odd}</b> (Edge: +{round(edge*100,1)}%)\n"
            f"💰 Ставим: <b>{rub}₽</b> ({round(k_pct*100,1)}%)\n")
    kb = [[InlineKeyboardButton("✅", callback_data=f"w_{rub}_{odd}"), InlineKeyboardButton("❌", callback_data=f"l_{rub}")]]
    await bot.send_message(ADMIN_ID, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

# --- ЗАПУСК ---
class Health(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, format, *args): return

async def post_init(app: Application): asyncio.create_task(scanner(app.bot))

def main():
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', port), Health).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("setbank", set_bank_cmd))
    app.add_handler(CommandHandler("keys", keys_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.run_polling()

if __name__ == "__main__": main()




