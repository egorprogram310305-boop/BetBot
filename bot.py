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

# --- ЛОГИРОВАНИЕ ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger("MonsterV3.8")

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("CHAT_ID")

# --- КЛЮЧИ И ЛИГИ ---
ODDS_KEYS = [os.getenv(f"ODDS_API_KEY_{i}") for i in range(1, 26) if os.getenv(f"ODDS_API_KEY_{i}")]
current_key_idx = 0

TIER_1_LEAGUES = [
    "soccer_epl", "soccer_uefa_champs_league", "soccer_germany_bundesliga",
    "soccer_italy_serie_a", "soccer_spain_la_liga", "soccer_france_ligue1"
]
TIER_2_LEAGUES = [
    "soccer_uefa_europa_league", "soccer_russia_premier_league", "soccer_netherlands_ere_divisie",
    "soccer_portugal_primeira_liga", "soccer_turkey_super_lig"
]

# Кэш для детектора падения линии (Line Lag)
# Структура: { 'event_id_market': last_fair_odd }
last_odds_cache = {}

# --- МАТЕМАТИКА ---
def get_fair_odds(bookies_data, market_key):
    """Вычисляет средний Fair Odds на основе Pinnacle и Betfair (Market Consensus)"""
    sharps = ['pinnacle', 'betfair_ex_eu', 'betonline_ag']
    all_fair_probs = []

    for b_key in sharps:
        bookie = next((b for b in bookies_data if b['key'] == b_key), None)
        if not bookie: continue
        
        market = next((m for m in bookie['markets'] if m['key'] == market_key), None)
        if not market: continue

        odds = [o['price'] for o in market['outcomes']]
        inv_sum = sum(1/o for o in odds)
        fair_probs = [(1/o) / inv_sum for o in odds]
        all_fair_probs.append(fair_probs)

    if not all_fair_probs: return None

    # Усредняем вероятности всех найденных шарпов
    avg_probs = [sum(p) / len(p) for p in zip(*all_fair_probs)]
    return [1/p for p in avg_probs]

# --- API КЛИЕНТ ---
def fetch_odds(league):
    global current_key_idx
    if not ODDS_KEYS: return None

    for _ in range(len(ODDS_KEYS)):
        api_key = ODDS_KEYS[current_key_idx]
        url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/"
        params = {'apiKey': api_key, 'regions': 'eu', 'markets': 'h2h,totals', 'oddsFormat': 'decimal'}
        
        try:
            logger.info(f"📡 Запрос: {league} (Ключ #{current_key_idx + 1})")
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 429:
                current_key_idx = (current_key_idx + 1) % len(ODDS_KEYS)
                continue
            return res.json()
        except:
            current_key_idx = (current_key_idx + 1) % len(ODDS_KEYS)
    return None

# --- СКАНЕР ---
async def scanner(bot):
    logger.info(f"🚀 МОНСТР v3.8 MASTER ЗАПУЩЕН. Обойма: {len(ODDS_KEYS)} ключей.")
    
    while True:
        all_leagues = TIER_1_LEAGUES + TIER_2_LEAGUES
        for league in all_leagues:
            data = await asyncio.to_thread(fetch_odds, league)
            if not data or not isinstance(data, list): continue

            is_tier1 = league in TIER_1_LEAGUES
            edge_threshold = 0.04 if is_tier1 else 0.08
            
            for event in data:
                event_id = event['id']
                h, a = event['home_team'], event['away_team']
                
                # Фильтр времени (от 15 мин до 20 часов)
                start_time = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
                time_to_start = (start_time - datetime.now(timezone.utc)).total_seconds() / 3600
                if time_to_start < 0.25 or time_to_start > 20: continue

                for m_type in ['h2h', 'totals']:
                    fair_odds_list = get_fair_odds(event['bookmakers'], m_type)
                    if not fair_odds_list: continue

                    # Детектор падения линии (Line Lag)
                    cache_key = f"{event_id}_{m_type}"
                    is_hot = False
                    if cache_key in last_odds_cache:
                        # Если текущий "честный" КФ стал значительно ниже старого — линия падает
                        if fair_odds_list[0] < last_odds_cache[cache_key][0] * 0.97:
                            is_hot = True
                            logger.info(f"⚡️ ОБНАРУЖЕНО ПАДЕНИЕ ЛИНИИ: {h} - {a}")
                    last_odds_cache[cache_key] = fair_odds_list

                    # Проверка валуя в "мягких" БК
                    for bookie in event['bookmakers']:
                        # Пропускаем шарпов при поиске валуя
                        if bookie['key'] in ['pinnacle', 'betfair_ex_eu']: continue
                        
                        soft_m = next((m for m in bookie['markets'] if m['key'] == m_type), None)
                        if not soft_m: continue

                        for i, outcome in enumerate(soft_m['outcomes']):
                            s_odd = outcome['price']
                            f_odd = fair_odds_list[i]
                            edge = (s_odd / f_odd) - 1

                            if 1.70 <= s_odd <= 2.60 and edge >= edge_threshold:
                                logger.info(f"✅ НАЙДЕНО: {h} | {outcome['name']} | Edge: {round(edge*100,1)}%")
                                await send_signal(bot, h, a, outcome['name'], s_odd, edge, bookie['title'], is_hot, is_tier1)

            await asyncio.sleep(2) # Защита от перегрузки

        logger.info("🛌 Круг завершен. Пауза 4 минуты.")
        await asyncio.sleep(240)

async def send_signal(bot, h, a, side, odd, edge, bookie, is_hot, is_tier1):
    # Умный стейкинг: база 3%, +1% если HOT, +1% если Tier-1
    percent = 3
    if is_hot: percent += 1
    if is_tier1: percent += 1
    
    label = "⚡️ HOT SIGNAL" if is_hot else "💎 PRO SIGNAL"
    tier_label = "🏆 TOP LEAGUE" if is_tier1 else "📈 REGULAR"
    
    text = (f"<b>{label} | {bookie}</b>\n\n"
            f"⚽️ {h} — {a}\n"
            f"🏅 Лига: {tier_label}\n"
            f"🎯 Ставка: <b>{side}</b>\n"
            f"📈 КФ: <b>{odd}</b> | Edge: <b>+{round(edge*100, 1)}%</b>\n"
            f"💰 Сумма: <b>{percent}%</b>\n\n"
            f"<i>*Консенсус-анализ рынка завершен</i>")
    
    kb = [[InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"w_{percent}_{odd}"),
           InlineKeyboardButton("❌ МИМО", callback_data=f"l_{percent}")]]
    
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    except: pass

# --- ЗАПУСК ---
async def post_init(app: Application):
    asyncio.create_task(scanner(app.bot))

def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), BaseHTTPRequestHandler).serve_forever(), daemon=True).start()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__": main()

