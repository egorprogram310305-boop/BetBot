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
logger = logging.getLogger("MonsterV4.1_Fixed")

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("CHAT_ID")
TIME_OFFSET = 3 

RAW_KEYS = os.getenv("ODDS_API_KEYS", "")
ODDS_KEYS = [k.strip() for k in RAW_KEYS.split(",") if k.strip()]

current_key_idx = 0
key_remaining = {}
odds_history = {}

# Лиги разделены по уровням для гибких порогов Edge
TIER_1_LEAGUES = ["soccer_epl", "soccer_germany_bundesliga", "soccer_italy_serie_a", "soccer_spain_la_liga", "soccer_france_ligue_one", "soccer_uefa_champs_league"]
TIER_2_LEAGUES = ["soccer_russia_premier_league", "soccer_netherlands_ere_divisie", "soccer_portugal_primeira_liga", "soccer_efl_champ", "soccer_uefa_europa_league", "soccer_turkey_super_league", "soccer_belgium_first_division_a", "soccer_usa_mls", "soccer_brazil_campeonato"]

# --- СТАТИСТИКА ---
def load_stats():
    if os.path.exists("stats.json"):
        try:
            with open("stats.json", "r") as f: return json.load(f)
        except: pass
    return {"bank": 1000.0, "wins": 0, "losses": 0}

def save_stats(s):
    with open("stats.json", "w") as f: json.dump(s, f)

# --- ЛОГИКА АНАЛИЗА ---
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
        
        # Расширенная маржа до 1.12 для захвата Tier-2 рынков
        if inv_sum > 1.12: continue
            
        all_fair_probs.append([(1/o) / inv_sum for o in odds])
        
    if not all_fair_probs: return None
    avg_probs = [sum(p) / len(p) for p in zip(*all_fair_probs)]
    return [1/p for p in avg_probs]

def fetch_odds(league):
    global current_key_idx
    for _ in range(len(ODDS_KEYS)):
        api_key = ODDS_KEYS[current_key_idx]
        url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/"
        params = {'apiKey': api_key, 'regions': 'eu', 'markets': 'h2h,totals,spreads', 'oddsFormat': 'decimal'}
        try:
            res = requests.get(url, params=params, timeout=15)
            if res.headers.get('x-requests-remaining'):
                key_remaining[current_key_idx + 1] = res.headers.get('x-requests-remaining')
            if res.status_code == 200: return res.json()
            elif res.status_code in [401, 403, 429]:
                current_key_idx = (current_key_idx + 1) % len(ODDS_KEYS)
                continue
            else: return None
        except:
            current_key_idx = (current_key_idx + 1) % len(ODDS_KEYS)
    return None

# --- ГЛАВНЫЙ СКАНЕР ---
async def scanner(bot):
    logger.info("🚀 МОНИТОРИНГ ЗАПУЩЕН (EDGE: 3.0%-3.5%)")
    global odds_history
    while True:
        try:
            if len(odds_history) > 15000: odds_history.clear()
            
            all_leagues = TIER_1_LEAGUES + TIER_2_LEAGUES
            for league in all_leagues:
                data = await asyncio.to_thread(fetch_odds, league)
                if data is None or not isinstance(data, list): continue
                
                # Мягкие пороги для выходных
                edge_threshold = 0.030 if league in TIER_1_LEAGUES else 0.035

                for event in data:
                    st = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                    diff_h = (st - datetime.now(timezone.utc)).total_seconds() / 3600
                    if not (0.25 < diff_h < 48): continue
                    
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
                            
                            # Логирование подозрительных матчей в консоль Render
                            if edge >= 0.015:
                                logger.info(f"🔍 {event['home_team']} | КФ: {s_odd} | Edge: {edge*100:.1f}%")
                            
                            # Основной фильтр: кэфы 1.5 - 4.0
                            if 1.50 <= s_odd <= 4.0 and edge >= edge_threshold:
                                uid = f"{event['id']}_{m_type}_{outcome.get('name')}_{outcome.get('point', '')}"
                                prev_f_odd = odds_history.get(uid)
                                is_steam = prev_f_odd and (prev_f_odd - f_odd >= 0.05)
                                odds_history[uid] = f_odd
                                
                                p = 1/f_odd; b = s_odd - 1
                                kelly = ((p * s_odd - 1) / b) * 0.25
                                kelly_pct = max(0.01, min(0.05, kelly))
                                await send_signal(bot, event, outcome.get('name', 'N/A'), outcome.get('point', ''), s_odd, edge, kelly_pct, m_type, is_steam)
                
                await asyncio.sleep(1)
            await asyncio.sleep(240)
        except Exception:
            logger.error(f"❌ Ошибка сканера: {traceback.format_exc()}")
            await asyncio.sleep(60)

