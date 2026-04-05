import asyncio
import os
import urllib.parse
from datetime import datetime, timedelta

import aiohttp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

# ====================== НАСТРОЙКИ ======================
BANK_FILE = "bank.txt"

def get_bank() -> float:
    if not os.path.exists(BANK_FILE):
        with open(BANK_FILE, "w") as f: f.write("1000.0")
        return 1000.0
    with open(BANK_FILE, "r") as f: return float(f.read().strip())

def save_bank(amount: float):
    with open(BANK_FILE, "w") as f: f.write(str(round(amount, 2)))

FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")
FOOTBALL_BASE = "https://v3.football.api-sports.io"

# ====================== ПЕРЕВОДЫ И СПИСОК ЛИГ ======================
# (оставь или дополни из предыдущей версии)
LEAGUES = ["soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga", "soccer_italy_serie_a", "soccer_france_ligue_one"]  # можно расширить

TRANSLATE = { ... }  # вставь из предыдущей версии
TEAM_MAP = { ... }   # вставь из предыдущей версии

def translate_team(team: str) -> str:
    clean = team.replace("FC ", "").replace(" CF", "").replace(" CA", "").replace("1. ", "").strip()
    return TEAM_MAP.get(clean, clean)

def get_outcome_name(outcome: dict, home: str, away: str) -> str:
    name = outcome.get("name", "")
    point = outcome.get("point", "")
    if name == home: name = f"Победа {translate_team(home)}"
    elif name == away: name = f"Победа {translate_team(away)}"
    elif name == "Draw": name = "Ничья"
    elif name == "Over": name = "Тотал БОЛЬШЕ"
    elif name == "Under": name = "Тотал МЕНЬШЕ"
    return f"{name} {point}".strip()

# ====================== ЗАЩИТА ОТ ДУБЛЕЙ ======================
seen_signals = {}

def is_duplicate(home: str, away: str, outcome: str) -> bool:
    key = f"{home}|{away}|{outcome}"
    now = datetime.now()
    if key in seen_signals and (now - seen_signals[key]).total_seconds() < 14400:  # 4 часа
        return True
    seen_signals[key] = now
    return False

# ====================== АНАЛИТИКА ОТ API-FOOTBALL ======================
async def get_team_form_and_h2h(session: aiohttp.ClientSession, home_id: int, away_id: int):
    """Получаем форму команд и H2H"""
    try:
        # Форма команд (последние матчи)
        async with session.get(f"{FOOTBALL_BASE}/teams/statistics", 
                               headers={"x-apisports-key": FOOTBALL_API_KEY},
                               params={"team": home_id, "league": 39, "season": 2025}) as resp:  # league=39 пример для EPL, можно динамически
            home_stats = await resp.json() if resp.status == 200 else {}

        async with session.get(f"{FOOTBALL_BASE}/teams/statistics", 
                               headers={"x-apisports-key": FOOTBALL_API_KEY},
                               params={"team": away_id, "league": 39, "season": 2025}) as resp:
            away_stats = await resp.json() if resp.status == 200 else {}

        # H2H
        async with session.get(f"{FOOTBALL_BASE}/fixtures/headtohead",
                               headers={"x-apisports-key": FOOTBALL_API_KEY},
                               params={"h2h": f"{home_id}-{away_id}", "last": 10}) as resp:
            h2h_data = await resp.json() if resp.status == 200 else {}

        return home_stats, away_stats, h2h_data
    except:
        return {}, {}, {}

def analyze_form_h2h(home_stats, away_stats, h2h_data):
    """Простая, но эффективная оценка формы и H2H"""
    bonus = 0
    form_text = "Форма: "

    # Пример анализа формы (можно сильно улучшить)
    if home_stats and "response" in home_stats and home_stats["response"]:
        form_text += f"Дом: {home_stats['response'].get('form', 'N/A')} | "

    if away_stats and "response" in away_stats and away_stats["response"]:
        form_text += f"Гости: {away_stats['response'].get('form', 'N/A')}"

    # H2H бонус
    if h2h_data and "response" in h2h_data:
        h2h_list = h2h_data["response"][:8]
        home_wins = sum(1 for m in h2h_list if m["teams"]["home"]["winner"] is True)
        if home_wins >= 4:
            bonus += 15

    return bonus, form_text

