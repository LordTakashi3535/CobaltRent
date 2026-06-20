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

# Переменные для Юзербота
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", 0))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
MAJESTIC_BOT_USERNAME = '@majestic_rp_bot'

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
MENU_MESSAGE_ID = None

main_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="▶️ Запустить Majestic", callback_data="start_majestic")],
    [InlineKeyboardButton(text="🚗 Выставить Аренду", callback_data="start_rent")],
    [InlineKeyboardButton(text="❌ Экстренный сброс игры", callback_data="kill_game")],
    [InlineKeyboardButton(text="⏹ Выключить ПК", callback_data="shutdown")]
])

async def execute_query(query, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    result = await conn.fetch(query, *args)
    await conn.close()
    return result

# ==========================================
# --- ЮЗЕРБОТ (КРУГЛОСУТОЧНЫЙ ЛОВЕЦ ЧЕКОВ) ---
# ==========================================
# Создаем сессию из нашей длинной текстовой строки
userbot = TelegramClient(StringSession(SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)

@userbot.on(events.NewMessage(chats=MAJESTIC_BOT_USERNAME))
async def handle_receipt(event):
    text = event.message.text
    if text and "Транспорт сдан в аренду!" in text:
        print("📩 Юзербот поймал чек! Обрабатываем...")
        
        # 1. СРАЗУ отправляем оригинальный текст чека тебе в Telegram
        if TARGET_CHAT_ID:
            try:
                await bot.send_message(
                    chat_id=TARGET_CHAT_ID,
                    text=f"🔔 <b>Новая сдача в аренду!</b>\n\n{text}",
                    parse_mode="HTML"
                )
            except Exception as e:
                print(f"❌ Ошибка отправки уведомления в ТГ: {e}")

        # 2. ПРОСТО записываем данные в базу для статистики
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

            # Сохраняем в базу данных
            await execute_query(
                "INSERT INTO rent_stats (car_name, price, duration, refund, rent_end) VALUES ($1, $2, $3, $4, $5)",
                car_name, price, duration, refund, rent_end
            )
            print("✅ Чек успешно сохранен в базу данных!")
            
        except Exception as e:
            print(f"❌ Ошибка парсинга или записи в БД: {e}")

# ==========================================
# --- ЖИЗНЕННЫЙ ЦИКЛ СЕРВЕРА ---
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Таблица для команд с ПК
    await execute_query("CREATE TABLE IF NOT EXISTS commands (id INTEGER PRIMARY KEY, cmd TEXT)")
    
    # Таблица для статистики
    await execute_query("""
        CREATE TABLE IF NOT EXISTS rent_stats (
            id SERIAL PRIMARY KEY,
            car_name TEXT,
            price INTEGER,
            duration INTEGER,
            refund INTEGER,
            rent_end TIMESTAMP
        )
    """)
    
    count = await execute_query("SELECT COUNT(*) FROM commands")
    if count[0][0] == 0:
        await execute_query("INSERT INTO commands (id, cmd) VALUES (1, 'none')")
    
    # 1. Запускаем Aiogram (Основной бот)
    print("🤖 Бот Aiogram запускается...")
    bot_task = asyncio.create_task(dp.start_polling(bot))
    
    # 2. Запускаем Юзербота (Прячется в фоне и слушает чеки)
    print("🕵️‍♂️ Юзербот подключается...")
    await userbot.connect()
    userbot_task = asyncio.create_task(userbot.run_until_disconnected())
    
    yield
    
    # Выключаем всё при перезагрузке сервера
    bot_task.cancel()
    userbot_task.cancel()
    await userbot.disconnect()
    print("👋 Сервер остановлен.")

app = FastAPI(lifespan=lifespan)

# ==========================================
# --- ОБРАБОТЧИКИ TELEGRAM МЕНЮ ---
# ==========================================
async def trigger_ifttt(event_name):
    if not IFTTT_KEY: return False
    url = f"https://maker.ifttt.com/trigger/{event_name}/with/key/{IFTTT_KEY}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as response:
                return response.status == 200
        except: return False

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

@dp.message(Command("on"))
async def cmd_on(message):
    if str(message.chat.id) != str(TARGET_CHAT_ID): return
    await message.answer("⚡ Сигнал IFTTT на включение ПК...")
    await trigger_ifttt("pc_on")

@dp.message(Command("off"))
async def cmd_off(message):
    if str(message.chat.id) != str(TARGET_CHAT_ID): return
    await execute_query("UPDATE commands SET cmd = 'shutdown' WHERE id = 1")
    await message.answer("🖥 Команда на выключение ПК отправлена.")
    await asyncio.sleep(60)
    await trigger_ifttt("pc_off")

@dp.callback_query(F.data.in_({"start_majestic", "start_rent", "kill_game", "shutdown"}))
async def process_menu_buttons(callback: CallbackQuery):
    if str(callback.message.chat.id) != str(TARGET_CHAT_ID): return
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
