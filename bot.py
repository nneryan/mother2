import os
import logging
import telebot

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
if ADMIN_IDS:
    try:
        ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS.split(',') if admin_id.strip().isdigit()]
        logger.info(f"Список администраторов: {ADMIN_IDS}")
    except Exception as e:
        logger.error(f"Ошибка при обработке ADMIN_IDS: {e}")
        ADMIN_IDS = []
else:
    ADMIN_IDS = []
    logger.warning("ADMIN_IDS не указаны. Администраторы отсутствуют.")

# Создаем экземпляр бота
bot = telebot.TeleBot(BOT_TOKEN)

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    
    if user_id in ADMIN_IDS:
        bot.send_message(
            user_id, 
            "👋 Добро пожаловать в Анонимный Чат!\n\n"
            "Вы авторизованы как администратор.\n"
            "Для доступа к полной функциональности требуется переустановка бота."
        )
    else:
        bot.send_message(
            user_id, 
            "👋 Добро пожаловать в Анонимный Чат!\n\n"
            "Здесь вы можете общаться с незнакомцами анонимно.\n"
            "Для доступа к полной функциональности требуется переустановка бота."
        )
    
    logger.info(f"Пользователь {user_id} запустил бота")

# Обработчик команды /debug
@bot.message_handler(commands=['debug'])
def debug_command(message):
    user_id = message.from_user.id
    
    if user_id in ADMIN_IDS:
        # Собираем отладочную информацию
        debug_info = (
            f"🔍 *Отладочная информация:*\n\n"
            f"Токен бота: {'Установлен (скрыт)' if BOT_TOKEN else 'Не установлен'}\n"
            f"Админы: {ADMIN_IDS if ADMIN_IDS else 'Не установлены'}\n"
            f"Ваш ID: {user_id}\n"
            f"Версия pyTelegramBotAPI: {telebot.__version__}\n"
            f"Переменные окружения: {list(os.environ.keys())}\n"
        )
        
        bot.send_message(user_id, debug_info, parse_mode="Markdown")
    else:
        bot.send_message(user_id, "⛔ У вас нет доступа к этой команде.")

# Обработчик для всех текстовых сообщений
@bot.message_handler(content_types=['text'])
def handle_text(message):
    user_id = message.from_user.id
    bot.send_message(
        user_id,
        "Бот находится в режиме отладки. Доступны только команды /start и /debug."
    )

if __name__ == "__main__":
    logger.info("Запуск отладочной версии анонимного чат-бота...")
    try:
        # Запуск бота в режиме polling
        bot.infinity_polling()
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
    logger.info("Бот остановлен")
