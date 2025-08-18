import asyncio
import uvicorn
from server import app
from bot import bot, dp


@app.get("/")
def home():
    return {"msg": "Hello from FastAPI + Bot"}

async def start_bot():
    await dp.start_polling(bot)

async def start():
    # Запускаем бота и сервер параллельно
    bot_task = asyncio.create_task(start_bot())
    server_config = uvicorn.Config(app, host="0.0.0.0", port=8000, loop="asyncio")
    server = uvicorn.Server(server_config)
    server_task = asyncio.create_task(server.serve())
    await asyncio.gather(bot_task, server_task)

if __name__ == "__main__":
    asyncio.run(start())