import os
import asyncio
import asyncpg
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import aiohttp
import uvicorn

# --- НАСТРОЙКИ (берутся из Railway) ---
DATABASE_URL = os.getenv("DATABASE_URL")
API_TOKEN = os.getenv("TELEGRAM_TOKEN")
IFTTT_KEY = os.getenv("IFTTT_KEY")
# ID вашего чата для отправки меню при включении ПК
TARGET_CHAT_ID = os.getenv("CHAT_ID") 

if not all([API_TOKEN, DATABASE_URL, TARGET_CHAT_ID]):
    print("❌ ОШИБКА: Не все переменные окружения (TELEGRAM_TOKEN, DATABASE_URL, CHAT_ID) установлены!")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Глобальная переменная для хранения ID сообщения с меню, чтобы обновлять статус
# (Она сбросится при перезагрузке сервера на Railway, но это не критично)
MENU_MESSAGE_ID = None

main_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="▶️ Запустить Majestic", callback_data="start_majestic")],
    [InlineKeyboardButton(text="🚗 Выставить Аренду", callback_data="start_rent")],
    [InlineKeyboardButton(text="❌ Экстренный сброс игры", callback_data="kill_game")],
    [InlineKeyboardButton(text="⏹ Выключить ПК", callback_data="shutdown")]
])

# --- Функции Базы Данных ---
async def execute_query(query, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    result = await conn.fetch(query, *args)
    await conn.close()
    return result

# --- Инициализация и жизненный цикл ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Создаем таблицу, если нет
    await execute_query("CREATE TABLE IF NOT EXISTS commands (id SERIAL PRIMARY KEY, cmd TEXT)")
    # Убеждаемся, что есть одна строка
    count = await execute_query("SELECT COUNT(*) FROM commands")
    if count[0][0] == 0:
        await execute_query("INSERT INTO commands (cmd) VALUES ('none')")
    
    # Запускаем бота в фоне
    print("🤖 Бот запускается...")
    task = asyncio.create_task(dp.start_polling(bot))
    yield
    # Остановка
    task.cancel()
    print("👋 Бот остановлен.")

app = FastAPI(lifespan=lifespan)

# --- Вспомогательные функции ---
async def trigger_ifttt(event_name):
    """Посылает сигнал в IFTTT Webhooks"""
    if not IFTTT_KEY: return False
    url = f"https://maker.ifttt.com/trigger/{event_name}/with/key/{IFTTT_KEY}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as response:
                return response.status == 200
        except:
            return False

async def update_telegram_menu_status(text):
    """Обновляет текст в существующем сообщении меню"""
    global MENU_MESSAGE_ID
    if MENU_MESSAGE_ID and TARGET_CHAT_ID:
        full_text = f"🖥 Агент запущен.\n🔄 Статус: {text}"
        try:
            await bot.edit_message_text(
                chat_id=TARGET_CHAT_ID,
                message_id=MENU_MESSAGE_ID,
                text=full_text,
                reply_markup=main_keyboard
            )
            return True
        except Exception as e:
            print(f"⚠️ Ошибка обновления статуса в ТГ: {e}")
            pass
    return False

# ==============================
# --- ОБРАБОТЧИКИ TELEGRAM ---
# ==============================

# Команда /on (Включение)
@dp.message(Command("on"))
async def cmd_on(message):
    if str(message.chat.id) != str(TARGET_CHAT_ID): return # Только для хозяина
    await message.answer("⚡ Посылаю сигнал IFTTT на включение розетки...")
    if await trigger_ifttt("pc_on"):
        await message.answer("✅ Сигнал подан. Ожидаю запуска агента на ПК...")
    else:
        await message.answer("❌ Ошибка связи с IFTTT (проверьте ключ в Railway).")

# Команда /off (Мягкое выключение)
@dp.message(Command("off"))
async def cmd_off(message):
    if str(message.chat.id) != str(TARGET_CHAT_ID): return
    # Записываем команду для агента
    await execute_query("UPDATE commands SET cmd = 'shutdown' WHERE id = 1")
    await message.answer("🖥 Команда на выключение Windows отправлена Агенту.")
    await message.answer("⏳ Жду 60 секунд, чтобы система завершила работу...")
    
    # Пауза перед отключением розетки
    await asyncio.sleep(60)
    
    await message.answer("🔌 Посылаю сигнал IFTTT на обесточивание розетки...")
    if await trigger_ifttt("pc_off"):
        await message.answer("✅ Розетка отключена.")
    else:
        await message.answer("❌ Ошибка отключения розетки в IFTTT.")
    
# --- Обработка кликов по меню ---
# Исправленный обработчик
@dp.callback_query(F.data.in_({"start_majestic", "start_rent", "kill_game", "shutdown"}))
async def process_menu_buttons(callback: CallbackQuery):
    if str(callback.message.chat.id) != str(TARGET_CHAT_ID): return
    
    # Записываем команду в базу
    await execute_query("UPDATE commands SET cmd = ? WHERE id = 1", callback.data)
    
    # Обрабатываем конкретные действия для уведомлений
    if callback.data == "start_majestic":
        await update_telegram_menu_status("Получена команда 'Старт'...")
        await callback.answer("Запускаю процедуру Majestic RP!")
        
    elif callback.data == "start_rent":
        await update_telegram_menu_status("Запускаю ИИ для аренды авто...")
        await callback.answer("Бот начинает выставлять машины!")    
        
    elif callback.data == "shutdown":
        await update_telegram_menu_status("Получена команда 'Выключение'...")
        await callback.answer("Компьютер будет выключен.")
        
    elif callback.data == "kill_game":
        await update_telegram_menu_status("Закрываю зависшие процессы...")
        await callback.answer("Команда на экстренный сброс отправлена!")

# ================================
# --- API ЭНДПОИНТЫ ДЛЯ АГЕНТА ---
# ================================

@app.get("/get_task")
async def get_task():
    """Агент каждые 3-5 секунд забирает задачу"""
    result = await execute_query("SELECT cmd FROM commands WHERE id = 1")
    if not result: raise HTTPException(status_code=500)
    cmd = result[0][0]
    # Сразу очищаем, чтобы не выполнять дважды
    if cmd != 'none':
        await execute_query("UPDATE commands SET cmd = 'none' WHERE id = 1")
    return {"command": cmd}

@app.get("/agent_started")
async def api_agent_started():
    """Агент дергает при включении ПК"""
    global MENU_MESSAGE_ID
    # === ВАЖНО: Очищаем очередь команд, чтобы ПК не выключился сразу ===
    await execute_query("UPDATE commands SET cmd = 'none' WHERE id = 1")
    
    if not TARGET_CHAT_ID: return {"status": "error"}
    
    try:
        # Отправляем новое сообщение с меню
        msg = await bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text="🖥 Агент запущен.\n✅ Статус: Ожидание команд.",
            reply_markup=main_keyboard
        )
        MENU_MESSAGE_ID = msg.message_id # Запоминаем для обновлений
        print(f"📩 Меню отправлено. ID: {MENU_MESSAGE_ID}")
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "details": str(e)}

@app.get("/update_status")
async def api_update_status(text: str):
    """Агент присылает сюда текст для обновления меню"""
    success = await update_telegram_menu_status(text)
    return {"status": "ok" if success else "ignored"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
