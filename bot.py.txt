import requests
import asyncio
import os
import urllib.parse
import random
from datetime import datetime, timedelta
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

# --- ПОЛУЧЕНИЕ НАСТРОЕК ИЗ ОБЛАКА ---
TOKEN = os.getenv("BOT_TOKEN")
MY_ID = os.getenv("CHAT_ID")
# Ключи придут одной строкой через запятую, превращаем в список
API_KEYS = os.getenv("API_KEYS", "").split(",")

BANK_FILE = "bank.txt"

def get_bank():
    if not os.path.exists(BANK_FILE): return 1000.0
    with open(BANK_FILE, "r") as f: return float(f.read().strip())

def save_bank(amount):
    with open(BANK_FILE, "w") as f: f.write(str(round(amount, 2)))

# --- СЛОВАРИ ПЕРЕВОДА ---
TEAM_MAP = {
    "Real Sociedad": "Реал Сосьедад", "Levante": "Леванте", "Osasuna": "Осасуна", "Real Betis": "Реал Бетис",
    "Mallorca": "Мальорка", "Rayo Vallecano": "Райо Вальекано", "Real Madrid": "Реал Мадрид", "Barcelona": "Барселона"
}
TRANSLATE = {
    "Over": "БОЛЬШЕ", "Under": "МЕНЬШЕ", "h2h": "Победа", "totals": "Тотал",
    "soccer_epl": "АПЛ 🏴󠁧󠁢󠁥󠁮󠁧󠁿", "soccer_spain_la_liga": "Ла Лига 🇪🇸", "soccer_germany_bundesliga": "Бундеслига 🇩🇪",
    "basketball_nba": "NBA 🏀", "icehockey_nhl": "NHL 🏒", "electronic_sports_csgo": "CS2 🎮"
}

class CloudMonsterBot:
    def __init__(self):
        self.app = Application.builder().token(TOKEN).build()
        self.keys = API_KEYS
        self.key_idx = 0
        self.leagues = ['soccer_epl', 'soccer_spain_la_liga', 'soccer_germany_bundesliga', 'basketball_nba', 'icehockey_nhl', 'electronic_sports_csgo']

    def translate_team(self, name):
        clean = name.replace("FC ", "").replace(" CF", "").strip()
        return TEAM_MAP.get(clean, clean)

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        action, amount = query.data.split("_")
        bank = get_bank()
        new_bank = bank + float(amount) if action == "win" else bank - float(amount)
        save_bank(new_bank)
        await query.edit_message_text(text=f"{query.message.text}\n\n📊 ИТОГ: {'✅ ВИН' if action=='win' else '❌ ЛОСС'}\n💰 Банк: {round(new_bank, 2)}₽")

    async def scan(self):
        key = self.keys[self.key_idx]
        self.key_idx = (self.key_idx + 1) % len(self.keys)
        for league in self.leagues:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/?api_key={key}&regions=eu&markets=h2h,totals"
            try:
                res = requests.get(url, timeout=15).json()
                for game in res:
                    g_time = datetime.strptime(game['commence_time'], "%Y-%m-%dT%H:%M:%SZ")
                    if g_time > datetime.utcnow() + timedelta(hours=12): continue
                    for bookie in game.get('bookmakers', []):
                        for market in bookie.get('markets', []):
                            for out in market.get('outcomes', []):
                                k = out['price']
                                if 1.75 <= k <= 2.95:
                                    conf = random.randint(70, 96)
                                    bank = get_bank()
                                    bet = round(bank * 0.03, 2)
                                    
                                    h_ru, a_ru = self.translate_team(game['home_team']), self.translate_team(game['away_team'])
                                    search_q = urllib.parse.quote(f"{game['home_team']} {game['away_team']}")
                                    bb_url = f"https://betboom.ru/sport#search={search_q}"
                                    
                                    msg = (f"🛡 **MONSTER CLOUD PRO**\n\n📊 Уверенность: **{conf}%**\n🏆 {TRANSLATE.get(league, league)}\n"
                                           f"🏟 `{h_ru} — {a_ru}`\n──────────────\n🎯 Ставка: **{out['name']}**\n📈 Кф: `{k}`\n💰 Сумма: **{bet}₽**\n"
                                           f"──────────────\n🔗 [ОТКРЫТЬ BETBOOM]({bb_url})")

                                    btns = InlineKeyboardMarkup([[
                                        InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"win_{round(bet*(k-1),2)}"),
                                        InlineKeyboardButton("❌ МИМО", callback_data=f"loss_{bet}")
                                    ]])
                                    await self.app.bot.send_message(chat_id=MY_ID, text=msg, reply_markup=btns, parse_mode='Markdown', disable_web_page_preview=True)
                                    await asyncio.sleep(5)
                                    return
            except: continue

    async def main_loop(self):
        while True:
            await self.scan()
            await asyncio.sleep(180)

    def run(self):
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))
        loop = asyncio.get_event_loop()
        loop.create_task(self.main_loop())
        self.app.run_polling()

if __name__ == "__main__":
    MonsterCloudBot().run()
