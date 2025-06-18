import os
import requests
import asyncio
from datetime import datetime
from typing import Dict, Tuple, Optional
import difflib
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

# Глобальное хранилище последних состояний заявок
last_ticket_states: Dict[int, int] = {}

async def on_startup(bot: Bot):
    """Запускается при старте бота"""
    asyncio.create_task(check_ticket_updates(bot))


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
        user_tickets = []
        for ticket in all_tickets:
            # Проверяем по ID в содержимом
            if f"Telegram (ID: {telegram_id})" in ticket.get('content', ''):
                user_tickets.append(ticket)
            # Или проверяем по автору заявки (если API возвращает эту информацию)
            elif ticket.get('users_id_recipient') == session_data.get('user_id'):
                user_tickets.append(ticket)
        return user_tickets

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
    
    short_content = (formatted_content[:200] + '...') if len(formatted_content) > 200 else formatted_content
    
    return (
        f"🔹 #{ticket.get('id', 'N/A')}\n"
        f"📌 Тема: {ticket.get('name', 'Без названия')}\n"
        f"📝 Описание: {short_content}\n"
        f"🔄 Статус: {get_status_name(ticket.get('status', 0))}\n"
        f"📅 Дата создания: {ticket.get('date', 'N/A')}\n"
        f"⚠️ Срочность: {get_urgency_name(ticket.get('urgency', 0))}\n"
        f"🔔 Тип: {get_type_name(ticket.get('type', 0))}\n"
        f"────────────────────"
    )

def get_status_name(status_id):
    """Преобразует ID статуса в читаемое название"""
    status_mapping = {
        1: "🆕 Новая",
        2: "🔄 В работе(назначена)", 
        3: "☑️ В работе(запланирована)",
        4: "⏳ В ожидании",
        5: "✅ Решена",
        6: "❌ Закрыта"  
    }
    return status_mapping.get(status_id, f"❓ Неизвестный статус ({status_id})")

def get_urgency_name(urgency_id):
    urgency_mapping = {
        1: "🟢 Очень ниизкая",
        2: "🟡 Низкая",
        3: "🟠 Средняя",
        4: "🚨 Высокая",
        5: "⚡ Очень высокая"
    }
    return urgency_mapping.get(urgency_id, f"❓ Неизвестная срочность ({urgency_id})")

def get_impact_name(impact_id):
    impact_mapping = {
        1: "🟢 Очень ниизкое",
        2: "🟡 Низкое",
        3: "🟠 Среднее",
        4: "🚨 Высокое",
        5: "⚡ Очень высокое"
    }
    return impact_mapping.get(impact_id, f"❓ Неизвестное влияение ({impact_id})")

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


# Функция для получения информации о конкретной заявке
def get_ticket_details(session_token: str, ticket_id: int) -> dict:
    """Получает детали заявки по ID"""
    url = f"{GLPI_URL}/Ticket/{ticket_id}"
    headers = {
        "Content-Type": "application/json",
        "App-Token": GLPI_API_KEY,
        "Session-Token": session_token
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"Ошибка получения заявки {ticket_id}: {str(e)}")
        return None

# Функция для поиска Telegram ID инициатора заявки
def find_telegram_id_in_content(content: str) -> int:
    """Ищет Telegram ID в содержимом заявки"""
    if not content:
        return None
    
    match = re.search(r"Telegram \(ID: (\d+)\)", content)
    return int(match.group(1)) if match else None


# Какие поля отслеживаем для изменений
TRACKED_FIELDS = {
    'status': ('🔄 Статус', get_status_name),
    'urgency': ('⚠️ Срочность', get_urgency_name),
    'impact': ('📍 Влияние', get_impact_name),
    'type': ('🔔 Тип', get_type_name),
    'name': ('📌 Тема', str),
    'content': ('📝 Описание', lambda x: x[:100] + '...' if len(x) > 100 else x),
    'time_to_resolve': ('⏳ Срок решения', str),
}

