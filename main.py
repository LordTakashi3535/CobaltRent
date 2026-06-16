import asyncio
import os # Импортируем для работы с переменными окружения
from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
import uvicorn

# Берем токен из переменной окружения, которую вы зададите в Railway
API_TOKEN = os.getenv("TELEGRAM_TOKEN")

app = FastAPI()
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

tasks = {"cmd": None}

@dp.message(Command("sleep"))
async def cmd_sleep(message):
    tasks["cmd"] = "sleep"
    await message.answer("Команда на сон сохранена для клиента!")

@app.get("/get_task")
async def get_task():
    cmd = tasks.get("cmd")
    tasks["cmd"] = None
    return {"command": cmd}

async def run_bot():
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Запускаем бота
    loop = asyncio.get_event_loop()
    loop.create_task(run_bot())
    
    # Запускаем сервер на порту, который требует Railway
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
