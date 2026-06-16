import os
import asyncio
import asyncpg
from fastapi import FastAPI
from contextlib import asynccontextmanager
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from tapo import ApiClient  # Используем именно эту библиотеку
import uvicorn

# Настройки из Railway
DATABASE_URL = os.getenv("DATABASE_URL")
API_TOKEN = os.getenv("TELEGRAM_TOKEN")
TAPO_EMAIL = os.getenv("TAPO_EMAIL")
TAPO_PASSWORD = os.getenv("TAPO_PASSWORD")
TAPO_IP = os.getenv("TAPO_IP", "192.168.1.9")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

async def execute_query(query, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    result = await conn.fetch(query, *args)
    await conn.close()
    return result

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Создаем таблицу если нет
    await execute_query("CREATE TABLE IF NOT EXISTS commands (id SERIAL PRIMARY KEY, cmd TEXT)")
    count = await execute_query("SELECT COUNT(*) FROM commands")
    if count[0][0] == 0:
        await execute_query("INSERT INTO commands (cmd) VALUES ('none')")
    
    task = asyncio.create_task(dp.start_polling(bot))
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

# --- Управление Tapo P110 ---

async def get_tapo_device():
    """Функция для правильного подключения к P110"""
    client = ApiClient(TAPO_EMAIL, TAPO_PASSWORD)
    # ВАЖНО: для P110 используем метод p110 и ОБЯЗАТЕЛЬНО await
    device = await client.p110(TAPO_IP) 
    return device

@dp.message(Command("on"))
async def cmd_on(message):
    await message.answer("⚡ Подключаюсь к облаку Tapo...")
    try:
        device = await get_tapo_device() # Ждем получения устройства
        await device.on()                # Ждем включения
        await message.answer("✅ Розетка P110 включена!")
    except Exception as e:
        await message.answer(f"❌ Ошибка включения: {e}")

@dp.message(Command("off"))
async def cmd_off(message):
    await execute_query("UPDATE commands SET cmd = 'shutdown' WHERE id = 1")
    await message.answer("🖥 Команда выключения Windows отправлена...")
    
    # Ждем 60 секунд, пока компьютер выключается
    await asyncio.sleep(60)
    
    try:
        device = await get_tapo_device() # Ждем получения устройства
        await device.off()               # Ждем выключения
        await message.answer("🔌 Питание P110 отключено.")
    except Exception as e:
        await message.answer(f"❌ Ошибка выключения: {e}")

# --- API для Агента ---

@app.get("/get_task")
async def get_task():
    result = await execute_query("SELECT cmd FROM commands WHERE id = 1")
    cmd = result[0][0]
    await execute_query("UPDATE commands SET cmd = 'none' WHERE id = 1")
    return {"command": cmd}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
