import os
import asyncio
import logging
import requests
import psycopg2
import random
import re
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web
from deep_translator import GoogleTranslator

# --- CONFIG & SYSTEM ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Baron_v7_2_FULL")

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHAT_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0"))
API_KEYS = [k.strip() for k in os.getenv("ODDS_API_KEYS", "").split(",") if k.strip()]
REQUISITES = os.getenv("PAYMENT_REQUISITES", "Реквизиты не установлены")
DB_URL = os.getenv("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)

T_MGMT = int(os.getenv("TOPIC_ID_MANAGEMENT", "0"))
T_LOGS = int(os.getenv("TOPIC_ID_LOGS", "0")) 
T_CASH = int(os.getenv("TOPIC_ID_PAYMENTS", "0"))
T_USERS = int(os.getenv("TOPIC_ID_USERS", "0"))
T_PRED = int(os.getenv("TOPIC_ID_PREDICTIONS", "0"))

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

TARIFFS = {
    "mini": {"name": "МиниⓂ️ (3 дней)", "days": 3, "price": 149},
    "lite": {"name": "Лайт✨ (7 дней)", "days": 7, "price": 299},
    "classic": {"name": "Классика🎩 (30 дней)", "days": 30, "price": 999},
    "level": {"name": "Уровень💹 (60 дней)", "days": 60, "price": 1666}
}

LEAGUES = [
    "soccer_germany_bundesliga", "soccer_epl", "soccer_netherlands_eredivisie",
    "soccer_spain_la_liga", "soccer_belgium_first_div", "soccer_switzerland_superleague",
    "soccer_austria_bundesliga", "soccer_usa_mls", "soccer_japan_j_league",
    "soccer_brazil_campeonato", "soccer_norway_eliteserien"
]

# --- FSM STATES ---
class AdminStates(StatesGroup):
    wait_min_odds = State()
    wait_mult = State()
    wait_time = State()
    wait_broadcast_msg = State()

class BetStates(StatesGroup):
    wait_amount = State()
    wait_odds = State()

# --- DATABASE LOGIC ---
def init_db():
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("CREATE TABLE IF NOT EXISTS users (uid BIGINT PRIMARY KEY, username TEXT, sub_end TIMESTAMP, trial_used INTEGER DEFAULT 0)")
        curr.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        curr.execute("""
    INSERT INTO settings (key, value) 
    VALUES ('min_odds', '1.50'), ('max_odds', '2.50'), ('multiplier', '0.90'), ('time_depth', '24'), ('ping_mode', '0') ON CONFLICT DO NOTHING""")
        conn.commit(); curr.close(); conn.close()
    except Exception as e: logger.error(f"DB Error: {e}")

def get_setting(key, default):
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("SELECT value FROM settings WHERE key = %s", (key,))
        res = curr.fetchone(); curr.close(); conn.close()
        return float(res[0]) if res else default
    except: return default

def set_setting(key, value):
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = %s", (key, str(value), str(value)))
        conn.commit(); curr.close(); conn.close()
    except: pass

def check_and_add_user(uid, username):
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("SELECT trial_used FROM users WHERE uid = %s", (uid,))
        res = curr.fetchone()
        if not res:
            end_trial = datetime.now(timezone.utc) + timedelta(days=3)
            curr.execute("INSERT INTO users (uid, username, sub_end, trial_used) VALUES (%s, %s, %s, 1)", (uid, username, end_trial))
            conn.commit(); curr.close(); conn.close()
            return True, True 
        curr.close(); conn.close()
        return False, False
    except: return False, False

def get_sub_status(uid):
    if uid == ADMIN_ID: return True
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("SELECT sub_end FROM users WHERE uid = %s", (uid,))
        res = curr.fetchone(); curr.close(); conn.close()
        if res: return res[0].replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
    except: pass
    return False

# --- ANALYTICS & UTILS ---
def clean_and_translate(name):
    bad = ["U23", "U21", "U19", "FC", "CF", "SSC", "AS", "Utd", "United", "BSC", "AC", "City", "Real", "St", "De", "Club", "FK"]
    cleaned = " ".join([w for w in name.split() if w not in bad]).strip()
    try: return GoogleTranslator(source='auto', target='ru').translate(cleaned)
    except: return cleaned

async def analyze_strict(team_name, is_home):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0)'}
        location = "home" if is_home else "away"
        query = f"{team_name} {location} goals last 5 matches results"
        res = requests.get(f"https://www.google.com/search?q={query}", headers=headers, timeout=7)
        content = res.text.lower()
        
        if "captcha" in content: return "CAPTCHA", 0
        if "clean sheet" in content or "strong defense" in content or "0-0" in content:
            return "CANCEL", 0
            
        scores = re.findall(r'(\d)-\d' if is_home else r'\d-(\d)', content)
        itb_count = sum(1 for s in scores[:5] if int(s) >= 2)
        return itb_count, 0
    except: return -1, 0

