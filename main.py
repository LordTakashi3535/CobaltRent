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

DATABASE_URL = os.getenv("DATABASE_URL")
API_TOKEN = os.getenv("TELEGRAM_TOKEN")
IFTTT_KEY = os.getenv("IFTTT_KEY")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Переменная для хранения ID сообщения с меню, чтобы обновлять в нем статус
MENU_MSG_ID = None

# Клавиатура вынесена отдельно
main_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="▶️ Запустить Majestic", callback_data="btn_start")],
    [InlineKeyboardButton(text="⏹ Выключить ПК", callback_data="btn_stop")],
    [InlineKeyboardButton(text="⚙️ Действие", callback_data="btn_action")]
])

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

async def trigger_ifttt(event_name):
    url = f"https://maker.ifttt.com/trigger/{event_name}/with/key/{IFTTT_KEY}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return response.status == 200

# --- Обработка кнопок ---
@dp.callback_query(F.data.in_({"btn_start", "btn_stop", "btn_action"}))
async def process_buttons(callback: CallbackQuery):
    if callback.data == "btn_start":
        await execute_query("UPDATE commands SET cmd = 'start_majestic' WHERE id = 1")
        await callback.answer("Команда на запуск отправлена!")
    elif callback.data == "btn_stop":
        await execute_query("UPDATE commands SET cmd = 'shutdown' WHERE id = 1")
        await callback.answer("Команда выключения отправлена!")
    elif callback.data == "btn_action":
        await callback.answer("⚙️ Действие в разработке!", show_alert=True)

# --- API эндпоинты ---
@app.get("/get_task")
async def get_task():
    result = await execute_query("SELECT cmd FROM commands WHERE id = 1")
    cmd = result[0][0]
    await execute_query("UPDATE commands SET cmd = 'none' WHERE id = 1")
    return {"command": cmd}

@app.get("/agent_started")
async def agent_started():
    global MENU_MSG_ID
    await execute_query("UPDATE commands SET cmd = 'none' WHERE id = 1")
    try:
        msg = await bot.send_message(
            chat_id=CHAT_ID, 
            text="🖥 Агент запущен.\n🟢 Статус: Ожидание команд.", 
            reply_markup=main_keyboard
        )
        MENU_MSG_ID = msg.message_id # Запоминаем ID сообщения
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "details": str(e)}

@app.get("/update_status")
async def update_status(text: str):
    """Эндпоинт для обновления текста в меню"""
    global MENU_MSG_ID
    if MENU_MSG_ID:
        try:
            await bot.edit_message_text(
                chat_id=CHAT_ID,
                message_id=MENU_MSG_ID,
                text=f"🖥 Агент запущен.\n🔄 Статус: {text}",
                reply_markup=main_keyboard
            )
            return {"status": "ok"}
        except Exception:
            pass
    return {"status": "ignored"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
