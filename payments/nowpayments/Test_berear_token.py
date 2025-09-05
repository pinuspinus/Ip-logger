# bot.py
import asyncio
import logging
import os
from html import escape

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import CommandStart

API_TOKEN = '8367088884:AAGx_hVpKTJWIzAb0wI8K12OJRXmHe3dXd4'

router = Router()
logging.basicConfig(level=logging.INFO)


@router.message(CommandStart())
async def cmd_start(msg: types.Message):
    await msg.answer(
        "Привет! Просто перешли мне сообщение из канала — я отправлю ID канала.\n"
        "Если пересылка скрывает источник, ID получить нельзя."
    )


@router.message(F.forward_from_chat)  # классическая пересылка (старое поле)
async def handle_forward_old(msg: types.Message):
    ch = msg.forward_from_chat
    title = escape(ch.title or "")
    await msg.answer(
        f"📡 Канал: <b>{title or 'без названия'}</b>\n"
        f"🆔 ID: <code>{ch.id}</code>",
        parse_mode="HTML"
    )


@router.message(F.forward_origin)  # новое поле forward_origin (MessageOrigin*)
async def handle_forward_new(msg: types.Message):
    """
    В новых версиях у пересланных сообщений источник может быть в forward_origin.
    Для каналов это MessageOriginChannel: есть .chat и .message_id.
    """
    origin = msg.forward_origin
    # Для aiogram v3 origin может быть dict-like или объектом класса.
    # Пытаемся безопасно вытащить чат.
    chat = getattr(origin, "chat", None)
    if chat and getattr(chat, "id", None):
        title = escape(getattr(chat, "title", "") or "")
        await msg.answer(
            f"📡 Канал: <b>{title or 'без названия'}</b>\n"
            f"🆔 ID: <code>{chat.id}</code>",
            parse_mode="HTML"
        )
        return

    # Если чат не доступен (скрытая пересылка / защищённый контент)
    await msg.answer(
        "Не удалось получить ID канала: источник пересылки скрыт или защищён.",
    )


@router.message()  # на случай, если прислали не пересланное из канала
async def fallback(msg: types.Message):
    await msg.answer("Перешли, пожалуйста, сообщение из канала (не из чата/лички).")


async def main():
    if not API_TOKEN:
        raise RuntimeError("Установите переменную окружения BOT_TOKEN с токеном вашего бота.")
    bot = Bot(API_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())