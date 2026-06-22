import os
import asyncio
import asyncpg
import re
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
import aiohttp
import uvicorn
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ==========================================
# --- НАСТРОЙКИ (Переменные Railway) ---
# ==========================================
DATABASE_URL = os.getenv("DATABASE_URL")
API_TOKEN = os.getenv("TELEGRAM_TOKEN")
IFTTT_KEY = os.getenv("IFTTT_KEY")
TARGET_CHAT_ID = os.getenv("CHAT_ID") 

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", 0))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
MAJESTIC_BOT_USERNAME = '@MajesticRolePlayBot'

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- СОСТОЯНИЯ FSM ДЛЯ ДОБАВЛЕНИЯ/РЕДАКТИРОВАНИЯ АВТО ---
class FleetStates(StatesGroup):
    waiting_for_new_car_name = State()
    waiting_for_new_car_price = State()
    waiting_for_edit_price = State()

async def execute_query(query, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    result = await conn.fetch(query, *args)
    await conn.close()
    return result

# ==========================================
# --- ГЕНЕРАЦИЯ МЕНЮ (DASHBOARD) ---
# ==========================================
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Запустить Majestic", callback_data="cmd_start_majestic"),
         InlineKeyboardButton(text="🚗 Выставить Аренду", callback_data="cmd_start_rent")],
        [InlineKeyboardButton(text="⚡ Вкл ПК", callback_data="cmd_turn_on"),
         InlineKeyboardButton(text="🛑 Выкл ПК", callback_data="cmd_shutdown"),
         InlineKeyboardButton(text="❌ Сброс", callback_data="cmd_kill_game")],
        [InlineKeyboardButton(text="⏳ Таймеры", callback_data="menu_timers"),
         InlineKeyboardButton(text="📊 Статистика", callback_data="menu_stats")],
        [InlineKeyboardButton(text="⚙️ Автопарк и Цены", callback_data="menu_fleet")]
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="menu_main")]
    ])

async def update_dashboard_ui():
    if not TARGET_CHAT_ID: return
    row = await execute_query("SELECT last_ping, status_text, menu_id FROM commands WHERE id = 1")
    if not row: return
    last_ping, status_text, menu_id = row[0]
    if not menu_id: return 
    
    is_online = False
    if last_ping and (datetime.now() - last_ping).total_seconds() < 90:
        is_online = True
            
    status_icon = "🟢 <b>Онлайн</b>" if is_online else "🔴 <b>Оффлайн</b>"
    text_status = status_text if status_text else "Ожидание команд..."
    
    text = (
        f"🎛 <b>Cobalt Rent | Панель управления</b>\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🖥 <b>Связь с агентом ПК:</b> {status_icon}\n"
        f"🔄 <b>Журнал событий:</b>\n └ <i>{text_status}</i>\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"Выберите действие:"
    )
    try:
        await bot.edit_message_text(chat_id=TARGET_CHAT_ID, message_id=menu_id, text=text, reply_markup=get_main_keyboard(), parse_mode="HTML")
    except Exception: pass

# ==========================================
# --- АВТОМАТИЧЕСКИЕ УВЕДОМЛЕНИЯ ---
# ==========================================
async def check_expired_rents():
    while True:
        try:
            query = "SELECT id, car_name FROM rent_stats WHERE rent_end <= NOW() AND (notified IS NULL OR notified = FALSE)"
            expired = await execute_query(query)
            if expired:
                for row in expired:
                    rent_id, car_name = row[0], row[1]
                    if TARGET_CHAT_ID:
                        try:
                            await bot.send_message(chat_id=TARGET_CHAT_ID, text=f"🟢 <b>Автомобиль вернулся!</b>\n\n🚗 <b>{car_name}</b> вышел из аренды.\nПора снова включать ПК и выставлять!", parse_mode="HTML")
                        except Exception: pass
                    await execute_query("UPDATE rent_stats SET notified = TRUE WHERE id = $1", rent_id)
        except Exception: pass
        await asyncio.sleep(60)

