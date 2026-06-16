import os
import asyncio
import asyncpg
from fastapi import FastAPI
from contextlib import asynccontextmanager
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from tapo import ApiClient  # Библиотека для управления через облако
import uvicorn

# --- Настройки из переменных окружения ---
DATABASE_URL = os.getenv("DATABASE_URL")
API_TOKEN = os.getenv("TELEGRAM_TOKEN")
TAPO_EMAIL = os.getenv("TAPO_EMAIL")
TAPO_PASSWORD = os.getenv("TAPO_PASSWORD")
TAPO_IP = os.getenv("TAPO_IP", "192.168.1.9")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- Функции БД ---
async def execute_query(query, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    result = await conn.fetch(query, *args)
    await conn.close()
    return result

# --- Жизненный цикл FastAPI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: создание таблицы
    await execute_query("""
        CREATE TABLE IF NOT EXISTS commands (id SERIAL PRIMARY KEY, cmd TEXT)
    """)
    count = await execute_query("SELECT COUNT(*) FROM commands")
    if count[0][0] == 0:
        await execute_query("INSERT INTO commands (cmd) VALUES ('none')")
    
    # Запуск бота в фоновом режиме
    task = asyncio.create_task(dp.start_polling(bot))
    yield
    # Shutdown
    task.cancel()

app = FastAPI(lifespan=lifespan)

# --- Команды бота (Telegram) ---

@dp.message(Command("on"))
async def cmd_on(message):
    await message.answer("⚡ Подаю питание на ПК через облако...")
    try:
        # Прямое облачное управление
        client = ApiClient(TAPO_EMAIL, TAPO_PASSWORD)
        device = client.p100(TAPO_IP)
        await device.turnOn()
        await message.answer("✅ Розетка включена! ПК запускается.")
    except Exception as e:
        await message.answer(f"❌ Ошибка включения: {e}")

@dp.message(Command("off"))
async def cmd_off(message):
    # 1. Даем команду агенту на ПК корректно выключить Windows
    await execute_query("UPDATE commands SET cmd = 'shutdown' WHERE id = 1")
    await message.answer("🖥 Команда выключения Windows отправлена. Жду 60 секунд...")
    
    # 3. Физически выключаем розетку через облако
    try:
        client = ApiClient(TAPO_EMAIL, TAPO_PASSWORD)
        device = client.p100(TAPO_IP)
        
        # Диагностика: выводим все методы объекта, чтобы увидеть правильное имя
        methods = [method for method in dir(device) if callable(getattr(device, method)) and not method.startswith("_")]
        print(f"Доступные методы: {methods}")
        
        # Если вы увидите в списке что-то вроде 'on', 'off', 'turn_on' или 'set_power_state',
        # подставьте его ниже:
        await device.off() # Попробуйте этот вариант первым
        
        await message.answer("🔌 Питание обесточено.")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}\nМетоды объекта: {methods}")

@dp.message(Command("sleep"))
async def cmd_sleep(message):
    await execute_query("UPDATE commands SET cmd = 'sleep' WHERE id = 1")
    await message.answer("✅ Команда сна сохранена!")

# --- API эндпоинт для агента на ПК ---

@app.get("/get_task")
async def get_task():
    result = await execute_query("SELECT cmd FROM commands WHERE id = 1")
    cmd = result[0][0]
    # Сбрасываем команду после получения
    await execute_query("UPDATE commands SET cmd = 'none' WHERE id = 1")
    return {"command": cmd}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
