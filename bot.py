import os
import asyncio
import threading
import logging
import json
import requests
import traceback
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- УЛУЧШЕННОЕ ЛОГИРОВАНИЕ ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("MonsterBot")

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("CHAT_ID")
STATS_FILE = "stats.json"

# --- РОТАЦИЯ 5 КЛЮЧЕЙ ---
FOOTBALL_KEYS = [os.getenv(f"FOOTBALL_API_KEY_{i}") for i in range(1, 6) if os.getenv(f"FOOTBALL_API_KEY_{i}")]
current_key_idx = 0

def fetch_data(endpoint, params=None):
    global current_key_idx
    if not FOOTBALL_KEYS:
        logger.error("❌ ОШИБКА: API-ключи не найдены в переменных окружения!")
        return None
    
    url = f"https://v3.football.api-sports.io/{endpoint}"
    
    for attempt in range(len(FOOTBALL_KEYS)):
        active_key = FOOTBALL_KEYS[current_key_idx]
        headers = {'x-apisports-key': active_key, 'x-rapidapi-host': 'v3.football.api-sports.io'}
        
        try:
            logger.info(f"📡 Запрос к {endpoint} (Ключ #{current_key_idx + 1})...")
            response = requests.get(url, headers=headers, params=params, timeout=20)
            res_json = response.json()
            
            # Переключаем ключ для следующего раза (ротация)
            current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
            
            if res_json.get("errors"):
                logger.warning(f"⚠️ API вернул ошибку на ключе #{current_key_idx}: {res_json['errors']}")
                continue
                
            return res_json
        except Exception as e:
            logger.error(f"❌ Ошибка сетевого запроса: {e}")
            current_key_idx = (current_key_idx + 1) % len(FOOTBALL_KEYS)
            
    return None

# --- СТАТИСТИКА И СЕРВЕР ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
    def log_message(self, *args): return

def run_health_server():
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 10000))), HealthHandler).serve_forever()

def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f: return json.load(f)
        except Exception as e:
            logger.error(f"⚠️ Не удалось прочитать stats.json: {e}")
    return {"bank": 1000.0, "wins": 0, "losses": 0}

def save_stats(s):
    try:
        with open(STATS_FILE, "w") as f: json.dump(s, f)
    except Exception as e:
        logger.error(f"⚠️ Не удалось сохранить stats.json: {e}")

# --- КОМАНДЫ ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"👤 Пользователь {update.effective_user.id} нажал /start")
    if str(update.effective_user.id) == str(ADMIN_ID):
        await update.message.reply_text("🚀 <b>Monster Pro v2.8.4</b> на связи! Логирование усилено. Жди сигналов.", parse_mode="HTML")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"👤 Пользователь {update.effective_user.id} запросил /stats")
    if str(update.effective_user.id) == str(ADMIN_ID):
        s = load_stats()
        roi = round(((s['bank'] - 1000)/10), 1)
        text = (f"📊 <b>СТАТИСТИКА:</b>\n\n💰 Банк: <b>{round(s['bank'], 2)}₽</b>\n"
                f"✅ Вин: {s['wins']} | ❌ Луз: {s['losses']}\n📈 Профит: {roi}%")
        await update.message.reply_text(text, parse_mode="HTML")

