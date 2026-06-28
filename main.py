import os
import asyncio
import asyncpg
import re
import uuid
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
import aiohttp
import uvicorn
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ==========================================
# --- НАСТРОЙКИ (Переменные Railway) ---
# ==========================================
DATABASE_URL = os.getenv("DATABASE_URL")
API_TOKEN = os.getenv("TELEGRAM_TOKEN")
IFTTT_KEY = os.getenv("IFTTT_KEY")
TARGET_CHAT_ID = os.getenv("CHAT_ID") 

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", 0))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
MAJESTIC_BOT_USERNAME = '@MajesticRolePlayBot'

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- СОСТОЯНИЯ FSM ДЛЯ ДОБАВЛЕНИЯ/РЕДАКТИРОВАНИЯ АВТО ---
class FleetStates(StatesGroup):
    waiting_for_new_car_name = State()
    waiting_for_new_car_price = State()
    waiting_for_edit_price = State()

class AuthStates(StatesGroup):
    waiting_for_key = State()
    waiting_for_name = State()

async def execute_query(query, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    result = await conn.fetch(query, *args)
    await conn.close()
    return result

# ==========================================
# --- ГЕНЕРАЦИЯ МЕНЮ (DASHBOARD) ---
# ==========================================
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 ПОЛНЫЙ АВТО-ЦИКЛ", callback_data="cmd_full_auto")],
        [InlineKeyboardButton(text="🕹 Ручное управление", callback_data="menu_manual")],
        [InlineKeyboardButton(text="⏳ Таймеры", callback_data="menu_timers"),
         InlineKeyboardButton(text="📊 Статистика", callback_data="menu_stats")],
        [InlineKeyboardButton(text="⚙️ Автопарк и Цены", callback_data="menu_fleet")]
    ])

def get_manual_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Запуск Majestic", callback_data="cmd_start_majestic"),
         InlineKeyboardButton(text="🚗 Старт Аренды", callback_data="cmd_start_rent")],
        [InlineKeyboardButton(text="⚡ Вкл ПК", callback_data="cmd_turn_on"),
         InlineKeyboardButton(text="🛑 Выкл ПК", callback_data="cmd_shutdown")],
        [InlineKeyboardButton(text="🛑 ОСТАНОВИТЬ БОТА", callback_data="cmd_stop_bot"),
         InlineKeyboardButton(text="❌ Закрыть игру", callback_data="cmd_kill_game")],
        [InlineKeyboardButton(text="🔙 Назад в главное меню", callback_data="menu_main")]
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="menu_main")]
    ])

async def update_dashboard_ui(user_id, menu_type="main", custom_text=None):
    row = await execute_query("SELECT last_ping, status_text, menu_id FROM clients WHERE user_id = $1", user_id)
    if not row: return
    last_ping, status_text, menu_id = row[0]
    if not menu_id: return 
    
    is_online = False
    if last_ping and (datetime.now() - last_ping).total_seconds() < 90:
        is_online = True
            
    status_icon = "🟢 <b>Онлайн</b>" if is_online else "🔴 <b>Оффлайн</b>"
    text_status = status_text if status_text else "Ожидание команд..."
    
    base_text = (
        f"🎛 <b>Cobalt Rent | Панель управления</b>\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"🖥 <b>Связь с агентом:</b> {status_icon}\n"
        f"🔄 <b>Журнал:</b> <i>{text_status}</i>\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
    )
    
    text = custom_text if custom_text else base_text + "Выберите действие:"
    keyboard = get_manual_keyboard() if menu_type == "manual" else get_main_keyboard()
    
    try:
        await bot.edit_message_text(chat_id=user_id, message_id=menu_id, text=text, reply_markup=keyboard, parse_mode="HTML")
    except Exception: pass