# --- UI HELPER ---
def main_menu_kb():
    builder = ReplyKeyboardBuilder()
    builder.button(text="🎯 Поиск Прогнозов")
    builder.button(text="👤 Профиль")
    builder.button(text="💳 Подписка")
    return builder.adjust(2).as_markup(resize_keyboard=True)

# --- BOT INTERFACE ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    is_new, trial_given = check_and_add_user(m.from_user.id, m.from_user.username)
    
    if is_new and T_USERS != 0:
        await bot.send_message(ADMIN_GROUP_ID, f"🆕 <b>Новый пользователь!</b>\n👤 @{m.from_user.username} (<code>{m.from_user.id}</code>)", message_thread_id=T_USERS, parse_mode=ParseMode.HTML)
    
    welcome_text = (
        "🎩 <b>Baron’s Verdict: Твой вердикт — прибыли!</b>\n\n"
        "Добро пожаловать в закрытый аналитический клуб! Здесь решения принимаются на основе данных, а не эмоций. Наши алгоритмы ежедневно фильтруют сотни событий, оставляя только самые перспективные индивидуальные тоталы.\n"
        "🎁 Тебе открыт VIP-доступ на 3 дня!\n"
        "Используй это время, чтобы бесплатно протестировать точность наших прогнозов и ощутить мощь алгоритмического беттинга.\n"
        "📍 Твой результат сегодня — это наша аналитика вчера.\n\n"
        "<i>Выбирай действие:\n1. 🎯 Получить прогноз — поиск актуальных исходов на ближайшие матчи.\n2. 👤 Профиль — статус твоей подписки и настройки.</i>"
    )
    if trial_given:
        welcome_text += "\n\n🎁 Вам автоматически начислен <b>пробный доступ на 3 дня!</b> Вы уже можете получать прогнозы."

    await m.answer(welcome_text, reply_markup=main_menu_kb(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "👤 Профиль")
async def btn_profile(m: types.Message):
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    try:
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("SELECT sub_end FROM users WHERE uid = %s", (m.from_user.id,))
        res = curr.fetchone(); curr.close(); conn.close()
        date_str = res[0].strftime('%d.%m.%Y %H:%M') if res and get_sub_status(m.from_user.id) else "Нет доступа или истекла"
        await m.answer(f"👤 <b>Ваш профиль</b>\nID: <code>{m.from_user.id}</code>\nПодписка до: <b>{date_str}</b>", parse_mode=ParseMode.HTML)
    except: await m.answer("Ошибка БД")

@dp.message(F.text == "💳 Подписка")
async def btn_sub(m: types.Message):
    kb = InlineKeyboardBuilder()
    for tid, d in TARIFFS.items(): kb.button(text=f"{d['name']} - {d['price']}₽", callback_data=f"buy_{tid}")
    await m.answer("📊 <b>Выберите тарифный план:</b>\n<i>Инвестируйте в качественную аналитику.</i>", reply_markup=kb.adjust(1).as_markup(), parse_mode=ParseMode.HTML)

@dp.message(F.text == "🎯 Поиск прогнозов")
async def btn_analytics(m: types.Message):
    if get_sub_status(m.from_user.id):
        # Теперь здесь нет ссылки, а есть callback_data
        kb_user = InlineKeyboardBuilder()
        kb_user.button(text="🎯 Поиск прогнозов", callback_data="start_scanning_msg")
        
        await m.answer(
            "Ваша подписка активна! Вы можете перейти в закрытый канал с прогнозами.", 
            reply_markup=kb_user.as_markup()
        )
    else:
        await m.answer("У вас нет активной подписки. Перейдите в раздел 💳 Подписка.")

@dp.callback_query(F.data.startswith("buy_"))
async def buy(c: types.CallbackQuery):
    tid = c.data.split("_")[1]
    pid = random.randint(1000, 9999)
    text = (f"💳 <b>Оплата тарифа {TARIFFS[tid]['name']}</b>\n\n"
            f"Переведите <b>{TARIFFS[tid]['price']}₽</b> по реквизитам:\n"
            f"💳 Карта: <code>{REQUISITES}</code>\n\n"
            f"⚠️ Важно: В комментарии к платежу укажите код: <code>#{pid}</code>\n\n"
            f"<i>После перевода нажмите кнопку ниже.</i>")
    kb = InlineKeyboardBuilder().button(text="✅ Я оплатил", callback_data=f"done_{tid}_{pid}")
    await c.message.edit_text(text, reply_markup=kb.adjust(1).as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("done_"))
async def done(c: types.CallbackQuery):
    _, tid, pid = c.data.split("_")
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Одобрить", callback_data=f"adm_ok_{tid}_{c.from_user.id}")
    kb.button(text="❌ Отклонить", callback_data=f"adm_no_{c.from_user.id}")
    await bot.send_message(ADMIN_GROUP_ID, f"💰 <b>Заявка на оплату!</b>\nОт: @{c.from_user.username}\nID: <code>{c.from_user.id}</code>\nТариф: {TARIFFS[tid]['name']}\nСумма: {TARIFFS[tid]['price']}₽\nКод: #{pid}", 
                           reply_markup=kb.adjust(2).as_markup(), message_thread_id=T_CASH, parse_mode=ParseMode.HTML)
    await c.message.edit_text("⏳ <b>Заявка отправлена!</b>\nОжидайте проверки администратором.", parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("adm_ok_"))
async def adm_ok(c: types.CallbackQuery):
    _, _, tid, uid = c.data.split("_")
    end = datetime.now(timezone.utc) + timedelta(days=TARIFFS[tid]['days'])
    conn = psycopg2.connect(DB_URL); curr = conn.cursor()
    curr.execute("UPDATE users SET sub_end = %s WHERE uid = %s", (end, int(uid))); conn.commit(); curr.close(); conn.close()
    
    try:
    # Теперь тут callback_data вместо url
        kb_user = InlineKeyboardBuilder()
        kb_user.button(text="🎯 Поиск прогнозов", callback_data="start_scanning_msg")
        
        await bot.send_message(
            int(uid), 
            "🎉 <b>Оплата успешно подтверждена!</b> Доступ открыт.", 
            reply_markup=kb_user.as_markup(), 
            parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Ошибка уведомления: {e}")

    await c.message.edit_text(f"{c.message.text}\n\n✅ <b>ОДОБРЕНО</b>")

@dp.callback_query(F.data.startswith("adm_no_"))
async def adm_no(c: types.CallbackQuery):
    uid = c.data.split("_")[2]
    try: await bot.send_message(int(uid), "❌ <b>Оплата не найдена.</b> Заявка отклонена. Если это ошибка, обратитесь к администратору в описании бота.", parse_mode=ParseMode.HTML)
    except: pass
    await c.message.edit_text(f"{c.message.text}\n\n❌ <b>ОТКЛОНЕНО</b>")
    
@dp.callback_query(F.data == "start_scanning_msg")
async def process_start_scanning_press(c: types.CallbackQuery):
    await c.message.answer(
        "🔎 Сканирование матчей началось… Как появится идеальный прогноз, вам придет уведомление 📣"
    )
    # Это убирает состояние загрузки на кнопке
    await c.answer()


# --- ADMIN PANEL ---

@dp.message(Command("admin"))
async def admin_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    
    mo = get_setting("min_odds", 1.5)
    ml = get_setting("multiplier", 0.9)
    td = get_setting("time_depth", 24)
    
    text = (f"🛠 <b>Панель управления</b>\n\n"
            f"Ключей API: <b>{len(API_KEYS)}</b>\n"
            f"Мин. Кэф: <b>{mo}</b>\n"
            f"Множитель: <b>{ml}</b>\n"
            f"Анализ (часы): <b>{td}ч</b>")
    
    kb = InlineKeyboardBuilder()
    kb.button(text="⚙️ Мин. Кэф", callback_data="set_adm_min_odds")
    kb.button(text="📉 Множитель", callback_data="set_adm_mult")
    kb.button(text="⏳ Глубина", callback_data="set_adm_time")
    kb.button(text="📢 Рассылка", callback_data="start_broadcast")
    
    await bot.send_message(m.chat.id, text, reply_markup=kb.adjust(2).as_markup(), parse_mode=ParseMode.HTML, message_thread_id=T_MGMT)

@dp.callback_query(F.data.startswith("set_adm_"))
async def set_adm_params(c: types.CallbackQuery, state: FSMContext):
    action = c.data.replace("set_adm_", "")
    prompts = {"min_odds": "минимальный кэф", "mult": "множитель", "time": "глубину анализа (в часах)"}
    
    await bot.send_message(c.message.chat.id, f"🔢 Введите новое значение для: <b>{prompts.get(action, 'параметра')}</b>", message_thread_id=T_MGMT, parse_mode=ParseMode.HTML)
    
    if action == "min_odds": await state.set_state(AdminStates.wait_min_odds)
    elif action == "mult": await state.set_state(AdminStates.wait_mult)
    elif action == "time": await state.set_state(AdminStates.wait_time)
    await c.answer()

@dp.message(StateFilter(AdminStates.wait_min_odds, AdminStates.wait_mult, AdminStates.wait_time))
async def save_adm_params(m: types.Message, state: FSMContext):
    if m.chat.id != ADMIN_GROUP_ID: return
    cur_state = await state.get_state()
    try:
        val = float(m.text.replace(",", "."))
        if "wait_min_odds" in str(cur_state): set_setting("min_odds", val)
        elif "wait_mult" in str(cur_state): set_setting("multiplier", val)
        elif "wait_time" in str(cur_state): set_setting("time_depth", val)
        
        await bot.send_message(m.chat.id, f"✅ Настройка <b>{val}</b> успешно сохранена!", message_thread_id=T_MGMT, parse_mode=ParseMode.HTML)
    except ValueError:
        await bot.send_message(m.chat.id, "❌ Ошибка! Введите число (например: 1.5 или 0.9)", message_thread_id=T_MGMT)
    await state.clear()

@dp.message(Command("ping"))
async def cmd_ping_toggle(m: types.Message, command: CommandObject):
    if m.from_user.id != ADMIN_ID: return
    arg = command.args
    if arg == "999":
        set_setting("ping_mode", "1")
        await m.answer("🛰 **Режим детального отчета ВКЛЮЧЕН.**", message_thread_id=T_MGMT, parse_mode=ParseMode.HTML)
    elif arg == "1":
        set_setting("ping_mode", "0")
        await m.answer("💤 **Режим детального отчета ВЫКЛЮЧЕН.**", message_thread_id=T_MGMT, parse_mode=ParseMode.HTML)

@dp.message(Command("give_sub"))
async def give_sub_cmd(m: types.Message, command: CommandObject):
    if m.from_user.id != ADMIN_ID: return
    try:
        uid, days = map(int, command.args.split())
        end = datetime.now(timezone.utc) + timedelta(days=days)
        conn = psycopg2.connect(DB_URL); curr = conn.cursor()
        curr.execute("UPDATE users SET sub_end = %s WHERE uid = %s", (end, uid)); conn.commit(); curr.close(); conn.close()
        await m.answer(f"✅ Доступ выдан ID {uid} на {days} дней.", message_thread_id=T_MGMT)
        try: await bot.send_message(uid, f"🎁 Администратор выдал вам доступ на {days} дней!")
        except: pass
    except: await m.answer("⚠️ Формат: `/give_sub ID ДНИ`", message_thread_id=T_MGMT)

@dp.callback_query(F.data == "start_broadcast")
async def start_broad(c: types.CallbackQuery, state: FSMContext):
    await bot.send_message(c.message.chat.id, "📝 Отправьте сообщение для рассылки:", message_thread_id=T_MGMT)
    await state.set_state(AdminStates.wait_broadcast_msg)
    await c.answer()

@dp.message(StateFilter(AdminStates.wait_broadcast_msg))
async def process_broad(m: types.Message, state: FSMContext):
    if m.chat.id != ADMIN_GROUP_ID: return
    conn = psycopg2.connect(DB_URL); curr = conn.cursor()
    curr.execute("SELECT uid FROM users"); users = curr.fetchall(); curr.close(); conn.close()
    count = 0
    for u in users:
        try:
            await m.copy_to(chat_id=u[0])
            count += 1
            await asyncio.sleep(0.05)
        except: continue
    await bot.send_message(m.chat.id, f"📢 Рассылка завершена. Доставлено: {count}", message_thread_id=T_MGMT)
    await state.clear()
@dp.message(Command("ping"))
async def cmd_ping_toggle(m: types.Message, command: CommandObject):
    # Проверяем, что пишет именно админ
    if m.from_user.id != ADMIN_ID: 
        return
        
    arg = command.args # Получаем то, что написано после /ping
    
    if arg == "999":
        set_setting("ping_mode", "1")
        await m.answer("🛰 <b>PING ON:</b> Детальные отчеты включены в Логи.", message_thread_id=T_MGMT, parse_mode=ParseMode.HTML)
    elif arg == "1":
        set_setting("ping_mode", "0")
        await m.answer("💤 <b>PING OFF:</b> Детальные отчеты выключены.", message_thread_id=T_MGMT, parse_mode=ParseMode.HTML)
    else:
        await m.answer("⚠️ Используйте: <code>/ping 999</code> (ВКЛ) или <code>/ping 1</code> (ВЫКЛ)", message_thread_id=T_MGMT, parse_mode=ParseMode.HTML)

# --- PREDICTIONS INTERACTION ---
@dp.callback_query(F.data == "pred_skip")
async def pred_skip(c: types.CallbackQuery):
    await c.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(c.message.chat.id, "⏩ Матч пропущен.", message_thread_id=T_PRED)

@dp.callback_query(F.data == "pred_place")
async def pred_place(c: types.CallbackQuery, state: FSMContext):
    msg = await bot.send_message(c.message.chat.id, "Введите сумму ставки (₽):", message_thread_id=T_PRED)
    await state.update_data(orig_msg_id=c.message.message_id, prompt1=msg.message_id)
    await state.set_state(BetStates.wait_amount)

@dp.message(StateFilter(BetStates.wait_amount))
async def wait_amount(m: types.Message, state: FSMContext):
    await state.update_data(amount=m.text, prompt2=m.message_id)
    msg = await m.answer("Введите реальный коэффициент в БК:", message_thread_id=T_PRED)
    await state.update_data(prompt3=msg.message_id)
    await state.set_state(BetStates.wait_odds)

@dp.message(StateFilter(BetStates.wait_odds))
async def wait_real_odds(m: types.Message, state: FSMContext):
    data = await state.get_data()
    orig_msg_id = data.get("orig_msg_id")
    amt = data.get("amount")
    odds = m.text
    for msg_id in [data.get("prompt1"), data.get("prompt2"), data.get("prompt3"), m.message_id]:
        try: await bot.delete_message(m.chat.id, msg_id)
        except: pass
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ ВИН", callback_data=f"res_win_{orig_msg_id}_{amt}_{odds}")
    kb.button(text="🔄 ВОЗВРАТ", callback_data=f"res_ret_{orig_msg_id}_{amt}_{odds}")
    kb.button(text="❌ ЛОСС", callback_data=f"res_loss_{orig_msg_id}_{amt}_{odds}")
    try:
        await bot.edit_message_reply_markup(chat_id=m.chat.id, message_id=orig_msg_id, reply_markup=kb.as_markup())
        await bot.send_message(m.chat.id, f"📝 Ставка: {amt}₽ под {odds}", reply_to_message_id=orig_msg_id, message_thread_id=T_PRED)
    except: pass
    await state.clear()

@dp.callback_query(F.data.startswith("res_"))
async def bet_result(c: types.CallbackQuery):
    _, res_type, msg_id, amt, odds = c.data.split("_")
    res_text = "✅ ПОБЕДА" if res_type == "win" else "🔄 ВОЗВРАТ" if res_type == "ret" else "❌ ПРОИГРЫШ"
    profit = round(float(amt) * float(odds) - float(amt), 2) if res_type == "win" else 0
    profit_str = f"+{profit}₽" if res_type == "win" else "0₽" if res_type == "ret" else f"-{amt}₽"
    final_kb = InlineKeyboardBuilder().button(text=f"{res_text} ({profit_str})", callback_data="noop")
    await c.message.edit_reply_markup(reply_markup=final_kb.as_markup())

# --- SCANNER ---
async def scanner():
    sent = set()
    idx = 0
    while True:
        if not API_KEYS: 
            await asyncio.sleep(60); continue
        ping_enabled = get_setting("ping_mode", 0) == 1
        stats = {"leagues_scanned": 0, "matches_found": 0, "api_errors": 0, "google_errors": 0, "skipped_odds": 0}
        m_odds = get_setting("min_odds", 1.50)
        max_o = get_setting("max_odds", 2.50)
        m_mult = get_setting("multiplier", 0.90)
        
        for league in LEAGUES:
            try:
                r = requests.get(f"https://api.the-odds-api.com/v4/sports/{league}/odds/", 
                                 params={'apiKey': API_KEYS[idx], 'regions': 'eu', 'markets': 'totals'}, timeout=10)
                if r.status_code == 200:
                    stats["leagues_scanned"] += 1
                    data = r.json()
                    for ev in data:
                        if ev['id'] in sent: continue
                        target_odds = None
                        try:
                            for bm in ev.get('bookmakers', []):
                                for mkt in bm.get('markets', []):
                                    if mkt['key'] == 'totals':
                                        for outcome in mkt['outcomes']:
                                            if outcome['name'] == 'Over' and outcome.get('point') == 2.5:
                                                target_odds = outcome['price']; break
                        except: continue
                        if not target_odds: continue
                        final_odds = round(target_odds * m_mult, 2)
                        if not (m_odds <= final_odds <= max_o):
                            stats["skipped_odds"] += 1; continue
                        itb_home, _ = await analyze_strict(ev['home_team'], is_home=True)
                        if itb_home == "CAPTCHA":
                            stats["google_errors"] += 1; continue
                        if isinstance(itb_home, int) and itb_home >= 3:
                            stats["matches_found"] += 1; sent.add(ev['id'])
                            h, a = clean_and_translate(ev['home_team']), clean_and_translate(ev['away_team'])
                            msg_vip = (f"💎 <b>Baron’s Verdict</b>\n⚽️ <code>{h}</code> — <code>{a}</code>\n"
                                       f"━━━━━━━━━━━━━━━━━━━━\n📊 Анализ: Высокая результативность ({itb_home}/5)\n"
                                       f"🔥 Ставка: <b>ИТБ 1 (1.5)</b>\n📈 Расч. кэф: <code>{final_odds}</code>\n━━━━━━━━━━━━━━━━━━━━")
                            await bot.send_message(CHANNEL_ID, msg_vip, parse_mode=ParseMode.HTML)
                            kb = InlineKeyboardBuilder()
                            kb.button(text="💰 Поставил", callback_data="pred_place")
                            kb.button(text="⏩ Пропустил", callback_data="pred_skip")
                            await bot.send_message(ADMIN_GROUP_ID, msg_vip, reply_markup=kb.as_markup(), message_thread_id=T_PRED, parse_mode=ParseMode.HTML)
                elif r.status_code == 429:
                    idx = (idx + 1) % len(API_KEYS); stats["api_errors"] += 1
            except: stats["api_errors"] += 1
            await asyncio.sleep(2)
        if ping_enabled:
            report = f"🛰 <b>Отчет круга анализа</b>\n✅ Лиг: {stats['leagues_scanned']}\n🎯 Сигналов: {stats['matches_found']}\n⚠️ Ошибки API: {stats['api_errors']}"
            await bot.send_message(ADMIN_GROUP_ID, report, message_thread_id=T_LOGS, parse_mode=ParseMode.HTML)
        await asyncio.sleep(1800)

async def main():
    init_db()
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Baron Alive"))
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    asyncio.create_task(scanner())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
