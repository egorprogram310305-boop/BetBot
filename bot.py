import os
import asyncio
import logging
import requests
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler

# --- НАСТРОЙКИ ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("CHAT_ID")

# Ключи
FOOTBALL_KEYS = [os.getenv("FOOTBALL_API_KEY"), os.getenv("FOOTBALL_API_KEY_2")]
FOOTBALL_KEYS = [k for k in FOOTBALL_KEYS if k]
current_key_idx = 0

def fetch_data(endpoint, params=None):
    global current_key_idx
    url = f"https://v3.football.api-sports.io/{endpoint}"
    for _ in range(len(FOOTBALL_KEYS)):
        headers = {'x-apisports-key': FOOTBALL_KEYS[current_key_idx], 'x-rapidapi-host': 'v3.football.api-sports.io'}
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            data = response.json()
            if response.status_code == 429 or (data.get("errors") and "limit" in str(data["errors"])):
                current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
                continue
            return data
        except: continue
    return None

async def scanner(bot):
    logging.info("🚀 ЭКОНОМ-СКАНЕР ЗАПУЩЕН (Лимит под контролем)")
    
    while True:
        try:
            # ШАГ 1: Берем всего 15 ближайших матчей (вместо 100)
            logging.info("[SYSTEM] Проверка 15 ближайших матчей...")
            data = await asyncio.to_thread(fetch_data, "fixtures", {"next": 15})

            if not data or not data.get("response"):
                await asyncio.sleep(3600) # Если ошибка, спим час
                continue

            for item in data['response']:
                f_id = item['fixture']['id']
                h_team = item['teams']['home']['name']
                a_team = item['teams']['away']['name']

                # ШАГ 2: Сначала берем КФ (1 запрос на матч)
                # Если КФ не в диапазоне 1.70-2.50, мы НЕ тратим запрос на статистику!
                odds_res = await asyncio.to_thread(fetch_data, "odds", {"fixture": f_id, "bookmaker": 8})
                
                o_p1, o_p2 = None, None
                if odds_res and odds_res.get("response"):
                    for bet in odds_res['response'][0]['bookmakers'][0]['bets']:
                        if bet['name'] == "Match Winner":
                            for val in bet['values']:
                                if val['value'] == 'Home': o_p1 = float(val['odd'])
                                if val['value'] == 'Away': o_p2 = float(val['odd'])

                # Проверка: стоит ли тратить запрос на статистику?
                if (o_p1 and 1.70 <= o_p1 <= 2.50) or (o_p2 and 1.70 <= o_p2 <= 2.50):
                    logging.info(f"📈 КФ подходит для {h_team} - {a_team}. Запрашиваю статистику...")
                    
                    # ШАГ 3: Только теперь тратим ценный запрос на прогнозы
                    pred_res = await asyncio.to_thread(fetch_data, "predictions", {"fixture": f_id})
                    if not pred_res or not pred_res.get("response"): continue

                    comp = pred_res['response'][0]['comparison']
                    f_h = float(comp['form']['home'].replace('%',''))
                    f_a = float(comp['form']['away'].replace('%',''))
                    h2h_h = float(comp['h2h']['home'].replace('%',''))
                    h2h_a = float(comp['h2h']['away'].replace('%',''))

                    # Проверка по ТЗ (55% форма, 50% H2H)
                    if o_p1 and 1.70 <= o_p1 <= 2.50 and f_h >= 55 and h2h_h >= 50:
                        await send_signal(bot, h_team, a_team, "П1", o_p1)
                    elif o_p2 and 1.70 <= o_p2 <= 2.50 and f_a >= 55 and h2h_a >= 50:
                        await send_signal(bot, h_team, a_team, "П2", o_p2)
                else:
                    logging.info(f"  [SKIP] {h_team} - {a_team}: КФ не интересны ({o_p1}/{o_p2})")

            # ШАГ 4: Увеличиваем время сна до 1 часа (3600 сек)
            # Это позволит боту работать весь день, не убивая лимит за утро
            logging.info("[SYSTEM] Цикл окончен. Сон 1 час...")
            await asyncio.sleep(3600)

        except Exception as e:
            logging.error(f"Ошибка: {e}")
            await asyncio.sleep(300)

async def send_signal(bot, home, away, market, odd):
    text = f"💳 <b>MONSTER PRO</b>\n\n⚽️ {home} — {away}\n🎯 Ставка: <b>{market}</b>\n📈 КФ: <b>{odd}</b>"
    await bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="HTML")

async def post_init(app: Application):
    asyncio.create_task(scanner(app.bot))

def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    logging.info("🤖 Бот запущен в режиме ЭКОНОМИИ ЛИМИТА...")
    app.run_polling()

if __name__ == "__main__":
    main()
