import asyncio
import os
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode

from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv())

from handlers import user_cmd

ALLOWED_UPDATES = ['message, edited_message']

bot = Bot(token=os.getenv('TOKEN'))
dp = Dispatcher()

async def on_startup(bot: Bot):
    """Запускается при старте бота"""
    asyncio.create_task(user_cmd.check_ticket_updates(bot))

dp.include_routers(
        user_cmd.user_private_router
    )

async def main(): 
    await bot.delete_webhook(drop_pending_updates=True)
    await on_startup(bot)  # Запускаем фоновую задачу
    
    await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)


asyncio.run(main())