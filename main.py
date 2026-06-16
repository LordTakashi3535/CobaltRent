import os
import asyncio
import asyncpg
from fastapi import FastAPI
from contextlib import asynccontextmanager
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from tapo import ApiClient  # Добавлено
import uvicorn

# Настройки
DATABASE_URL = os.getenv("DATABASE_URL")
API_TOKEN = os.getenv("TELEGRAM_TOKEN")
# Настройки Tapo из переменных окружения
TAPO_EMAIL = os.getenv("TAPO_EMAIL")
TAPO_PASSWORD = os.getenv("TAPO_PASSWORD")
TAPO_IP = os.getenv("TAPO_IP", "192.168.1.9")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Инициализация Tapo клиента
tapo_client = ApiClient(TAPO_EMAIL, TAPO_PASSWORD)
device = tapo_client.p100(TAPO_IP)

# Функция БД
async def execute_query(query, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    result = await conn.fetch(query, *args)
    await conn.close()
    return result

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await execute_query("""
        CREATE TABLE IF NOT EXISTS commands (id SERIAL PRIMARY KEY, cmd TEXT)
    """)
    count = await execute_query("SELECT COUNT(*) FROM commands")
    if count[0][0] == 0:
        await execute_query("INSERT INTO commands (cmd) VALUES ('none')")
    
    task = asyncio.create_task(dp.start_polling(bot))
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

# --- Команды бота ---

@dp.message(Command("on"))
async def cmd_on(message):
    await message.answer("⚡ Подаю питание на ПК...")
    try:
        await device.turn_on()
        await message.answer("✅ Розетка включена!")
    except Exception as e:
        await message.answer(f"❌ Ошибка включения: {e}")

@dp.message(Command("off"))
async def cmd_off(message):
    await execute_query("UPDATE commands SET cmd = 'shutdown' WHERE id = 1")
    await message.answer("🖥 Windows завершает работу. Жду 60 секунд...")
    
    await asyncio.sleep(60)
    
    try:
        await device.turn_off()
        await message.answer("🔌 Розетка выключена.")
    except Exception as e:
        await message.answer(f"❌ Ошибка при выключении: {e}")

@dp.message(Command("sleep"))
async def cmd_sleep(message):
    await execute_query("UPDATE commands SET cmd = 'sleep' WHERE id = 1")
    await message.answer("✅ Команда сна сохранена!")

# --- API эндпоинт для клиента ---

@app.get("/get_task")
async def get_task():
    result = await execute_query("SELECT cmd FROM commands WHERE id = 1")
    cmd = result[0][0]
    await execute_query("UPDATE commands SET cmd = 'none' WHERE id = 1")
    return {"command": cmd}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
