import asyncio
import os
import urllib.parse
import json
from datetime import datetime, timedelta, timezone
import aiohttp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

# ====================== НАСТРОЙКИ ======================
TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEYS = [k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()]
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")

BANK_FILE = "bank.txt"
STATS_FILE = "stats.json"
FOOTBALL_BASE = "https://v3.football.api-sports.io"

team_id_cache = {} 
processed_matches = {}
odds_history = {}

# ====================== РАСШИРЕННЫЙ СЛОВАРЬ ======================
TRANSLATIONS = {
    "Arsenal": "Арсенал", "Liverpool": "Ливерпуль", "Manchester City": "Манчестер Сити",
    "Manchester United": "Манчестер Юнайтед", "Chelsea": "Челси", "Tottenham": "Тоттенхэм",
    "Real Madrid": "Реал Мадрид", "Barcelona": "Барселона", "Atletico Madrid": "Атлетико Мадрид",
    "Bayern Munich": "Бавария", "Borussia Dortmund": "Боруссия Д", "PSG": "ПСЖ",
    "Juventus": "Ювентус", "Inter Milan": "Интер", "AC Milan": "Милан", "Napoli": "Наполи",
    "Bayer Leverkusen": "Байер", "RB Leipzig": "РБ Лейпциг", "Lazio": "Лацио", "Roma": "Рома",
    "Monaco": "Монако", "Marseille": "Марсель", "Lyon": "Лион", "Benfica": "Бенфика", 
    "Porto": "Порту", "Sporting CP": "Спортинг", "Ajax": "Аякс", "PSV Eindhoven": "ПСВ",
    "Galatasaray": "Галатасарай", "Fenerbahce": "Фенербахче", "Zenit": "Зенит", "Spartak Moscow": "Спартак",
    "Draw": "Ничья"
}

def translate_team(name):
    return TRANSLATIONS.get(name, name)

def format_bet_name(outcome_name):
    if outcome_name == "Draw": return "Ничья"
    return f"Победа {translate_team(outcome_name)}"

# ====================== РАБОТА С ДАННЫМИ ======================

def get_bank() -> float:
    try:
        if not os.path.exists(BANK_FILE):
            with open(BANK_FILE, "w") as f: f.write("1000.0")
            return 1000.0
        with open(BANK_FILE, "r") as f: return float(f.read().strip())
    except: return 1000.0

def save_bank(amount: float):
    with open(BANK_FILE, "w") as f: f.write(str(round(amount, 2)))

def update_stats(is_win, profit_or_loss, bet_amount):
    data = {"total": 0, "wins": 0, "losses": 0, "turnover": 0.0, "net_profit": 0.0}
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r") as f:
            try: data = json.load(f)
            except: pass
    data["total"] += 1
    data["turnover"] += bet_amount
    if is_win:
        data["wins"] += 1
        data["net_profit"] += profit_or_loss
    else:
        data["losses"] += 1
        data["net_profit"] -= profit_or_loss
    with open(STATS_FILE, "w") as f: json.dump(data, f)

# ====================== МОДУЛЬ АНАЛИТИКИ ======================

async def get_team_id(session, team_name):
    if team_name in team_id_cache: return team_id_cache[team_name]
    headers = {"x-apisports-key": FOOTBALL_API_KEY}
    try:
        async with session.get(f"{FOOTBALL_BASE}/teams", headers=headers, params={"search": team_name}) as resp:
            data = await resp.json()
            if data.get("response"):
                t_id = data["response"][0]["team"]["id"]
                team_id_cache[team_name] = t_id
                return t_id
    except: pass
    return None

async def get_deep_analytics(session, home_id, away_id, target_name, home_name):
    if not home_id or not away_id: return 0
    headers = {"x-apisports-key": FOOTBALL_API_KEY}
    bonus = 0
    if target_name == home_name: bonus += 12
    try:
        params = {"h2h": f"{home_id}-{away_id}", "last": 5}
        async with session.get(f"{FOOTBALL_BASE}/fixtures/headtohead", headers=headers, params=params) as resp:
            h2h = await resp.json()
            matches = h2h.get("response", [])
            win_count = sum(1 for m in matches if (target_name == home_name and m["teams"]["home"]["winner"]) or (target_name != home_name and m["teams"]["away"]["winner"]))
            if win_count >= 3: bonus += 15
    except: pass
    return bonus

# ====================== ЯДРО СКАНЕРА ======================

