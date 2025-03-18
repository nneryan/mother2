import os
import random
import logging
import telebot
from telebot import types
from collections import deque
import threading
import time

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Получаем токен бота из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("Токен бота не указан! Проверьте переменную окружения BOT_TOKEN")
    raise ValueError("BOT_TOKEN не указан")

# Создаем экземпляр бота
bot = telebot.TeleBot(BOT_TOKEN)

# Состояния пользователей
USER_SEARCHING = 1      # Ищет собеседника
USER_CHATTING = 2       # Общается с собеседником
USER_IDLE = 0           # Не в поиске и не в чате

# Хранилище данных
users = {}              # Словарь пользователей и их состояний
search_queue = deque()  # Очередь людей в поиске
chat_pairs = {}         # Пары собеседников: user_id -> companion_id

# Статистика
total_messages = 0
started_chats = 0
active_chats = 0

# Блокировка для безопасного доступа к общим данным
users_lock = threading.RLock()

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

def create_chat_pair(user1_id, user2_id):
    """Создать пару для чата"""
    global started_chats, active_chats
    with users_lock:
        chat_pairs[user1_id] = user2_id
        chat_pairs[user2_id] = user1_id
        set_user_state(user1_id, USER_CHATTING)
        set_user_state(user2_id, USER_CHATTING)
        started_chats += 1
        active_chats += 1

def end_chat(user_id):
    """Завершить чат для пользователя"""
    global active_chats
    with users_lock:
        if user_id in chat_pairs:
            companion_id = chat_pairs[user_id]
            if companion_id in chat_pairs:
                del chat_pairs[companion_id]
            del chat_pairs[user_id]
            set_user_state(user_id, USER_IDLE)
            set_user_state(companion_id, USER_IDLE)
            active_chats -= 1
            return companion_id
    return None

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
                
            create_chat_pair(user1_id, user2_id)
            
            # Уведомляем обоих пользователей о начале чата
            bot.send_message(user1_id, "🎭 Собеседник найден! Теперь вы можете общаться анонимно.")
            bot.send_message(user2_id, "🎭 Собеседник найден! Теперь вы можете общаться анонимно.")
            logger.info(f"Создана пара чата: {user1_id} <-> {user2_id}")

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
    elif state == USER_SEARCHING:
        markup.add(types.KeyboardButton("❌ Отменить поиск"))
        markup.add(types.KeyboardButton("ℹ️ Инфо"))
    elif state == USER_CHATTING:
        markup.add(types.KeyboardButton("👋 Закончить чат"))
        markup.add(types.KeyboardButton("🎲 Случайная тема"))
    
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
    bot.send_message(
        user_id, 
        "👋 Добро пожаловать в Анонимный Чат!\n\n"
        "Здесь вы можете общаться с незнакомцами анонимно.\n"
        "Нажмите '🔍 Найти собеседника', чтобы начать.",
        reply_markup=get_keyboard(user_id)
    )
    logger.info(f"Пользователь {user_id} запустил бота")

# Обработчик команды /help
@bot.message_handler(commands=['help'])
def help_command(message):
    user_id = message.from_user.id
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
    
    bot.send_message(
        user_id,
        f"📊 *Статистика бота:*\n\n"
        f"👥 Активных чатов: {active_chats}\n"
        f"🔄 Всего начато чатов: {started_chats}\n"
        f"💬 Всего сообщений: {total_messages}\n"
        f"⏳ Ожидают собеседника: {waiting_users}\n",
        parse_mode="Markdown",
        reply_markup=get_keyboard(user_id)
    )

# Обработчик кнопок
@bot.message_handler(func=lambda message: message.text in ["🔍 Найти собеседника", "❌ Отменить поиск", "👋 Закончить чат", "ℹ️ Инфо", "🎲 Случайная тема"])
def button_handler(message):
    user_id = message.from_user.id
    text = message.text
    
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
                logger.info(f"Пользователь {user_id} запросил случайную тему")
        else:
            bot.send_message(
                user_id,
                "❗ Вы не находитесь в чате.",
                reply_markup=get_keyboard(user_id)
            )

