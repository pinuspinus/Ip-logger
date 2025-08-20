from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from cryptography.fernet import Fernet
import json
from datetime import datetime
from config import SECRET_KEY, BOT_TOKEN, SERVER_URL
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from database.db_api import get_links, add_link, add_user

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
cipher = Fernet(SECRET_KEY)

# Главное меню
main_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text='👤 Личный кабинет', callback_data='user_panel'),
            InlineKeyboardButton(text='🔗 Мои ссылки', callback_data='my_links')
        ],
        [InlineKeyboardButton(text='🛠 Сгенерировать ссылку', callback_data='generate_link')]
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
        f"👋 {msg.from_user.first_name} 👋\n\nГлавное меню ⬇️",
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
    await state.set_state(LinkStates.waiting_for_url)
    await callback.answer()

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

        # сохраняем ссылку
        if add_link(original_url, short_link, msg.from_user.id):
            await msg.reply(f"Твоя ссылка (доступен 1 переход): {short_link}")
        else:
            await msg.reply("Ошибка: не удалось сохранить ссылку. Попробуй еще раз.")
    except Exception as e:
        await msg.reply(f"Ошибка: {e}")
    finally:
        await state.clear()

# Пагинация ссылок
LINKS_PER_PAGE = 5

def paginate_links(links, page: int):
    start = page * LINKS_PER_PAGE
    end = start + LINKS_PER_PAGE
    return links[start:end]

def build_links_keyboard(page: int, total_pages: int):
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"my_links:{page-1}"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"my_links:{page+1}"))
    buttons.append(InlineKeyboardButton(text="🏠 Меню", callback_data="back_to_menu"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])

@dp.callback_query(lambda c: c.data.startswith("my_links"))
async def my_links_callback(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    page = int(parts[1]) if len(parts) > 1 else 0

    links = get_links(callback.from_user.id)
    if not links:
        await callback.message.edit_text("-- Нет ссылок --", reply_markup=back_to_menu)
        return

    total_pages = max((len(links) + LINKS_PER_PAGE - 1) // LINKS_PER_PAGE, 1)
    page_links = paginate_links(links, page)

    text = f"🔗 Твои ссылки (стр. {page+1}/{total_pages}):\n\n"
    for l in page_links:
        text += (
            f"🌍 Оригинальная: {l.get('original_url', 'N/A')}\n"
            f"➡️ Короткая: {l.get('link', 'N/A')}\n"
            f"👀 Клики: {l.get('clicks', 0)}\n"
            f"🕒 Создана: {l.get('created_at', 'N/A')} UTC\n\n"
        )

    await callback.message.edit_text(text, reply_markup=build_links_keyboard(page, total_pages))

@dp.callback_query(lambda c: c.data == "back_to_menu")
async def back_to_menu_callback(callback: types.CallbackQuery):
    await callback.message.edit_text(
        f"👋 {callback.from_user.first_name} 👋\n\nГлавное меню ⬇️",
        reply_markup=main_menu
    )
    await callback.answer()

async def main():
    commands = [BotCommand(command="start", description="Запустить меню")]
    await bot.set_my_commands(commands)
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())