# ==========================================
# --- АВТОМАТИЧЕСКИЕ УВЕДОМЛЕНИЯ ---
# ==========================================
async def check_expired_rents():
    while True:
        try:
            query = "SELECT id, car_name FROM rent_stats WHERE rent_end <= NOW() AND (notified IS NULL OR notified = FALSE)"
            expired = await execute_query(query)
            
            if expired:
                for row in expired:
                    rent_id, car_name = row[0], row[1]
                    
                    if TARGET_CHAT_ID:
                        # 1. Создаем клавиатуру (без лишнего try)
                        auto_kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🤖 Запустить полный цикл", callback_data="cmd_full_auto")]
                        ])
                        
                        # 2. Пробуем отправить сообщение
                        try:
                            await bot.send_message(
                                chat_id=TARGET_CHAT_ID, 
                                text=f"🟢 <b>Автомобиль вернулся!</b>\n\n🚗 <b>{car_name}</b> вышел из аренды.\nПора выставлять!", 
                                parse_mode="HTML",
                                reply_markup=auto_kb
                            )
                        except Exception: 
                            pass
                            
                    # 3. Отмечаем в БД, что уведомили (вне зависимости от успеха отправки в ТГ)
                    await execute_query("UPDATE rent_stats SET notified = TRUE WHERE id = $1", rent_id)
                    
        except Exception: 
            pass
            
        await asyncio.sleep(60)