# ==========================================
# --- ЮЗЕРБОТ (ЛОВЕЦ ЧЕКОВ) ---
# ==========================================
userbot = TelegramClient(StringSession(SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)

@userbot.on(events.NewMessage(chats=MAJESTIC_BOT_USERNAME))
async def handle_receipt(event):
    text = event.message.text
    if text and "Транспорт сдан в аренду!" in text:
        try:
            car_match = re.search(r"Транспорт:\s*(.+)", text)
            car_name = car_match.group(1).strip() if car_match else "Неизвестно"
            price_match = re.search(r"Цена:\s*\$([\d\s]+)", text)
            price = int(re.sub(r"\s", "", price_match.group(1))) if price_match else 0
            dur_match = re.search(r"Длительность:\s*(\d+)", text)
            duration = int(dur_match.group(1)) if dur_match else 0
            ref_match = re.search(r"Возврат денег за объявление:\s*\$([\d\s]+)", text)
            refund = int(re.sub(r"\s", "", ref_match.group(1))) if ref_match else 0

            rent_end = datetime.now() + timedelta(hours=duration)
            net_profit = price - 1250 + refund

            await execute_query("INSERT INTO rent_stats (car_name, price, duration, refund, rent_end, notified) VALUES ($1, $2, $3, $4, $5, FALSE)", car_name, price, duration, refund, rent_end)
            if TARGET_CHAT_ID:
                await bot.send_message(chat_id=TARGET_CHAT_ID, text=(f"🔔 <b>Новая сдача в аренду!</b>\n\n🚗 Авто: <b>{car_name}</b>\n💰 <b>Чистая прибыль: {net_profit}$</b>\n⏳ Освободится: {rent_end.strftime('%d.%m %H:%M')}"), parse_mode="HTML")
        except Exception: pass

# ==========================================
# --- ЖИЗНЕННЫЙ ЦИКЛ СЕРВЕРА ---
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await execute_query("CREATE TABLE IF NOT EXISTS commands (id INTEGER PRIMARY KEY, cmd TEXT, last_ping TIMESTAMP, menu_id BIGINT, status_text TEXT DEFAULT 'Ожидание')")
    
    # НОВАЯ ТАБЛИЦА: Автопарк
    await execute_query("""
        CREATE TABLE IF NOT EXISTS fleet (
            id SERIAL PRIMARY KEY,
            car_name TEXT UNIQUE,
            price INTEGER
        )
    """)
    
    await execute_query("""
        CREATE TABLE IF NOT EXISTS rent_stats (
            id SERIAL PRIMARY KEY, car_name TEXT, price INTEGER, duration INTEGER,
            refund INTEGER, rent_end TIMESTAMP, created_at TIMESTAMP DEFAULT NOW(), notified BOOLEAN DEFAULT FALSE
        )
    """)
    
    count = await execute_query("SELECT COUNT(*) FROM commands")
    if count[0][0] == 0: await execute_query("INSERT INTO commands (id, cmd) VALUES (1, 'none')")
    
    bot_task = asyncio.create_task(dp.start_polling(bot))
    await userbot.connect()
    userbot_task = asyncio.create_task(userbot.run_until_disconnected())
    timer_task = asyncio.create_task(check_expired_rents())
    
    yield
    bot_task.cancel()
    userbot_task.cancel()
    timer_task.cancel()
    await userbot.disconnect()

app = FastAPI(lifespan=lifespan)

# ==========================================
# --- ФУНКЦИИ IFTTT ---
# ==========================================
async def trigger_ifttt(event_name):
    if not IFTTT_KEY: return False
    url = f"https://maker.ifttt.com/trigger/{event_name}/with/key/{IFTTT_KEY}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as response: return response.status == 200
        except: return False

async def shutdown_pc_task():
    await execute_query("UPDATE commands SET cmd = 'shutdown', status_text = '🖥 Процедура выключения ПК...' WHERE id = 1")
    await update_dashboard_ui()
    await asyncio.sleep(60) 
    await trigger_ifttt("pc_off") 
    await execute_query("UPDATE commands SET status_text = '💤 ПК обесточен' WHERE id = 1")
    await update_dashboard_ui()

# ==========================================
# --- ОБРАБОТЧИКИ TELEGRAM ---
# ==========================================
@dp.message(Command("menu", "start"))
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear() # Сбрасываем состояния, если они были
    if str(message.chat.id) != str(TARGET_CHAT_ID): return
    try: await message.delete()
    except Exception: pass
    msg = await message.answer("🔄 Загрузка панели управления...", parse_mode="HTML")
    await execute_query("UPDATE commands SET menu_id = $1 WHERE id = 1", msg.message_id)
    await update_dashboard_ui()

@dp.callback_query()
async def process_callbacks(callback: CallbackQuery, state: FSMContext):
    if str(callback.message.chat.id) != str(TARGET_CHAT_ID): return
    data = callback.data
    
    if data.startswith("cmd_"):
        command = data.replace("cmd_", "")
        if command == "turn_on":
            await trigger_ifttt("pc_on")
            await execute_query("UPDATE commands SET status_text = '⚡ Отправлен IFTTT сигнал на включение' WHERE id=1")
            await callback.answer("Сигнал отправлен!")
        elif command == "shutdown":
            asyncio.create_task(shutdown_pc_task())
            await callback.answer("Запущено выключение!")
        else:
            await execute_query("UPDATE commands SET cmd = $1, status_text = $2 WHERE id = 1", command, f"Отправлена команда: {command}")
            await callback.answer("Команда в очереди!")
        await update_dashboard_ui()
        
    elif data == "menu_timers":
        active_rents = await execute_query("SELECT car_name, rent_end FROM rent_stats WHERE rent_end > NOW() ORDER BY rent_end ASC")
        if not active_rents: text = "🤷‍♂️ <b>Прямо сейчас нет машин в аренде.</b>"
        else:
            text = "⏳ <b>Таймеры текущих аренд:</b>\n\n"
            now = datetime.now()
            for car_name, rent_end in active_rents:
                diff = rent_end - now
                if diff.total_seconds() > 0:
                    hours, remainder = divmod(int(diff.total_seconds()), 3600)
                    minutes, _ = divmod(remainder, 60)
                    time_left = f"{hours} ч. {minutes} мин." if hours > 0 else f"{minutes} мин."
                    text += f"🚗 <b>{car_name}</b> — осталось: {time_left} (до {rent_end.strftime('%H:%M')})\n"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_back_keyboard())
        
    elif data == "menu_stats":
        try:
            res_today = await execute_query("SELECT COALESCE(SUM(price - 1250 + refund), 0), COUNT(*) FROM rent_stats WHERE created_at >= CURRENT_DATE")
            res_yest = await execute_query("SELECT COALESCE(SUM(price - 1250 + refund), 0), COUNT(*) FROM rent_stats WHERE created_at >= CURRENT_DATE - INTERVAL '1 day' AND created_at < CURRENT_DATE")
            res_7 = await execute_query("SELECT COALESCE(SUM(price - 1250 + refund), 0), COUNT(*) FROM rent_stats WHERE created_at >= CURRENT_DATE - INTERVAL '7 days'")
            res_cars_today = await execute_query("SELECT car_name, COALESCE(SUM(price - 1250 + refund), 0) as total, COUNT(*) FROM rent_stats WHERE created_at >= CURRENT_DATE GROUP BY car_name ORDER BY total DESC")
            res_cars_all = await execute_query("SELECT car_name, COALESCE(SUM(price - 1250 + refund), 0) as total FROM rent_stats GROUP BY car_name ORDER BY total DESC LIMIT 5")
            
            text = (f"📊 <b>Статистика Аренды</b>\n\n🔹 <b>Сегодня:</b> {res_today[0][0]}$ ({res_today[0][1]} сдач)\n🔹 <b>Вчера:</b> {res_yest[0][0]}$ ({res_yest[0][1]} сдач)\n🔹 <b>За 7 дней:</b> {res_7[0][0]}$ ({res_7[0][1]} сдач)\n\n🚗 <b>Доход авто за СЕГОДНЯ:</b>\n")
            if res_cars_today:
                for car_name, total, count in res_cars_today: text += f"▫️ {car_name}: {total}$ (x{count})\n"
            else: text += "▫️ Пока нет сдач\n"
            text += f"\n🏆 <b>Топ-5 авто (всё время):</b>\n"
            if res_cars_all:
                for car_name, total in res_cars_all: text += f"▫️ {car_name}: {total}$\n"
            else: text += "▫️ Пока нет данных\n"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🗑 Очистить базу", callback_data="cmd_clear_stats")], [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="menu_main")]])
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e: await callback.answer(f"Ошибка: {e}")
        
    elif data == "cmd_clear_stats":
        await execute_query("DELETE FROM rent_stats")
        await callback.answer("База стерта!", show_alert=True)
        await update_dashboard_ui()
        
    # --- БЛОК АВТОПАРКА ---
    elif data == "menu_fleet":
        cars = await execute_query("SELECT id, car_name, price FROM fleet ORDER BY id ASC")
        text = "⚙️ <b>Твой Автопарк</b>\n\n"
        
        keyboard = []
        if not cars: text += "🤷‍♂️ <i>В базе пока нет ни одной машины.</i>\n\n"
        else:
            for car_id, name, price in cars:
                text += f"🚗 <b>{name}</b> — {price}$/ч\n"
                # Создаем кнопки редактирования и удаления для каждой машины
                keyboard.append([
                    InlineKeyboardButton(text=f"✏️ Цена {name}", callback_data=f"editcar_{car_id}"),
                    InlineKeyboardButton(text=f"❌ Удал. {name}", callback_data=f"delcar_{car_id}")
                ])
                
        keyboard.append([InlineKeyboardButton(text="➕ Добавить новое авто", callback_data="add_car")])
        keyboard.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="menu_main")])
        
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))

    elif data == "add_car":
        await callback.message.answer("📝 <b>Пришли мне точное название авто из игры:</b>\n<i>(Например: RL Daimler Runner)</i>", parse_mode="HTML")
        await state.set_state(FleetStates.waiting_for_new_car_name)
        await callback.answer()

    elif data.startswith("delcar_"):
        car_id = int(data.split("_")[1])
        await execute_query("DELETE FROM fleet WHERE id = $1", car_id)
        await callback.answer("Авто удалено!")
        # Искусственно вызываем обновление меню автопарка
        await process_callbacks(callback.model_copy(update={'data': 'menu_fleet'}), state)
        
    elif data.startswith("editcar_"):
        car_id = int(data.split("_")[1])
        await state.update_data(edit_car_id=car_id)
        await callback.message.answer("💵 <b>Пришли новую цену аренды за час (только цифры):</b>", parse_mode="HTML")
        await state.set_state(FleetStates.waiting_for_edit_price)
        await callback.answer()

    elif data == "menu_main":
        await state.clear()
        await update_dashboard_ui()

