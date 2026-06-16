import os
import asyncio
import asyncpg
from fastapi import FastAPI
import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.filters import Command

# Инициализация
DATABASE_URL = os.getenv("DATABASE_URL")
API_TOKEN = os.getenv("TELEGRAM_TOKEN")

app = FastAPI()
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Функция для работы с PostgreSQL
async def execute_query(query, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    result = await conn.fetch(query, *args)
    await conn.close()
    return result

@app.on_event("startup")
async def startup():
    await execute_query("""
        CREATE TABLE IF NOT EXISTS commands (id SERIAL PRIMARY KEY, cmd TEXT)
    """)
    count = await execute_query("SELECT COUNT(*) FROM commands")
    if count[0][0] == 0:
        await execute_query("INSERT INTO commands (cmd) VALUES ('none')")

@dp.message(Command("sleep"))
async def cmd_sleep(message):
    await execute_query("UPDATE commands SET cmd = 'sleep' WHERE id = 1")
    await message.answer("✅ Команда на сон сохранена в базе!")

@app.get("/get_task")
async def get_task():
    result = await execute_query("SELECT cmd FROM commands WHERE id = 1")
    cmd = result[0][0]
    # Сбрасываем команду сразу после получения
    await execute_query("UPDATE commands SET cmd = 'none' WHERE id = 1")
    return {"command": cmd}

async def run_bot():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.create_task(run_bot())
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
