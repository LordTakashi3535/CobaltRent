import os
import asyncio
import asyncpg
from fastapi import FastAPI
from contextlib import asynccontextmanager
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
import aiohttp
import uvicorn

# Настройки
DATABASE_URL = os.getenv("DATABASE_URL")
API_TOKEN = os.getenv("TELEGRAM_TOKEN")
IFTTT_KEY = os.getenv("IFTTT_KEY") # Ваш ключ от IFTTT

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

# --- Отправка команд в IFTTT ---
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
        await message.answer("✅ Розетка включена! ПК должен стартовать.")
    else:
        await message.answer("❌ Ошибка связи с IFTTT.")

@dp.message(Command("off"))
async def cmd_off(message):
    await execute_query("UPDATE commands SET cmd = 'shutdown' WHERE id = 1")
    await message.answer("🖥 Компьютер выключается. Жду 60 секунд...")
    
    success = await trigger_ifttt("pc_off")
    if success:
        await message.answer("🔌 Питание успешно обесточено.")
    else:
        await message.answer("❌ Ошибка отключения розетки в IFTTT.")

# --- API эндпоинт для агента на ПК ---
@app.get("/get_task")
async def get_task():
    result = await execute_query("SELECT cmd FROM commands WHERE id = 1")
    cmd = result[0][0]
    await execute_query("UPDATE commands SET cmd = 'none' WHERE id = 1")
    return {"command": cmd}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
