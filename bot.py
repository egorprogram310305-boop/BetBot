import asyncio
import os
import urllib.parse
import requests
import aiohttp
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

# ====================== НАСТРОЙКИ ======================
TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEYS = [k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()]
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY") # Опционально

BANK_FILE = "bank.txt"
seen_signals = {}
FOOTBALL_BASE = "https://v3.football.api-sports.io"

# Словари (минимум для работы)
TRANSLATE = {"soccer_epl": "Англия. Премьер-лига", "soccer_spain_la_liga": "Испания. Ла Лига"}
TEAM_MAP = {} # Сюда можно добавлять переводы: {"Arsenal": "Арсенал"}

def get_bank() -> float:
    try:
        if not os.path.exists(BANK_FILE):
            with open(BANK_FILE, "w") as f: f.write("1000.0")
            return 1000.0
        with open(BANK_FILE, "r") as f: return float(f.read().strip())
    except: return 1000.0

def save_bank(amount: float):
    with open(BANK_FILE, "w") as f: f.write(str(round(amount, 2)))

def translate_team(team: str) -> str:
    return TEAM_MAP.get(team, team)

def get_outcome_name(outcome: dict, home: str, away: str) -> str:
    name = outcome.get("name", "")
    point = str(outcome.get("point", ""))
    if name == home: name = f"Победа {translate_team(home)}"
    elif name == away: name = f"Победа {translate_team(away)}"
    elif name == "Draw": name = "Ничья"
    elif name == "Over": name = "Тотал БОЛЬШЕ"
    elif name == "Under": name = "Тотал МЕНЬШЕ"
    return f"{name} {point}".strip()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🤖 Monster PRO запущен!\nТекущий банк: {get_bank()}₽")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    action = data[0]
    amount = float(data[1])
    
    bank = get_bank()
    if action == "win":
        new_bank = bank + amount
        text = f"✅ Доход зафиксирован: +{amount}₽"
    else:
        new_bank = bank - amount
        text = f"❌ Убыток зафиксирован: -{amount}₽"
    
    save_bank(new_bank)
    await query.edit_message_caption(caption=query.message.caption + f"\n\n💰 {text}\nНовый банк: {round(new_bank, 2)}₽")

def is_duplicate(home: str, away: str, outcome: str) -> bool:
    key = f"{home}|{away}|{outcome}"
    now = datetime.now()
    if key in seen_signals and (now - seen_signals[key]).total_seconds() < 14400:
        return True
    seen_signals[key] = now
    return False

# ====================== ОСНОВНОЙ СКАНЕР ======================
async def scanner(bot):
    print(f"🚀 Monster PRO запущен. Ключей: {len(API_KEYS)}")
    async with aiohttp.ClientSession() as session:
        key_idx = 0
        leagues = ["soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga", "soccer_italy_serie_a"]
        
        while True:
            for league in leagues:
                if not API_KEYS: continue
                current_key = API_KEYS[key_idx % len(API_KEYS)]
                key_idx += 1
                
                url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?api_key={current_key}&regions=eu&markets=h2h,totals"
                
                try:
                    async with session.get(url) as resp:
                        if resp.status != 200: continue
                        data = await resp.json()
                        
                        for game in data:
                            home = game["home_team"]
                            away = game["away_team"]
                            
                            for bookie in game.get("bookmakers", []):
                                if bookie['key'] not in ['marathonbet', 'pinnacle', 'williamhill']: continue
                                
                                for market in bookie.get("markets", []):
                                    for out in market.get("outcomes", []):
                                        k = out['price']
                                        if not (1.80 <= k <= 2.80): continue
                                        
                                        outcome_name = get_outcome_name(out, home, away)
                                        if is_duplicate(home, away, outcome_name): continue

                                        # Расчет уверенности
                                        conf = 75 if 1.9 <= k <= 2.2 else 70
                                        bank = get_bank()
                                        bet = round(bank * 0.03, 2)
                                        profit = round(bet * (k - 1), 2)
                                        
                                        search_q = urllib.parse.quote(f"{home} {away}")
                                        bb_url = f"https://betboom.ru/sport#search={search_q}"

                                        msg = (
                                            f"🛡 **MONSTER PRO — SIGNAL**\n\n"
                                            f"📊 Уверенность: **{conf}%**\n"
                                            f"🏆 Турнир: {TRANSLATE.get(league, league)}\n"
                                            f"🏟 `{home} — {away}`\n"
                                            f"🎯 Ставка: **{outcome_name}** | Кф: `{k}`\n"
                                            f"💰 Ставим: **{bet}₽**\n"
                                            f"──────────────────\n"
                                            f"🔗 [СТАВИТЬ В BETBOOM]({bb_url})"
                                        )

                                        btns = InlineKeyboardMarkup([[ 
                                            InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{profit}"),
                                            InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet}")
                                        ]])

                                        await bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=btns, parse_mode="Markdown")
                                        await asyncio.sleep(2)
                except: continue
            await asyncio.sleep(180)

def main():
    if not TOKEN or not CHAT_ID: return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # Запуск сканера
    loop = asyncio.get_event_loop()
    loop.create_task(scanner(app.bot))
    
    app.run_polling()

if __name__ == "__main__":
    main()
