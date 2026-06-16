import os
import asyncio
import asyncpg
from fastapi import FastAPI
from contextlib import asynccontextmanager
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
import uvicorn

# Настройки
DATABASE_URL = os.getenv("DATABASE_URL")
API_TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Функция БД
async def execute_query(query, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    result = await conn.fetch(query, *args)
    await conn.close()
    return result

# 1. Используем Lifespan для запуска фоновых задач
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: создаем таблицу
    await execute_query("""
        CREATE TABLE IF NOT EXISTS commands (id SERIAL PRIMARY KEY, cmd TEXT)
    """)
    count = await execute_query("SELECT COUNT(*) FROM commands")
    if count[0][0] == 0:
        await execute_query("INSERT INTO commands (cmd) VALUES ('none')")
    
    # Запускаем бота как фоновую задачу
    task = asyncio.create_task(dp.start_polling(bot))
    yield
    # Shutdown: отменяем задачу
    task.cancel()

app = FastAPI(lifespan=lifespan)

@dp.message(Command("sleep"))
async def cmd_sleep(message):
    await execute_query("UPDATE commands SET cmd = 'sleep' WHERE id = 1")
    await message.answer("✅ Команда сохранена!")

@app.get("/get_task")
async def get_task():
    result = await execute_query("SELECT cmd FROM commands WHERE id = 1")
    cmd = result[0][0]
    await execute_query("UPDATE commands SET cmd = 'none' WHERE id = 1")
    return {"command": cmd}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
