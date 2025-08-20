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
from database.db_api import get_links, add_link, add_user

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

back_to_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text='Назад', callback_data='back_to_menu')]
    ]
)

@dp.message(Command("start"))
async def start(msg: types.Message):
    await msg.reply(
        f"\U0001F44B\U0001F44B\U0001F44B {msg.from_user.first_name} \U0001F44B\U0001F44B\U0001F44B \n\n Главное меню ⬇️",
        reply_markup=main_menu
    )
    add_user(msg.from_user.id)

# FSM: состояние ожидания ссылки
class LinkStates(StatesGroup):
    waiting_for_url = State()

# Callback для кнопки "Сгенерировать ссылку"
@dp.callback_query(lambda c: c.data == "generate_link")
async def generate_link_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Отправь мне ссылку, и я сгенерирую короткую версию для тебя.")
    await state.set_state(LinkStates.waiting_for_url)  # переводим пользователя в состояние ожидания ссылки
    await callback.answer()  # закрываем индикатор на кнопке

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

        # отвечаем пользователю
        await msg.reply(f"Твоя ссылка (доступен 1 переход): {short_link}")

        # сохраняем обе ссылки
        add_link(original_url, short_link, msg.from_user.id)
    except Exception as e:
        await msg.reply(f"Ошибка: {e}")
    finally:
        await state.clear()

LINKS_PER_PAGE = 5  # сколько ссылок на одной странице


def paginate_links(links, page: int):
    start = page * LINKS_PER_PAGE
    end = start + LINKS_PER_PAGE
    return links[start:end]


def build_links_keyboard(page: int, total_pages: int):
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"my_links:{page-1}"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"my_links:{page+1}"))
    buttons.append(InlineKeyboardButton("🏠 Меню", callback_data="back_to_menu"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


@dp.callback_query(lambda c: c.data.startswith("my_links"))
async def my_links_callback(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    page = int(parts[1]) if len(parts) > 1 else 0

    links = get_links(callback.from_user.id)

    if not links:
        await callback.message.edit_text(
            "-- Нет ссылок --", reply_markup=back_to_menu
        )
        return

    total_pages = (len(links) + LINKS_PER_PAGE - 1) // LINKS_PER_PAGE
    page_links = paginate_links(links, page)

    text = f"🔗 Твои ссылки (стр. {page+1}/{total_pages}):\n\n"
    for l in page_links:
        text += (
            f"🌍 Оригинальная: {l['original_url']}\n"
            f"➡️ Короткая: {l['link']}\n"
            f"👀 Клики: {l['clicks']}\n"
            f"🕒 Создана: {l['created_at']}\n\n"
        )

    await callback.message.edit_text(text, reply_markup=build_links_keyboard(page, total_pages))

@dp.callback_query(lambda c: c.data == "back_to_menu")
async def back_to_menu_callback(callback: types.CallbackQuery):
    await callback.message.edit_text(
        f"\U0001F44B\U0001F44B\U0001F44B {callback.from_user.first_name} \U0001F44B\U0001F44B\U0001F44B \n\nГлавное меню ⬇️",
        reply_markup=main_menu
    )
    await callback.answer()  # закрыть "часики" у кнопки


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