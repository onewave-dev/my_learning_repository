from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, ApplicationHandlerStop
from telegram.error import TelegramError, TimedOut
from telegram.constants import ChatAction

import asyncio

from datetime import datetime, timezone

import logging
log = logging.getLogger("app.handlers")  # дочерний логгер

THROTTLE_SECONDS = 1.0  # задержка от спама, время можно менять под себя

# глобальный throttle (защита от бот-сообщений)
async def global_throttle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = getattr(update, "effective_user", None)
    user_id = getattr(user, "id", None)
    if user_id is None:
        return  # нет пользователя — не троттлим

    now = datetime.now(timezone.utc).timestamp()
    last = context.user_data.get("last_msg_ts", 0.0)

    if now - last < THROTTLE_SECONDS:
        # НЕ отвечаем в чат (чтобы не спамить),
        # просто жёстко прерываем обработку этого апдейта
        raise ApplicationHandlerStop()

    context.user_data["last_msg_ts"] = now
    # обычный return → остальные хэндлеры выполнятся

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # счетчик запусков
    # 1) достаём старое значение (или 0, если его ещё нет)
    visits = context.user_data.get("visits", 0)

    # 2) увеличиваем на 1
    visits += 1
    context.user_data["visits"] = visits

    # 3) формируем ответ
    text = (
        f"Привет! Я твой первый бот 🙂\n"
        f"Ты запускал команду /start уже {visits} раз(а)."
    )
    await update.message.reply_text(text)

# Обработчик обычного текста (эхо)
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Ты сказал: {update.message.text}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Команды:\n"
        "/start — поздороваться\n"
        "/help — чем я умею помогать\n"
        "/settings — открыть настройки\n"
        "Просто напишите текст — я отвечу эхом."
    )
    await update.message.reply_text(text)

# Обработчик диалога
ASK_NAME = 0  # состояние диалога

async def survey_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.debug("start(): entered")
    # 🔹 Показываем "печатает..." перед отправкой вопроса
    await update.message.chat.send_action(ChatAction.TYPING)
    await asyncio.sleep(2)  # небольшая пауза для "естественности"
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

async def non_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # короткий ответ на любые сообщения, которые не являются текстом/командой
    await update.message.reply_text(
        "Я пока понимаю только текст и команды. Попробуй написать сообщение 🙂 или команду /help"
    )

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # короткая подсказка и направление к /help
    await update.message.reply_text("Не знаю такую команду. Напиши /help 🙂")
    
async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = context.user_data.get("name")
    if name:
        await update.message.reply_text(f"Ты представился как: {name}")
    else:
        await update.message.reply_text("Я пока не знаю, как тебя зовут. Запусти /survey 🙂")



# /settings — показать кнопки
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.chat.send_action(ChatAction.TYPING)
    await asyncio.sleep(1.2)
    # 1) читаем текущее состояние (например, подписка)
    is_subscribed = bool(context.user_data.get("subscribed"))

    # 2) собираем клавиатуру (кнопки в один ряд)
    keyboard = [
        [
            InlineKeyboardButton(
                text="✅ Подписка ВКЛ" if is_subscribed else "🔔 Включить подписку",
                callback_data="settings:toggle_sub"
            ),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # 3) отправляем сообщение с клавиатурой
    await update.message.reply_text("Настройки:", reply_markup=reply_markup)

# обработка нажатий на кнопки из /settings
async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # важно: подтвердить нажатие, чтобы Telegram убрал "часики"

    # 1) проверяем, какая кнопка нажата
    if query.data == "settings:toggle_sub":
        # 2) переключаем флаг в user_data
        is_subscribed = bool(context.user_data.get("subscribed"))
        context.user_data["subscribed"] = not is_subscribed
        new_state = "включена" if context.user_data["subscribed"] else "выключена"

        # 3) обновляем текст сообщения и клавиатуру под новое состояние
        keyboard = [
            [
                InlineKeyboardButton(
                    text="✅ Подписка ВКЛ" if context.user_data["subscribed"] else "🔔 Включить подписку",
                    callback_data="settings:toggle_sub"
                ),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text=f"Настройки:\nПодписка {new_state}.",
            reply_markup=reply_markup
        )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    # 1) Тихо логируем стек с максимумом контекста
    user_id = getattr(getattr(update, "effective_user", None), "id", None)
    chat_id = getattr(getattr(update, "effective_chat", None), "id", None)
    update_type = type(update).__name__ if update else "None"
    log.exception("Handler error | user=%s chat=%s update=%s", user_id, chat_id, update_type)
    context.application.logger.exception(
        "Handler error | user=%s chat=%s update=%s",
        user_id, chat_id, update_type
    )


    # 2) Аккуратно уведомим пользователя (если можно ответить)
    try:
        if update and getattr(update, "effective_message", None):
            await update.effective_message.reply_text(
                "Упс… Что-то пошло не так. Попробуйте ещё раз позже."
            )
    except (TelegramError, TimedOut):
        # если даже ответить не удалось — просто молчим
        pass