import asyncio
import os
import urllib.parse
import json
from datetime import datetime, timedelta, timezone
import aiohttp
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
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

# ====================== ОБМАНКА ДЛЯ RENDER (PORT BINDING) ======================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Monster PRO is running")

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# ====================== ПЕРЕВОД И ФИНАНСЫ ======================
TRANSLATIONS = {
    "Arsenal": "Арсенал", "Liverpool": "Ливерпуль", "Manchester City": "Манчестер Сити",
    "Real Madrid": "Реал Мадрид", "Barcelona": "Барселона", "Draw": "Ничья"
}

def translate_team(name): return TRANSLATIONS.get(name, name)

def get_bank():
    try:
        if not os.path.exists(BANK_FILE): return 1000.0
        with open(BANK_FILE, "r") as f: return float(f.read().strip())
    except: return 1000.0

def save_bank(amount):
    with open(BANK_FILE, "w") as f: f.write(str(round(amount, 2)))

def update_stats(is_win, profit_or_loss, bet_amount):
    data = {"total": 0, "wins": 0, "losses": 0, "turnover": 0.0, "net_profit": 0.0}
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r") as f: data = json.load(f)
    data["total"] += 1
    data["turnover"] += bet_amount
    if is_win:
        data["wins"] += 1
        data["net_profit"] += profit_or_loss
    else:
        data["losses"] += 1
        data["net_profit"] -= profit_or_loss
    with open(STATS_FILE, "w") as f: json.dump(data, f)

# ====================== АНАЛИТИКА И СКАНЕР ======================

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

async def scanner(bot):
    leagues = ["soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga", "soccer_italy_serie_a", "soccer_france_ligue_one"]
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
                        data = await resp.json()
                        for game in data:
                            comm_time = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
                            if comm_time > max_future or comm_time < now_utc: continue
                            
                            home_en, away_en = game["home_team"], game["away_team"]
                            match_id = f"{home_en}_{away_en}_{game['commence_time']}"
                            if match_id in processed_matches: continue
                            
                            market = game["bookmakers"][0]["markets"][0]
                            for best in market["outcomes"]:
                                price = best['price']
                                if 1.85 <= price <= 2.80:
                                    h_id = await get_team_id(session, home_en)
                                    a_id = await get_team_id(session, away_en)
                                    bonus = await get_deep_analytics(session, h_id, a_id, best['name'], home_en)
                                    
                                    conf = round((1/price)*100 + bonus, 1)
                                    kelly = min(( (price-1)*(conf/100) - (1-conf/100) ) / (price-1), 0.05)
                                    if kelly < 0.015: continue
                                    
                                    bank = get_bank()
                                    bet_amt = round(bank * kelly, 2)
                                    
                                    msg = (f"📊 **PRO АНАЛИТИКА**\n\n🏟 `{translate_team(home_en)} — {translate_team(away_en)}`\n"
                                           f"🎯 Ставка: **Победа {translate_team(best['name'])}**\n📈 Кф: `{price}`\n🔥 Шанс: **{conf}%**\n"
                                           f"💰 Сумма: **{bet_amt}₽**\n──────────────────\n"
                                           f"🔗 [BETBOOM](https://betboom.ru/sport#search={urllib.parse.quote(home_en)})")
                                    
                                    btns = InlineKeyboardMarkup([[InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{round(bet_amt*(price-1),2)}_{bet_amt}"),
                                                                  InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet_amt}_{bet_amt}")]])
                                    await bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=btns, parse_mode="Markdown")
                                    processed_matches[match_id] = True
                except: continue
            await asyncio.sleep(300)

# ====================== ОБРАБОТКА ======================

async def show_stats(update, context):
    if not os.path.exists(STATS_FILE): return
    with open(STATS_FILE, "r") as f: data = json.load(f)
    roi = (data["net_profit"] / data["turnover"] * 100) if data["turnover"] > 0 else 0
    await update.message.reply_text(f"📈 ROI: {round(roi, 2)}%\n💰 Профит: {round(data['net_profit'], 2)}₽")

async def handle_callback(update, context):
    q = update.callback_query
    res, val, bet = q.data.split("_")
    bank = get_bank()
    if res == "win":
        new_bank = bank + float(val)
        update_stats(True, float(val), float(bet))
    else:
        new_bank = bank - float(bet)
        update_stats(False, float(bet), float(bet))
    save_bank(new_bank)
    await q.edit_message_text(text=q.message.text + f"\n\n🏁 Учтено. Банк: {round(new_bank, 2)}₽")

def main():
    import signal

# ... (предыдущий код функций scanner, show_stats и т.д. остается) ...

def main():
    # 1. Запуск сервера-пустышки для Render в отдельном потоке
    print("📡 Запуск Health-Check сервера...")
    threading.Thread(target=run_health_server, daemon=True).start()
    
    # 2. Инициализация приложения Telegram
    if not TOKEN:
        print("❌ ОШИБКА: Токен бота (BOT_TOKEN) не найден в переменных окружения!")
        return

    app = Application.builder().token(TOKEN).build()
    
    # 3. Регистрация обработчиков (ВАЖНО: до запуска сканера)
    app.add_handler(CommandHandler("stats", show_stats))
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Бот запущен и готов!")))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # 4. Запуск сканера в фоновом цикле через задачу
    async def start_background_tasks(application: Application):
        asyncio.create_task(scanner(application.bot))
        print("🚀 Сканер матчей запущен в фоновом режиме")

    # Добавляем задачу в хук после инициализации бота
    app.post_init = start_background_tasks

    print("✅ Бот начинает прослушивание сообщений...")
    
    # 5. Запуск бота (drop_pending_updates=True очистит старые нажатия)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