# Обработчик для всех текстовых сообщений
@bot.message_handler(content_types=['text'])
def handle_text(message):
    global total_messages
    user_id = message.from_user.id
    state = get_user_state(user_id)
    
    if state == USER_CHATTING:
        companion_id = chat_pairs.get(user_id)
        if companion_id:
            bot.send_message(companion_id, message.text)
            total_messages += 1
    else:
        # Если пользователь не в чате и не нажал кнопку
        if message.text not in ["🔍 Найти собеседника", "❌ Отменить поиск", "👋 Закончить чат", "ℹ️ Инфо"]:
            bot.send_message(
                user_id,
                "Используйте кнопки внизу для взаимодействия с ботом или введите /help для помощи.",
                reply_markup=get_keyboard(user_id)
            )

# Обработчик для фото
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    user_id = message.from_user.id
    state = get_user_state(user_id)
    
    if state == USER_CHATTING:
        companion_id = chat_pairs.get(user_id)
        if companion_id:
            # Получаем фото с наивысшим разрешением
            photo = message.photo[-1]
            file_id = photo.file_id
            
            # Передаем фото собеседнику
            caption = message.caption if message.caption else ""
            bot.send_photo(companion_id, photo=file_id, caption=caption)
            global total_messages
            total_messages += 1
    else:
        bot.send_message(
            user_id,
            "Вы не находитесь в чате. Нажмите '🔍 Найти собеседника', чтобы начать.",
            reply_markup=get_keyboard(user_id)
        )

# Обработчик для стикеров
@bot.message_handler(content_types=['sticker'])
def handle_sticker(message):
    user_id = message.from_user.id
    state = get_user_state(user_id)
    
    if state == USER_CHATTING:
        companion_id = chat_pairs.get(user_id)
        if companion_id:
            file_id = message.sticker.file_id
            bot.send_sticker(companion_id, file_id)
            global total_messages
            total_messages += 1
    else:
        bot.send_message(
            user_id,
            "Вы не находитесь в чате. Нажмите '🔍 Найти собеседника', чтобы начать.",
            reply_markup=get_keyboard(user_id)
        )

# Обработчик для голосовых сообщений
@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    user_id = message.from_user.id
    state = get_user_state(user_id)
    
    if state == USER_CHATTING:
        companion_id = chat_pairs.get(user_id)
        if companion_id:
            file_id = message.voice.file_id
            bot.send_voice(companion_id, file_id)
            global total_messages
            total_messages += 1
    else:
        bot.send_message(
            user_id,
            "Вы не находитесь в чате. Нажмите '🔍 Найти собеседника', чтобы начать.",
            reply_markup=get_keyboard(user_id)
        )

# Обработчик для остальных типов сообщений
@bot.message_handler(content_types=['audio', 'document', 'video', 'video_note', 'location', 'contact', 'animation'])
def handle_other(message):
    user_id = message.from_user.id
    state = get_user_state(user_id)
    
    if state != USER_CHATTING:
        bot.send_message(
            user_id,
            "Вы не находитесь в чате. Нажмите '🔍 Найти собеседника', чтобы начать.",
            reply_markup=get_keyboard(user_id)
        )
        return
        
    companion_id = chat_pairs.get(user_id)
    if not companion_id:
        return
        
    global total_messages
    total_messages += 1
        
    # Определяем тип сообщения и пересылаем соответственно
    if message.content_type == 'audio':
        bot.send_audio(companion_id, message.audio.file_id, caption=message.caption)
    elif message.content_type == 'document':
        bot.send_document(companion_id, message.document.file_id, caption=message.caption)
    elif message.content_type == 'video':
        bot.send_video(companion_id, message.video.file_id, caption=message.caption)
    elif message.content_type == 'video_note':
        bot.send_video_note(companion_id, message.video_note.file_id)
    elif message.content_type == 'location':
        bot.send_location(companion_id, message.location.latitude, message.location.longitude)
    elif message.content_type == 'contact':
        bot.send_message(companion_id, "Собеседник пытался отправить контакт, но это запрещено в целях анонимности.")
        bot.send_message(user_id, "⚠️ Отправка контактов запрещена в целях анонимности.")
    elif message.content_type == 'animation':
        bot.send_animation(companion_id, message.animation.file_id, caption=message.caption)

if __name__ == "__main__":
    logger.info("Запуск анонимного чат-бота...")
    # Запуск бота в режиме polling
    bot.infinity_polling()
    logger.info("Бот остановлен")