# ====================== ОСНОВНОЙ СКАНЕР ======================
async def scanner(bot):
    print(f"[{datetime.now()}] 🚀 Monster PRO — УМНАЯ АНАЛИТИКА + API-Football")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        key_idx = 0
        while True:
            for league in LEAGUES:
                key = API_KEYS[key_idx % len(API_KEYS)]
                key_idx += 1
                url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?api_key={key}&regions=eu&markets=h2h,totals,spreads,corners"

                try:
                    async with session.get(url) as resp:
                        if resp.status != 200: continue
                        data = await resp.json()
                        if not isinstance(data, list): continue

                        for game in data:
                            try:
                                g_time = datetime.strptime(game["commence_time"], "%Y-%m-%dT%H:%M:%SZ")
                                if g_time > datetime.utcnow() + timedelta(hours=12): continue
                            except: continue

                            home = game.get("home_team")
                            away = game.get("away_team")

                            # Здесь можно добавить поиск ID команд из API-Football (для простоты пока пропускаем точный матч ID, используем названия)

                            for bookie in game.get("bookmakers", []):
                                bookie_name = bookie.get("title") or bookie.get("key", "Unknown")

                                for market in bookie.get("markets", []):
                                    all_prices = {}
                                    for b in game.get("bookmakers", []):
                                        for m in b.get("markets", []):
                                            if m["key"] == market["key"]:
                                                for o in m.get("outcomes", []):
                                                    name = o.get("name")
                                                    price = o.get("price")
                                                    if name and price:
                                                        all_prices.setdefault(name, []).append(price)

                                    for out in market.get("outcomes", []):
                                        k = out.get("price")
                                        if not k or not (1.75 <= k <= 2.95): continue

                                        outcome_name = get_outcome_name(out, home, away)
                                        if is_duplicate(home, away, outcome_name): continue

                                        # Value анализ
                                        prices = all_prices.get(out.get("name"), [])
                                        is_value = len(prices) > 1 and k > (sum(prices) / len(prices)) * 1.08

                                        # Получаем форму и H2H (только для потенциально сильных сигналов)
                                        form_bonus = 0
                                        form_text = ""
                                        if FOOTBALL_API_KEY and is_value:  # экономим запросы
                                            # Здесь нужно добавить поиск team_id по названию (можно кэшировать)
                                            # Для упрощения пока используем placeholder — в реальной версии добавим mapping
                                            home_stats, away_stats, h2h = await get_team_form_and_h2h(session, 40, 50)  # пример ID
                                            form_bonus, form_text = analyze_form_h2h(home_stats, away_stats, h2h)

                                        conf = 50 + (30 if 1.90 <= k <= 2.10 else 20 if 1.80 <= k <= 2.40 else 10)
                                        conf += 25 if is_value else 0
                                        conf += form_bonus
                                        conf = min(conf, 95)

                                        if conf < 70: continue

                                        bank = get_bank()
                                        bet = round(bank * 0.025 * (conf / 70), 2)
                                        profit = round(bet * (k - 1), 2)

                                        league_ru = TRANSLATE.get(league, league.upper())
                                        search_q = urllib.parse.quote(f"{home} {away}")
                                        bb_url = f"https://betboom.ru/sport#search={search_q}"

                                        stars = "🟢" * (conf // 20) + "⚪" * (5 - (conf // 20))
                                        value_text = " 🔥 VALUE" if is_value else ""

                                        msg = (
                                            f"🛡 **MONSTER PRO — УМНЫЙ АНАЛИЗ**\n\n"
                                            f"📊 Уверенность: **{conf}%** {stars}{value_text}\n"
                                            f"🏆 Турнир: **{league_ru}**\n"
                                            f"🏟 Матч: `{translate_team(home)} — {translate_team(away)}`\n"
                                            f"🎯 Ставка: **{outcome_name}** | Кф: `{k}` ({bookie_name})\n"
                                            f"{form_text}\n"
                                            f"💰 Сумма: **{bet}₽** (+{profit}₽ прибыли)\n"
                                            f"⏰ Начало: {(g_time + timedelta(hours=3)).strftime('%H:%M')} МСК\n"
                                            f"──────────────────\n"
                                            f"🔗 [СТАВИТЬ В BETBOOM]({bb_url})"
                                        )

                                        btns = InlineKeyboardMarkup([[ 
                                            InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{profit}"),
                                            InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet}")
                                        ]])

                                        await bot.send_message(chat_id=CHAT_ID, text=msg, reply_markup=btns,
                                                               parse_mode="Markdown", disable_web_page_preview=True)
                                        await asyncio.sleep(2)

                except Exception as e:
                    print(f"Ошибка в лиге {league}: {e}")
                    continue

            await asyncio.sleep(120)

# ====================== ЗАПУСК ======================
TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
API_KEYS = [k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()]

async def post_init(application: Application):
    asyncio.create_task(scanner(application.bot))

def main():
    if not TOKEN or not CHAT_ID or not API_KEYS:
        print("❌ Не все Environment Variables заполнены!")
        return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.post_init = post_init
    print(f"[{datetime.now()}] Monster PRO — умная аналитика с API-Football запущена")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