# ==========================================
# --- ЮЗЕРБОТ (ЛОВЕЦ ЧЕКОВ) ---
# ==========================================
userbot = TelegramClient(StringSession(SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)

@userbot.on(events.NewMessage(chats=MAJESTIC_BOT_USERNAME))
async def handle_receipt(event):
    text = event.message.text
    if text and "Транспорт сдан в аренду!" in text:
        try:
            car_match = re.search(r"Транспорт:\s*(.+)", text)
            car_name = car_match.group(1).strip() if car_match else "Неизвестно"
            price_match = re.search(r"Цена:\s*\$([\d\s]+)", text)
            price = int(re.sub(r"\s", "", price_match.group(1))) if price_match else 0
            dur_match = re.search(r"Длительность:\s*(\d+)", text)
            duration = int(dur_match.group(1)) if dur_match else 0
            ref_match = re.search(r"Возврат денег за объявление:\s*\$([\d\s]+)", text)
            refund = int(re.sub(r"\s", "", ref_match.group(1))) if ref_match else 0

            rent_end = datetime.now() + timedelta(hours=duration)
            net_profit = price - 1250 + refund

            await execute_query("INSERT INTO rent_stats (car_name, price, duration, refund, rent_end, notified) VALUES ($1, $2, $3, $4, $5, FALSE)", car_name, price, duration, refund, rent_end)
            if TARGET_CHAT_ID:
                await bot.send_message(chat_id=TARGET_CHAT_ID, text=(f"🔔 <b>Новая сдача в аренду!</b>\n\n🚗 Авто: <b>{car_name}</b>\n💰 <b>Чистая прибыль: {net_profit}$</b>\n⏳ Освободится: {rent_end.strftime('%d.%m %H:%M')}"), parse_mode="HTML")
        except Exception: pass

# ==========================================
# --- ЖИЗНЕННЫЙ ЦИКЛ СЕРВЕРА ---
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Таблица ключей доступа
    await execute_query("""
        CREATE TABLE IF NOT EXISTS access_keys (
            key_code TEXT PRIMARY KEY,
            is_used BOOLEAN DEFAULT FALSE,
            used_by BIGINT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    
# 2. Таблица клиентов (у каждого свой статус ПК, пинг и команды)
    await execute_query("""
        CREATE TABLE IF NOT EXISTS clients (
            user_id BIGINT PRIMARY KEY,
            cmd TEXT DEFAULT 'none',
            last_ping TIMESTAMP,
            menu_id BIGINT,
            status_text TEXT DEFAULT 'Ожидание'
        )
    """)

    # Безопасно добавляем колонку для имени (на случай, если таблица уже существует, но без этой колонки)
    try:
        await execute_query("ALTER TABLE clients ADD COLUMN client_name TEXT DEFAULT 'Пользователь'")
    except Exception:
        pass
    
    # 3. Автопарк (привязан к владельцу)
    await execute_query("""
        CREATE TABLE IF NOT EXISTS fleet (
            id SERIAL PRIMARY KEY,
            owner_id BIGINT,
            car_name TEXT,
            price INTEGER,
            UNIQUE(owner_id, car_name)
        )
    """)
    
    # 4. Статистика аренды (привязана к владельцу)
    await execute_query("""
        CREATE TABLE IF NOT EXISTS rent_stats (
            id SERIAL PRIMARY KEY,
            owner_id BIGINT,
            car_name TEXT,
            price INTEGER,
            duration INTEGER,
            refund INTEGER,
            rent_end TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            notified BOOLEAN DEFAULT FALSE
        )
    """)

    # === ОБНОВЛЕНИЕ СТАРЫХ ТАБЛИЦ ДЛЯ ПРОДАЖ ===
    try:
        # 1. Добавляем колонку owner_id в старые таблицы
        await execute_query("ALTER TABLE fleet ADD COLUMN owner_id BIGINT")
        await execute_query("ALTER TABLE rent_stats ADD COLUMN owner_id BIGINT")
        
        # 2. Привязываем твой старый автопарк и статистику к тебе (Админу), чтобы ничего не пропало
        admin_id = int(TARGET_CHAT_ID)
        await execute_query("UPDATE fleet SET owner_id = $1 WHERE owner_id IS NULL", admin_id)
        await execute_query("UPDATE rent_stats SET owner_id = $1 WHERE owner_id IS NULL", admin_id)
        print("✅ База данных успешно обновлена под Multi-Account!")
    except Exception as e:
        # Если колонки уже есть, база выдаст ошибку, мы ее просто игнорируем
        pass
    
    print("🤖 Бот запускается...")
    bot_task = asyncio.create_task(dp.start_polling(bot))
    await userbot.connect()
    userbot_task = asyncio.create_task(userbot.run_until_disconnected())
    timer_task = asyncio.create_task(check_expired_rents())
    
    yield
    bot_task.cancel()
    userbot_task.cancel()
    timer_task.cancel()
    await userbot.disconnect()

app = FastAPI(lifespan=lifespan)

# ==========================================
# --- ФУНКЦИИ IFTTT ---
# ==========================================
async def trigger_ifttt(event_name):
    if not IFTTT_KEY: return False
    url = f"https://maker.ifttt.com/trigger/{event_name}/with/key/{IFTTT_KEY}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as response: return response.status == 200
        except: return False

async def shutdown_pc_task():
    await execute_query("UPDATE commands SET cmd = 'shutdown', status_text = '🖥 Процедура выключения ПК...' WHERE id = 1")
    await update_dashboard_ui()
    await asyncio.sleep(60) 
    await trigger_ifttt("pc_off") 
    await execute_query("UPDATE commands SET status_text = '💤 ПК обесточен' WHERE id = 1")
    await update_dashboard_ui()

# ==========================================
# --- ОБРАБОТЧИКИ TELEGRAM ---
# ==========================================
@dp.message(Command("keygen"))
async def cmd_keygen(message: Message):
    # Генерировать ключи можешь ТОЛЬКО ТЫ (админ)
    if str(message.chat.id) != str(TARGET_CHAT_ID): return
    new_key = str(uuid.uuid4())[:8].upper()
    try:
        await execute_query("INSERT INTO access_keys (key_code, is_used) VALUES ($1, FALSE)", new_key)
        await message.answer(f"🔑 <b>Создан новый ключ доступа!</b>\n\n<code>{new_key}</code>\n\n<i>Передайте его покупателю.</i>", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("menu", "start"))
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    is_admin = str(user_id) == str(TARGET_CHAT_ID)
    
    # Ищем ключ покупателя
    auth_check = await execute_query("SELECT key_code FROM access_keys WHERE used_by = $1", user_id)
    
    # Если это ты (админ) или активированный клиент — пускаем!
    if is_admin or auth_check:
        await state.clear()
        
        # Убеждаемся, что клиент есть в таблице статусов
        client = await execute_query("SELECT user_id FROM clients WHERE user_id = $1", user_id)
        if not client:
            await execute_query("INSERT INTO clients (user_id, cmd) VALUES ($1, 'none')", user_id)
            
        try: await message.delete()
        except: pass
        
        msg = await message.answer("🔄 Загрузка панели управления...", parse_mode="HTML")
        await execute_query("UPDATE clients SET menu_id = $1 WHERE user_id = $2", msg.message_id, user_id)
        await update_dashboard_ui(user_id)
    else:
        # Если это левый человек без ключа
        await message.answer("🔑 <b>Добро пожаловать в Cobalt Rent!</b>\nПожалуйста, введите ваш лицензионный ключ доступа:", parse_mode="HTML")
        await state.set_state(AuthStates.waiting_for_key)

@dp.message(AuthStates.waiting_for_key)
async def process_key(message: Message, state: FSMContext):
    key = message.text.strip()
    row = await execute_query("SELECT is_used FROM access_keys WHERE key_code = $1", key)
    
    if row and not row[0][0]: # Ключ найден и не активирован
        # Сохраняем валидный ключ во временную память FSM
        await state.update_data(valid_key=key)
        await message.answer("✅ <b>Ключ принят!</b>\nКак я могу к вам обращаться? (Введите ваше имя/никнейм):", parse_mode="HTML")
        # Переводим бота в режим ожидания имени
        await state.set_state(AuthStates.waiting_for_name)
    else:
        await message.answer("❌ Неверный или уже активированный ключ. Попробуйте еще раз:")

@dp.message(AuthStates.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    client_name = message.text.strip()
    data = await state.get_data()
    key = data['valid_key']
    user_id = message.from_user.id
    
    # 1. Привязываем ключ к Telegram ID
    await execute_query("UPDATE access_keys SET is_used = TRUE, used_by = $1 WHERE key_code = $2", user_id, key)
    
    # 2. Сохраняем ID клиента и его ИМЯ в таблицу клиентов
    await execute_query("""
        INSERT INTO clients (user_id, client_name, cmd) 
        VALUES ($1, $2, 'none') 
        ON CONFLICT (user_id) DO UPDATE SET client_name = EXCLUDED.client_name
    """, user_id, client_name)
    
    await message.answer(f"🎉 <b>Добро пожаловать, {client_name}!</b>\nНапишите /menu чтобы открыть панель управления.", parse_mode="HTML")
    await state.clear()

@dp.callback_query()
async def process_callbacks(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    # Защита: кнопки работают только у тебя и покупателей
    is_admin = str(user_id) == str(TARGET_CHAT_ID)
    auth_check = await execute_query("SELECT key_code FROM access_keys WHERE used_by = $1", user_id)
    if not is_admin and not auth_check:
        await callback.answer("У вас нет доступа!", show_alert=True)
        return

    data = callback.data
    
    if data.startswith("cmd_"):
        command = data.replace("cmd_", "")
        
        if command == "full_auto":
            if is_admin: await trigger_ifttt("pc_on") # Розетка пока только у админа
            await execute_query("UPDATE clients SET cmd = 'full_auto', status_text = '⚡ Запуск полного цикла. Включаю ПК...' WHERE user_id = $1", user_id)
            await callback.answer("Полный авто-цикл запущен!")
            
        elif command == "stop_bot":
            await execute_query("UPDATE clients SET cmd = 'stop_bot', status_text = '🔴 Экстренная остановка!' WHERE user_id = $1", user_id)
            await callback.answer("Останавливаю агента!", show_alert=True)
            
        elif command == "turn_on":
            if is_admin: await trigger_ifttt("pc_on")
            await execute_query("UPDATE clients SET status_text = '⚡ Отправлен сигнал на включение' WHERE user_id = $1", user_id)
            await callback.answer("Сигнал отправлен!")
            
        elif command == "shutdown":
            if is_admin: asyncio.create_task(delayed_power_off(user_id))
            else: await execute_query("UPDATE clients SET cmd = 'shutdown', status_text = '🖥 Процедура выключения ПК...' WHERE user_id = $1", user_id)
            await callback.answer("Запущено выключение!")
            
        else:
            await execute_query("UPDATE clients SET cmd = $1, status_text = $2 WHERE user_id = $3", command, f"Команда: {command}", user_id)
            await callback.answer("Команда в очереди!")
            
        await update_dashboard_ui(user_id)
        
    elif data == "menu_manual":
        await update_dashboard_ui(user_id, menu_type="manual")
        await callback.answer()
        
    elif data == "menu_timers":
        active_rents = await execute_query("SELECT car_name, rent_end FROM rent_stats WHERE rent_end > NOW() AND owner_id = $1 ORDER BY rent_end ASC", user_id)
        if not active_rents: text = "🤷‍♂️ <b>Прямо сейчас нет машин в аренде.</b>"
        else:
            text = "⏳ <b>Таймеры текущих аренд:</b>\n\n"
            now = datetime.now()
            for car_name, rent_end in active_rents:
                diff = rent_end - now
                if diff.total_seconds() > 0:
                    hours, remainder = divmod(int(diff.total_seconds()), 3600)
                    minutes, _ = divmod(remainder, 60)
                    time_left = f"{hours} ч. {minutes} мин." if hours > 0 else f"{minutes} мин."
                    text += f"🚗 <b>{car_name}</b> — осталось: {time_left} (до {rent_end.strftime('%H:%M')})\n"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_back_keyboard())
        
    elif data == "menu_stats":
        try:
            # 1. Общие данные
            res = await execute_query("""
                SELECT 
                    COALESCE(SUM(price - 1250 + refund), 0),
                    COUNT(*) 
                FROM rent_stats WHERE owner_id = $1
            """, user_id)
            
            # 2. Сегодня
            res_today = await execute_query("SELECT COALESCE(SUM(price - 1250 + refund), 0), COUNT(*) FROM rent_stats WHERE created_at >= CURRENT_DATE AND owner_id = $1", user_id)
            
            # 3. Вчера (используем INTERVAL '1 day')
            res_yesterday = await execute_query("""
                SELECT COALESCE(SUM(price - 1250 + refund), 0), COUNT(*) 
                FROM rent_stats 
                WHERE created_at >= CURRENT_DATE - INTERVAL '1 day' 
                  AND created_at < CURRENT_DATE 
                  AND owner_id = $1
            """, user_id)
            
            # 4. За 7 дней
            res_7 = await execute_query("SELECT COALESCE(SUM(price - 1250 + refund), 0), COUNT(*) FROM rent_stats WHERE created_at >= CURRENT_DATE - INTERVAL '7 days' AND owner_id = $1", user_id)
            
            # 5. Топ машин
            res_cars = await execute_query("SELECT car_name, COALESCE(SUM(price - 1250 + refund), 0) as total FROM rent_stats WHERE owner_id = $1 GROUP BY car_name ORDER BY total DESC LIMIT 5", user_id)

            total_sum, total_count = res[0]
            
            text = (
                f"📊 <b>Детальная статистика</b>\n\n"
                f"💰 <b>Всего заработано:</b> {total_sum}$\n"
                f"🚗 <b>Всего сдач:</b> {total_count}\n\n"
                f"📅 <b>Сегодня:</b> {res_today[0][0]}$ ({res_today[0][1]} шт.)\n"
                f"⬅️ <b>Вчера:</b> {res_yesterday[0][0]}$ ({res_yesterday[0][1]} шт.)\n"
                f"🗓 <b>За 7 дней:</b> {res_7[0][0]}$ ({res_7[0][1]} шт.)\n\n"
                f"🏆 <b>Топ-5 машин:</b>\n"
            )
            
            if res_cars:
                for car_name, total in res_cars:
                    text += f"▫️ {car_name}: {total}$\n"
            else:
                text += "▫️ Пока данных нет\n"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Очистить всю статистику", callback_data="cmd_clear_stats")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_main")]
            ])
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e: 
            await callback.answer(f"Ошибка: {e}")
        
    # --- БЛОК АВТОПАРКА ---
    elif data == "menu_fleet":
        cars = await execute_query("SELECT id, car_name, price FROM fleet WHERE owner_id = $1 ORDER BY id ASC", user_id)
        text = "⚙️ <b>Твой Автопарк</b>\n\n"
        keyboard = []
        if not cars: text += "🤷‍♂️ <i>В базе пока нет ни одной машины.</i>\n\n"
        else:
            for car_id, name, price in cars:
                text += f"🚗 <b>{name}</b> — {price}$/ч\n"
                keyboard.append([InlineKeyboardButton(text=f"✏️ Цена {name}", callback_data=f"editcar_{car_id}"), InlineKeyboardButton(text=f"❌ Удал. {name}", callback_data=f"delcar_{car_id}")])
                
        keyboard.append([InlineKeyboardButton(text="➕ Добавить новое авто", callback_data="add_car")])
        keyboard.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="menu_main")])
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))

    elif data == "add_car":
        await callback.message.answer("📝 <b>Пришли мне точное название авто из игры:</b>", parse_mode="HTML")
        await state.set_state(FleetStates.waiting_for_new_car_name)
        await callback.answer()

    elif data.startswith("delcar_"):
        car_id = int(data.split("_")[1])
        await execute_query("DELETE FROM fleet WHERE id = $1 AND owner_id = $2", car_id, user_id)
        await callback.answer("Авто удалено!")
        await process_callbacks(callback.model_copy(update={'data': 'menu_fleet'}), state)
        
    elif data.startswith("editcar_"):
        car_id = int(data.split("_")[1])
        await state.update_data(edit_car_id=car_id)
        await callback.message.answer("💵 <b>Пришли новую цену аренды за час (цифры):</b>", parse_mode="HTML")
        await state.set_state(FleetStates.waiting_for_edit_price)
        await callback.answer()

    elif data == "menu_main":
        await state.clear()
        await update_dashboard_ui(user_id)

# --- ОБРАБОТЧИКИ ВВОДА ТЕКСТА (АВТОПАРК) ---
@dp.message(FleetStates.waiting_for_new_car_price)
async def process_new_car_price(message: Message, state: FSMContext):
    # 1. Сначала проверим, что ввели именно число
    if not message.text.strip().isdigit():
        await message.answer("⚠️ Ошибка: Введите цену только цифрами!")
        return

    try:
        price = int(message.text.strip())
        data = await state.get_data()
        user_id = message.from_user.id
        car_name = data.get('car_name')

        if not car_name:
            await message.answer("❌ Ошибка: Не удалось найти название машины. Начните сначала: /menu")
            await state.clear()
            return

        # 2. Выполняем запрос
        await execute_query("""
            INSERT INTO fleet (owner_id, car_name, price) 
            VALUES ($1, $2, $3) 
            ON CONFLICT (owner_id, car_name) 
            DO UPDATE SET price = EXCLUDED.price
        """, user_id, car_name, price)
        
        await message.answer(f"✅ Машина <b>{car_name}</b> сохранена (цена: {price}$/ч)!", parse_mode="HTML")
        await state.clear()
        await update_dashboard_ui(user_id) # Обновляем меню

    except Exception as e:
        # Теперь бот пришлет ошибку прямо в чат, если что-то пойдет не так
        await message.answer(f"❌ Критическая ошибка: {str(e)}")
        await state.clear()

# ==========================================
# --- API ДЛЯ АГЕНТА НА ПК ---
# ==========================================
# ==========================================
# --- API ДЛЯ АГЕНТА НА ПК (MULTI-ACCOUNT) ---
# ==========================================

@app.get("/get_task")
async def get_task(user_id: int):
    # 1. Обновляем пинг конкретного клиента
    await execute_query("UPDATE clients SET last_ping = NOW() WHERE user_id = $1", user_id)
    
    # 2. Получаем команду
    result = await execute_query("SELECT cmd FROM clients WHERE user_id = $1", user_id)
    
    if not result: 
        await execute_query("INSERT INTO clients (user_id, cmd) VALUES ($1, 'none')", user_id)
        return {"command": "none"}
        
    cmd = result[0][0]
    
    # 3. ИСПРАВЛЕНИЕ: Теперь мы точно сбрасываем любую команду (включая full_auto) 
    # СРАЗУ ПОСЛЕ того, как агент ее прочитал!
    if cmd != 'none': 
        await execute_query("UPDATE clients SET cmd = 'none' WHERE user_id = $1", user_id)
        
    return {"command": cmd}

@app.get("/auth_agent")
async def api_auth_agent(key: str):
    # 👇 ДОБАВЛЯЕМ ХАРДКОД ПРОВЕРКУ ДЛЯ ТЕБЯ (АДМИНА) 👇
    if key == "ADMIN3565": # Можешь заменить "ADMIN" на свой секретный пароль
        # Автоматически возвращаем твой ID и имя "Администратор"
        return {"user_id": int(TARGET_CHAT_ID), "client_name": "Администратор"}
        
    # Дальше идет старый код для обычных покупателей:
    result = await execute_query("""
        SELECT a.used_by, c.client_name 
        FROM access_keys a
        JOIN clients c ON a.used_by = c.user_id
        WHERE a.key_code = $1 AND a.is_used = TRUE
    """, key)
    
    if not result:
        raise HTTPException(status_code=404, detail="Invalid or unused key")
        
    return {"user_id": result[0][0], "client_name": result[0][1]}

@app.get("/trigger_shutdown")
async def api_trigger_shutdown(user_id: int):
    # ЗАПУСКАЕМ ТАЙМЕР В ФОНЕ (чтобы Railway сразу отдал ответ "ok" и не завис)
    asyncio.create_task(delayed_power_off(user_id))
    return {"status": "ok"}

async def delayed_power_off(user_id: int):
    """Фоновая задача: ждет 60 сек и отключает розетку"""
    await asyncio.sleep(60) 
    await trigger_ifttt("pc_off")
    await execute_query("UPDATE clients SET status_text = '💤 ПК обесточен' WHERE user_id = $1", user_id)
    await update_dashboard_ui() # Обновляем меню в ТГ

@app.get("/agent_started")
async def api_agent_started(user_id: int):
    # Если текущая команда 'full_auto', оставляем её, иначе сбрасываем в 'none'
    query = """
        UPDATE clients 
        SET status_text = '✅ Агент на ПК запущен!',
            cmd = CASE 
                    WHEN cmd = 'full_auto' THEN 'full_auto' 
                    ELSE 'none' 
                  END
        WHERE user_id = $1
    """
    await execute_query(query, user_id)
    await update_dashboard_ui()
    return {"status": "ok"}

@app.get("/update_status")
async def api_update_status(user_id: int, text: str):
    # Записываем статус в профиль конкретного клиента
    await execute_query("UPDATE clients SET status_text = $2 WHERE user_id = $1", user_id, text)
    await update_dashboard_ui()
    return {"status": "ok"}

# НОВЫЙ ЭНДПОИНТ: Отдаем список машин для ПК
@app.get("/get_fleet")
async def api_get_fleet(user_id: int):
    # Ищем машины, которые принадлежат только этому клиенту
    cars = await execute_query("SELECT car_name, price FROM fleet WHERE owner_id = $1", user_id)
    return {"fleet": [{"name": c[0], "price": c[1]} for c in cars]}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
