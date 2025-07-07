from aiogram.types import KeyboardButtonPollType, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

start_kb = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Создать заявку"),
        ],
        [
            KeyboardButton(text="Мои заявки"),
        ]
    ],
    resize_keyboard=True,
)

cancel = ReplyKeyboardMarkup( 
    keyboard=[
        [
            KeyboardButton(text="Отменить заявку ❌")
        ]
    ],
    resize_keyboard=True,
)

type = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="Инцидент"),
        ],
        [
            KeyboardButton(text="Запрос"),
        ],
        [
            KeyboardButton(text="Отменить заявку ❌")
        ]
    ],
    resize_keyboard=True,
)


urgency = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="1 - Очень низкая"),
        ],
        [
            KeyboardButton(text="2 - Низкая"),
        ],
        [
            KeyboardButton(text="3 - Средняя"),
        ],
         [
            KeyboardButton(text="4 - Высокая"),
        ],
         [
            KeyboardButton(text="5 - Очень высокая"),
        ],
        [
            KeyboardButton(text="Отменить заявку ❌")
        ]
    ],
    resize_keyboard=True,
)