async def scanner(bot):
    print("🌍 Monster PRO: Глобальный охват лиг запущен")
    # Расширенный список лиг
    leagues = [
        "soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga", 
        "soccer_italy_serie_a", "soccer_france_ligue_one", "soccer_netherlands_ere_divisie",
        "soccer_portugal_primeira_liga", "soccer_turkey_super_lig", "soccer_belgium_first_div",
        "soccer_brazil_campeonato", "soccer_usa_mls", "soccer_uefa_champs_league"
    ]
    
    async with aiohttp.ClientSession() as session:
        key_idx = 0
        while True:
            now_utc = datetime.now(timezone.utc)
            max_future = now_utc + timedelta(hours=24)

            for league in leagues:
                if not API_KEYS: continue
                current_key = API_KEYS[key_idx % len(API_KEYS)]
                key_idx += 1
                
                url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?api_key={current_key}&regions=eu&markets=h2h"
                
                try:
                    async with session.get(url) as resp:
                        if resp.status != 200: continue
                        data = await resp.json()
                        
                        for game in data:
                            commence_time = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
                            if commence_time > max_future or commence_time < now_utc: continue

                            home_en, away_en = game["home_team"], game["away_team"]
                            match_id = f"{home_en}_{away_en}_{game['commence_time']}"
                            if match_id in processed_matches: continue

                            if not game.get("bookmakers"): continue
                            market = game["bookmakers"][0]["markets"][0]
                            
                            for best in market["outcomes"]:
                                price = best['price']
                                # Качественный диапазон КФ
                                if 1.85 <= price <= 2.80:
                                    h_id = await get_team_id(session, home_en)
                                    a_id = await get_team_id(session, away_en)
                                    bonus = await get_deep_analytics(session, h_id, a_id, best['name'], home_en)
                                    
                                    # Фильтр качества: не пускать слабые сигналы в новых лигах
                                    if bonus < 10 and price > 2.30: continue

                                    base_prob = (1 / price) * 100
                                    confidence = round(base_prob + bonus, 1)
                                    if confidence > 98: confidence = 98.0
                                    
                                    prob_decimal = confidence / 100
                                    b = price - 1
                                    kelly = (b * prob_decimal - (1 - prob_decimal)) / b
                                    
                                    if kelly < 0.015: continue # Увеличен порог входа для стабильности
                                    kelly = min(kelly, 0.05) 
                                    
                                    bank = get_bank()
                                    bet_amount = round(bank * kelly, 2)
                                    profit = round(bet_amount * b, 2)
                                    
                                    bars = int(confidence / 10)
                                    progress_bar = "🟢" * bars + "⚪" * (10 - bars)
                                    
                                    home_ru, away_ru = translate_team(home_en), translate_team(away_en)
                                    bet_text_final = format_bet_name(best['name'])

                                    search_q = urllib.parse.quote(f"{home_en} {away_en}")
                                    msg = (
                                        f"📊 **PRO АНАЛИТИКА: {home_ru}**\n\n"
                                        f"🏟 `{home_ru} — {away_ru}`\n"
                                        f"⏰ Начало: `{(commence_time + timedelta(hours=3)).strftime('%d.%m %H:%M')} (МСК)`\n"
                                        f"──────────────────\n"
                                        f"🎯 СТАВКА: **{bet_text_final}**\n"
                                        f"📈 Коэффициент: `{price}`\n"
                                        f"🔥 Проходимость: **{confidence}%**\n"
                                        f"{progress_bar}\n"
                                        f"──────────────────\n"
                                        f"⚖️ Индекс Келли: `{round(kelly*100, 1)}%`\n"
                                        f"💰 Сумма: **{bet_amount}₽**\n"
                                        f"──────────────────\n"
                                        f"🔗 [BETBOOM](https://betboom.ru/sport#search={search_q})"
                                    )
                                    
                                    btns = InlineKeyboardMarkup([[ 
                                        InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{profit}_{bet_amount}"),
                                        InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet_amount}_{bet_amount}")
                                    ]])
                                    
                                    await bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=btns, parse_mode="Markdown")
                                    processed_matches[match_id] = True
                                    break 
                except: continue
            await asyncio.sleep(300)

# ====================== ОБРАБОТКА КОМАНД ======================

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not os.path.exists(STATS_FILE):
        await update.message.reply_text("Статистика пока пуста.")
        return
    with open(STATS_FILE, "r") as f:
        try: data = json.load(f)
        except: return
    winrate = (data["wins"] / data["total"] * 100) if data["total"] > 0 else 0
    roi = (data["net_profit"] / data["turnover"] * 100) if data["turnover"] > 0 else 0
    msg = (
        f"📈 **ОТЧЕТ ПО ПРИБЫЛИ**\n\n"
        f"Всего сигналов: `{data['total']}`\n"
        f"🎯 Winrate: **{round(winrate, 1)}%**\n"
        f"🚀 ROI: **{round(roi, 2)}%**\n"
        f"💰 Чистая прибыль: **{round(data['net_profit'], 2)}₽**\n"
        f"🏦 Текущий банк: `{get_bank()}₽`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    res, val, bet = query.data.split("_")
    bank = get_bank()
    if res == "win":
        new_bank = bank + float(val)
        update_stats(True, float(val), float(bet))
        res_text = f"✅ ВЫИГРЫШ (+{val}₽)"
    else:
        new_bank = bank - float(bet)
        update_stats(False, float(bet), float(bet))
        res_text = f"❌ ПРОИГРЫШ (-{bet}₽)"
    save_bank(new_bank)
    await query.edit_message_text(text=query.message.text + f"\n\n🏁 **{res_text}**\nТекущий банк: {round(new_bank, 2)}₽")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("stats", show_stats))
    app.add_handler(CallbackQueryHandler(handle_callback))
    asyncio.get_event_loop().create_task(scanner(app.bot))
    app.run_polling()
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

# Мини-сервер, чтобы Render не ругался
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive")

def run_health_check():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# В функции main() добавь запуск этого потока:
# threading.Thread(target=run_health_check, daemon=True).start()

if __name__ == "__main__":
    main()
