from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

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

# Обработчик диалога
ASK_NAME = 0  # состояние диалога

async def survey_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Давай познакомимся! Как тебя зовут?")
    return ASK_NAME

async def survey_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.text
    context.user_data["name"] = user_name   # 📝 сохраняем в словарь user_data
    await update.message.reply_text(f"Приятно познакомиться, {user_name}!")
    return ConversationHandler.END

async def survey_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Опрос отменён.")
    return ConversationHandler.END

async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = context.user_data.get("name")
    if name:
        await update.message.reply_text(f"Ты представился как: {name}")
    else:
        await update.message.reply_text("Я пока не знаю, как тебя зовут. Запусти /survey 🙂")