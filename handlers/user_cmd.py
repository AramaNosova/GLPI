import os
import requests
from aiogram import F, types, Router, Bot,Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, or_f
from aiogram.fsm.state import StatesGroup, State, default_state
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.utils.formatting import as_list, as_marked_section, Bold,Spoiler #Italic, as_numbered_list и тд 
from aiogram.types import Message, FSInputFile
from handlers.keyboards import reply
from filters.chat_types import ChatTypeFilter
from utils.states import Excursion
import base64
from html import unescape
import re

user_private_router = Router()
user_private_router.message.filter(ChatTypeFilter(["private"]))

GLPI_URL = os.getenv('glpi_url')
GLPI_API_KEY = os.getenv('glpi_api_key')

bot = Bot(token=os.getenv('TOKEN'))
storage = MemoryStorage()
dp = Dispatcher()

class NewTicketForm(StatesGroup):
    TITLE = State()        # Название заявки
    DESCRIPTION = State()  # Описание проблемы
    URGENCY = State()      # Срочность (1-5)
    TYPE = State()         # Тип заявки


class AuthForm(StatesGroup):
    LOGIN = State()
    PASSWORD = State()
    SESSION_TOKEN = State()

# ... (предыдущий импорт остается без изменений)

from typing import List, Dict, Tuple
import numpy as np
from sentence_transformers import SentenceTransformer

# Инициализация модели для эмбеддингов
model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')

# Векторная база данных категорий
CATEGORIES = {
    "Авторизация": {
        "description": "Проблемы с входом в систему, сброс пароля, блокировка учетной записи",
        "embedding": None  # Будет заполнено при инициализации
    },
    "Вопросы HR": {
        "description": "Кадровые вопросы, отпуска, больничные, оформление документов",
        "embedding": None
    },
    "Вопросы о времени": {
        "description": "График работы, табель учета времени, опоздания, перенос встреч",
        "embedding": None
    },
    "Технические проблемы": {
        "description": "Неисправности оборудования, проблемы с ПО, доступ к ресурсам",
        "embedding": None
    }
}

# Инициализация эмбеддингов для категорий
def initialize_category_embeddings():
    for category_name, category_data in CATEGORIES.items():
        # Создаем эмбеддинг для названия категории и ее описания
        text = f"{category_name}: {category_data['description']}"
        CATEGORIES[category_name]['embedding'] = model.encode(text)

initialize_category_embeddings()

def find_best_category(title: str, description: str) -> Tuple[str, float]:
    """
    Находит наиболее подходящую категорию для заявки на основе названия и описания.
    Возвращает название категории и score сходства (0-1).
    """
    if not title and not description:
        return "Другое", 0.0
    
    # Создаем эмбеддинг для входного текста
    input_text = f"{title}: {description}"
    input_embedding = model.encode(input_text)
    
    best_category = "Другое"
    best_score = 0.0
    
    # Сравниваем с каждой категорией
    for category_name, category_data in CATEGORIES.items():
        category_embedding = category_data['embedding']
        if category_embedding is None:
            continue
            
        # Вычисляем косинусное сходство
        similarity = np.dot(input_embedding, category_embedding) / (
            np.linalg.norm(input_embedding) * np.linalg.norm(category_embedding)
        )
        
        if similarity > best_score:
            best_score = similarity
            best_category = category_name
    
    # Если сходство слишком низкое, возвращаем "Другое"
    if best_score < 0.3:  # Пороговое значение можно настроить
        return "Другое", best_score
    
    return best_category, best_score

# Модифицированная функция создания заявки с учетом категории
def create_glpi_ticket(session_token: str, ticket_data: dict, telegram_id: int) -> bool:
    url = f"{GLPI_URL}/Ticket"
    headers = {
        "Content-Type": "application/json",
        "App-Token": GLPI_API_KEY,
        "Session-Token": session_token
    }
    
    # Определяем категорию заявки
    category, score = find_best_category(ticket_data['title'], ticket_data['description'])
    content = f"Заявка от пользователя Telegram (ID: {telegram_id})\n\n"
    content += f"Категория (определено автоматически): {category}\n\n"
    content += f"Описание проблемы:\n{ticket_data['description']}"
    data = {
        "input": {
            "name": ticket_data['title'],
            "content": content,
            "urgency": ticket_data['urgency'],
            "type": ticket_data['type'],
            "itilcategories_id": 1  # Пример категории (можно настроить маппинг)
        }
    }
    print(f"сходство: {score:.2f}")
    print(data)
    try:
        response = requests.post(url, headers=headers, json=data)
        return response.status_code == 201
    except Exception as e:
        print(f"Ошибка создания заявки: {str(e)}")
        return False

# ... (остальной код остается без изменений)

# Обработчик команды авторизации
@user_private_router.message(Command("start"))
#@user_private_router.message(F.text.lower() == "авторизация")
async def start_auth(message: Message, state: FSMContext):
    await state.set_state(AuthForm.LOGIN)
    await message.answer("Введите ваш логин для GLPI:", reply_markup=types.ReplyKeyboardRemove())

