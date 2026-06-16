import os
import asyncio
import asyncpg
from fastapi import FastAPI
from contextlib import asynccontextmanager
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import aiohttp
import uvicorn

# Настройки
DATABASE_URL = os.getenv("DATABASE_URL")
API_TOKEN = os.getenv("TELEGRAM_TOKEN")
IFTTT_KEY = os.getenv("IFTTT_KEY")
CHAT_ID = os.getenv("CHAT_ID") # Ваш новый Chat ID

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- Функции БД ---
async def execute_query(query, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    result = await conn.fetch(query, *args)
    await conn.close()
    return result

@asynccontextmanager
async def lifespan(app: FastAPI):
    await execute_query("CREATE TABLE IF NOT EXISTS commands (id SERIAL PRIMARY KEY, cmd TEXT)")
    count = await execute_query("SELECT COUNT(*) FROM commands")
    if count[0][0] == 0:
        await execute_query("INSERT INTO commands (cmd) VALUES ('none')")
    
    task = asyncio.create_task(dp.start_polling(bot))
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

# --- IFTTT (Управление розеткой) ---
async def trigger_ifttt(event_name):
    url = f"https://maker.ifttt.com/trigger/{event_name}/with/key/{IFTTT_KEY}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return response.status == 200

# --- Команды Telegram ---
@dp.message(Command("on"))
async def cmd_on(message):
    await message.answer("⚡ Передаю сигнал на включение розетки...")
    success = await trigger_ifttt("pc_on")
    if success:
        await message.answer("✅ Розетка включена! Ждем запуска агента...")
    else:
        await message.answer("❌ Ошибка связи с IFTTT.")

@dp.message(Command("off"))
async def cmd_off(message):
    await execute_query("UPDATE commands SET cmd = 'shutdown' WHERE id = 1")
    await message.answer("🖥 Компьютер выключается. Жду 60 секунд...")
    await asyncio.sleep(60)
    
    success = await trigger_ifttt("pc_off")
    if success:
        await message.answer("🔌 Питание успешно обесточено.")
    else:
        await message.answer("❌ Ошибка отключения розетки в IFTTT.")

# --- Обработка нажатия Inine-кнопок ---
@dp.callback_query(F.data.in_({"btn_start", "btn_stop", "btn_action"}))
async def process_buttons(callback: CallbackQuery):
    # Пока кнопки пустые, бот просто будет присылать всплывающее уведомление
    if callback.data == "btn_start":
        await callback.answer("▶️ Команда СТАРТ в разработке!", show_alert=True)
    elif callback.data == "btn_stop":
        await callback.answer("⏹ Команда СТОП в разработке!", show_alert=True)
    elif callback.data == "btn_action":
        await callback.answer("⚙️ Действие в разработке!", show_alert=True)

# --- API эндпоинты для Агента на ПК ---
@app.get("/get_task")
async def get_task():
    result = await execute_query("SELECT cmd FROM commands WHERE id = 1")
    cmd = result[0][0]
    await execute_query("UPDATE commands SET cmd = 'none' WHERE id = 1")
    return {"command": cmd}

@app.get("/agent_started")
async def agent_started():
    """Этот адрес дергает ПК при включении, чтобы бот прислал меню"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Старт", callback_data="btn_start")],
        [InlineKeyboardButton(text="⏹ Стоп", callback_data="btn_stop")],
        [InlineKeyboardButton(text="⚙️ Действие", callback_data="btn_action")]
    ])
    
    try:
        await bot.send_message(
            chat_id=CHAT_ID, 
            text="🖥 Агент успешно запущен и готов к работе!", 
            reply_markup=keyboard
        )
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "details": str(e)}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