# --- ОБРАБОТЧИКИ ВВОДА ТЕКСТА (ДЛЯ АВТОПАРКА) ---
@dp.message(FleetStates.waiting_for_new_car_name)
async def process_new_car_name(message: Message, state: FSMContext):
    await state.update_data(car_name=message.text.strip())
    await message.answer("💵 <b>Отлично! Теперь напиши цену аренды за час (только цифры):</b>", parse_mode="HTML")
    await state.set_state(FleetStates.waiting_for_new_car_price)

@dp.message(FleetStates.waiting_for_new_car_price)
async def process_new_car_price(message: Message, state: FSMContext):
    try:
        price = int(message.text.strip())
        data = await state.get_data()
        car_name = data['car_name']
        
        await execute_query("INSERT INTO fleet (car_name, price) VALUES ($1, $2) ON CONFLICT (car_name) DO UPDATE SET price = EXCLUDED.price", car_name, price)
        await message.answer(f"✅ Машина <b>{car_name}</b> добавлена в базу с ценой {price}$/ч!", parse_mode="HTML")
        await state.clear()
        
        # Обновляем основной Dashboard
        await update_dashboard_ui()
    except ValueError:
        await message.answer("⚠️ Пожалуйста, введи цену только цифрами (без букв и знаков $).")

