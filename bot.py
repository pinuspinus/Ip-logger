from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from cryptography.fernet import Fernet
import json
from datetime import datetime, timedelta
from config import SECRET_KEY, BOT_TOKEN, SERVER_URL

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
cipher = Fernet(SECRET_KEY)

# Главное меню
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Сгенерировать ссылку")],
        [KeyboardButton(text="Информация о боте")]
    ],
    resize_keyboard=True
)

@dp.message(Command("start"))
async def start(msg: types.Message):
    await msg.reply(
        "Привет! Я помогу тебе сгенерировать безопасные короткие ссылки.\n"
        "Выбери действие в меню ниже ⬇️",
        reply_markup=main_menu
    )

@dp.message()
async def handle_message(msg: types.Message):
    if msg.text == "Сгенерировать ссылку":
        await msg.reply("Отправь мне ссылку, и я сгенерирую короткую версию для тебя.")
        # Ставим ожидание следующего сообщения как URL
        return
    elif msg.text == "Информация о боте":
        await msg.reply(
            "Я бот для создания безопасных коротких ссылок.\n"
            "Ссылки действуют 24 часа.\n"
            "Нажми 'Сгенерировать ссылку', чтобы получить короткую ссылку."
        )
        return
    else:
        # Обрабатываем сообщение как URL
        original_url = msg.text
        try:
            expires_timestamp = (datetime.utcnow() + timedelta(hours=24)).timestamp()
            data = {
                "url": original_url,
                "user_id": msg.from_user.id,
                "expires": expires_timestamp
            }
            encrypted_data = cipher.encrypt(json.dumps(data).encode()).decode()
            short_link = f"{SERVER_URL}/link/{encrypted_data}"
            await msg.reply(f"Твоя ссылка (действует 24 часа): {short_link}")
        except Exception as e:
            await msg.reply(f"Ошибка: {e}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))