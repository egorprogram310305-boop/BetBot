import asyncio
import os
import urllib.parse
import json
from datetime import datetime
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

# Кэширование
team_id_cache = {} 
processed_matches = {}
odds_history = {} # Для отслеживания движения линии

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

def update_stats(is_win, profit_or_loss):
    data = {"total": 0, "wins": 0, "losses": 0, "turnover": 0.0, "net_profit": 0.0}
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r") as f: data = json.load(f)
    
    data["total"] += 1
    data["turnover"] += abs(profit_or_loss)
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
    if target_name == home_name: bonus += 10 # Home Advantage
    
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
    leagues = ["soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga", "soccer_italy_serie_a"]
    async with aiohttp.ClientSession() as session:
        key_idx = 0
        while True:
            for league in leagues:
                current_key = API_KEYS[key_idx % len(API_KEYS)]
                key_idx += 1
                url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?api_key={current_key}&regions=eu&markets=h2h"
                
                try:
                    async with session.get(url) as resp:
                        if resp.status != 200: continue
                        data = await resp.json()
                        
                        for game in data:
                            home, away = game["home_team"], game["away_team"]
                            match_id = f"{home}_{away}"
                            
                            # Фильтр Dropping Odds
                            current_odds = game["bookmakers"][0]["markets"][0]["outcomes"][0]["price"]
                            if match_id in odds_history:
                                prev_odds = odds_history[match_id]
                                if current_odds < prev_odds * 0.90: # Падение на 10%
                                    continue 
                            odds_history[match_id] = current_odds

                            if match_id in processed_matches: continue

                            # Выбор лучшего исхода
                            best = game["bookmakers"][0]["markets"][0]["outcomes"][0]
                            price = best['price']
                            
                            if 1.85 <= price <= 2.70:
                                h_id = await get_team_id(session, home)
                                a_id = await get_team_id(session, away)
                                bonus = await get_deep_analytics(session, h_id, a_id, best['name'], home)
                                
                                # Критерий Келли (упрощенный b*p-q / b)
                                prob = (0.65 + (bonus/100)) # Наша оценка вероятности
                                q = 1 - prob
                                b = price - 1
                                kelly_factor = (b * prob - q) / b
                                if kelly_factor < 0: kelly_factor = 0.02 # Минимум
                                if kelly_factor > 0.07: kelly_factor = 0.05 # Лимит 5% на ставку
                                
                                bank = get_bank()
                                bet_amount = round(bank * kelly_factor, 2)
                                profit = round(bet_amount * b, 2)
                                
                                msg = (
                                    f"📊 **PRO ANALYTICS: KELLY METHOD**\n\n"
                                    f"🏟 `{home} — {away}`\n"
                                    f"🎯 Ставка: **{best['name']}**\n"
                                    f"📈 Коэффициент: `{price}`\n"
                                    f"⚖️ Индекс Келли: **{round(kelly_factor*100, 1)}% от банка**\n"
                                    f"💰 Сумма: **{bet_amount}₽**\n"
                                    f"📉 Линия: `Стабильна` ✅\n"
                                    f"──────────────────\n"
                                    f"🔗 [BETBOOM](https://betboom.ru/sport#search={urllib.parse.quote(home)})"
                                )
                                
                                btns = InlineKeyboardMarkup([[ 
                                    InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{profit}_{bet_amount}"),
                                    InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet_amount}_{bet_amount}")
                                ]])
                                
                                await bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=btns, parse_mode="Markdown")
                                processed_matches[match_id] = True
                except: continue
            await asyncio.sleep(300)

# ====================== ОБРАБОТКА КОМАНД ======================

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not os.path.exists(STATS_FILE):
        await update.message.reply_text("Статистика пока пуста.")
        return
    with open(STATS_FILE, "r") as f: data = json.load(f)
    
    winrate = (data["wins"] / data["total"] * 100) if data["total"] > 0 else 0
    roi = (data["net_profit"] / data["turnover"] * 100) if data["turnover"] > 0 else 0
    
    msg = (
        f"📈 **ОТЧЕТ ПО ПРИБЫЛИ**\n\n"
        f"Всего сигналов: `{data['total']}`\n"
        f"Победы/Проигрыши: `{data['wins']}/{data['losses']}`\n"
        f"🎯 Winrate: **{round(winrate, 1)}%**\n"
        f"💰 Чистая прибыль: **{round(data['net_profit'], 2)}₽**\n"
        f"🚀 ROI: **{round(roi, 2)}%**\n"
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
        update_stats(True, float(val))
    else:
        new_bank = bank - float(bet)
        update_stats(False, float(bet))
        
    save_bank(new_bank)
    await query.edit_message_text(text=query.message.text + f"\n\n✅ Результат учтен. Банк: {round(new_bank, 2)}₽")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("stats", show_stats))
    app.add_handler(CallbackQueryHandler(handle_callback))
    asyncio.get_event_loop().create_task(scanner(app.bot))
    app.run_polling()

if __name__ == "__main__":
    main()
