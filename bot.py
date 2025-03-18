import os
import random
import logging
import telebot
from telebot import types
from collections import deque, defaultdict
import threading
import time
import json

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Получаем токен бота из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("Токен бота не указан! Проверьте переменную окружения BOT_TOKEN")
    raise ValueError("BOT_TOKEN не указан")

# Получаем список админов из переменных окружения
ADMIN_IDS = os.environ.get('ADMIN_IDS', '')
ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS.split(',') if admin_id.strip().isdigit()]
logger.info(f"Список администраторов: {ADMIN_IDS}")

# Создаем экземпляр бота
bot = telebot.TeleBot(BOT_TOKEN)

# Состояния пользователей
USER_SEARCHING = 1      # Ищет собеседника
USER_CHATTING = 2       # Общается с собеседником
USER_IDLE = 0           # Не в поиске и не в чате
ADMIN_MONITORING = 3    # Админ мониторит чат

# Хранилище данных
users = {}              # Словарь пользователей и их состояний
search_queue = deque()  # Очередь людей в поиске
chat_pairs = {}         # Пары собеседников: user_id -> companion_id
chat_history = defaultdict(list)  # История сообщений: chat_id -> [messages]
admin_monitoring = {}   # Какой админ мониторит какой чат: admin_id -> chat_id
chat_id_map = {}        # Мэппинг пар пользователей к unique chat_id: (user1_id, user2_id) -> chat_id

# Статистика
total_messages = 0
started_chats = 0
active_chats = 0

# Блокировка для безопасного доступа к общим данным
users_lock = threading.RLock()
history_lock = threading.RLock()

def get_user_state(user_id):
    """Получить состояние пользователя"""
    with users_lock:
        return users.get(user_id, {}).get('state', USER_IDLE)

def set_user_state(user_id, state):
    """Установить состояние пользователя"""
    with users_lock:
        if user_id not in users:
            users[user_id] = {'state': state}
        else:
            users[user_id]['state'] = state

def add_to_search_queue(user_id):
    """Добавить пользователя в очередь поиска"""
    with users_lock:
        if user_id not in search_queue:
            search_queue.append(user_id)
            set_user_state(user_id, USER_SEARCHING)

def remove_from_search_queue(user_id):
    """Удалить пользователя из очереди поиска"""
    with users_lock:
        if user_id in search_queue:
            search_queue.remove(user_id)
        if get_user_state(user_id) == USER_SEARCHING:
            set_user_state(user_id, USER_IDLE)

def generate_chat_id():
    """Генерировать уникальный ID для чата"""
    return str(int(time.time())) + str(random.randint(1000, 9999))

def create_chat_pair(user1_id, user2_id):
    """Создать пару для чата"""
    global started_chats, active_chats
    with users_lock:
        chat_pairs[user1_id] = user2_id
        chat_pairs[user2_id] = user1_id
        set_user_state(user1_id, USER_CHATTING)
        set_user_state(user2_id, USER_CHATTING)
        
        # Создаем уникальный ID для чата
        chat_id = generate_chat_id()
        chat_id_map[(user1_id, user2_id)] = chat_id
        chat_id_map[(user2_id, user1_id)] = chat_id
        
        started_chats += 1
        active_chats += 1
        
        # Инициализируем историю чата
        with history_lock:
            chat_history[chat_id] = []
        
        return chat_id

def get_chat_id(user_id):
    """Получить ID чата пользователя"""
    with users_lock:
        companion_id = chat_pairs.get(user_id)
        if companion_id:
            return chat_id_map.get((user_id, companion_id))
    return None

def add_message_to_history(chat_id, sender_id, message_type, content, file_id=None):
    """Добавить сообщение в историю чата"""
    if not chat_id:
        return
        
    with history_lock:
        chat_history[chat_id].append({
            'timestamp': time.time(),
            'sender_id': sender_id,
            'type': message_type,
            'content': content,
            'file_id': file_id
        })

