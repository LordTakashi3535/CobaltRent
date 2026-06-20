import os
import asyncio
import asyncpg
import re
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
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
MENU_MESSAGE_ID = None

# Обновленное меню с кнопками включения/выключения и таймером
main_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="▶️ Запустить Majestic", callback_data="start_majestic"),
     InlineKeyboardButton(text="🚗 Выставить Аренду", callback_data="start_rent")],
    [InlineKeyboardButton(text="⚡ Включить ПК", callback_data="turn_on"),
     InlineKeyboardButton(text="🛑 Выключить ПК", callback_data="shutdown")],
    [InlineKeyboardButton(text="⏳ Авто в аренде (Таймеры)", callback_data="show_timers")],
    [InlineKeyboardButton(text="❌ Экстренный сброс игры", callback_data="kill_game")]
])

async def execute_query(query, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    result = await conn.fetch(query, *args)
    await conn.close()
    return result

# ==========================================
# --- ЮЗЕРБОТ (КРУГЛОСУТОЧНЫЙ ЛОВЕЦ ЧЕКОВ) ---
# ==========================================
userbot = TelegramClient(StringSession(SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)

@userbot.on(events.NewMessage(chats=MAJESTIC_BOT_USERNAME))
async def handle_receipt(event):
    text = event.message.text
    if text and "Транспорт сдан в аренду!" in text:
        print("📩 Юзербот поймал чек! Обрабатываем...")
        
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
            
            # Считаем чистую прибыль (1250 - фиксированная стоимость размещения)
            net_profit = price - 1250 + refund

            # Сохраняем в базу данных
            await execute_query(
                "INSERT INTO rent_stats (car_name, price, duration, refund, rent_end) VALUES ($1, $2, $3, $4, $5)",
                car_name, price, duration, refund, rent_end
            )
            
            # Отправляем уведомление владельцу с учетом расходов
            if TARGET_CHAT_ID:
                await bot.send_message(
                    chat_id=TARGET_CHAT_ID,
                    text=(
                        f"🔔 <b>Новая сдача в аренду!</b>\n\n"
                        f"🚗 Авто: <b>{car_name}</b>\n"
                        f"💵 Выручка: {price}$\n"
                        f"📉 Комиссия: -1250$\n"
                        f"📈 Возврат: +{refund}$\n"
                        f"➖➖➖➖➖➖\n"
                        f"💰 <b>Чистая прибыль: {net_profit}$</b>\n"
                        f"⏳ Освободится: {rent_end.strftime('%d.%m %H:%M')}"
                    ),
                    parse_mode="HTML"
                )
        except Exception as e:
            print(f"❌ Ошибка парсинга или записи в БД: {e}")

# ==========================================
# --- ЖИЗНЕННЫЙ ЦИКЛ СЕРВЕРА ---
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await execute_query("CREATE TABLE IF NOT EXISTS commands (id INTEGER PRIMARY KEY, cmd TEXT)")
    
    await execute_query("""
        CREATE TABLE IF NOT EXISTS rent_stats (
            id SERIAL PRIMARY KEY,
            car_name TEXT,
            price INTEGER,
            duration INTEGER,
            refund INTEGER,
            rent_end TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    
    # Безопасное обновление таблицы, если она была создана в прошлый раз без created_at
    try: 
        await execute_query("ALTER TABLE rent_stats ADD COLUMN created_at TIMESTAMP DEFAULT NOW()")
    except Exception: 
        pass
    
    count = await execute_query("SELECT COUNT(*) FROM commands")
    if count[0][0] == 0:
        await execute_query("INSERT INTO commands (id, cmd) VALUES (1, 'none')")
    
    print("🤖 Бот Aiogram запускается...")
    bot_task = asyncio.create_task(dp.start_polling(bot))
    
    print("🕵️‍♂️ Юзербот подключается...")
    await userbot.connect()
    userbot_task = asyncio.create_task(userbot.run_until_disconnected())
    
    yield
    bot_task.cancel()
    userbot_task.cancel()
    await userbot.disconnect()

app = FastAPI(lifespan=lifespan)

# ==========================================
# --- ФУНКЦИИ IFTTT И УПРАВЛЕНИЯ ПИТАНИЕМ ---
# ==========================================
async def trigger_ifttt(event_name):
    if not IFTTT_KEY: return False
    url = f"https://maker.ifttt.com/trigger/{event_name}/with/key/{IFTTT_KEY}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as response:
                return response.status == 200
        except: return False

async def shutdown_pc_task():
    """Фоновая задача для мягкого выключения"""
    await execute_query("UPDATE commands SET cmd = 'shutdown' WHERE id = 1")
    await asyncio.sleep(60) # Ждем, пока агент закроет игру и погасит Windows
    await trigger_ifttt("pc_off")

async def update_telegram_menu_status(text):
    global MENU_MESSAGE_ID
    if MENU_MESSAGE_ID and TARGET_CHAT_ID:
        full_text = f"🖥 Агент запущен.\n🔄 Статус: {text}"
        try:
            await bot.edit_message_text(
                chat_id=TARGET_CHAT_ID, message_id=MENU_MESSAGE_ID,
                text=full_text, reply_markup=main_keyboard
            )
            return True
        except: pass
    return False

# ==========================================
# --- ОБРАБОТЧИКИ TELEGRAM КОМАНД ---
# ==========================================
@dp.message(Command("stats"))
async def cmd_stats(message):
    if str(message.chat.id) != str(TARGET_CHAT_ID): return
    
    # Формула чистой прибыли встроена прямо в SQL запрос: (price - 1250 + refund)
    q_today = "SELECT COALESCE(SUM(price - 1250 + refund), 0), COUNT(*) FROM rent_stats WHERE created_at >= CURRENT_DATE"
    q_7days = "SELECT COALESCE(SUM(price - 1250 + refund), 0), COUNT(*) FROM rent_stats WHERE created_at >= CURRENT_DATE - INTERVAL '7 days'"
    q_30days = "SELECT COALESCE(SUM(price - 1250 + refund), 0), COUNT(*) FROM rent_stats WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'"
    
    res_today = await execute_query(q_today)
    res_7 = await execute_query(q_7days)
    res_30 = await execute_query(q_30days)
    
    sum_today, cnt_today = res_today[0]
    sum_7, cnt_7 = res_7[0]
    sum_30, cnt_30 = res_30[0]
    
    text = (
        f"📊 <b>Статистика Аренды</b>\n\n"
        f"🔹 <b>За сегодня:</b> {sum_today}$ ({cnt_today} авто)\n"
        f"🔹 <b>За 7 дней:</b> {sum_7}$ ({cnt_7} авто)\n"
        f"🔹 <b>За 30 дней:</b> {sum_30}$ ({cnt_30} авто)\n\n"
        f"<i>*Суммы указаны с учетом вычета затрат на маркетплейс (1250$/шт).</i>"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Очистить статистику", callback_data="clear_stats")]
    ])
    
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@dp.message(Command("on"))
async def cmd_on(message):
    if str(message.chat.id) != str(TARGET_CHAT_ID): return
    await message.answer("⚡ Сигнал IFTTT на включение ПК...")
    await trigger_ifttt("pc_on")

@dp.message(Command("off"))
async def cmd_off(message):
    if str(message.chat.id) != str(TARGET_CHAT_ID): return
    await message.answer("🖥 Команда на выключение ПК отправлена. Обесточу через 60 сек.")
    asyncio.create_task(shutdown_pc_task())

@dp.callback_query(F.data.in_({"start_majestic", "start_rent", "kill_game", "shutdown", "turn_on", "clear_stats", "show_timers"}))
async def process_menu_buttons(callback: CallbackQuery):
    if str(callback.message.chat.id) != str(TARGET_CHAT_ID): return
    
    if callback.data == "turn_on":
        await callback.message.answer("⚡ Сигнал IFTTT на включение ПК...")
        await trigger_ifttt("pc_on")
        await callback.answer("Включаю ПК!")
        
    elif callback.data == "shutdown":
        await callback.message.answer("🖥 Запущена процедура выключения...")
        await callback.answer("Выключаю ПК!")
        asyncio.create_task(shutdown_pc_task())
        
    elif callback.data == "clear_stats":
        await execute_query("DELETE FROM rent_stats")
        await callback.message.edit_text("✅ Статистика успешно очищена!")
        await callback.answer("База стерта")
        
    elif callback.data == "show_timers":
        # Достаем все машины, у которых время окончания аренды больше, чем текущее время
        query = "SELECT car_name, rent_end FROM rent_stats WHERE rent_end > NOW() ORDER BY rent_end ASC"
        active_rents = await execute_query(query)
        
        if not active_rents:
            await callback.message.answer("🤷‍♂️ Прямо сейчас нет машин в аренде.")
            await callback.answer()
            return
            
        text = "⏳ <b>Таймеры текущих аренд:</b>\n\n"
        now = datetime.now()
        
        for car_name, rent_end in active_rents:
            diff = rent_end - now
            if diff.total_seconds() > 0:
                hours, remainder = divmod(int(diff.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                
                # Формируем красивый вывод
                time_left_str = f"{hours} ч. {minutes} мин." if hours > 0 else f"{minutes} мин."
                text += f"🚗 <b>{car_name}</b> — осталось: {time_left_str} (до {rent_end.strftime('%H:%M')})\n"
        
        await callback.message.answer(text, parse_mode="HTML")
        await callback.answer()
        
    else:
        # Передаем команды start_majestic, start_rent, kill_game агенту на ПК
        await execute_query("UPDATE commands SET cmd = $1 WHERE id = 1", callback.data)
        await callback.answer("Команда отправлена агенту!")

# ==========================================
# --- API ДЛЯ АГЕНТА НА ПК ---
# ==========================================
@app.get("/get_task")
async def get_task():
    result = await execute_query("SELECT cmd FROM commands WHERE id = 1")
    if not result: raise HTTPException(status_code=500)
    cmd = result[0][0]
    if cmd != 'none': await execute_query("UPDATE commands SET cmd = 'none' WHERE id = 1")
    return {"command": cmd}

@app.get("/agent_started")
async def api_agent_started():
    global MENU_MESSAGE_ID
    await execute_query("UPDATE commands SET cmd = 'none' WHERE id = 1")
    if not TARGET_CHAT_ID: return {"status": "error"}
    try:
        msg = await bot.send_message(chat_id=TARGET_CHAT_ID, text="🖥 Агент запущен.\n✅ Статус: Ожидание.", reply_markup=main_keyboard)
        MENU_MESSAGE_ID = msg.message_id
        return {"status": "ok"}
    except: return {"status": "error"}

@app.get("/update_status")
async def api_update_status(text: str):
    success = await update_telegram_menu_status(text)
    return {"status": "ok" if success else "ignored"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