# Шаг 1: Получение логина
@user_private_router.message(AuthForm.LOGIN)
async def process_login(message: Message, state: FSMContext):
    await state.update_data(login=message.text)
    await state.set_state(AuthForm.PASSWORD)
    await message.answer("Введите ваш пароль:")

user_sessions = {}

# Шаг 2: Получение пароля и попытка авторизации
@user_private_router.message(AuthForm.PASSWORD)
async def process_password(message: Message, state: FSMContext):
    user_data = await state.get_data()
    login = user_data['login']
    password = message.text
    
    # Пытаемся авторизоваться и получить session_token + профиль
    session_data = init_session_with_auth(login, password)
    
    if session_data:
        # Сохраняем в глобальное хранилище
        user_sessions[message.from_user.id] = session_data
        await message.answer(f"✅ Вы успешно авторизованы как {session_data['profile']}!")
        await message.answer("Выберите действие", reply_markup=reply.start_kb)
    else:
        await message.answer("❌ Ошибка авторизации. Проверьте логин и пароль")
    
    await state.clear()

def init_session_with_auth(login: str, password: str) -> dict:
    """Авторизация в GLPI и получение session_token + профиля пользователя"""
    url = f'{GLPI_URL}/initSession'
    headers = {
        'Content-Type': 'application/json',
        'App-Token': GLPI_API_KEY,
        'Authorization': f'Basic {base64.b64encode(f"{login}:{password}".encode()).decode()}'
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code in (200, 206):
            session_token = response.json().get('session_token')
            if not session_token:
                return None
            
            # Получаем информацию о профиле пользователя
            profile = get_glpi_user_profile(session_token)
            if not profile:
                return None
                
            return {
                'session_token': session_token,
                'profile': profile
            }
        else:
            print(f"Ошибка авторизации: {response.status_code}, {response.text}")
            return None
    except Exception as e:
        print(f"Ошибка подключения: {str(e)}")
        return None

def get_glpi_user_profile(session_token: str) -> str:
    """Определяет профиль пользователя в GLPI"""
    url = f"{GLPI_URL}/getFullSession/"
    headers = {
        "Content-Type": "application/json",
        "App-Token": GLPI_API_KEY,
        "Session-Token": session_token
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            profile_name = response.json().get('session', {}).get('glpiactiveprofile', {}).get('name', 'Normal')
            return profile_name
        return "Normal"
    except Exception as e:
        print(f"Ошибка получения профиля: {str(e)}")
        return "Normal"

def get_glpi_tickets(session_token):
    print(session_token)
    if not session_token:
        return None
    
    url = f'{GLPI_URL}/Ticket'
    headers = {
        'Content-Type': 'application/json',
        'App-Token': GLPI_API_KEY,
        'Session-Token': session_token
    }

     # Параметры для фильтрации (можно настроить под свои нужды)
    params = {
        'range': '0-10',  # Получаем первые 10 заявок
        'order': 'DESC',  # Сортировка по убыванию (новые сначала)
        'sort': 'id',     # Сортировка по ID (правильное имя поля)
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code in (200, 206):
            return response.json() or []
        else:
            print(f"Ошибка при получении заявок: {response.status_code}, {response.text}")
            return None
    except Exception as e:
        print(f"Ошибка подключения: {str(e)}")
        return None

# Создание новой заявки
@user_private_router.message(F.text.lower() == "создать заявку")
async def cmd_create_ticket(message: Message, state: FSMContext):
    await state.set_state(NewTicketForm.TITLE)
    await message.answer("Введите название заявки:")

# Шаг 1: Получение названия
@user_private_router.message(NewTicketForm.TITLE)
async def process_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(NewTicketForm.DESCRIPTION)
    await message.answer("Опишите проблему подробно:")

# Шаг 2: Получение описания
@user_private_router.message(NewTicketForm.DESCRIPTION)
async def process_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(NewTicketForm.URGENCY)

    await message.answer("Выберите срочность (1-5):", reply_markup=reply.urgency)

# Шаг 3: Получение срочности
@user_private_router.message(NewTicketForm.URGENCY)
async def process_urgency(message: Message, state: FSMContext):
    if message.text[0].isdigit() and 1 <= int(message.text[0]) <= 5:
        await state.update_data(urgency=int(message.text[0]))
        await state.set_state(NewTicketForm.TYPE)
        await message.answer("Выберите тип заявки:", reply_markup=reply.type)
    else:
        await message.answer("Пожалуйста, выберите срочность от 1 до 5")

# Шаг 5: Получение типа и финальное создание
@user_private_router.message(NewTicketForm.TYPE)
async def process_type(message: Message, state: FSMContext):
    ticket_types = {
        "инцидент": 1,
        "запрос": 2,
    }
    
    type_lower = message.text.lower()
    if type_lower in ticket_types:
        await state.update_data(type=ticket_types[type_lower])
        
        # Получаем все данные
        data = await state.get_data()
        
        # Создаем заявку в GLPI
         # Получаем токен из хранилища
        session_data = user_sessions.get(message.from_user.id)
        session_token = session_data.get('session_token') if session_data else None 

        if session_token:
            success = create_glpi_ticket(session_token, data, message.from_user.id)
            if success:
                await message.answer("✅ Заявка успешно создана!", reply_markup=reply.start_kb)
            else:
                await message.answer("❌ Ошибка при создании заявки")
        else:
            await message.answer("❌ Ошибка авторизации в GLPI")
        
        await state.clear()
    else:
        await message.answer("Пожалуйста, выберите тип из предложенных вариантов")

def get_user_tickets(session_data: dict, telegram_id: int) -> list:
    """Получает заявки в зависимости от роли пользователя в GLPI"""
    if not session_data:
        return None
    
    session_token = session_data.get('session_token')
    profile = session_data.get('profile', 'Normal')
    
    all_tickets = get_glpi_tickets(session_token)
    if not all_tickets:
        return None
    
    # Для администраторов и техников показываем все заявки
    if profile in ('Admin', 'Super-Admin', 'Technician'):
        return all_tickets
    # Для обычных пользователей — только их заявки
    else:
        return [
            ticket for ticket in all_tickets 
            if f"Telegram (ID: {telegram_id})" in ticket.get('content', '')
        ]

# Просмотр заявок
@user_private_router.message(F.text.lower() == "мои заявки")
async def cmd_my_tickets(message: Message):
    await message.answer("Получаю список заявок...")
    session_data = user_sessions.get(message.from_user.id)
    
    if not session_data:
        await message.answer("❌ Вы не авторизованы в GLPI")
        return
    
    try:
        tickets = get_user_tickets(session_data, message.from_user.id)
        if not tickets:
            await message.answer("🚫 Нет доступных заявок")
            return
            
        response = "📋 Ваши последние заявки:\n\n" + \
                  "\n".join([format_ticket(t) for t in tickets[:10]])
        
        await message.answer(response[:4000])
        
    except Exception as e:
        await message.answer("⚠️ Произошла ошибка при получении заявок")
        print(f"Ошибка: {str(e)}") 

def format_ticket(ticket):
     # Обрезаем длинное описание до 200 символов
    content = clean_html_content(ticket.get('content', 'Нет описания'))

    # Добавляем переносы строк для ключевых частей
    formatted_content = content.replace(
        "Заявка от пользователя Telegram", 
        "\nЗаявка от пользователя Telegram"
    ).replace(
        "Категория (определено автоматически):", 
        "\nКатегория (определено автоматически):"
    ).replace(
        "Описание проблемы:", 
        "\nОписание проблемы:"
    )
    
    short_content = (content[:200] + '...') if len(content) > 200 else content
    
    return (
        f"🔹 #{ticket.get('id', 'N/A')}\n"
        f"📌 Тема: {ticket.get('name', 'Без названия')}\n"
        f"📝 Описание: {short_content}\n"
        f"🔄 Статус: {get_status_name(ticket.get('status', 0))}\n"
        f"📅 Дата создания: {ticket.get('date', 'N/A')}\n"
        f"⚠️ Приоритет: {get_priority_name(ticket.get('priority', 0))}\n"
        f"🔔 Тип: {get_type_name(ticket.get('type', 0))}\n"
        f"────────────────────"
    )

def get_status_name(status_id):
    """Преобразует ID статуса в читаемое название"""
    status_mapping = {
        1: "🆕 Новая",
        2: "🔄 В обработке", 
        3: "✅ Решена",
        4: "☑️ Проверена",
        5: "❌ Закрыта",
        6: "⏳ Ожидание"
    }
    return status_mapping.get(status_id, f"❓ Неизвестный статус ({status_id})")

def get_priority_name(priority_id):
    priority_mapping = {
        1: "🟢 Очень ниизкая",
        2: "🟡 Низкая",
        3: "🔴 Средняя",
        4: "🚨 Высокая",
        5: "⚡ Очень высокая"
    }
    return priority_mapping.get(priority_id, f"❓ Неизвестный приоритет ({priority_id})")

def get_type_name(type_id):
    type_mapping = {
        1: "Инцидент",
        2: "Запрос"
    }
    return type_mapping.get(type_id, f"❓ Неизвестный тип ({type_id})")

def clean_html_content(text):
    """Удаляет HTML-теги и преобразует HTML-сущности в нормальные символы"""
    if not text:
        return ""
    
    # Заменяем HTML-сущности на соответствующие символы
    text = unescape(text)
    
    # Удаляем HTML-теги
    text = re.sub(r'<[^>]+>', '', text)
    
    # Удаляем лишние пробелы и переносы строк
    text = ' '.join(text.split())
    
    return text