def end_chat(user_id):
    """Завершить чат для пользователя"""
    global active_chats
    with users_lock:
        if user_id in chat_pairs:
            companion_id = chat_pairs[user_id]
            
            # Получаем ID чата перед удалением
            chat_id = chat_id_map.get((user_id, companion_id))
            
            # Удаляем записи о паре
            if companion_id in chat_pairs:
                del chat_pairs[companion_id]
            del chat_pairs[user_id]
            
            # Отключаем админов от мониторинга завершенного чата
            admins_to_update = []
            for admin_id, monitored_chat in admin_monitoring.items():
                if monitored_chat == chat_id:
                    admins_to_update.append(admin_id)
            
            for admin_id in admins_to_update:
                del admin_monitoring[admin_id]
                set_user_state(admin_id, USER_IDLE)
                bot.send_message(
                    admin_id,
                    "⚠️ Чат, который вы мониторили, был завершен.",
                    reply_markup=get_admin_keyboard(admin_id)
                )
            
            # Удаляем маппинг ID чата
            if (user_id, companion_id) in chat_id_map:
                del chat_id_map[(user_id, companion_id)]
            if (companion_id, user_id) in chat_id_map:
                del chat_id_map[(companion_id, user_id)]
            
            # Сохраняем историю чата на диск
            with history_lock:
                if chat_id and chat_id in chat_history:
                    save_chat_history(chat_id)
                    del chat_history[chat_id]
            
            set_user_state(user_id, USER_IDLE)
            set_user_state(companion_id, USER_IDLE)
            active_chats -= 1
            return companion_id
    return None

def save_chat_history(chat_id):
    """Сохранить историю чата в файл"""
    try:
        with history_lock:
            if chat_id in chat_history:
                history_dir = "chat_histories"
                os.makedirs(history_dir, exist_ok=True)
                filename = f"{history_dir}/chat_{chat_id}.json"
                
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(chat_history[chat_id], f, ensure_ascii=False, indent=2)
                
                logger.info(f"История чата {chat_id} сохранена в {filename}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении истории чата {chat_id}: {e}")

def find_chat_match():
    """Найти пары среди ищущих собеседников"""
    with users_lock:
        while len(search_queue) >= 2:
            user1_id = search_queue.popleft()
            user2_id = search_queue.popleft()
            
            # Проверяем, что пользователи всё еще ищут чат
            if get_user_state(user1_id) != USER_SEARCHING or get_user_state(user2_id) != USER_SEARCHING:
                # Возвращаем в очередь только тех, кто еще ищет
                if get_user_state(user1_id) == USER_SEARCHING:
                    search_queue.append(user1_id)
                if get_user_state(user2_id) == USER_SEARCHING:
                    search_queue.append(user2_id)
                continue
                
            chat_id = create_chat_pair(user1_id, user2_id)
            
            # Уведомляем обоих пользователей о начале чата
            bot.send_message(user1_id, "🎭 Собеседник найден! Теперь вы можете общаться анонимно.", reply_markup=get_keyboard(user1_id))
            bot.send_message(user2_id, "🎭 Собеседник найден! Теперь вы можете общаться анонимно.", reply_markup=get_keyboard(user2_id))
            
            # Оповещаем админов о новом чате
            notify_admins_about_new_chat(chat_id, user1_id, user2_id)
            
            logger.info(f"Создана пара чата: {user1_id} <-> {user2_id}, chat_id: {chat_id}")

