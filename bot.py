import os
import telebot
import logging

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
logger.info(f"Бот инициализирован с токеном: {BOT_TOKEN[:5]}...")

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def start_command(message):
    logger.info(f"Получена команда /start от пользователя {message.from_user.id}")
    bot.send_message(message.chat.id, "Маму твою знал лично!")

# Обработчик для всех текстовых сообщений
@bot.message_handler(content_types=['text'])
def handle_text(message):
    logger.info(f"Получено текстовое сообщение от пользователя {message.from_user.id}: {message.text[:10]}...")
    bot.send_message(message.chat.id, "Маму твою знал лично!")

if __name__ == "__main__":
    logger.info("Запуск бота в режиме polling...")
    # Запуск бота в режиме polling
    bot.infinity_polling()
    logger.info("Бот остановлен")