@dp.message(FleetStates.waiting_for_edit_price)
async def process_edit_car_price(message: Message, state: FSMContext):
    try:
        price = int(message.text.strip())
        data = await state.get_data()
        car_id = data['edit_car_id']
        
        await execute_query("UPDATE fleet SET price = $1 WHERE id = $2", price, car_id)
        await message.answer(f"✅ Цена успешно обновлена на {price}$/ч!", parse_mode="HTML")
        await state.clear()
        
        await update_dashboard_ui()
    except ValueError:
        await message.answer("⚠️ Пожалуйста, введи новую цену только цифрами.")

# ==========================================
# --- API ДЛЯ АГЕНТА НА ПК ---
# ==========================================
@app.get("/get_task")
async def get_task():
    await execute_query("UPDATE commands SET last_ping = NOW() WHERE id = 1")
    result = await execute_query("SELECT cmd FROM commands WHERE id = 1")
    if not result: raise HTTPException(status_code=500)
    cmd = result[0][0]
    if cmd != 'none': await execute_query("UPDATE commands SET cmd = 'none' WHERE id = 1")
    return {"command": cmd}

@app.get("/agent_started")
async def api_agent_started():
    await execute_query("UPDATE commands SET cmd = 'none', status_text = '✅ Агент успешно запущен и готов к работе!' WHERE id = 1")
    await update_dashboard_ui()
    return {"status": "ok"}

@app.get("/update_status")
async def api_update_status(text: str):
    await execute_query("UPDATE commands SET status_text = $1 WHERE id = 1", text)
    await update_dashboard_ui()
    return {"status": "ok"}

# НОВЫЙ ЭНДПОИНТ: Отдаем список машин для ПК
@app.get("/get_fleet")
async def api_get_fleet():
    cars = await execute_query("SELECT car_name, price FROM fleet")
    return {"fleet": [{"name": c[0], "price": c[1]} for c in cars]}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
