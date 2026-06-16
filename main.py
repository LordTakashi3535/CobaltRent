import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv # Установите через pip install python-dotenv

# Загружаем переменные из .env файла (локально)
# На хостинге (Railway) переменные подхватятся автоматически из системы
load_dotenv()

# Получаем данные из переменных окружения
API_TOKEN = os.getenv('TELEGRAM_TOKEN')
MY_USER_ID = int(os.getenv('USER_ID', 0)) # Превращаем в число, по умолчанию 0

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

@dp.message(Command("sleep"))
async def cmd_sleep(message: types.Message):
    if message.from_user.id == MY_USER_ID:
        # Здесь логика записи команды в базу или файл, 
        # который будет проверять ваш домашний компьютер
        await message.answer("Команда на сон отправлена на ваш компьютер.")
    else:
        await message.answer("У вас нет прав доступа.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