# --- СКАНЕР ---
async def scanner(bot):
    logger.info(f"✅ СКАНЕР ЗАПУЩЕН. Доступно ключей: {len(FOOTBALL_KEYS)}")
    while True:
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            logger.info(f"📅 --- НАЧИНАЮ НОВЫЙ ЦИКЛ СКАНЕРОВКИ ({today}) ---")
            
            data = await asyncio.to_thread(fetch_data, "fixtures", {"date": today, "timezone": "Europe/Moscow"})
            
            if not data or not data.get("response"):
                logger.warning("📭 Список матчей пуст или API недоступно. Жду 10 минут...")
                await asyncio.sleep(600); continue

            upcoming = [m for m in data['response'] if m['fixture']['status']['short'] == 'NS']
            logger.info(f"🔎 Нашел матчей со статусом NS: {len(upcoming)}. Беру первые 25 для детального анализа.")

            for item in upcoming[:25]:
                f_id = item['fixture']['id']
                h_n, a_n = item['teams']['home']['name'], item['teams']['away']['name']
                logger.info(f"🏟 Проверяю матч: {h_n} vs {a_n} (ID: {f_id})")
                
                # 1. Запрос коэффициентов
                odds_data = await asyncio.to_thread(fetch_data, "odds", {"fixture": f_id, "bookmaker": 8})
                await asyncio.sleep(10) # ТЗ: пауза 10 сек

                o_p1, o_p2, o_tb = None, None, None
                if odds_data and odds_data.get("response") and odds_data['response'][0].get('bookmakers'):
                    try:
                        for b in odds_data['response'][0]['bookmakers'][0]['bets']:
                            if b['name'] == "Match Winner":
                                for v in b['values']:
                                    if v['value'] == 'Home': o_p1 = float(v['odd'])
                                    if v['value'] == 'Away': o_p2 = float(v['odd'])
                            if b['name'] == "Over/Under":
                                for v in b['values']:
                                    if v['value'] == 'Over 2.5': o_tb = float(v['odd'])
                        logger.info(f"   📈 КФ найдены: П1:{o_p1}, П2:{o_p2}, ТБ2.5:{o_tb}")
                    except Exception as e:
                        logger.error(f"   ⚠️ Ошибка при парсинге КФ: {e}")
                else:
                    logger.info("   ⏩ КФ отсутствуют в API для этого матча. Пропускаю.")
                    continue

                target = None
                if o_p1 and 1.70 <= o_p1 <= 2.50: target = ("П1", o_p1, "home")
                elif o_p2 and 1.70 <= o_p2 <= 2.50: target = ("П2", o_p2, "away")
                elif o_tb and 1.70 <= o_tb <= 2.30: target = ("ТБ 2.5", o_tb, "over")
                
                if not target:
                    logger.info(f"   ⏩ Отклонено: КФ не входят в диапазоны ТЗ.")
                    continue

                # 2. Запрос прогноза
                logger.info(f"   🎯 КФ подходят ({target[0]}). Запрашиваю прогноз (Predictions)...")
                pred_data = await asyncio.to_thread(fetch_data, "predictions", {"fixture": f_id})
                await asyncio.sleep(10) # ТЗ: пауза 10 сек

                if not pred_data or not pred_data.get("response"):
                    logger.warning("   ⚠️ Не удалось получить прогноз для матча.")
                    continue
                
                res = pred_data['response'][0]
                prob = res['predictions']['percent']
                market, odd, side = target
                
                try:
                    p_val = float(prob[side].replace('%','')) / 100
                    edge = (odd / (1 / p_val)) - 1
                    logger.info(f"   📊 Расчет Edge: Вероятность {p_val*100}%, Edge: {round(edge*100, 2)}%")
                except Exception as e:
                    logger.error(f"   ⚠️ Ошибка расчета Edge: {e}")
                    edge = 0

                # Фильтр валуя
                if edge < 0.06:
                    logger.info(f"   ❌ Отклонено: Низкий Edge ({round(edge*100, 1)}% < 6%)")
                    continue

                advice = res['predictions']['advice']
                comp = res['comparison']
                
                if market in ["П1", "П2"]:
                    f_val = float(comp['form'][side].replace('%',''))
                    h2h_val = float(comp['h2h'][side].replace('%',''))
                    logger.info(f"   📝 Проверка формы: Форма={f_val}%, H2H={h2h_val}%")
                    if f_val >= 55 and h2h_val >= 50:
                        logger.info("   🔥 МАТЧ ПРОШЕЛ ВСЕ ФИЛЬТРЫ! Отправляю сигнал.")
                        await process_signal(bot, h_n, a_n, market, odd, edge, f_val, h2h_val, advice)
                    else:
                        logger.info(f"   ❌ Отклонено: Слабая форма/H2H (нужно 55/50)")
                
                elif market == "ТБ 2.5":
                    logger.info(f"   📝 Проверка ТБ: Совет API = {advice}")
                    if "over 2.5" in str(advice).lower():
                        logger.info("   🔥 МАТЧ ПРОШЕЛ ВСЕ ФИЛЬТРЫ (ТБ)! Отправляю сигнал.")
                        await process_signal(bot, h_n, a_n, market, odd, edge, 0, 0, advice)
                    else:
                        logger.info(f"   ❌ Отклонено: API не подтверждает ТБ 2.5")

            logger.info("🛌 ВСЕ МАТЧИ ПРОВЕРЕНЫ. Ухожу в сон на 90 минут...")
            await asyncio.sleep(5400)
        except Exception as e:
            logger.error(f"‼️ КРИТИЧЕСКАЯ ОШИБКА В ЦИКЛЕ СКАНЕРА:\n{traceback.format_exc()}")
            await asyncio.sleep(600)

async def process_signal(bot, h, a, market, odd, edge, form, h2h, advice):
    score = 0
    if form >= 75 and h2h >= 70: score += 1
    adv_low = str(advice).lower()
    is_adv = (market=="П1" and "home" in adv_low) or (market=="П2" and "away" in adv_low) or (market=="ТБ 2.5" and "over 2.5" in adv_low)
    if edge >= 0.12 or is_adv: score += 1
    
    percent = 3 + score
    stats = load_stats()
    rub = round(stats['bank'] * (percent/100), 2)
    
    text = (f"💳 <b>MONSTER PRO: BETBOOM</b>\n\n⚽️ {h} — {a}\n🎯 Ставка: <b>{market}</b>\n"
            f"📊 Уверенность: [{'⭐' * (score + 1)}]\n📈 КФ: <b>{odd}</b> | Валуй: <b>+{round(edge*100, 1)}%</b>\n"
            f"💰 Сумма: <b>{percent}% ({rub}₽)</b>\n\n⚠️ Не ниже {round(odd - 0.05, 2)}")
    
    kb = [[InlineKeyboardButton("✅ ЗАШЛО", callback_data=f"w_{rub}_{odd}"),
           InlineKeyboardButton("❌ МИМО", callback_data=f"l_{rub}")]]
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        logger.info(f"✅ Сигнал на {h} успешно отправлен в Telegram.")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки сообщения в Telegram: {e}")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    s = load_stats()
    d = query.data.split("_")
    try:
        if d[0] == "w":
            s["bank"] += float(d[1]) * (float(d[2]) - 1); s["wins"] += 1
        else:
            s["bank"] -= float(d[1]); s["losses"] += 1
        save_stats(s)
        await query.edit_message_text(f"{query.message.text_html}\n\n<b>{'✅ ЗАШЛО' if d[0]=='w' else '❌ МИМО'}</b>", parse_mode="HTML")
        logger.info(f"📈 Статистика обновлена: {'Вин' if d[0]=='w' else 'Луз'}. Новый банк: {s['bank']}")
    except Exception as e:
        logger.error(f"❌ Ошибка обработки кнопки: {e}")

async def post_init(app: Application):
    logger.info("🤖 Инициализация фоновых задач...")
    asyncio.create_task(scanner(app.bot))

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    logger.info("🌐 Health-сервер запущен.")
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    logger.info("🚀 Бот начинает опрос обновлений (polling)...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"ГЛОБАЛЬНЫЙ СБОЙ ПРИ ЗАПУСКЕ: {e}")
