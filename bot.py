from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from cryptography.fernet import Fernet
import json
from config import SECRET_KEY, BOT_TOKEN, SERVER_URL

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
cipher = Fernet(SECRET_KEY)

@dp.message(Command("start"))
async def start(msg: types.Message):
    await msg.reply("Привет! Отправь ссылку, и я сгенерирую безопасную короткую ссылку.")

@dp.message()
async def generate_short_link(msg: types.Message):
    original_url = msg.text
    try:
        from datetime import datetime, timedelta

        # Время жизни ссылки — 24 часа
        expires_timestamp = (datetime.utcnow() + timedelta(minutes=1)).timestamp()

        data = {
            "url": original_url,
            "user_id": msg.from_user.id,
            "expires": expires_timestamp  # поле с временем жизни
        }

        encrypted_data = cipher.encrypt(json.dumps(data).encode()).decode()
        short_link = f"{SERVER_URL}/link/{encrypted_data}"
        await msg.reply(f"Твоя ссылка (действует 24 часа): {short_link}")

    except Exception as e:
        await msg.reply(f"Ошибка: {e}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))