async def send_signal(bot, ev, side, point, odd, edge, k_pct, m_type, is_steam_move):
    s = load_stats(); rub = round(s['bank'] * k_pct, 2)
    local_time = (datetime.fromisoformat(ev['commence_time'].replace('Z', '+00:00')) + timedelta(hours=3)).strftime("%H:%M")
    market_name = "ФОРА" if m_type == 'spreads' else "ТОТАЛ" if m_type == 'totals' else "ИСХОД"
    point_str = f"({point})" if point is not None and point != '' else ""
    
    text = f"<b>🔥 BETBOOM: {market_name}</b>\n\n"
    if is_steam_move: text += "📉 <b>STEAM MOVE: КЭФ В МИРЕ ПАДАЕТ!</b>\n\n"
    text += (f"⚽️ {ev['home_team']} — {ev['away_team']}\n"
             f"⏰ Начало: <b>{local_time}</b> (МСК)\n"
             f"🎯 Ставка: <b>{side} {point_str}</b>\n"
             f"📈 КФ: <b>{odd}</b> (Edge: +{round(edge*100,1)}%)\n"
             f"💰 Ставим: <b>{rub}₽</b>\n")
             
    kb = [[InlineKeyboardButton("✅ WIN", callback_data=f"w_{rub}_{odd}"), InlineKeyboardButton("❌ LOSS", callback_data=f"l_{rub}")]]
    await bot.send_message(ADMIN_ID, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

# --- ОБРАБОТКА КНОПОК ---
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('_')
    action = data[0]
    s = load_stats()

    if action == 'w':
        profit = float(data[1]) * (float(data[2]) - 1)
        s['bank'] += profit; s['wins'] += 1
        res = f"✅ +{round(profit, 2)}₽"
    elif action == 'l':
        loss = float(data[1])
        s['bank'] -= loss; s['losses'] += 1
        res = f"❌ -{round(loss, 2)}₽"

    save_stats(s)
    new_text = query.message.text + f"\n\nИТОГ: {res}\n💰 БАНК: {round(s['bank'], 2)}₽"
    await query.edit_message_text(text=new_text, parse_mode="HTML")

# --- КОМАНДЫ ---
async def start_cmd(u, c): 
    if str(u.effective_user.id) == str(ADMIN_ID): await u.message.reply_text("🚀 Monster v4.1: СИСТЕМА ОНЛАЙН")
async def stats_cmd(u, c):
    s = load_stats(); await u.message.reply_text(f"📊 БАНК: {round(s['bank'], 2)}₽\n✅ {s['wins']} | ❌ {s['losses']}")
async def set_bank_cmd(u, c):
    try: b = float(c.args[0]); s = load_stats(); s['bank'] = b; save_stats(s); await u.message.reply_text(f"💰 Банк обновлен: {b}₽")
    except: pass
async def keys_cmd(u, c):
    t = "🔑 Остаток запросов:\n"
    for k, v in key_remaining.items(): t += f"Ключ #{k}: {v}\n"
    await u.message.reply_text(t)

# --- ИНФРАСТРУКТУРА ---
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

if __name__ == "__main__":
    main()