async def check_ticket_updates(bot: Bot):
    """Периодически проверяет изменения заявок и отправляет уведомления"""
    while True:
        try:
            # Для каждого авторизованного пользователя проверяем его заявки
            for telegram_id, session_data in user_sessions.items():
                session_token = session_data.get('session_token')
                if not session_token:
                    continue
                
                tickets = get_user_tickets(session_data, telegram_id)
                if not tickets:
                    continue
                
                for ticket in tickets:
                    ticket_id = ticket.get('id')
                    current_data = {
                        'id': ticket_id,
                        'name': ticket.get('name'),
                        'content': clean_html_content(ticket.get('content', '')),
                        'status': ticket.get('status'),
                        'urgency': ticket.get('urgency'),
                        'impact': ticket.get('impact'),
                        'type': ticket.get('type'),
                        'time_to_resolve': ticket.get('time_to_resolve'),
                    }
                    
                    # Если заявка новая, сохраняем ее состояние
                    if ticket_id not in last_ticket_states:
                        last_ticket_states[ticket_id] = current_data
                        continue
                    
                    # Получаем предыдущее состояние
                    previous_data = last_ticket_states[ticket_id]
                    
                    # Проверяем изменения по всем отслеживаемым полям
                    changes = detect_ticket_changes(previous_data, current_data)
                    
                    if changes:
                        # Получаем полные данные заявки
                        ticket_details = get_ticket_details(session_token, ticket_id)
                        if not ticket_details:
                            continue
                        
                        # Ищем Telegram ID инициатора
                        initiator_id = find_telegram_id_in_content(ticket_details.get('content', ''))
                        if not initiator_id:
                            continue
                        
                        # Формируем и отправляем уведомление об изменениях
                        await send_ticket_update_notification(
                            bot, 
                            ticket_id, 
                            previous_data, 
                            current_data, 
                            changes, 
                            initiator_id
                        )
                        
                        # Обновляем последнее известное состояние
                        last_ticket_states[ticket_id] = current_data
            
            await asyncio.sleep(10)  # Пауза между проверками
            
        except Exception as e:
            print(f"Ошибка в check_ticket_updates: {str(e)}")
            await asyncio.sleep(10)

def detect_ticket_changes(previous: dict, current: dict) -> Dict[str, Tuple]:
    """Обнаруживает изменения между состояниями заявки"""
    changes = {}
    
    for field, (field_name, formatter) in TRACKED_FIELDS.items():
        prev_value = previous.get(field)
        curr_value = current.get(field)
        
        if prev_value != curr_value:
            changes[field] = (
                field_name,
                formatter(prev_value) if prev_value is not None else "Не указано",
                formatter(curr_value) if curr_value is not None else "Не указано"
            )
    
    return changes

async def send_ticket_update_notification(
    bot: Bot, 
    ticket_id: int, 
    previous_data: dict, 
    current_data: dict, 
    changes: Dict[str, Tuple], 
    user_id: int
):
    """Отправляет уведомление об изменениях в заявке"""
    message_lines = [
        f"📢 Изменения в заявке #{ticket_id}",
        f"📌 Тема: {current_data.get('name', 'Без названия')}",
        "",
        "Измененные параметры:"
    ]
    
    for field, (field_name, prev_val, curr_val) in changes.items():
        message_lines.append(f"▫️ {field_name}:")
        message_lines.append(f"    Было: {prev_val}")
        message_lines.append(f"    Стало: {curr_val}")
        message_lines.append("")
    
    message_lines.extend([
        f"📅 Последнее изменение: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    ])
    
    try:
        await bot.send_message(
            user_id,
            "\n".join(message_lines),
            disable_web_page_preview=True
        )
    except Exception as e:
        print(f"Ошибка отправки уведомления пользователю {user_id}: {str(e)}")

def get_ticket_details(session_token: str, ticket_id: int) -> Optional[dict]:
    """Получает полные детали заявки"""
    url = f"{GLPI_URL}/Ticket/{ticket_id}"
    headers = {
        "Content-Type": "application/json",
        "App-Token": GLPI_API_KEY,
        "Session-Token": session_token
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Ошибка получения деталей заявки {ticket_id}: {str(e)}")
    return None
