from telegram import Update
from telegram.ext import ContextTypes

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я твой первый бот 🙂")

# Обработчик обычного текста (эхо)
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Ты сказал: {update.message.text}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Команды:\n"
        "/start — поздороваться\n"
        "/help — чем я умею помогать\n\n"
        "Просто напишите текст — я отвечу эхом."
    )
    await update.message.reply_text(text)