def notify_admins_about_new_chat(chat_id, user1_id, user2_id):
    """Оповестить админов о новом чате"""
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(
                admin_id,
                f"🆕 Создан новый чат: {chat_id}\n👤 Участники: {user1_id}, {user2_id}",
                reply_markup=get_admin_keyboard(admin_id)
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления админу {admin_id}: {e}")

# Периодический поиск пар
def search_pairs_periodically():
    while True:
        try:
            find_chat_match()
        except Exception as e:
            logger.error(f"Ошибка при поиске пар: {e}")
        time.sleep(1)  # Проверяем каждую секунду

# Запуск потока для поиска пар
search_thread = threading.Thread(target=search_pairs_periodically, daemon=True)
search_thread.start()

# Функция для создания клавиатуры в зависимости от состояния пользователя
def get_keyboard(user_id):
    state = get_user_state(user_id)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    if state == USER_IDLE:
        markup.add(types.KeyboardButton("🔍 Найти собеседника"))
        markup.add(types.KeyboardButton("ℹ️ Инфо"))
        # Добавляем админскую кнопку, если пользователь админ
        if user_id in ADMIN_IDS:
            markup.add(types.KeyboardButton("👁️ Админ-панель"))
    elif state == USER_SEARCHING:
        markup.add(types.KeyboardButton("❌ Отменить поиск"))
        markup.add(types.KeyboardButton("ℹ️ Инфо"))
    elif state == USER_CHATTING:
        markup.add(types.KeyboardButton("👋 Закончить чат"))
        markup.add(types.KeyboardButton("🎲 Случайная тема"))
    elif state == ADMIN_MONITORING:
        markup.add(types.KeyboardButton("⬅️ Вернуться в админ-панель"))
    
    return markup

def get_admin_keyboard(admin_id):
    """Клавиатура для админа"""
    state = get_user_state(admin_id)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    if state == USER_IDLE or state == ADMIN_MONITORING:
        markup.add(types.KeyboardButton("📊 Статистика"))
        markup.add(types.KeyboardButton("🔍 Активные чаты"))
        markup.add(types.KeyboardButton("📜 История чатов"))
        markup.add(types.KeyboardButton("⬅️ Выйти из админ-панели"))
    
    return markup

# Список случайных тем для разговора
RANDOM_TOPICS = [
    "Какое ваше любимое место, где вы побывали?",
    "Расскажите о последнем фильме, который вас впечатлил.",
    "Если бы вы могли приобрести любой навык моментально, что бы это было?",
    "Какую еду вы могли бы есть каждый день?",
    "Что бы вы сделали, если бы выиграли миллион?",
    "Какая ваша любимая книга и почему?",
    "Есть ли у вас необычное хобби?",
    "Какой период истории вам интересен больше всего?",
    "Как выглядит ваш идеальный выходной день?",
    "Если бы вы могли жить в любой стране, какую бы выбрали?"
]

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    keyboard = get_keyboard(user_id)
    
    # Если пользователь админ, показываем админское приветствие
    if user_id in ADMIN_IDS:
        bot.send_message(
            user_id, 
            "👋 Добро пожаловать в Анонимный Чат!\n\n"
            "Вы авторизованы как администратор.\n"
            "Для доступа к панели администратора нажмите '👁️ Админ-панель'.",
            reply_markup=keyboard
        )
    else:
        bot.send_message(
            user_id, 
            "👋 Добро пожаловать в Анонимный Чат!\n\n"
            "Здесь вы можете общаться с незнакомцами анонимно.\n"
            "Нажмите '🔍 Найти собеседника', чтобы начать.",
            reply_markup=keyboard
        )
    
    logger.info(f"Пользователь {user_id} запустил бота")

# Обработчик команды /help
@bot.message_handler(commands=['help'])
def help_command(message):
    user_id = message.from_user.id
    
    if user_id in ADMIN_IDS:
        bot.send_message(
            user_id,
            "📌 *Как пользоваться ботом:*\n\n"
            "• Нажмите '🔍 Найти собеседника' для поиска\n"
            "• Дождитесь, пока бот найдет вам собеседника\n"
            "• Общайтесь анонимно!\n"
            "• Нажмите '👋 Закончить чат', когда захотите завершить разговор\n\n"
            "📝 *Команды:*\n"
            "/start - Перезапустить бота\n"
            "/help - Показать это сообщение\n"
            "/stats - Статистика бота\n\n"
            "⚙️ *Админские команды:*\n"
            "/admin - Открыть админ-панель\n"
            "/monitor [chat_id] - Мониторить указанный чат",
            parse_mode="Markdown",
            reply_markup=get_keyboard(user_id)
        )
    else:
        bot.send_message(
            user_id,
            "📌 *Как пользоваться ботом:*\n\n"
            "• Нажмите '🔍 Найти собеседника' для поиска\n"
            "• Дождитесь, пока бот найдет вам собеседника\n"
            "• Общайтесь анонимно!\n"
            "• Нажмите '👋 Закончить чат', когда захотите завершить разговор\n\n"
            "📝 *Команды:*\n"
            "/start - Перезапустить бота\n"
            "/help - Показать это сообщение\n"
            "/stats - Статистика бота",
            parse_mode="Markdown",
            reply_markup=get_keyboard(user_id)
        )

# Обработчик команды /stats
@bot.message_handler(commands=['stats'])
def stats_command(message):
    user_id = message.from_user.id
    with users_lock:
        waiting_users = len(search_queue)
    
    stats_message = (
        f"📊 *Статистика бота:*\n\n"
        f"👥 Активных чатов: {active_chats}\n"
        f"🔄 Всего начато чатов: {started_chats}\n"
        f"💬 Всего сообщений: {total_messages}\n"
        f"⏳ Ожидают собеседника: {waiting_users}\n"
    )
    
    # Добавляем админскую статистику, если пользователь админ
    if user_id in ADMIN_IDS:
        with history_lock:
            saved_chats = len(os.listdir("chat_histories")) if os.path.exists("chat_histories") else 0
        
        stats_message += (
            f"\n👁️ *Админская статистика:*\n"
            f"📁 Сохраненных историй чатов: {saved_chats}\n"
            f"👮 Количество администраторов: {len(ADMIN_IDS)}\n"
        )
    
    bot.send_message(
        user_id,
        stats_message,
        parse_mode="Markdown",
        reply_markup=get_keyboard(user_id)
    )

# Обработчик команды /admin
@bot.message_handler(commands=['admin'])
def admin_command(message):
    user_id = message.from_user.id
    
    if user_id in ADMIN_IDS:
        bot.send_message(
            user_id,
            "👁️ *Панель администратора*\n\n"
            "Добро пожаловать в панель управления.\n"
            "Выберите действие с помощью кнопок ниже:",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard(user_id)
        )
        set_user_state(user_id, ADMIN_MONITORING)
    else:
        bot.send_message(
            user_id,
            "⛔ У вас нет доступа к панели администратора.",
            reply_markup=get_keyboard(user_id)
        )

# Обработчик команды /monitor
@bot.message_handler(commands=['monitor'])
def monitor_command(message):
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        bot.send_message(user_id, "⛔ У вас нет доступа к этой команде.")
        return
    
    # Получить chat_id из команды
    command_args = message.text.split()
    if len(command_args) < 2:
        bot.send_message(
            user_id,
            "❗ Укажите ID чата для мониторинга.\n"
            "Пример: `/monitor 16872938475`",
            parse_mode="Markdown"
        )
        return
    
    chat_id = command_args[1]
    
    # Проверяем, существует ли такой чат в истории
    with history_lock:
        if chat_id not in chat_history:
            active_chat_ids = list(chat_history.keys())
            if not active_chat_ids:
                bot.send_message(user_id, "❗ Указанный чат не найден. В данный момент нет активных чатов.")
            else:
                bot.send_message(
                    user_id, 
                    f"❗ Указанный чат не найден. Доступные чаты:\n\n" +
                    "\n".join([f"• `{chat_id}`" for chat_id in active_chat_ids]),
                    parse_mode="Markdown"
                )
            return
    
    # Добавляем админа в мониторинг чата
    admin_monitoring[user_id] = chat_id
    set_user_state(user_id, ADMIN_MONITORING)
    
    bot.send_message(
        user_id,
        f"👁️ Вы начали мониторинг чата {chat_id}.\n"
        "Теперь вы будете получать все сообщения из этого чата.\n"
        "Пользователи не узнают, что их чат мониторится.",
        reply_markup=get_keyboard(user_id)
    )
    
    # Отправляем историю чата
    with history_lock:
        if chat_id in chat_history and chat_history[chat_id]:
            bot.send_message(user_id, "📜 *История чата:*", parse_mode="Markdown")
            
            for msg in chat_history[chat_id]:
                sender = f"👤 Пользователь {msg['sender_id']}"
                content = msg['content']
                
                if msg['type'] == 'text':
                    bot.send_message(user_id, f"{sender}:\n{content}")
                elif msg['type'] == 'photo' and msg['file_id']:
                    bot.send_photo(user_id, msg['file_id'], caption=f"{sender}:\n{content if content else ''}")
                elif msg['type'] == 'sticker' and msg['file_id']:
                    bot.send_sticker(user_id, msg['file_id'])
                    bot.send_message(user_id, f"{sender} отправил стикер")
                elif msg['type'] == 'voice' and msg['file_id']:
                    bot.send_voice(user_id, msg['file_id'], caption=f"{sender}")
                # Другие типы сообщений можно добавить по аналогии

# Обработчик кнопок
@bot.message_handler(func=lambda message: message.text in [
    "🔍 Найти собеседника", "❌ Отменить поиск", "👋 Закончить чат", "ℹ️ Инфо", "🎲 Случайная тема",
    "👁️ Админ-панель", "⬅️ Вернуться в админ-панель", "⬅️ Выйти из админ-панели",
    "📊 Статистика", "🔍 Активные чаты", "📜 История чатов"
])
def button_handler(message):
    user_id = message.from_user.id
    text = message.text
    
    # Кнопки обычного пользователя
    if text == "🔍 Найти собеседника":
        state = get_user_state(user_id)
        if state == USER_IDLE:
            add_to_search_queue(user_id)
            bot.send_message(
                user_id, 
                "🔍 Ищем собеседника...\nПожалуйста, подождите.",
                reply_markup=get_keyboard(user_id)
            )
            logger.info(f"Пользователь {user_id} начал поиск собеседника")
        else:
            bot.send_message(
                user_id,
                "❗ Вы не можете начать поиск, пока находитесь в чате или уже ищете собеседника.",
                reply_markup=get_keyboard(user_id)
            )
    
    elif text == "❌ Отменить поиск":
        state = get_user_state(user_id)
        if state == USER_SEARCHING:
            remove_from_search_queue(user_id)
            bot.send_message(
                user_id,
                "🛑 Поиск отменен.",
                reply_markup=get_keyboard(user_id)
            )
            logger.info(f"Пользователь {user_id} отменил поиск собеседника")
        else:
            bot.send_message(
                user_id,
                "❗ Вы не находитесь в режиме поиска.",
                reply_markup=get_keyboard(user_id)
            )
    
    elif text == "👋 Закончить чат":
        state = get_user_state(user_id)
        if state == USER_CHATTING:
            companion_id = end_chat(user_id)
            if companion_id:
                bot.send_message(
                    companion_id,
                    "👋 Собеседник завершил чат.\nНажмите '🔍 Найти собеседника', чтобы начать новый разговор.",
                    reply_markup=get_keyboard(companion_id)
                )
            bot.send_message(
                user_id,
                "👋 Чат завершен.\nНажмите '🔍 Найти собеседника', чтобы начать новый разговор.",
                reply_markup=get_keyboard(user_id)
            )
            logger.info(f"Пользователь {user_id} завершил чат с {companion_id}")
        else:
            bot.send_message(
                user_id,
                "❗ Вы не находитесь в чате.",
                reply_markup=get_keyboard(user_id)
            )
    
    elif text == "ℹ️ Инфо":
        bot.send_message(
            user_id,
            "🤖 *О боте:*\n\n"
            "Этот бот позволяет анонимно общаться с случайными собеседниками.\n\n"
            "Ваши сообщения передаются напрямую собеседнику, бот не хранит историю переписки.\n\n"
            "Чтобы узнать больше команд, введите /help\n"
            "Для просмотра статистики введите /stats",
            parse_mode="Markdown",
            reply_markup=get_keyboard(user_id)
        )
    
    elif text == "🎲 Случайная тема":
        state = get_user_state(user_id)
        if state == USER_CHATTING:
            companion_id = chat_pairs.get(user_id)
            if companion_id:
                random_topic = random.choice(RANDOM_TOPICS)
                bot.send_message(user_id, f"🎲 *Случайная тема:* {random_topic}", parse_mode="Markdown")
                bot.send_message(companion_id, f"🎲 *Собеседник предложил тему:* {random_topic}", parse_mode="Markdown")
                
                # Сохраняем в историю чата
                chat_id = get_chat_id(user_id)
                if chat_id:
                    add_message_to_history(chat_id, user_id, "system", f"Предложил тему: {random_topic}")
                
                logger.info(f"Пользователь {user_id} запросил случайную тему")
        else:
            bot.send_message(
                user_id,
                "❗ Вы не находитесь в чате.",
                reply_markup=get_keyboard(user_id)
            )
    
    # Кнопки администратора
    elif text == "👁️ Админ-панель" and user_id in ADMIN_IDS:
        set_user_state(user_id, ADMIN_MONITORING)
        bot.send_message(
            user_id,
            "👁️ *Панель администратора*\n\n"
            "Выберите действие с помощью кнопок ниже:",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard(user_id)
        )
    
    elif text == "⬅️ Вернуться в админ-панель" and user_id in ADMIN_IDS:
        # Если админ мониторил чат, удаляем его из мониторинга
        if user_id in admin_monitoring:
            del admin_monitoring[user_id]
        
        set_user_state(user_id, ADMIN_MONITORING)
        bot.send_message(
            user_id,
            "👁️ *Панель администратора*\n\n"
            "Выберите действие с помощью кнопок ниже:",
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard(user_id)
        )
    
    elif text == "⬅️ Выйти из админ-панели" and user_id in ADMIN_IDS:
        # Если админ мониторил чат, удаляем его из мониторинга
        if user_id in admin_monitoring:
            del admin_monitoring[user_id]
        
        set_user_state(user_id, USER_IDLE)
        bot.send_message(
            user_id,
            "✅ Вы вышли из панели администратора.",
            reply_markup=get_keyboard(user_id)
        )
    
    elif text == "📊 Статистика" and user_id in ADMIN_IDS:
        with users_lock:
            waiting_users = len(search_queue)
            total_users = len(users)
        
        with history_lock:
            saved_chats = len(os.listdir("chat_histories")) if os.path.exists("chat_histories") else 0
            active_chat_ids = list(chat_history.keys())
        
        stats_message = (
            f"📊 *Подробная статистика бота:*\n\n"
            f"👥 Активных чатов: {active_chats}\n"
            f"🔄 Всего начато чатов: {started_chats}\n"
            f"💬 Всего сообщений: {total_messages}\n"
            f"⏳ Ожидают собеседника: {waiting_users}\n"
            f"👤 Всего пользователей: {total_users}\n"
            f"📁 Сохраненных историй чатов: {saved_chats}\n"
            f"👮 Количество администраторов: {len(ADMIN_IDS)}\n\n"
            f"🔍 *IDs активных чатов:*\n"
        )
        
        if active_chat_ids:
            for chat_id in active_chat_ids:
                stats_message += f"• `{chat_id}`\n"
        else:
            stats_message += "Нет активных чатов\n"
        
        bot.send_message(
            user_id,
            stats_message,
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard(user_id)
        )
    
    elif text == "🔍 Активные чаты" and user_id in ADMIN_IDS:
        with history_lock:
            active_chat_ids = list(chat_history.keys())
        
        if not active_chat_ids:
            bot.send_message(
                user_id,
                "❌ В данный момент нет активных чатов.",
                reply_markup=get_admin_keyboard(user_id)
            )
            return
        
        # Находим пользователей для каждого чата
        chat_users = {}
        with users_lock:
            for user1_id, user2_id in chat_id_map:
                chat_id = chat_id_map.get((user1_id, user2_id))
                if chat_id and chat_id in active_chat_ids:
                    chat_users[chat_id] = (user1_id, user2_id)
        
        message = "👁️ *Активные чаты:*\n\n"
        
        for chat_id in active_chat_ids:
            if chat_id in chat_users:
                user1_id, user2_id = chat_users[chat_id]
                message += f"🆔 Чат: `{chat_id}`\n"
                message += f"👤 Пользователи: {user1_id}, {user2_id}\n"
                message += f"💬 Сообщений: {len(chat_history[chat_id])}\n"
                message += f"🕒 Начат: {time.strftime('%d.%m.%Y %H:%M', time.localtime(chat_history[chat_id][0]['timestamp'] if chat_history[chat_id] else time.time()))}\n"
                message += f"📱 Мониторить: /monitor {chat_id}\n\n"
        
        bot.send_message(
            user_id,
            message,
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard(user_id)
        )
    
    elif text == "📜 История чатов" and user_id in ADMIN_IDS:
        history_dir = "chat_histories"
        if not os.path.exists(history_dir) or not os.listdir(history_dir):
            bot.send_message(
                user_id,
                "❌ История чатов пуста.",
                reply_markup=get_admin_keyboard(user_id)
            )
            return
        
        history_files = os.listdir(history_dir)
        history_files.sort(key=lambda f: os.path.getmtime(os.path.join(history_dir, f)), reverse=True)
        
        message = "📜 *История сохраненных чатов:*\n\n"
        
        for i, file_name in enumerate(history_files[:10]):  # Показываем только 10 последних чатов
            chat_id = file_name.replace("chat_", "").replace(".json", "")
            file_path = os.path.join(history_dir, file_name)
            file_time = time.strftime('%d.%m.%Y %H:%M', time.localtime(os.path.getmtime(file_path)))
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    chat_data = json.load(f)
                    message_count = len(chat_data)
                    
                    message += f"🆔 Чат: `{chat_id}`\n"
                    message += f"💬 Сообщений: {message_count}\n"
                    message += f"🕒 Завершен: {file_time}\n"
                    message += f"📱 Просмотреть: /view_history {chat_id}\n\n"
            except Exception as e:
                logger.error(f"Ошибка при чтении истории чата {file_name}: {e}")
        
        if len(history_files) > 10:
            message += f"...и еще {len(history_files) - 10} сохраненных чатов."
        
        bot.send_message(
            user_id,
            message,
            parse_mode="Markdown",
            reply_markup=get_admin_keyboard(user_id)
        )
