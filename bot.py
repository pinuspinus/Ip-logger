from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, callback_data
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, \
    CallbackQuery
from cryptography.fernet import Fernet
import json
from datetime import datetime, timedelta
from config import SECRET_KEY, BOT_TOKEN, SERVER_URL
import asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
cipher = Fernet(SECRET_KEY)

# Главное меню
main_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text='\U0001F464 Личный кабинет \U0001F464', callback_data='user_panel'), InlineKeyboardButton(text='\U0001F517 Мои ссылки \U0001F517', callback_data='my_links')],
        [InlineKeyboardButton(text=' \U0001F6E0 Сгенерировать ссылку \U0001F6E0', callback_data='generate_link')]
    ]
)


@dp.message(Command("start"))
async def start(msg: types.Message):
    await msg.reply(
        f"\U0001F44B\U0001F44B\U0001F44B {msg.from_user.first_name} \U0001F44B\U0001F44B\U0001F44B \n\n Главное меню ⬇️",
        reply_markup=main_menu
    )

# FSM: состояние ожидания ссылки
class LinkStates(StatesGroup):
    waiting_for_url = State()

# Callback для кнопки "Сгенерировать ссылку"
@dp.callback_query(lambda c: c.data == "generate_link")
async def generate_link_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Отправь мне ссылку, и я сгенерирую короткую версию для тебя.")
    await state.set_state(LinkStates.waiting_for_url)  # переводим пользователя в состояние ожидания ссылки
    await callback.answer()  # закрываем индикатор на кнопке

# Хендлер для следующего сообщения с ссылкой
@dp.message(LinkStates.waiting_for_url)
async def handle_url(msg: types.Message, state: FSMContext):
    original_url = msg.text
    try:
        data = {
            "url": original_url,
            "user_id": msg.from_user.id,
        }
        encrypted_data = cipher.encrypt(json.dumps(data).encode()).decode()
        short_link = f"{SERVER_URL}/link/{encrypted_data}"
        await msg.reply(f"Твоя ссылка (доступен 1 переход): {short_link}")
    except Exception as e:
        await msg.reply(f"Ошибка: {e}")
    finally:
        await state.clear()  # выходим из состояния ожидания ссылки

async def set_commands():
    commands = [
        BotCommand(command="start", description="Запустить меню"),
    ]
    await bot.set_my_commands(commands)

async def main():
    # Регистрируем команды бота
    commands = [
        BotCommand(command="start", description="Запустить меню"),
    ]
    await bot.set_my_commands(commands)
    # Запускаем polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())